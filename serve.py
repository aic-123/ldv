# -*- coding: utf-8 -*-
"""服务化设计的**最小壳**（阶段 3.1 / 3.2 / 3.3，2026-10-16）。

按 [`docs/服务化设计.md`](docs/服务化设计.md) §2.3 的请求流：

    HTTP 请求 → 租户解析 → 壳加载租户语料 → `run_one(loaded=...)` → 响应

**三个阶段的落点**：
- **3.1** `GET /<tenant>/run` —— 同步跑（等价判据：与 CLI 逐字相同）
- **3.2** 租户解析 + 隔离（不完整/不存在 ⇒ 404，**不自动创建**）
- **3.3** `POST /<tenant>/run` 提交 + `GET /tasks/<id>` 轮询 ——
  **每租户同时最多 N=1 个进行中任务**（限流与任务队列**同一个计数**，§1 第 3 条），
  超出**排队**；同租户连点重建必须排队 —— 否则撞单写者约定。

★ **与 CLI 的关键差别（也是本壳要验证的东西）**：CLI 走 `find_corpus()`
（环境变量 `LDV_CORPUS` → 目录），壳**自己读租户目录**再传给 `run_one` 的
`loaded` 参数。`run_one` 的 docstring 有一条警告：**传进来的那份必须与
`load()` 同源**，否则「判的是这一份、跑的是那一份」会**静默**成立。
⇒ 壳正是「传入 loaded」的真实使用者 ⇒ 等价判据（壳响应 == CLI 输出）
同时验证了「壳的加载路径与 CLI 同源」。

⚠️ **这是壳，不是内核** —— 约束 ①（壳可依赖、内核零依赖，`docs/服务化设计.md` §0）：
本文件可以 import 任何东西，`ldv` 包一行 import 都不许多。

## P1（2026-10-29）：从「验收壳」变成「能给别人用的壳」

四件事，各自一个文件 / 一段：

| 事 | 在哪 | 一句话 |
|---|---|---|
| **契约** `ldv-http/1` | 本文件的 `_send` | 每个响应带 `X-Ldv-Protocol` / `X-Request-Id`；错误另带 `X-Ldv-Error`。**只增不改** |
| **认证 + 配额** | `serve_auth.py` | 盘上只存摘要；缺配额默认 60/分（取紧的那侧）；401 / 403 / 429 分开 |
| **租户生命周期** | `serve_tenants.py` | 建出来是**空库**且**能跑**；删是**进回收站**不是 `rm` |
| **调度器** | `serve_sched.py` | 按槽位幂等 —— `tick()` 自己不幂等，重复 tick 会**催熟 k** |

观测（`/healthz` / `/readyz` / `/metrics` + 每请求一行 JSON）在本文件里，因为它就是
「每个响应怎么发」这件事的另一面。

用法：
    python serve.py --tokens tokens.json            # 正常起（要 token）
    python serve.py --allow-anonymous               # 本地自用（显式）
    python serve.py --mint-token alice --tenant '*'  # 造一个 token，不启服务
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import sys
import threading
import time
import uuid
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from ldv.cli import run_one                                    # noqa: E402
from ldv.corpus.loader import load_edges, load_nodes           # noqa: E402

import serve_auth as AUTH                                      # noqa: E402
import serve_sched                                             # noqa: E402
import serve_tenants as TEN                                    # noqa: E402

#: 契约版本（`docs/P1落地.md` §1）。**只增不改** —— 已有的字段含义不许变。
PROTOCOL = AUTH.PROTOCOL

#: 租户根 = `_data/`（与 `_fixtures.external_corpus_candidates` 同一个约定）
TENANT_ROOT = ROOT / "_data"

#: 一个租户要「完整」必须有什么（缺 view_spec ⇒ 404，不自动补 —— 建库是人的决定）
REQUIRED = ("nodes", "view_spec.json")

#: 每租户同时最多几个进行中任务（§1：限流与排队是**同一个计数**）
MAX_PER_TENANT = 1

#: **队列容量** —— `排队 + 进行中` 的上限，超了 ⇒ **429**（不是无限堆内存）。
#: ⚠️ **暂定值、未经标定**（2026-10-23 由实现方裁决；要改先定刻度：
#:    「一个租户允许积压多少」取决于调用方的重试策略与语料规模）。
QUEUE_CAP_PER_TENANT = 8
QUEUE_CAP_GLOBAL = 64


class QueueFull(Exception):
    """队列已满 ⇒ 调用方应返回 **429**（可重试，不是客户端写错了）。"""

#: `plugin` 的合法值（与 `ldv.cli.DIRECTIONS` 同一个集合）
PLUGIN_OK = ("keyset", "reach", "sequence")
#: `only` 的合法值（与 `cli.run_one` 的分段一致；`None` = 全跑）
ONLY_OK = (None, "b", "a", "d", "r")
#: `holdout` 的上界 —— **防呆**，不是业务约束（真上界由语料大小定）
HOLDOUT_MAX = 100_000


def _parse_run_args(qs: dict) -> tuple[dict | None, str]:
    """校验 `?plugin=&only=&holdout=`。返回 `(参数, 错误文案)`。

    ★★ 客户端错误一律 **400**（2026-10-23）：原来 `int(qs.get("holdout"))` 裸调，
       `holdout=abc` ⇒ `ValueError` 从 handler 里抛出去 ⇒ **连接被断开**，
       调用方拿不到任何原因，日志里只有 traceback。
       ⇒ 客户端错误不许混成服务端异常（也就不会被当成「服务挂了」）。
    """
    plugin = qs.get("plugin", ["keyset"])[0]
    # ★ 不认识的参数 ⇒ **400**（P1 契约）：静默忽略的话，`?plugin=react` 这类
    #   拼写错误会得到「用默认值跑出来的答案」，而调用方以为自己换了一条方向。
    unknown = sorted(set(qs) - {"plugin", "only", "holdout"})
    if unknown:
        return None, (f"400：不认识的查询参数 {unknown}"
                      f"（只接受 plugin / only / holdout）")
    if plugin not in PLUGIN_OK:
        return None, f"400：plugin 必须是 {PLUGIN_OK} 之一（收到 {plugin!r}）"
    only = qs.get("only", [None])[0]
    if only not in ONLY_OK:
        return None, f"400：only 必须是 {ONLY_OK} 之一（收到 {only!r}）"
    raw = qs.get("holdout", ["12"])[0]
    try:
        holdout = int(raw)
    except (TypeError, ValueError):
        return None, f"400：holdout 必须是整数（收到 {raw!r}）"
    if holdout < 0 or holdout > HOLDOUT_MAX:
        return None, f"400：holdout 必须在 0..{HOLDOUT_MAX} 之间（收到 {holdout}）"
    return {"plugin": plugin, "only": only, "holdout": holdout}, ""


def _load_tenant(name: str, root: Path | None = None):
    """读一个租户的语料。返回 `(nodes, edges, keyset)` 或 `None`（不完整/不存在）。

    ⚠️ `root` 不给才回落模块级 `TENANT_ROOT` —— `--root` 必须能**真的换目录**
       （2026-10-23：原来它只进了启动日志，`_load_tenant` 仍读常量 ⇒
       操作者以为隔离了数据目录，实际没有）。
    """
    base = (root if root is not None else TENANT_ROOT) / name
    # ★ 名字先过白名单（P1，2026-10-29）：`tenant` 来自 URL 路径，`../x` 在**拼接之后**
    #   才检查就已经晚了（`Path` 已经把它规约掉了）。⇒ 拼之前判，非法一律当「不存在」。
    if not TEN.name_ok(name) or not base.is_dir():
        return None
    for req in REQUIRED:
        if not (base / req).exists():
            return None
    nodes_dir = base / "nodes"
    # ★ **空库是合法的**（P1 对齐内核，2026-10-25 用户已定）：`loader.load_nodes` 明说
    #   「新库就是 0 项，它不该因为还没往里写东西而起不来」。原来这里要求
    #   `nodes/` 下至少一个 `.md` ⇒ **壳与内核在「空库」上口径不一致**，
    #   而后来的「建租户」建出来的正是空库 ⇒ 建完立刻 404，等于建了个用不了的租户。
    #   ⚠️ 与「路径写错」仍分得开：目录不存在上面就返回了。
    nodes = load_nodes(nodes_dir)
    edges, keyset = load_edges(nodes)
    return nodes, edges, keyset


def _execute(tenant: str, plugin: str, only, holdout: int,
             root: Path | None = None) -> tuple[int, str]:
    """跑一个任务（**同步执行体** —— 3.1 的 GET 与 3.3 的 worker 共用）。"""
    # ⚠️ `_load_tenant` 也可能抛（权限 / 磁盘 / 符号链接**以及坏语料**）——
    #    它原来只捕 `OSError`，而「语料坏了」（`CorpusError`：重复 id / front-matter 写错）
    #    抛的是另一个类型 ⇒ **从 handler 里穿出去 ⇒ 连接被断开**，
    #    调用方拿到的是 `RemoteDisconnected`，日志里只有 traceback。
    #    （2026-10-29 端到端实测：往语料里放一个重复 id 的节点，`/run` 就是这样死的。）
    #    ⇒ 客户端错误/数据错误/服务端错误必须各自有话说，不许变成「对面没回应」。
    try:
        loaded = _load_tenant(tenant, root=root)
    except OSError as exc:
        FAULTS.note(tenant, f"{type(exc).__name__}: {exc}")
        return 500, f"500：读租户目录失败 —— {type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001 - 坏语料是**数据**问题，如实报，不炸连接
        FAULTS.note(tenant, f"{type(exc).__name__}: {exc}")
        return 500, (f"500：租户 {tenant!r} 的语料读不进来 —— "
                     f"{type(exc).__name__}: {exc}")
    if loaded is None:
        return 404, f"404：租户 {tenant!r} 不存在或不完整（需要 nodes/ + view_spec.json）"
    FAULTS.clear(tenant)
    nodes, edges, keyset = loaded
    try:
        # ⚠️ **不**用 `contextlib.redirect_stdout` 包 run_one —— `run_one` 内部
        #    不 print（print 都在 `cli.main` 里），包它是**多余**的；而且
        #    `redirect_stdout` 改的是**全局** `sys.stdout`，在 worker 线程里
        #    会**劫持同进程其他线程的输出**（3.3 实测踩到：探针主线程的
        #    判据输出被吞进 worker 的 StringIO）。
        ok, text = run_one(plugin, holdout, only, loaded=(nodes, edges, keyset))
    except Exception as exc:  # 壳层如实报，不吞
        return 500, f"500：{type(exc).__name__}: {exc}"
    # 逐字 = `run_one` 的返回值（与 CLI `print(text)` 同源）
    return 200, text


class Metrics:
    """观测计数（**进程内**，重启归零）。`/metrics` 吐它，`/readyz` 不看它。

    ⚠️ 边界：它是**计数器**，不是**时序库**。要知道「过去一小时慢不慢」得靠落盘/外采，
       这里只回答「这个进程自起来以来，发生过几次」。
    只存聚合数（**不存租户名**）—— `/metrics` 因此不泄露「谁在用这个服务」。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._n: dict[str, int] = {}

    def bump(self, key: str, n: int = 1) -> None:
        with self._lock:
            self._n[key] = self._n.get(key, 0) + n

    def snapshot(self) -> dict:
        with self._lock:
            return dict(sorted(self._n.items()))


