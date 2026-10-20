# -*- coding: utf-8 -*-
"""验证阶段 3.3：限流 + 任务队列 —— 按 `docs/服务化设计.md` §3 阶段 3.3 的判据。

判据（全部**确定性**，不靠抢窗口 —— 用大语料当慢任务）：

  ⑩ **排队生效**   同租户：任务 A（大语料，必然跑几秒）跑到 `running` 后，
                    再提交同租户 B ⇒ B **必然 `queued`**（A 占着唯一槽位）
  ⑪ **跨租户不受影响**（**单变量**：只换租户名）A 还 running 时，
                    提交**另一个租户** C ⇒ C **立刻 `running`**
  ⑫ **完成后续跑**   A 完成后，排队的 B 自动开始（FIFO）⇒ 最终全 done

反向对照（证明判据会红）：`MAX_PER_TENANT` 从 1 改成 ∞（不限流）
⇒ ⑩ 红（B 不等 ⇒ 直接 running）。跑完还原 + sha256。

跑法：python outputs/_probe_service_queue.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent / "repo"
sys.path.insert(0, str(REPO))
os.chdir(REPO)

PORT = 8793
BASE = f"http://127.0.0.1:{PORT}"

#: 慢任务租户 = ext-coauth（9875 节点，全流程实测 ~112 s ⇒ 窗口确定性极强）
SLOW = "ext-coauth"
#: 跨租户对照 = 小语料（毫秒级）
FAST = "pypi-deps"


def ok(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  [{'过' if cond else '红'}] {label}" + (f"  —— {detail}" if detail else ""))
    return cond


def _req(method: str, path: str) -> tuple[int, dict]:
    req = urllib.request.Request(BASE + path, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
    try:
        return r.status if "r" in dir() else 0, json.loads(raw)
    except json.JSONDecodeError:
        return 0, {"raw": raw[:200]}


def _status(tid: str) -> dict:
    with urllib.request.urlopen(BASE + f"/tasks/{tid}", timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    import serve  # noqa: PLC0415

    srv = ThreadingHTTPServer(("127.0.0.1", PORT), serve.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"壳已起（探针内线程，每租户并发 {serve.TABLE.max}）：{BASE}\n")

    all_ok = True

    # ── ⑩ 排队生效 ────────────────────────────────────────────────────
    print("── ⑩ 排队生效（同租户：A running 期间，B 必须 queued）──")
    with urllib.request.urlopen(urllib.request.Request(BASE + f"/{SLOW}/run", data=b"", method="POST")) as r:
        tid_a = json.loads(r.read().decode("utf-8"))["task_id"]
    # 轮询到 A 真的 running（**不等完成** —— 窗口由此打开）
    t0 = time.time()
    while _status(tid_a)["status"] not in ("running", "done") and time.time() - t0 < 30:
        time.sleep(0.05)
    st_a = _status(tid_a)["status"]
    all_ok &= ok(f"A（{SLOW}）进入 running", st_a == "running", f"状态 {st_a}")

    with urllib.request.urlopen(urllib.request.Request(BASE + f"/{SLOW}/run", data=b"", method="POST")) as r:
        tid_b = json.loads(r.read().decode("utf-8"))["task_id"]
    st_b = _status(tid_b)["status"]
    all_ok &= ok("同租户 B 在 A running 期间 ⇒ **queued**（不并发）", st_b == "queued",
                 f"B 状态 {st_b}（若 running ⇒ 限流没生效）")

    # ── ⑪ 跨租户不受影响（单变量：只换租户名）────────────────────────
    print("\n── ⑪ 跨租户不受影响（A 还 running，另一个租户 C ⇒ 立刻 running）──")
    with urllib.request.urlopen(urllib.request.Request(BASE + f"/{FAST}/run", data=b"", method="POST")) as r:
        tid_c = json.loads(r.read().decode("utf-8"))["task_id"]
    t0 = time.time()
    st_c = "queued"
    while time.time() - t0 < 10:
        st_c = _status(tid_c)["status"]
        if st_c == "running":
            break
        time.sleep(0.02)
    all_ok &= ok(f"跨租户 C（{FAST}）⇒ running（不被 A 挡住）", st_c == "running",
                 f"C 状态 {st_c}")

    # ── ⑫ 完成后续跑（FIFO）──────────────────────────────────────────
    print("\n── ⑫ 完成后续跑（A 完成后，排队的 B 自动开始，最终全 done）──")
    t0 = time.time()
    st_b2 = _status(tid_b)["status"]
    while st_b2 in ("queued", "running") and time.time() - t0 < 300:
        time.sleep(0.2)
        st_b2 = _status(tid_b)["status"]
    body_b = _status(tid_b)
    all_ok &= ok("B 最终 done（A 完成后自动开始）", st_b2 == "done",
                 f"B 状态 {st_b2}，耗时 {time.time()-t0:.1f}s"
                 + (f"｜code={body_b.get('code')}" if st_b2 != "done" else ""))
    st_c2 = _status(tid_c)["status"]
    t0 = time.time()
    while st_c2 == "running" and time.time() - t0 < 60:
        time.sleep(0.1)
        st_c2 = _status(tid_c)["status"]
    all_ok &= ok("C 最终 done", st_c2 == "done", f"C 状态 {st_c2}")

    # ── 反向对照：不限流（max=∞）⇒ ⑩ 必须红 ─────────────────────────
    print("\n── 反向对照（每租户并发改成 ∞ ⇒ 排队失效，⑩ 红）──")
    sha_path = REPO / "serve.py"
    orig = sha_path.read_bytes()
    sha_before = hashlib.sha256(orig).hexdigest()
    try:
        serve.TABLE.max = 10**9          # 不限流
        with urllib.request.urlopen(urllib.request.Request(BASE + f"/{SLOW}/run", data=b"", method="POST")) as r:
            tid_x = json.loads(r.read().decode("utf-8"))["task_id"]
        t0 = time.time()
        while _status(tid_x)["status"] not in ("running", "done") and time.time() - t0 < 30:
            time.sleep(0.05)
        with urllib.request.urlopen(urllib.request.Request(BASE + f"/{SLOW}/run", data=b"", method="POST")) as r:
            tid_y = json.loads(r.read().decode("utf-8"))["task_id"]
        st_y = _status(tid_y)["status"]
        red_10 = st_y == "running"       # ⑩ 红：B 不排队直接跑
        all_ok &= ok("不限流 ⇒ 同租户第二个直接 running（⑩ 红）", red_10,
                     f"Y 状态 {st_y}")
    finally:
        serve.TABLE.max = 1              # 还原
        sha_path.write_bytes(orig)
        sha_after = hashlib.sha256(sha_path.read_bytes()).hexdigest()
        assert sha_after == sha_before, "serve.py 没还原！"
        print(f"  ✓ serve.py 字节级还原（sha256 {sha_before[:12]}）")

    srv.shutdown()
    print(f"\n═══ 探针汇总：{'全过' if all_ok else '有红'} ═══")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
