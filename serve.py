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


def _load_tenant(name: str):
    """读一个租户的语料。返回 `(nodes, edges, keyset)` 或 `None`（不完整/不存在）。"""
    base = TENANT_ROOT / name
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


def _execute(tenant: str, plugin: str, only, holdout: int) -> tuple[int, str]:
    """跑一个任务（**同步执行体** —— 3.1 的 GET 与 3.3 的 worker 共用）。"""
    loaded = _load_tenant(tenant)
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
        code, body = t["_fn"]()
        with self.cond:
            t["status"] = "done" if code == 200 else "error"
            t["finished"] = time.time()
            t["code"], t["body"] = code, body
            self.running[tenant] -= 1
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
            tenants = sorted(d.name for d in TENANT_ROOT.iterdir()
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
        only = qs.get("only", [None])[0]
        plugin = qs.get("plugin", ["keyset"])[0]
        holdout = int(qs.get("holdout", ["12"])[0])
        code, body = _execute(tenant, plugin, only, holdout)
        self._send(code, body)

    # ── 3.3 提交 + 轮询 ───────────────────────────────────────────────
    def _submit(self, tenant: str, qs: dict) -> None:
        # 先校验租户（**不完整/不存在 ⇒ 404**，与 GET 同步端点同一条规则）
        if _load_tenant(tenant) is None:
            self._send(404, f"404：租户 {tenant!r} 不存在或不完整"
                            f"（需要 nodes/ + view_spec.json）")
            return
        only = qs.get("only", [None])[0]
        plugin = qs.get("plugin", ["keyset"])[0]
        holdout = int(qs.get("holdout", ["12"])[0])

        tid = TABLE.submit(tenant, lambda: _execute(tenant, plugin, only, holdout))
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
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"壳已起：http://127.0.0.1:{args.port}/（租户根 {args.root}，"
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