#: 全局计数（单进程；跨进程要外采 —— 与 `TABLE` 同一条升级路径）
METRICS = Metrics()


class AppCache:
    """**一个租户一个 `entry.App`**（懒建 + 目录变更时重建）。

    ## 为什么必须缓存（不是为了快）

    `entry.App` 里有**两处跨请求的状态**，正好是 agent 流程的两半：

        `_last_shown`  「刚才摆了哪些支」 —— `drill()` 只认这份名单里的支
        `_tendency`    「这一支的历史倾向」 —— 决定下次**先说哪一支**

    如果每个请求都新建 App：`/drill` 拿到的 `_last_shown` 是空的 ⇒
    使用记录**永远记不下来**，`recorded` 恒 `False` —— 而它返回的文案是
    「这一支不在刚展示的候选里，不记」，听起来**像**是正常的业务判断。
    「功能没接上」与「业务上不该记」长得一模一样，这是最该防的形状。

    ## 失效：只看**目录 mtime**

    加/删/改名节点会改目录 mtime ⇒ 自动重建。**原地改一个文件的内容不会** ——
    那要靠 `?refresh=1` 显式重建。
    ⚠️ 这是**取舍**，不是没想到：逐文件 stat 在 28 万项语料上是每请求 O(n)，
    比读错更糟的是「每个请求都慢 2 秒」。所以把「什么时候会看到新内容」写在这里：
    **加/删会看到，原地改要 refresh**。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._apps: dict[tuple[str, str], tuple[int, object]] = {}

    def _stamp(self, nodes_dir: Path) -> int:
        try:
            return nodes_dir.stat().st_mtime_ns
        except OSError:
            return -1

    def get(self, root, tenant: str, refresh: bool = False):
        import entry as ENTRY

        nodes_dir = Path(root) / tenant / "nodes"
        key = (str(Path(root).resolve()), tenant)
        stamp = self._stamp(nodes_dir)
        with self._lock:
            hit = self._apps.get(key)
            if hit is not None and not refresh and hit[0] == stamp:
                return hit[1]
        app = ENTRY.App(root=nodes_dir)          # 慢的那一步在锁外
        with self._lock:
            self._apps[key] = (stamp, app)
        return app

    def drop(self, root, tenant: str) -> None:
        with self._lock:
            self._apps.pop((str(Path(root).resolve()), tenant), None)

    def names(self) -> list[str]:
        with self._lock:
            return sorted(t for _r, t in self._apps)


#: 全局 App 缓存（单进程；与 `TABLE` / `METRICS` 同一条升级路径）
APPS = AppCache()


class LoadFaults:
    """**已知装载失败** —— 本进程**亲眼见过**的、读不进来的租户。

    为什么要有它：`/readyz` 原来只查「目录在不在、view_spec 在不在」——
    那答的是「有没有这个租户」，**不是**「它读得进来」。实测（2026-10-29）：
    往语料里放一个重复 id 的节点，`/ask` 与 `/run` 全 500，而 `/readyz` 照样 200
    —— 一个说「我准备好了」的服务，实际上一个租户都答不了。
    「能接客 ≠ 能干活」这条纪律，在 readyz 上原来只做了一半。

    ⚠️ 它**只记发生过的事**，不做主动扫描：每请求把全部租户装载一遍，在 28 万项
       语料上是每个 readyz 几秒 —— 那会让探针本身变成故障源。
       所以语义是「**已知**的装载失败」，不是「全部装载失败的集合」。这句差别
       写在 `/readyz` 的返回里（`已知装载失败`），不藏在注释里。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._m: dict[str, str] = {}

    def note(self, tenant: str, why: str) -> None:
        with self._lock:
            self._m[tenant] = why

    def clear(self, tenant: str) -> None:
        with self._lock:
            self._m.pop(tenant, None)

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._m)


