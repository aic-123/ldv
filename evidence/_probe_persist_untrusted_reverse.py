"""持久化白名单的**反向对照**：拔掉白名单 ⇒ 不可信存档必须能进来。

跑法（cwd = repo）：python evidence/_probe_persist_untrusted_reverse.py

背景（2026-10-23 外部审计点名）：`_dc_lookup` 旧实现是「存档里写哪个模块就
import 哪个」，`_dec` 再 `cls(**kwargs)` ⇒ **存档字符串决定 import 哪个模块、
构造哪个类、传什么参数**。docstring 一直写着「存档是可信输入」，
但边界不能只是文档。判据 `test_persist_untrusted` 守正向（白名单外拒收）；
本探针守另一半：**拔掉白名单，那个原语必须回来**。

    V1  现状：`_dc_lookup("os.system")` ⇒ PersistenceError（白名单挡着）
    V2  回退白名单（`if name not in ALLOWED` ⇒ `if False and …`）
        ⇒ 同一个调用**拿到 os.system 本尊**（判据 ① 会红）
    V3  还原后 sha256 逐字相同

⚠️ 只 import + getattr，**不调用** —— V2 证明的是「原语够得着」，
   不真的执行任何东西。跑到一半被杀 ⇒ git 还源。
"""
from __future__ import annotations

import hashlib
import importlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from ldv.core import persist as P                            # noqa: E402

FAIL = 0


def chk(tag: str, what: str, good: bool, detail: str) -> None:
    global FAIL
    if not good:
        FAIL += 1
    print(f"  [{'过' if good else '红'}] {tag} {what}\n        {detail}")


def main() -> int:
    src = REPO / "ldv" / "core" / "persist.py"
    orig = src.read_bytes()
    sha0 = hashlib.sha256(orig).hexdigest()
    print("=" * 78)
    print("持久化白名单反向对照｜拔掉白名单 ⇒ 不可信存档够得着")
    print("=" * 78)
    try:
        # ── V1 现状 ────────────────────────────────────────────────────
        try:
            P._dc_lookup("os.system")
            chk("V1", "现状：白名单外 ⇒ 拒收", False, "居然没拒")
        except P.PersistenceError as e:
            chk("V1", "现状：白名单外 ⇒ 拒收", "白名单" in str(e), f"{str(e)[:70]}")

        # ── V2 回退白名单 ⇒ 原语回来 ───────────────────────────────────
        t = orig.decode("utf-8")
        wired = "    if name not in ALLOWED_DATACLASSES:"
        bare = "    if False and name not in ALLOWED_DATACLASSES:"
        if wired not in t:
            chk("V2", "回退白名单", False, "锚点失配（persist.py 改过了？）")
        else:
            src.write_text(t.replace(wired, bare, 1), encoding="utf-8")
            importlib.reload(P)
            got = P._dc_lookup("os.system")
            chk("V2", "回退 ⇒ `_dc_lookup('os.system')` 拿到本尊（判据 ① 会红）",
                got is __import__("os").system, f"拿到 {got!r}")
            src.write_bytes(orig)
            importlib.reload(P)
    finally:
        src.write_bytes(orig)
        importlib.reload(P)

    chk("V3", "还原后 sha256 与开头逐字相同",
        hashlib.sha256(src.read_bytes()).hexdigest() == sha0,
        f"{hashlib.sha256(src.read_bytes()).hexdigest()[:12]} vs {sha0[:12]}")
    print(f"\n{'✓ 全过' if not FAIL else f'✗ 红 {FAIL} 条'}（V1–V3）")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
