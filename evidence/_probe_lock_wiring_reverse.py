# -*- coding: utf-8 -*-
"""反向对照：把「接线」**从真源码里回退掉** ⇒ ⑧b 必须红。

⚠️ **不用 monkeypatch 假实现** —— 改的是 `ldv/cli.py` 里的**真源码**
   （把 `(LockedKernel if locked else Kernel)(…)` 回退成 `Kernel(…)`），
   跑完**字节级还原**并比 sha256。

跑法：python outputs/_probe_lock_wiring_reverse.py
"""

from __future__ import annotations

import hashlib
import io
import subprocess
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

HERE = Path(__file__).resolve().parent
REPO = HERE.parent / "repo"
CLI = REPO / "ldv" / "cli.py"
PY = sys.executable

AFTER = "        kernel = (LockedKernel if locked else Kernel)(plug, init_items)"
BEFORE = "        kernel = Kernel(plug, init_items)"


def check() -> int:
    """⑧b 的判据本体（在**子进程**里跑，好让改动后的源码被重新 import）。"""
    sys.path.insert(0, str(REPO))
    from ldv import cli
    from ldv.checks._fixtures import load
    from ldv.core.locking import LockedKernel

    loaded = load()
    if loaded is None:
        print("VERDICT SKIP")
        return 2
    nodes, edges, _ = loaded
    k, *_ = cli._assemble("keyset", nodes, edges, 12, locked=True)
    taken = len(getattr(getattr(k, "locks", None), "taken", []))
    good = isinstance(k, LockedKernel) and taken > 0
    print(f"VERDICT {'GREEN' if good else 'RED'}｜类型={type(k).__name__}｜拿锁 {taken} 次")
    return 0 if good else 1


def run_check() -> tuple[int, str]:
    r = subprocess.run([PY, str(Path(__file__).resolve()), "--check"],
                       capture_output=True, text=True, encoding="utf-8")
    line = [l for l in (r.stdout or "").splitlines() if l.startswith("VERDICT")]
    return r.returncode, (line[-1] if line else (r.stderr or "").strip()[-120:])


def main() -> int:
    if "--check" in sys.argv:
        return check()

    raw0 = CLI.read_bytes()
    sha0 = hashlib.sha256(raw0).hexdigest()
    print("=" * 78)
    print("反向对照：把 `--locked` 的接线**从真源码里回退掉** ⇒ ⑧b 必须红")
    print("=" * 78)
    print(f"改前 cli.py sha256 = {sha0[:16]}…（{len(raw0)} 字节）\n")

    fails: list[str] = []
    rc_a, out_a = run_check()
    print(f"① 接线**在**（真源码）        ⇒ rc={rc_a}｜{out_a}")
    if rc_a != 0:
        fails.append("接线在的时候⑧b 居然红了")

    text = raw0.decode("utf-8")
    n = text.count(AFTER)
    assert n == 1, f"锚点命中 {n} 次（必须恰好 1）—— 源码变了？"
    CLI.write_bytes(text.replace(AFTER, BEFORE).encode("utf-8"))
    try:
        rc_b, out_b = run_check()
        print(f"② 接线**回退掉**（修前源码）  ⇒ rc={rc_b}｜{out_b}")
        if rc_b == 0:
            fails.append("把接线回退掉之后⑧b **仍然绿** —— 这条判据是恒绿的")
    finally:
        CLI.write_bytes(raw0)
    sha1 = hashlib.sha256(CLI.read_bytes()).hexdigest()
    print(f"\n还原后 cli.py sha256 = {sha1[:16]}…")
    if sha1 != sha0:
        fails.append("还原失败：sha256 不一致")
    else:
        print("  ✓ 字节级还原（sha256 相符）")

    print()
    if fails:
        print("✗ 反向对照：红 —— " + "；".join(fails))
        return 1
    print("✓ 反向对照：**如期**（接线在 ⇒ 绿；接线回退 ⇒ 红）⇒ ⑧b **会红**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