#: 已知装载失败（单进程）
FAULTS = LoadFaults()


class TaskTable:
    """任务表（**内存**版 —— 单进程验证用；跨进程版 = 落盘，标注为升级路径）。

    限流与排队**同一个计数**：`running[tenant] < MAX_PER_TENANT` 才放行，
    否则排队。完成时唤醒等这个租户的下一个（按提交顺序）。
    """

    def __init__(self, max_per_tenant: int = MAX_PER_TENANT,
                 cap_per_tenant: int = QUEUE_CAP_PER_TENANT,
                 cap_global: int = QUEUE_CAP_GLOBAL):
        self.max = max_per_tenant
        self.cap_tenant = cap_per_tenant
        self.cap_global = cap_global
        self.cond = threading.Condition()
        self.tasks: dict[str, dict] = {}
        self.running: dict[str, int] = {}     # 租户 → 进行中数
        self.queues: dict[str, list[str]] = {}  # 租户 → 排队的 task_id（FIFO）

    def load_of(self, tenant: str) -> tuple[int, int]:
        """`(该租户的排队+进行中, 全局的排队+进行中)` —— 容量判定只看这两个数。"""
        with self.cond:
            mine = len(self.queues.get(tenant, [])) + self.running.get(tenant, 0)
            allq = sum(len(q) for q in self.queues.values()) + sum(self.running.values())
            return mine, allq

    def tenant_of(self, tid: str) -> str | None:
        """任务属于哪个租户（`None` = 没这个任务）。**鉴权要用它** ——
        否则拿到 task_id 的人就能越过租户边界读结果。"""
        with self.cond:
            t = self.tasks.get(tid)
            return t["tenant"] if t else None

    def submit(self, tenant: str, fn) -> str:
        """提交一个任务；返回 task_id。**立即返回**（不等执行）。

        ⚠️ **撞容量上限 ⇒ 抛 `QueueFull`**（调用方返回 429）——
           原来是**无上限**地往内存里堆，`tasks` 与 `queues` 只增不减。
        """
        # ★ **完整 uuid**（2026-10-23）：原来取 `hex[:12]`（48 bit），碰撞概率不高
        #   但**不是严格的唯一性保证**，而任务 id 是轮询的唯一句柄 ——
        #   「大概率不撞」与「不会撞」在**撞了以后**才分得开。代价是 URL 长 20 字符。
        tid = uuid.uuid4().hex
        with self.cond:
            mine = len(self.queues.get(tenant, [])) + self.running.get(tenant, 0)
            allq = sum(len(q) for q in self.queues.values()) + sum(self.running.values())
            if mine >= self.cap_tenant:
                raise QueueFull(f"租户 {tenant!r} 已有 {mine} 个排队/进行中任务"
                                f"（上限 {self.cap_tenant}）")
            if allq >= self.cap_global:
                raise QueueFull(f"全局已有 {allq} 个排队/进行中任务"
                                f"（上限 {self.cap_global}）")
            # ⚠️ `_fn` 必须在**持锁时**存进去 —— `_pump` 可能立刻启动 worker，
            #    而 worker 要取 `t["_fn"]`；晚一步就是竞态（KeyError）。
            self.tasks[tid] = {"id": tid, "tenant": tenant, "status": "queued",
                               "submitted": time.time(), "started": None,
                               "finished": None, "code": None, "body": None,
                               "_fn": fn}
            self.queues.setdefault(tenant, []).append(tid)
            self._pump(tenant)
        return tid

    def _pump(self, tenant: str) -> None:
        """在持锁状态下把能放行的排队任务放出去（调用方负责 notify）。"""
        while self.running.get(tenant, 0) < self.max and self.queues.get(tenant):
            tid = self.queues[tenant].pop(0)
            t = self.tasks[tid]
            t["status"] = "running"
            t["started"] = time.time()
            self.running[tenant] = self.running.get(tenant, 0) + 1
            threading.Thread(target=self._work, args=(tenant, tid), daemon=True).start()

    def _work(self, tenant: str, tid: str) -> None:
        t = self.tasks[tid]
        # 执行体由 submit 时闭包带进来 —— 存在任务里，worker 取用
        #
        # ★★ **任务失败不许带走队列**（2026-10-23 修）：原来这里是裸调
        #    `code, body = t["_fn"]()`，一旦抛异常 ⇒ 本线程直接退出 ⇒
        #    `running[tenant]` **不减**、`_pump` **不再被调用** ⇒
        #    `MAX_PER_TENANT = 1` 时该租户**永久卡死**（后续任务永远 queued）。
        #    ⇒ try/except/finally：每个任务最终都进入终态 + 释放名额 + 唤醒等待者。
        code, body = 500, "500：任务异常（未捕获）"
        try:
            code, body = t["_fn"]()
        except BaseException as exc:        # noqa: BLE001 - worker 不许被任务带走
            code, body = 500, f"500：{type(exc).__name__}: {exc}"
        finally:
            with self.cond:
                t["status"] = "done" if code == 200 else "error"
                t["finished"] = time.time()
                t["code"], t["body"] = code, body
                # `max(0, …)` 防重复减 —— 名额少减一次会**永久少放行**一个
                self.running[tenant] = max(0, self.running.get(tenant, 1) - 1)
                self._pump(tenant)
                self.cond.notify_all()

    def status(self, tid: str) -> tuple[int, dict]:
        with self.cond:
            t = self.tasks.get(tid)
            if t is None:
                return 404, {"error": f"没有这个任务：{tid!r}"}
            out = {k: v for k, v in t.items() if not k.startswith("_")}
            # done 时把结果一起给（轮询者不必再发一次请求）
            if t["status"] == "done":
                out["result"] = t["body"]
            elif t["status"] == "error":
                out["result"] = t["body"]
            return 200, out


