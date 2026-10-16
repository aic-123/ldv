# -*- coding: utf-8 -*-
"""服务化设计的**最小壳**（阶段 3.1 + 3.2 验证用，2026-10-16）。

按 [`docs/服务化设计.md`](docs/服务化设计.md) §2.3 的请求流：

    HTTP 请求 → 租户解析 → 壳加载租户语料 → `run_one(loaded=...)` → 响应

★ **与 CLI 的关键差别（也是本壳要验证的东西）**：CLI 走 `find_corpus()`
（环境变量 `LDV_CORPUS` → 目录），壳**自己读租户目录**再传给 `run_one` 的
`loaded` 参数。`run_one` 的 docstring 有一条警告：**传进来的那份必须与
`load()` 同源**，否则「判的是这一份、跑的是另一份」会**静默**成立。
⇒ 壳正是「传入 loaded」的真实使用者 ⇒ 等价判据（壳响应 == CLI 输出）
同时验证了「壳的加载路径与 CLI 同源」。

⚠️ **这是壳，不是内核** —— 约束 ①（壳可依赖、内核零依赖，`docs/服务化设计.md` §0）：
本文件可以 import 任何东西，`ldv` 包一行 import 都不许多。

用法：`python serve.py [--port N] [--root DIR]`
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
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


class Handler(BaseHTTPRequestHandler):
    """最小端点：`GET /<tenant>/run?only=a&holdout=12`。"""

    server_version = "ldv-shell/0.1"

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

    # ── 端点 ──────────────────────────────────────────────────────────
    def do_GET(self):  # noqa: N802（http.server 的约定名）
        url = urlparse(self.path)
        parts = [p for p in url.path.split("/") if p]

        if not parts:  # `/` —— 列出租户
            tenants = sorted(d.name for d in TENANT_ROOT.iterdir()
                             if d.is_dir() and (d / "view_spec.json").exists())
            self._json(200, {"租户": tenants})
            return

        if len(parts) == 2 and parts[1] == "run":
            self._run(parts[0], parse_qs(url.query))
            return

        self._send(404, f"404：没有这个端点（{url.path}）")

    def _run(self, tenant: str, qs: dict) -> None:
        loaded = _load_tenant(tenant)
        if loaded is None:
            self._send(404, f"404：租户 {tenant!r} 不存在或不完整"
                            f"（需要 nodes/ + view_spec.json）")
            return
        only = qs.get("only", [None])[0]
        plugin = qs.get("plugin", ["keyset"])[0]
        holdout = int(qs.get("holdout", ["12"])[0])
        nodes, edges, keyset = loaded
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                ok, text = run_one(plugin, holdout, only, loaded=(nodes, edges, keyset))
        except Exception as exc:  # 壳层如实报，不吞
            self._send(500, f"500：{type(exc).__name__}: {exc}")
            return
        self._send(200, text)   # 逐字 = `run_one` 的返回值（与 CLI `print(text)` 同源）


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="ldv 最小服务壳（阶段 3.1/3.2 验证）")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--root", default=str(TENANT_ROOT))
    args = ap.parse_args(argv[1:])
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"壳已起：http://127.0.0.1:{args.port}/（租户根 {args.root}）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
