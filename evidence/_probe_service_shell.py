# -*- coding: utf-8 -*-
"""验证最小服务壳（`repo/serve.py`）—— 按 `docs/服务化设计.md` §3 阶段 3.1/3.2 的判据。

判据（三条一起才不空转，缺一条就是恒真或空转）：

  ⑧a **等价**   壳响应 == CLI 基线（`run_one` 走 `find_corpus` 的返回值）**逐字相同**
                 ⚠️ 恒真陷阱：把壳整个绕掉、直接返回 CLI 输出也相同
                 ⇒ 它**不能单独**证明「接上了」—— 必须配 ⑧b
  ⑧b **链路**   壳**真的调了内核**：响应里含**语料特有**的内核算出量（节点数/方向数），
                 换个租户这些数**跟着变**（固定假响应 ⇒ 红）
  ⑨  **隔离**   不完整租户（缺 view_spec）⇒ **404**；不存在的租户 ⇒ 404

反向对照（证明判据会红）：把壳的 `_run` 换成**固定假响应**（不调 run_one）
⇒ ⑧a 红（与 CLI 不同）+ ⑧b 红（换租户响应不变）。跑完字节级还原 + sha256。

跑法：python outputs/_probe_service_shell.py
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent / "repo"
sys.path.insert(0, str(REPO))
os.chdir(REPO)

PORT = 8791
BASE = f"http://127.0.0.1:{PORT}"

#: 4 份**新领域**租户（都不是 12 份 ext-* 实验语料）+ 1 份不完整对照
TENANTS = ["arxiv-taxonomy", "crossref-journals", "gh-langs", "pypi-deps"]
INCOMPLETE = "cora-labeled"          # 缺 view_spec.json ⇒ 应 404
MISSING = "no-such-tenant"           # 不存在 ⇒ 应 404
CASES = [(t, "keyset", "a") for t in TENANTS] + [
    ("arxiv-taxonomy", "sequence", None),      # 换方向：输出必须变（链路）
    ("crossref-journals", "keyset", None),
]


def ok(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  [{'过' if cond else '红'}] {label}" + (f"  —— {detail}" if detail else ""))
    return cond


def _http(path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(BASE + path, timeout=60) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def _cli_baseline(tenant: str, plugin: str, only) -> tuple[bool, str]:
    """CLI 基线：设 LDV_CORPUS，走 `find_corpus` 调 run_one（loaded=None）。"""
    os.environ["LDV_CORPUS"] = tenant
    from ldv.cli import run_one
    return run_one(plugin, 12, only)


def main() -> int:
    import serve  # noqa: PLC0415（cwd=repo，sys.path 已含）

    srv = ThreadingHTTPServer(("127.0.0.1", PORT), serve.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"壳已起（探针内线程）：{BASE}\n")

    all_ok = True

    # ── ⑧a 等价：逐字比对 ────────────────────────────────────────────
    print("── ⑧a 等价（壳响应 == CLI 基线，逐字）──")
    for tenant, plugin, only in CASES:
        st, body = _http(f"/{tenant}/run?plugin={plugin}"
                         + (f"&only={only}" if only else ""))
        ok_cli, text_cli = _cli_baseline(tenant, plugin, only)
        same = st == 200 and body == text_cli
        all_ok &= ok(f"{tenant} · {plugin} · only={only}", same,
                     f"壳 {len(body.encode())}B vs CLI {len(text_cli.encode())}B"
                     + ("" if same else f"｜首差 @ {next((i for i,(x,y) in enumerate(zip(body,text_cli)) if x!=y), min(len(body),len(text_cli)))}"))

    # ── ⑧b 链路：语料特有的量跟着租户变 ──────────────────────────────
    print("\n── ⑧b 链路（响应含内核算出的语料特有量，且随租户变）──")
    bodies = {}
    for tenant in TENANTS:
        st, body = _http(f"/{tenant}/run?plugin=keyset")
        assert st == 200, f"{tenant}: {st}"
        bodies[tenant] = body
    # 判据 = 四个租户的输出**互不相同**。⚠️ 不用「节点数出现在输出里」那种脆比对：
    # 输出里没有裸节点数字，且格式会变 —— 「互不相同 + ⑧a 逐字等于各自 CLI 基线」
    # 合起来才是链路的完整证据（假响应 ⇒ 全同 ⇒ ⑧b 红）。
    distinct = len({b for b in bodies.values()}) == len(TENANTS)
    all_ok &= ok("四个租户的输出**互不相同**（假响应 ⇒ 全同）", distinct,
                 "｜".join(f"{t}:{len(b.encode())}B" for t, b in bodies.items()))

    # ── ⑨ 隔离：不完整/不存在 ⇒ 404 ──────────────────────────────────
    print("\n── ⑨ 隔离（不完整租户 / 不存在租户 ⇒ 404）──")
    st_bad, _ = _http(f"/{INCOMPLETE}/run?plugin=keyset")
    all_ok &= ok(f"缺 view_spec 的 {INCOMPLETE} ⇒ 404", st_bad == 404, f"实得 {st_bad}")
    st_miss, _ = _http(f"/{MISSING}/run?plugin=keyset")
    all_ok &= ok(f"不存在的 {MISSING} ⇒ 404", st_miss == 404, f"实得 {st_miss}")

    # ── 反向对照：壳换成固定假响应 ⇒ ⑧a/⑧b 必须红 ────────────────────
    # ⚠️ 关键：patch 的必须是**服务器正在用的那个类**（`import serve` 时那个
    #    `serve.Handler`）—— 不能 `importlib.reload`（那会造一个**新类**，
    #    patch 落在新类上、服务器还用旧的 ⇒ 打不中，判据假绿）。
    print("\n── 反向对照（壳 → 固定假响应，不调 run_one）──")
    sha_path = REPO / "serve.py"
    orig = sha_path.read_bytes()
    sha_before = hashlib.sha256(orig).hexdigest()
    orig_run = serve.Handler._run_sync        # 服务器此刻正在用的方法（3.3 从 _run 改名）
    fake_body = "假响应：不调内核"

    def fake_run(self, tenant, qs):
        self._send(200, fake_body)

    try:
        serve.Handler._run_sync = fake_run       # 直接改在用的类
        st_fake, body_fake = _http(f"/{TENANTS[0]}/run?plugin=keyset")
        red_a = body_fake != bodies[TENANTS[0]]          # ⑧a 红：与基线不同
        # 换租户 ⇒ 假响应不变 ⇒ ⑧b 红
        _, body_fake2 = _http(f"/{TENANTS[1]}/run?plugin=keyset")
        red_b = body_fake == body_fake2 and body_fake != bodies[TENANTS[1]]
        all_ok &= ok("假响应 ⇒ ⑧a 红（与基线不同）", red_a)
        all_ok &= ok("假响应 ⇒ ⑧b 红（换租户响应不变）", red_b)
    finally:
        serve.Handler._run_sync = orig_run       # 类属性还原
        sha_path.write_bytes(orig)          # 文件字节级还原（双保险）
        sha_after = hashlib.sha256(sha_path.read_bytes()).hexdigest()
        assert sha_after == sha_before, "serve.py 没还原！"
        print(f"  ✓ serve.py 字节级还原（sha256 {sha_before[:12]}）")

    srv.shutdown()
    print(f"\n═══ 探针汇总：{'全过' if all_ok else '有红'} ═══")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
