#!/usr/bin/env python3
"""最小复现：server 对含 ⇒ 文本的 remember 在不同 env 下的行为。"""
import sys, os, subprocess, json, itertools, time
from pathlib import Path

ARROW = chr(0x21D2)  # ⇒

store = Path(__file__).resolve().parent.parent / "e4_store3"
(store / "_data" / "t1" / "nodes").mkdir(parents=True, exist_ok=True)


def t(env_extra, label):
    env = dict(os.environ)
    env.update({"LDV_MCP_STORE": str(store)})
    env.update(env_extra)
    p = subprocess.Popen([sys.executable, "server.py"], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         env=env, text=True, encoding="utf-8", errors="replace",
                         bufsize=1, cwd=str(store.parent / "ldv-mcp"))
    ids = itertools.count(1)

    def rpc(method, params):
        rid = next(ids)
        p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method,
                                  "params": params}, ensure_ascii=True) + "\n")
        p.stdin.flush()
        deadline = time.time() + 30
        while time.time() < deadline:
            line = p.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if msg.get("id") == rid:
                return msg
        return {"result": "TIMEOUT"}

    rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "t", "version": "0"}})
    rpc("notifications/initialized", {})
    body = "测试要点：性能提升 " + ARROW + " 用户满意度上升。"
    r = rpc("tools/call", {"name": "remember", "arguments": {
        "corpus": "t1", "id": "x1",
        "fields": {"type": "陈述", "title": "测试", "sid": "T01"},
        "body": body, "actor": "agent", "session": "t1"}})
    out = str(r.get("result") or r.get("error"))[:160].replace("\n", " ")
    print(label, "->", out, flush=True)
    p.kill()


t({}, "default-env")
t({"PYTHONIOENCODING": "utf-8:replace", "PYTHONUTF8": "1"}, "utf8-forced")
