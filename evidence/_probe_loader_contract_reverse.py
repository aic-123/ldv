"""P0 修法的**反向对照**：逐条回退真源码 ⇒ 静默误读必须回来。

跑法（cwd = repo）：python evidence/_probe_loader_contract_reverse.py

背景（2026-10-23 外部审计实测复现的 P0）：语料 front matter 解析器把三个
**合法**形状静默解析成了另一份有效数据 —— 引号内的逗号被切开、重复键后者
覆盖前者、裸 `true`/`null` 当字符串。判据 `test_loader` 的新增条守正向
（三种形状都被拒/被正确切）；本探针守另一半：**回退任一条修法，
对应的误读必须重现** —— 否则那条判据只是「恰好通过」。

    R0  现状：三种形状都被正确处理（正向）
    R1  回退「引号外切分」⇒ 引号内逗号重新被切开（判据会红）
    R2  回退「重复键拒收」 ⇒ 重复键重新静默覆盖（判据会红）
    R3  回退「歧义标量拒收」⇒ 裸 true 重新变字符串（判据会红）
    R4  还原后 sha256 与开头逐字相同

⚠️ 会**临时改写** `ldv/corpus/loader.py`，`try/finally` 保证还原。
   跑到一半被杀 ⇒ 用 git 还源。
"""
from __future__ import annotations

import hashlib
import importlib
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from ldv.corpus import loader as L                          # noqa: E402

FAIL = 0
NL = chr(10)


def chk(tag: str, what: str, good: bool, detail: str) -> None:
    global FAIL
    if not good:
        FAIL += 1
    print(f"  [{'过' if good else '红'}] {tag} {what}\n        {detail}")


def _fm(text: str):
    return L.parse_front_matter(text, "probe")


def main() -> int:
    src = REPO / "ldv" / "corpus" / "loader.py"
    orig = src.read_bytes()
    sha0 = hashlib.sha256(orig).hexdigest()
    print("=" * 78)
    print("P0 反向对照｜回退真源码 ⇒ 静默误读必须重现")
    print("=" * 78)
    try:
        # ── R0 现状 ────────────────────────────────────────────────────
        f, _ = _fm("---" + NL + 'id: x' + NL + 'aliases: ["a,b", "c"]' + NL + "---" + NL)
        chk("R0", "现状：引号内的逗号不被切开",
            f.get("aliases") == ["a,b", "c"], f"{f.get('aliases')}")

        # ── R1 回退引号外切分 ─────────────────────────────────────────
        t = orig.decode("utf-8")
        wired = "for p in _split_inline(inner, where, lineno)"
        bare = "for p in [q.strip() for q in inner.split(\",\")]"
        if wired not in t:
            chk("R1", "回退「引号外切分」", False, "锚点失配（loader.py 改过了？）")
        else:
            src.write_text(t.replace(wired, bare, 1), encoding="utf-8")
            importlib.reload(L)
            f1, _ = _fm("---" + NL + 'id: x' + NL + 'aliases: ["a,b", "c"]' + NL + "---" + NL)
            chk("R1", "回退 ⇒ 引号内逗号重新被切开（判据会红）",
                f1.get("aliases") == ['"a', 'b"', 'c'], f"{f1.get('aliases')}")
            src.write_bytes(orig)
            importlib.reload(L)

        # ── R2 回退重复键拒收 ─────────────────────────────────────────
        t = orig.decode("utf-8")
        wired2 = "    if key in out:"
        bare2 = "    if False and key in out:"
        if wired2 not in t:
            chk("R2", "回退「重复键拒收」", False, "锚点失配")
        else:
            src.write_text(t.replace(wired2, bare2, 1), encoding="utf-8")
            importlib.reload(L)
            f2, _ = _fm("---" + NL + "id: a" + NL + "id: b" + NL + "---" + NL)
            chk("R2", "回退 ⇒ 重复键重新静默覆盖（判据会红）",
                f2 == {"id": "b"}, f"{f2}")
            src.write_bytes(orig)
            importlib.reload(L)

        # ── R3 回退歧义标量拒收 ───────────────────────────────────────
        t = orig.decode("utf-8")
        wired3 = "    if raw in _AMBIGUOUS_SCALARS:"
        bare3 = "    if False and raw in _AMBIGUOUS_SCALARS:"
        if wired3 not in t:
            chk("R3", "回退「歧义标量拒收」", False, "锚点失配")
        else:
            src.write_text(t.replace(wired3, bare3, 1), encoding="utf-8")
            importlib.reload(L)
            f3, _ = _fm("---" + NL + "id: x" + NL + "flag: true" + NL + "---" + NL)
            chk("R3", "回退 ⇒ 裸 true 重新变成字符串（判据会红）",
                f3.get("flag") == "true", f"{f3}")
            src.write_bytes(orig)
            importlib.reload(L)
    finally:
        src.write_bytes(orig)
        importlib.reload(L)

    chk("R4", "还原后 sha256 与开头逐字相同",
        hashlib.sha256(src.read_bytes()).hexdigest() == sha0,
        f"{hashlib.sha256(src.read_bytes()).hexdigest()[:12]} vs {sha0[:12]}")
    print(f"\n{'✓ 全过' if not FAIL else f'✗ 红 {FAIL} 条'}（R0–R4）")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