#: 全局任务表（单进程验证；跨进程要落盘 —— 升级路径，见设计文档 §1）
TABLE = TaskTable()


def prometheus_text(snapshot: dict) -> str | None:
    """把计数转成 **Prometheus 文本**；没装 `prometheus_client` ⇒ `None`（调用方回退 JSON）。

    ★ 为什么是「转换」而不是「另存一份 Counter 对象」：**两套计数一定会漂**。
      同一件事记在两个地方，出问题时你分不清「哪一份是对的」——这正是本仓库
      最忌的「两个真相」。所以唯一真相仍是 `METRICS` 那个字典，这里只做**呈现**。

    ⚠️ 版本细节（0.26.0 实测）：`CounterMetricFamily` **不在顶层**，在
      `prometheus_client.core` 里。照文档写 `from prometheus_client import CounterMetricFamily`
      会 `ImportError` —— 这个坑记在这里，免得下次又踩。
    ⚠️ 多进程模式下自定义 collector 不工作（官方文档的限制清单）。我们单进程，
      将来真上 `uvicorn --workers N`，这里要换成 `PROMETHEUS_MULTIPROC_DIR` +
      `multiprocess.MultiProcessCollector` —— 但那时 `AppCache` 也得先外置（见 `docs/壳层选型.md` §5.2）。
    """
    try:
        from prometheus_client import CollectorRegistry, generate_latest
        from prometheus_client.core import CounterMetricFamily
    except ImportError:
        return None

    class _One:
        """把「已经攒好样本的 family」包成 collector。

        ⚠️ **必须包这一层**（0.26.0 实测）：直接把 `CounterMetricFamily` 交给
        `registry.register()` 会抛 `TypeError: unhashable type: 'CounterMetricFamily'`
        —— 注册表要拿 collector 当字典的键，而 MetricFamily 在这个版本里不可哈希。
        文档示例用的是 `Counter(...)`（那类对象可哈希），照抄到 MetricFamily 上就炸。
        """

        def __init__(self, fams):
            self._f = fams

        def collect(self):
            return self._f

    fam = CounterMetricFamily("ldv_shell_events",
                              "服务壳事件计数（进程内；重启归零）", labels=["kind"])
    for k in sorted(snapshot):
        fam.add_metric([k], float(snapshot[k]))
    reg = CollectorRegistry()
    reg.register(_One([fam]))
    return generate_latest(reg).decode("utf-8")


#: 状态码 → `X-Ldv-Error` 的默认值。**机器读这个头，人读正文**：
#: 正文是中文说明（会变），头是契约（只增不改）。
ERROR_KIND = {400: "bad-request", 401: "unauthorized", 403: "forbidden",
              404: "not-found", 405: "method-not-allowed", 409: "conflict",
              429: "rate-limited", 500: "internal", 503: "unavailable"}

