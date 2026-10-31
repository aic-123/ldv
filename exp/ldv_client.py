#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""最小 MCP stdio 客户端 —— E1/E2 的 M 组经它与 ldv-mcp 通信。

用法：
    from ldv_client import LdvSession
    with LdvSession(server_cmd=[sys.executable, "server.py"],
                    env={"LDV_MCP_STORE": str(store)}) as s:
        tools = s.list_tools()
        out = s.call("remember", {"text": "..."})

协议：MCP stdio = 逐行 JSON-RPC 2.0（无 Content-Length 头）。initialize → initialized 通知
→ tools/list → tools/call。**不依赖任何第三方 SDK**（内核零依赖纪律的镜像）。
"""
from __future__ import annotations

import itertools
import json
import os
import subprocess
import sys
import threading
import queue


class LdvError(RuntimeError):
    pass


class LdvSession:
    def __init__(self, server_cmd: list[str], env: dict | None = None,
                 timeout: float = 120.0, cwd: str | None = None):
        e = dict(os.environ)
        e.update(env or {})
        # E4 根因修复：Windows 管道下 server 的 stdio/open 默认 locale（GBK），
        # 输出 GBK 外字符（如 ⇒）即 UnicodeEncodeError 崩溃、停止回传响应
        #（表象是"server 僵住"）。强制 server 全 UTF-8 且编码错误降级为替换。
        e.setdefault("PYTHONIOENCODING", "utf-8:replace")
        e.setdefault("PYTHONUTF8", "1")
        self.p = subprocess.Popen(server_cmd, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=e, text=True, encoding="utf-8",
                                  errors="replace",   # 双向兜底：读写均不因坏字节崩溃
                                  bufsize=1, cwd=cwd)
        self.timeout = timeout
        self._cmd, self._env, self._cwd = list(server_cmd), env or {}, cwd
        self._q: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        self._ids = itertools.count(1)
        self.server_info = self._rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "e1-client", "version": "0.1"},
        })
        self.notify("notifications/initialized", {})

    # ── 传输 ──────────────────────────────────────────────────────────
    def _read(self):
        try:
            for line in self.p.stdout:
                if self._stop.is_set():
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    self._q.put(json.loads(line))
                except ValueError:
                    self._q.put({"_bad": line})
        except Exception:                              # noqa: BLE001
            # reader 线程绝不允许静默死亡（E4 教训：死掉后一切调用超时且无诊断）
            if not self._stop.is_set():
                self._q.put({"_reader_dead": True})

    def _send(self, obj: dict):
        # ensure_ascii=True：纯 ASCII 转义输出，彻底绕开管道编码层的一切
        # surrogate/GBK 问题（E4 实测：server 端 encode surrogate 崩 → 停止回传）
        self.p.stdin.write(json.dumps(obj, ensure_ascii=True) + "\n")
        self.p.stdin.flush()

    def _wait_id(self, rid: int) -> dict:
        import time
        deadline = time.time() + self.timeout
        pending: dict[int, dict] = {}
        while time.time() < deadline:
            try:
                msg = self._q.get(timeout=1.0)
            except queue.Empty:
                continue
            if msg.get("id") == rid:
                return msg
            if "id" in msg:
                pending[msg["id"]] = msg      # 乱序响应先存
        raise LdvError(f"等待响应 #{rid} 超时（{self.timeout}s）")

    def _rpc(self, method: str, params: dict) -> dict:
        rid = next(self._ids)
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        msg = self._wait_id(rid)
        if "error" in msg:
            raise LdvError(f"{method}: {msg['error']}")
        return msg.get("result", {})

    def notify(self, method: str, params: dict):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    # ── MCP 面 ────────────────────────────────────────────────────────
    def list_tools(self) -> list[dict]:
        return self._rpc("tools/list", {}).get("tools", [])

    def _restart(self):
        """server 进程死亡后的自救：重拉一个并重新握手（E2 长跑实测 server 会中途死）。"""
        try:
            self.close()
        except Exception:                              # noqa: BLE001
            pass
        self.p = subprocess.Popen(self._cmd, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=self._env, text=True, encoding="utf-8",
                                  bufsize=1, cwd=self._cwd)
        self._q: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        self._rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                 "clientInfo": {"name": "e1-client", "version": "0.1"}})
        self.notify("notifications/initialized", {})

    def recycle(self):
        """主动重启 server（E4 实测：server 长跑会随机"僵住"——实际收到请求并
        写入成功，但响应不再回传；重启是唯一可靠恢复手段。单次调用隔离模式
        下每次 call 前调用，84 次调用仅多花 ~1 分钟握手开销。"""
        self._restart()

    def call(self, tool: str, args: dict) -> str:
        """tools/call，返回首条文本内容。异常（isError）抛 LdvError。

        ⚠️ server 进程中途死亡（E2 长跑实测）⇒ 管道断裂 OSError ⇒ **自动重启一次再试**。
        """
        for attempt in (0, 1):
            try:
                res = self._rpc("tools/call", {"name": tool, "arguments": args})
                break
            except (OSError, ValueError) as exc:
                if attempt == 1:
                    raise LdvError(f"工具 {tool}: server 重启后仍失败——{exc}") from exc
                self._restart()
            except LdvError as exc:
                # E4 实测：server 可能"僵住"（进程活着但不响应）⇒ 超时也是僵尸征兆，
                # 与管道断裂同样处理：重启一次再试。业务性 isError 会在重试后原样抛出。
                if attempt == 1:
                    raise
                self._restart()
        if res.get("isError"):
            raise LdvError(str(res.get("content")))
        for c in res.get("content", []):
            if c.get("type") == "text":
                return c.get("text", "")
        return ""

    # ── 生命周期 ──────────────────────────────────────────────────────
    def close(self):
        self._stop.set()
        try:
            self.p.stdin.close()
        except OSError:
            pass
        try:
            self.p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.p.kill()
        if self._reader.is_alive():
            self._reader.join(timeout=2)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


if __name__ == "__main__":
    # 自检：不带 server 起不来时给出清晰报错
    try:
        with LdvSession([sys.executable, "server.py"],
                        env={"LDV_MCP_STORE": "/tmp/ldv-selftest"}) as s:
            print("server:", s.server_info.get("serverInfo"))
            for t in s.list_tools():
                print(" -", t.get("name"))
    except (LdvError, FileNotFoundError, OSError) as exc:
        print(f"起不来（预期：需在 ldv-mcp 目录、依赖就绪）：{type(exc).__name__}: {exc}")
        raise SystemExit(2)
