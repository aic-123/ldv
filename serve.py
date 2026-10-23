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

用法：`python serve.py [--port N] [--root DIR] [--max-per-tenant N]`
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from ldv.cli import run_one                                    # noqa: E402
from ldv.corpus.loader import load_edges, load_nodes           # noqa: E402

#: 租户根 = `_data/`（与 `_fixtures.external_corpus_candidates` 同一个约定）
TENANT_ROOT = ROOT / "_data"

#: 一个租户要「完整」必须有什么（缺 view_spec ⇒ 404，不自动补 —— 建库是人的决定）
REQUIRED = ("nodes", "view_spec.json")

#: 每租户同时最多几个进行中任务（§1：限流与排队是**同一个计数**）
MAX_PER_TENANT = 1

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
    if not base.is_dir():
        return None
    for req in REQUIRED:
        if not (base / req).exists():
            return None
    nodes_dir = base / "nodes"
    if not any(nodes_dir.glob("*.md")):
        return None
    nodes = load_nodes(nodes_dir)
    edges, keyset = load_edges(nodes)
    return nodes, edges, keyset


def _execute(tenant: str, plugin: str, only, holdout: int,
             root: Path | None = None) -> tuple[int, str]:
    """跑一个任务（**同步执行体** —— 3.1 的 GET 与 3.3 的 worker 共用）。"""
    # ⚠️ `_load_tenant` 也可能抛（权限 / 磁盘 / 符号链接）—— 它原来在 try **之外**，
    #    抛出去就是 worker 线程直接死 ⇒ 见 `TaskTable._work` 的 try/finally。
    try:
        loaded = _load_tenant(tenant, root=root)
    except OSError as exc:
        return 500, f"500：读租户目录失败 —— {type(exc).__name__}: {exc}"
    if loaded is None:
        return 404, f"404：租户 {tenant!r} 不存在或不完整（需要 nodes/ + view_spec.json）"
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


class TaskTable:
    """任务表（**内存**版 —— 单进程验证用；跨进程版 = 落盘，标注为升级路径）。

    限流与排队**同一个计数**：`running[tenant] < MAX_PER_TENANT` 才放行，
    否则排队。完成时唤醒等这个租户的下一个（按提交顺序）。
    """

    def __init__(self, max_per_tenant: int = MAX_PER_TENANT):
        self.max = max_per_tenant
        self.cond = threading.Condition()
        self.tasks: dict[str, dict] = {}
        self.running: dict[str, int] = {}     # 租户 → 进行中数
        self.queues: dict[str, list[str]] = {}  # 租户 → 排队的 task_id（FIFO）

    def submit(self, tenant: str, fn) -> str:
        """提交一个任务；返回 task_id。**立即返回**（不等执行）。"""
        tid = uuid.uuid4().hex[:12]
        with self.cond:
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


class Handler(BaseHTTPRequestHandler):
    """端点：
    - `GET /` —— 列出租户
    - `GET /<tenant>/run?plugin=&only=&holdout=` —— **同步**跑（3.1 遗留）
    - `POST /<tenant>/run` —— 提交任务（3.3），返回 task_id
    - `GET /tasks/<id>` —— 轮询状态（queued / running / done / error）
    """

    server_version = "ldv-shell/0.2"

    #: 租户根 —— **可被 `main()` 换掉**（`--root` 真正生效的地方）。
    #: ⚠️ 用类属性而不是模块常量：模块常量改不动（`--root` 原来只进了日志）。
    root: Path = TENANT_ROOT

    # ── 工具 ──────────────────────────────────────────────────────────
    def _send(self, code: int, body: str, ctype: str = "text/plain; charset=utf-8") -> None:
        raw = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False, indent=2),
                   "application/json; charset=utf-8")

    def log_message(self, fmt, *args):  # 静音访问日志（留档由探针写）
        pass

    # ── GET ───────────────────────────────────────────────────────────
    def do_GET(self):  # noqa: N802（http.server 的约定名）
        url = urlparse(self.path)
        parts = [p for p in url.path.split("/") if p]

        if not parts:  # `/` —— 列出租户
            tenants = sorted(d.name for d in self.root.iterdir()
                             if d.is_dir() and (d / "view_spec.json").exists())
            self._json(200, {"租户": tenants})
            return

        if len(parts) == 2 and parts[0] == "tasks":
            self._task_status(parts[1])
            return

        if len(parts) == 2 and parts[1] == "run":
            self._run_sync(parts[0], parse_qs(url.query))
            return

        self._send(404, f"404：没有这个端点（{url.path}）")

    # ── POST ──────────────────────────────────────────────────────────
    def do_POST(self):  # noqa: N802
        url = urlparse(self.path)
        parts = [p for p in url.path.split("/") if p]
        if len(parts) == 2 and parts[1] == "run":
            self._submit(parts[0], parse_qs(url.query))
            return
        self._send(404, f"404：没有这个端点（{url.path}）")

    # ── 3.1 同步端点（保留：等价判据还在它身上）──────────────────────
    def _run_sync(self, tenant: str, qs: dict) -> None:
        args, err = _parse_run_args(qs)
        if args is None:
            self._send(400, err)          # ★ 客户端错误 ⇒ 400，不走异常路径
            return
        code, body = _execute(tenant, args["plugin"], args["only"], args["holdout"],
                              root=self.root)
        self._send(code, body)

    # ── 3.3 提交 + 轮询 ───────────────────────────────────────────────
    def _submit(self, tenant: str, qs: dict) -> None:
        args, err = _parse_run_args(qs)
        if args is None:
            self._send(400, err)
            return
        # 先校验租户（**不完整/不存在 ⇒ 404**，与 GET 同步端点同一条规则）
        if _load_tenant(tenant, root=self.root) is None:
            self._send(404, f"404：租户 {tenant!r} 不存在或不完整"
                            f"（需要 nodes/ + view_spec.json）")
            return

        tid = TABLE.submit(tenant, lambda: _execute(
            tenant, args["plugin"], args["only"], args["holdout"], root=self.root))
        self._json(202, {"task_id": tid, "tenant": tenant, "status": "queued"})

    def _task_status(self, tid: str) -> None:
        code, obj = TABLE.status(tid)
        self._json(code, obj)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="ldv 最小服务壳（阶段 3.1/3.2/3.3）")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--root", default=str(TENANT_ROOT))
    ap.add_argument("--max-per-tenant", type=int, default=MAX_PER_TENANT)
    args = ap.parse_args(argv[1:])
    TABLE.max = args.max_per_tenant
    # ★ `--root` **真的换目录**（2026-10-23）：原来只把它打进启动日志，
    #   `_load_tenant` / 租户枚举仍读模块常量 ⇒ 「日志说 A、实际读 B」。
    Handler.root = Path(args.root).resolve()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"壳已起：http://127.0.0.1:{args.port}/（租户根 {Handler.root}，"
          f"每租户并发 {args.max_per_tenant}）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