#: 客户端能自己带的请求 id 的合法形状（别的一律忽略并重新生成）
_REQ_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class Handler(BaseHTTPRequestHandler):
    """端点（P1 之后的完整表面）：

    | 方法 | 路径 | 谁 | 说明 |
    |---|---|---|---|
    | GET | `/healthz` | 任何人 | 进程活着 |
    | GET | `/readyz` | 任何人 | **能干活**（语料/策略/调度器），不行 ⇒ 503 |
    | GET | `/metrics` | 管理员 | 进程内计数（聚合，不含租户名） |
    | GET | `/` 或 `/tenants` | 有 token | 列出租户（只看得到自己的） |
    | POST | `/tenants/<名>` | 管理员 | 建（空库，可跑） |
    | DELETE | `/tenants/<名>` | 管理员 | 删 ⇒ **进回收站**，不是 rm |
    | GET | `/<租户>/ask?require=&forbid=&plugin=` | 该租户 | **答题**：需求 → 这一带的节点 / 分叉 |
    | POST | `/<租户>/drill?did=&verdict=` | 该租户 | 下钻 = 「这一支有用」的唯一真实信号 |
    | GET | `/<租户>/agenda?k=&tick=` | 该租户 | **现在轮到谁**（`tick=1` 记一行 seen） |
    | GET | `/<租户>/run?plugin=&only=&holdout=` | 该租户 | **同步**跑流水线（3.1 遗留，诊断用） |
    | POST | `/<租户>/run` | 该租户 | 提交任务（3.3），返回 task_id |
    | GET | `/tasks/<id>` | 该租户 | 轮询（queued / running / done / error） |

    ## 失败顺序（**顺序本身是契约**）

        401 你是谁 → 403 不是你的租户 → 400 参数写错 → 404 没这个租户 → 429 配额没了

    为什么是这个顺序：400 在 429 之前 ⇒ **写错的请求不烧配额**；
    404 在 429 之前 ⇒ 摸不存在的租户不烧配额；
    403 在 404 之前 ⇒ 「不是你的」与「不存在」分开报，**不泄露租户是否存在**。

    ⚠️ 四种失败必须**长得不一样**（状态码 + `X-Ldv-Error` 都不同）——
       否则「token 过期」会被当成「服务挂了」或「租户没了」。
    """

    server_version = "ldv-shell/0.3"

    #: 租户根 —— **可被 `main()` 换掉**（`--root` 真正生效的地方）。
    #: ⚠️ 用类属性而不是模块常量：模块常量改不动（`--root` 原来只进了日志）。
    root: Path = TENANT_ROOT

    #: 认证策略。**类属性默认「匿名全通」** —— 这一条只对**进程内嵌**（判据/探针）成立；
    #: `main()` 启动时必须二选一（给 `--tokens` 或显式 `--allow-anonymous`）才肯起，
    #: 所以「忘了配认证就上线」在入口处就被挡住，而不是靠这里的默认值。
    store: AUTH.TokenStore = AUTH.TokenStore.open_for_all()
    #: 配额计数器（进程内）。测试会换一个干净的实例。
    quota: AUTH.Quota = AUTH.Quota()
    #: 调度器（`main()` 里起；进程内嵌时为 None ⇒ `/readyz` 不算这一项）
    scheduler = None
    #: 访问日志（JSON 行到 stderr）。`--quiet` / 测试可以关掉。
    quiet = False

    # ── 工具 ──────────────────────────────────────────────────────────
    def _begin(self) -> None:
        """每个请求的开头：定 `req_id` 与计时起点。"""
        raw = (self.headers.get("X-Request-Id") or "").strip()
        # 客户端给的 id **沿用**（便于跨服务串一条链），但不合法的（含换行/超长）不用
        self.req_id = raw if _REQ_ID_RE.match(raw) else uuid.uuid4().hex
        self._t0 = time.monotonic()

    def _send(self, code: int, body: str, ctype: str = "text/plain; charset=utf-8",
              error: str | None = None, extra: dict | None = None) -> None:
        raw = body.encode("utf-8")
        kind = error or (ERROR_KIND.get(code) if code >= 400 else None)
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("X-Ldv-Protocol", PROTOCOL)
            self.send_header("X-Request-Id", getattr(self, "req_id", "-"))
            if kind:
                self.send_header("X-Ldv-Error", kind)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(raw)
        finally:
            METRICS.bump("请求")
            METRICS.bump(f"响应 {code // 100}xx")
            if kind:
                METRICS.bump(f"拒绝 {kind}")
            if not self.quiet:
                # 一行一条 JSON：出事了先看 `req`，再看 `code` / `error`
                sys.stderr.write(json.dumps({
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "req": getattr(self, "req_id", "-"),
                    "m": self.command,
                    "path": getattr(self, "path", ""),
                    "code": code,
                    "error": kind,
                    "ms": round((time.monotonic() - getattr(self, "_t0",
                                                            time.monotonic())) * 1000, 1),
                }, ensure_ascii=False) + "\n")

    def _json(self, code: int, obj, error: str | None = None,
              extra: dict | None = None) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False, indent=2),
                   "application/json; charset=utf-8", error=error, extra=extra)

    def log_message(self, fmt, *args):  # 静音 http.server 自带的访问日志
        pass

    # ── 三条守卫（顺序即契约，见类文档）────────────────────────────────
    def _who(self):
        """401。返回 `Principal`；认不出 ⇒ 已回包，返回 `None`。

        ⚠️ **四种原因写在同一句话里会害人**：「没带」「认不出」「过期了」都是 401，
        但该做的事完全不同 —— 过期的要去换新的，认不出的要查是不是抄错了，
        没带的要看客户端有没有把头的拼写写对。所以正文分开写（状态码仍统一 401，
        这样客户端不需要为「为什么没通过」写四个分支）。
        """
        p, why = self.store.authenticate_detail(self.headers.get("Authorization"))
        if p is not None:
            return p
        note = {
            "missing": "没有带 token —— `Authorization: Bearer <token>`",
            "unknown": "token 认不出（抄错了，或者已经被删掉）",
            "expired": ("token **已过期** —— 换一个新的：`python serve.py "
                        "--rotate <名字> --tokens <文件>`"),
        }.get(why, why)
        self._send(401, f"401：{note}", error="unauthorized",
                   extra={"WWW-Authenticate": 'Bearer realm="ldv"'})
        return None

    def _may(self, p, tenant: str) -> bool:
        """403。**在「租户存不存在」之前判**，否则差别会泄露是否存在。"""
        if p.may(tenant):
            return True
        self._send(403, f"403：token {p.name!r} 无权访问租户 {tenant!r}",
                   error="forbidden")
        return False

    def _charge(self, p) -> bool:
        """429。**在任务表之前判** —— 超配额的请求**不消耗任务表**（判据 63 守这条）。"""
        wait = self.quota.take(p)
        if wait is None:
            return True
        self._send(429, f"429：配额已用尽（每分钟 {p.quota_per_min} 次）",
                   error="rate-limited",
                   extra={"Retry-After": str(int(round(wait)))})
        return False

    # ── GET ───────────────────────────────────────────────────────────
    def do_GET(self):  # noqa: N802（http.server 的约定名）
        self._begin()
        url = urlparse(self.path)
        parts = [p for p in url.path.split("/") if p]

        if not parts or (len(parts) == 1 and parts[0] == "tenants"):
            self._list_tenants()
            return

        if len(parts) == 1 and parts[0] in ("healthz", "readyz", "metrics", "metrics.json"):
            {"healthz": self._healthz, "readyz": self._readyz,
             "metrics": self._metrics_text,
             "metrics.json": self._metrics_json}[parts[0]]()
            return

        if len(parts) == 2 and parts[0] == "tasks":
            self._task_status(parts[1])
            return

        if len(parts) == 2 and parts[1] in ("ask", "agenda"):
            {"ask": self._ask, "agenda": self._agenda}[parts[1]](
                parts[0], parse_qs(url.query))
            return

        if len(parts) == 2 and parts[1] == "run":
            self._run_sync(parts[0], parse_qs(url.query))
            return

        self._send(404, f"404：没有这个端点（{url.path}）")

    # ── POST ──────────────────────────────────────────────────────────
    def do_POST(self):  # noqa: N802
        self._begin()
        url = urlparse(self.path)
        parts = [p for p in url.path.split("/") if p]
        if len(parts) == 2 and parts[0] == "tenants":
            self._create_tenant(parts[1])
            return
        if len(parts) == 2 and parts[1] == "drill":
            self._drill(parts[0], parse_qs(url.query))
            return
        if len(parts) == 2 and parts[1] == "run":
            self._submit(parts[0], parse_qs(url.query))
            return
        self._send(404, f"404：没有这个端点（{url.path}）")

    # ── DELETE ────────────────────────────────────────────────────────
    def do_DELETE(self):  # noqa: N802
        self._begin()
        url = urlparse(self.path)
        parts = [p for p in url.path.split("/") if p]
        if len(parts) == 2 and parts[0] == "tenants":
            self._delete_tenant(parts[1])
            return
        self._send(404, f"404：没有这个端点（{url.path}）")

    # ── 观测 ──────────────────────────────────────────────────────────
    def _healthz(self) -> None:
        """**进程活着**。不碰磁盘、不看配置 —— 它答的是「这个端口有人听吗」。"""
        self._json(200, {"ok": True, "协议": PROTOCOL, "壳": self.server_version,
                         "pid": os.getpid()})

    def _readyz(self) -> None:
        """**能干活**。不行 ⇒ **503**（不是 200 敷衍）。

        四项各自独立，`不 ready` 时**逐项说哪项不行** ——
        「服务坏了」与「还没建好」要分得开，否则排障从看日志变成猜。
        """
        root = Path(self.root)
        try:
            readable = root.is_dir() and os.access(root, os.R_OK)
        except OSError:
            readable = False
        complete = [t for t in TEN.list_tenants(root) if TEN.complete(root, t)] \
            if readable else []
        sched = self.scheduler
        faults = FAULTS.snapshot()
        items = {
            "租户根可读": readable,
            "有可服务的租户": bool(complete),
            "认证策略": bool(self.store.open or len(self.store) > 0),
            # 没起调度器（进程内嵌 / `--no-scheduler`）**不算不 ready** —— 那是配置，不是故障
            "调度器": True if sched is None else sched.alive,
            # ★ 「有租户」不等于「租户读得进来」：坏语料（重复 id / front-matter 写错）
            #   会让 /ask 与 /run 全 500，而上面那三项全部为真。
            #   ⚠️ 这一项只覆盖**本进程亲眼见过**的失败（不做主动扫描，理由见 LoadFaults）。
            "没有已知装载失败": not faults,
        }
        ready = all(items.values())
        body = {"ready": ready, "协议": PROTOCOL, "检查": items,
                "可服务租户": len(complete),
                "已知装载失败": faults,
                "调度器": ("未启用" if sched is None else
                           ("运行中" if sched.alive else "已停"))}
        self._json(200 if ready else 503, body)
        if self.scheduler is not None and not self.scheduler.alive:
            METRICS.bump("readyz 不ready · 调度器停")

    def _metrics_text(self) -> None:
        """`/metrics` —— **Prometheus 文本**（标准出口，看板直接吃）。

        没装 `prometheus_client` 时**回退成 JSON**，并在正文里写明「为什么是 JSON」——
        回退要能被看见，不能让人以为自己接的是标准格式。
        """
        if self._admin() is None:
            return
        text = prometheus_text(METRICS.snapshot())
        if text is None:
            METRICS.bump("metrics 回退 JSON")
            self._json(200, {"格式": "json（未装 prometheus_client，Prometheus 文本不可用）",
                             "计数": METRICS.snapshot()})
            return
        # `version=0.0.4` 是 Prometheus 的文本格式版本，不是我们的协议版本
        self._send(200, text, ctype="text/plain; version=0.0.4; charset=utf-8")

    def _metrics_json(self) -> None:
        """`/metrics.json` —— 结构化读数（判据与端到端读它，字段比文本出口细）。"""
        if self._admin() is None:
            return
        mine, allq = TABLE.load_of("")            # 全局队列深度（租户名为空 ⇒ 只看全局）
        sched = self.scheduler
        self._json(200, {
            "计数": METRICS.snapshot(),
            "任务表": {"在表": len(TABLE.tasks), "排队+进行中": allq},
            "调度器": ({"启用": False} if sched is None else {
                "启用": True, "存活": sched.alive, "间隔秒": sched.interval_s,
                "已 tick": sched.ticks_total, "跳过": sched.skipped_total,
                "上次": sched.last}),
            "租户数": len(TEN.list_tenants(self.root)),
            "已建 App 的租户": APPS.names(),
            "已知装载失败": FAULTS.snapshot(),
        })

    # ── 租户生命周期 ──────────────────────────────────────────────────
    def _list_tenants(self) -> None:
        p = self._who()
        if p is None:
            return
        names = [t for t in TEN.list_tenants(self.root) if p.may(t)]
        self._json(200, {"租户": names})

    def _admin(self):
        """管理员门（建/删租户）。401 与 403 分开报，理由同 `_may`。"""
        p = self._who()
        if p is None:
            return None
        if not p.admin:
            self._send(403, f"403：token {p.name!r} 不是管理员（只能看自己的租户）",
                       error="forbidden")
            return None
        return p

    def _create_tenant(self, name: str) -> None:
        if self._admin() is None:
            return
        try:
            base = TEN.create(self.root, name)
        except TEN.TenantError as exc:
            code = {"exists": 409, "bad-name": 400}.get(exc.kind, 400)
            self._send(code, f"{code}：{exc}",
                       error=("conflict" if exc.kind == "exists" else "bad-request"))
            return
        METRICS.bump("建租户")
        self._json(201, {"租户": name, "路径": str(base), "空库": True,
                         "说明": "空的但能跑：`nodes/` 是 0 项，喂节点用你们自己的摄入流程"})

    def _delete_tenant(self, name: str) -> None:
        if self._admin() is None:
            return
        try:
            dest = TEN.delete(self.root, name)
        except TEN.TenantError as exc:
            if exc.kind == "missing":
                self._send(404, f"404：{exc}", error="not-found")
            else:
                self._send(400, f"400：{exc}", error="bad-request")
            return
        APPS.drop(self.root, name)      # 否则「删了又建同名」会拿到旧内核
        METRICS.bump("删租户")
        self._json(200, {"租户": name, "已移入回收站": str(dest),
                         "说明": "没删掉：整目录挪到 `.trash/`，拷回来就恢复"})

    # ── agent 真用得上的三件（P1.5，2026-10-29）────────────────────────
    #
    # 这三件补的是一个**结构性缺口**：P1 之前，壳只会在租户上「跑一遍流水线」并把
    # 追踪文本吐出来 —— 那是**诊断**，不是**答案**。真正回答问题的 `entry.App`
    # （需求 → 这一带的节点 / 分叉处的几支）在**另一个进程**里，而且它自己写着
    # 「只绑 127.0.0.1，无鉴权，不是对外服务」。⇒ 一个 agent 走不通全程：
    # 有鉴权的那半不会答题，会答题的那半没有鉴权。
    #
    # 这里把三件接到**同一个端口、同一套 token**上：
    #   · `/<租户>/ask`    需求 → 节点/支（只读）
    #   · `/<租户>/drill`  「这一支有用」的唯一真实信号（写使用记录）
    #   · `/<租户>/agenda` 「现在轮到谁」（主动性在 agent 侧的出口）

    def _app(self, tenant: str, refresh: bool = False):
        """一个租户的 `entry.App`（**缓存**，理由见 `AppCache` 的文档）。

        ⚠️ 走 `root=`，**不碰 `LDV_CORPUS`** —— 环境变量是进程级的，
        多租户下两个并发请求会互相覆盖，而覆盖的表现是**安静地读错语料**。
        """
        return APPS.get(self.root, tenant, refresh=refresh)

    def _ask(self, tenant: str, qs: dict) -> None:
        p = self._who()
        if p is None:
            return
        if not self._may(p, tenant):
            return
        if not TEN.complete(self.root, tenant):
            self._send(404, f"404：租户 {tenant!r} 不存在或不完整")
            return
        unknown = sorted(set(qs) - {"require", "forbid", "plugin", "limit", "refresh"})
        if unknown:
            self._send(400, f"400：不认识的查询参数 {unknown}"
                            f"（只接受 require / forbid / plugin / limit / refresh）",
                       error="bad-request")
            return
        if not self._charge(p):
            return

        def _keys(name: str) -> list[str]:
            raw = (qs.get(name, [""])[0] or "").strip()
            return [x.strip() for x in raw.split(",") if x.strip()]

        spec = {"plugin": (qs.get("plugin", ["keyset"])[0] or "keyset"),
                "require": _keys("require"), "forbid": _keys("forbid")}
        refresh = (qs.get("refresh", ["0"])[0] or "").lower() in ("1", "true", "yes")
        try:
            out = self._app(tenant, refresh=refresh).query(spec)
        except SystemExit as exc:              # `App` 用 SystemExit 报「找不到语料」
            FAULTS.note(tenant, str(exc))
            self._send(500, f"500：{exc}", error="internal")
            return
        except Exception as exc:               # noqa: BLE001 - 壳层如实报
            FAULTS.note(tenant, f"{type(exc).__name__}: {exc}")
            self._send(500, f"500：{type(exc).__name__}: {exc}", error="internal")
            return
        FAULTS.clear(tenant)
        # ⚠️ 两种「不是成功但也不是失败」的答案，都给 **200**：
        #    · `out_of_structure`：这个问题**超出本结构的词汇**（请人介入 / 换个问法）
        #    · `empty_corpus`   ：这个库**还是空的**（还没喂材料）
        #    它们都是**关于状态的事实**，不是「你写错了」。混成 400 会让调用方
        #    把「库是空的」当成「我参数错了」，然后去改一个本来没错的参数。
        if out.get("ok") or out.get("out_of_structure") or out.get("empty_corpus"):
            METRICS.bump("提问")
            self._json(200, out)
        else:
            self._send(400, "400：" + str(out.get("error")), error="bad-request")

    def _drill(self, tenant: str, qs: dict) -> None:
        p = self._who()
        if p is None:
            return
        if not self._may(p, tenant):
            return
        if not TEN.complete(self.root, tenant):
            self._send(404, f"404：租户 {tenant!r} 不存在或不完整")
            return
        did = (qs.get("did", [""])[0] or "").strip()
        if not did:
            self._send(400, "400：drill 需要 did（上一问返回的 `nodes[].did`）",
                       error="bad-request")
            return
        verdict = (qs.get("verdict", [""])[0] or "").strip()
        # ★ **判错只能人来给**（Kernel `App.drill` 的 `actor` 就是为这件事设的）。
        #   服务层**不转发** agent 的 `verdict=wrong` —— 不是「信不信它」的问题：
        #   接口一旦发出去，agent 就会把自己读不懂的标成错的。
        if verdict == "wrong":
            self._send(403, "403：`verdict=wrong`（这一支被推翻）只能由人来做 —— "
                            "本服务不转发 agent 的判错（agent 可以提议，不能判错）",
                       error="forbidden")
            return
        if not self._charge(p):
            return
        try:
            out = self._app(tenant).drill(did, verdict=verdict, actor="agent")
        except Exception as exc:               # noqa: BLE001
            FAULTS.note(tenant, f"{type(exc).__name__}: {exc}")
            self._send(500, f"500：{type(exc).__name__}: {exc}", error="internal")
            return
        FAULTS.clear(tenant)
        if out.get("ok"):
            METRICS.bump("下钻")
            self._json(200, out)
        else:
            self._send(400, "400：" + str(out.get("error")), error="bad-request")

    def _agenda(self, tenant: str, qs: dict) -> None:
        """`?k=0&tick=1` —— `tick=1` 会**记一行 seen**（这次「看到了待办」），
        `tick=0` 只读。⚠️ 记了 seen 就把「闲置」的基线往前推 ⇒ 影响 k，
        所以「看一眼」与「只是看看」是**两件事**，用参数分开而不是替调用方猜。"""
        p = self._who()
        if p is None:
            return
        if not self._may(p, tenant):
            return
        if not TEN.complete(self.root, tenant):
            self._send(404, f"404：租户 {tenant!r} 不存在或不完整")
            return
        try:
            k = int((qs.get("k", ["0"])[0] or 0))
        except ValueError:
            self._send(400, "400：k 必须是整数", error="bad-request")
            return
        want_tick = (qs.get("tick", ["0"])[0] or "").lower() in ("1", "true", "yes")
        if not self._charge(p):
            return
        try:
            from ldv.checks._fixtures import make_keyset_root
            from ldv.core import agenda as AG
            from ldv.corpus.loader import load_nodes

            nodes_dir = Path(self.root) / tenant / "nodes"
            kern = make_keyset_root(load_nodes(nodes_dir))()
            out = (AG.tick(kern, nodes_dir, k=k) if want_tick
                   else AG.agenda(kern, k=k))
        except Exception as exc:               # noqa: BLE001
            FAULTS.note(tenant, f"{type(exc).__name__}: {exc}")
            self._send(500, f"500：{type(exc).__name__}: {exc}", error="internal")
            return
        FAULTS.clear(tenant)
        METRICS.bump("问议程")
        self._json(200, out)

    # ── 3.1 同步端点（保留：等价判据还在它身上）──────────────────────
    def _run_sync(self, tenant: str, qs: dict) -> None:
        # 顺序即契约：401 → 403 → 400 → 404 → 429（见类文档）
        p = self._who()
        if p is None:
            return
        if not self._may(p, tenant):
            return
        args, err = _parse_run_args(qs)
        if args is None:
            self._send(400, err, error="bad-request")   # ★ 客户端错误 ⇒ 400，不走异常路径
            return
        # ⚠️ 用 `complete()`（只看文件在不在）而不是 `_load_tenant()`（**会真读语料**）：
        #    后者在 `_execute` 里还要再读一次 ⇒ 每个请求两趟装载。
        if not TEN.complete(self.root, tenant):
            self._send(404, f"404：租户 {tenant!r} 不存在或不完整"
                            f"（需要 nodes/ + view_spec.json）")
            return
        if not self._charge(p):
            return
        code, body = _execute(tenant, args["plugin"], args["only"], args["holdout"],
                              root=self.root)
        self._send(code, body)

    # ── 3.3 提交 + 轮询 ───────────────────────────────────────────────
    def _submit(self, tenant: str, qs: dict) -> None:
        p = self._who()
        if p is None:
            return
        if not self._may(p, tenant):
            return
        args, err = _parse_run_args(qs)
        if args is None:
            self._send(400, err, error="bad-request")
            return
        # 先校验租户（**不完整/不存在 ⇒ 404**，与 GET 同步端点同一条规则）
        if not TEN.complete(self.root, tenant):
            self._send(404, f"404：租户 {tenant!r} 不存在或不完整"
                            f"（需要 nodes/ + view_spec.json）")
            return
        # ★ 配额**在这里**扣：超了立刻 429，**不进任务表**（判据 63 守这条）
        if not self._charge(p):
            return

        try:
            tid = TABLE.submit(tenant, lambda: _execute(
                tenant, args["plugin"], args["only"], args["holdout"], root=self.root))
        except QueueFull as exc:
            # ★ **429**：可重试的拒绝，不是客户端写错了（400），也不是服务坏了（500）
            self._send(429, f"429：队列已满 —— {exc}", error="queue-full")
            return
        METRICS.bump("提交任务")
        self._json(202, {"task_id": tid, "tenant": tenant, "status": "queued",
                         "request_id": getattr(self, "req_id", "-")})

    def _task_status(self, tid: str) -> None:
        p = self._who()
        if p is None:
            return
        owner = TABLE.tenant_of(tid)
        if owner is None:
            self._json(404, {"error": f"没有这个任务：{tid!r}"}, error="not-found")
            return
        if not self._may(p, owner):
            return
        code, obj = TABLE.status(tid)
        self._json(code, obj)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="ldv 服务壳（3.1/3.2/3.3 + P1：认证 / 租户 / 调度 / 观测）")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--root", default=str(TENANT_ROOT))
    ap.add_argument("--max-per-tenant", type=int, default=MAX_PER_TENANT)
    # ── P1 ────────────────────────────────────────────────────────────
    ap.add_argument("--tokens", default=None,
                    help="token 文件（{\"tokens\":[{\"name\",\"sha256\",\"tenant\",\"quota_per_min\"}]}）")
    ap.add_argument("--allow-anonymous", action="store_true",
                    help="**显式**允许无 token（本地自用；对公网一律不要）")
    ap.add_argument("--tick-interval", type=int, default=serve_sched.DEFAULT_INTERVAL_S,
                    help="调度间隔秒（0 = 不起调度器）")
    ap.add_argument("--quiet", action="store_true", help="关掉每请求的 JSON 访问日志")
    # ── 造 token（不启服务，打完就退）──────────────────────────────────
    ap.add_argument("--mint-token", default=None, metavar="名字",
                    help="生成一个 token，打印明文与可直接粘进 tokens.json 的条目")
    ap.add_argument("--tenant", default="*", help="配合 --mint-token：绑哪个租户（`*`=管理员）")
    ap.add_argument("--quota", type=int, default=AUTH.DEFAULT_QUOTA_PER_MIN,
                    help="配合 --mint-token：每分钟配额（0 = 不限）")
    ap.add_argument("--expires-days", type=int, default=AUTH.DEFAULT_EXPIRES_DAYS,
                    help="配合 --mint-token / --rotate：多少天后过期（0 = 永不过期）")
    ap.add_argument("--rotate", default=None, metavar="名字",
                    help="给已有 token 造一个**新的**（旧的留着到期，双 key 重叠）")
    args = ap.parse_args(argv[1:])

    def _entry_for(name: str, tenant: str, quota: int, days: int) -> tuple[str, dict]:
        """造一个 token + 它在 `tokens.json` 里的条目。`days<=0` ⇒ 不写 `expires_at`。"""
        tok = AUTH.mint()
        entry: dict = {"name": name, "sha256": AUTH.hash_token(tok),
                       "tenant": tenant, "quota_per_min": quota}
        if days and days > 0:
            entry["expires_at"] = (date.today() + timedelta(days=days)).isoformat()
        return tok, entry

    def _show(tok: str, entry: dict) -> None:
        print("这个明文 token 只出现这一次（盘上只存摘要）：\n")
        print(f"    {tok}\n")
        print("把它粘进 tokens.json 的这一条：\n")
        print(json.dumps(entry, ensure_ascii=False, indent=2))
        print(f"\n调用方式：curl -H 'Authorization: Bearer {tok}' http://127.0.0.1:{args.port}/")

    if args.mint_token:
        _show(*_entry_for(args.mint_token, args.tenant, args.quota, args.expires_days))
        return 0

    if args.rotate:
        # ★ 轮换 = **再加一条同名的新条目**，不是把旧的删掉。旧的留到它自己的
        #   `expires_at` 为止 —— 这叫双 key 重叠，是「换得动、又不中断」的前提。
        if not args.tokens:
            print("--rotate 要配 --tokens（要从那个文件里读旧条目，才知道给谁换）")
            return 2
        try:
            old = AUTH.TokenStore.load(args.tokens)
        except (OSError, ValueError) as exc:
            print(f"token 文件读不动：{args.tokens} —— {type(exc).__name__}: {exc}")
            return 2
        prev = old.by_name(args.rotate)
        if not prev:
            print(f"{args.tokens} 里没有叫 {args.rotate!r} 的条目（现有："
                  f"{sorted({p.name for p in old.entries()})}）")
            return 2
        p0 = prev[0]
        tenant = "*" if p0.tenants is None else sorted(p0.tenants)[0]
        _show(*_entry_for(args.rotate, tenant, p0.quota_per_min or 0,
                          args.expires_days))
        print(f"\n⚠️ **别删旧条目**：旧 token 还能用到它自己的 expires_at"
              f"（{p0.expires_at or '永不过期 —— 那就永远不会自己退场'}）。")
        print("   两条同时有效 = 双 key 重叠：客户端换完、观察一阵，再把旧条目删掉。")
        print("   改完 tokens.json 要**重启服务**才生效（目前没有热加载）。")
        return 0

    TABLE.max = args.max_per_tenant
    # ★ `--root` **真的换目录**（2026-10-23）：原来只把它打进启动日志，
    #   `_load_tenant` / 租户枚举仍读模块常量 ⇒ 「日志说 A、实际读 B」。
    Handler.root = Path(args.root).resolve()
    Handler.quiet = args.quiet

    # ★ 认证**没有默认放行**（P1）：不配 token 就得**明说** `--allow-anonymous`。
    #   默认值的取法必须取紧的那一侧 —— 「忘了配」的结果不能是「对外裸奔」。
    if args.tokens:
        try:
            Handler.store = AUTH.TokenStore.load(args.tokens)
        except (OSError, ValueError) as exc:
            print(f"token 文件读不动：{args.tokens} —— {type(exc).__name__}: {exc}")
            return 2
        if len(Handler.store) == 0:
            print(f"⚠ {args.tokens} 里一个 token 都没有 ⇒ **所有请求都会 401**。"
                  f"\n  造一个：python serve.py --mint-token alice --tenant '*'")
        else:
            # ★ 缺省不过期是**可接受的**，但不能是**看不见的**：
            #   业界要求有轮换与过期；一个永不过期的 token 是一条永久凭据。
            wo = Handler.store.without_expiry()
            if wo:
                print(f"⚠ {len(wo)} 个 token 没有 `expires_at`（**永不过期**）："
                      f"{', '.join(wo[:6])}{' …' if len(wo) > 6 else ''}")
                print("   要给它一个期限：python serve.py --rotate <名字> --tokens "
                      f"{args.tokens}（新条目带 expires_at，旧的留着到期）")
    elif args.allow_anonymous:
        Handler.store = AUTH.TokenStore.open_for_all()
        print("⚠ 匿名模式（--allow-anonymous）：**任何人**都能跑任何租户。"
              "只该在 127.0.0.1 自用时这么起。")
    else:
        print("拒绝启动：没有配认证。二选一 ——\n"
              "  ① 造 token：python serve.py --mint-token alice --tenant '*'"
              "，把输出写进 tokens.json，再 `--tokens tokens.json`\n"
              "  ② 本地自用：--allow-anonymous")
        return 2

    Handler.quota = AUTH.Quota()
    sched = None
    if args.tick_interval > 0:
        sched = serve_sched.Scheduler(Handler.root, interval_s=args.tick_interval)
    Handler.scheduler = sched

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    if sched is not None:
        sched.start()
    print(f"壳已起：http://127.0.0.1:{args.port}/（租户根 {Handler.root}，"
          f"每租户并发 {args.max_per_tenant}，"
          f"认证 {'匿名' if Handler.store.open else f'{len(Handler.store)} 个 token'}，"
          f"调度 {'关' if sched is None else f'每 {args.tick_interval}s'}）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if sched is not None:
            sched.stop()
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
