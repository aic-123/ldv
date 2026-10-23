"""§I7 接线的**反向对照**：把接线拔掉，判据必须红（留档，不进退出码闸门）。

跑法（cwd = repo）：python evidence/_probe_usage_snapshot_reverse.py

为什么单独立一个探针、不写进 `run_tests`：回退**真源码**跑一遍太重
（本仓库惯例，见 `run_tests` 第 72 条附近的说明）。判据 `[接入] ⑨`
守正向（接线在 ⇒ 快照在）；这个探针守的是另一半 ——
**接线不在 ⇒ 快照不在**，即判据⑨-① 会红。只有正向没有反向，
「快照在」可能只是巧合（比如别处顺手写了同样的字段）。

四条判据：

    V1  接线在（现状）      ⇒ 下钻后记录**有** snapshot
    V2  回退 entry 的接线   ⇒ 记录**没有** snapshot（判据 ⑨-① 会红）
    V3  回退 usage_store 的格式行 ⇒ 同样没有（接线 ≠ 格式，两边各守一层）
    V4  还原后两个文件 sha256 与开头逐字相同（回退没留痕）

⚠️ 会**临时改写** `entry.py` 与 `ldv/tools/usage_store.py`，`try/finally`
   保证还原；V4 用 sha256 验字节级一致。跑到一半被杀 ⇒ 用 git 还源。
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

import entry as E                                            # noqa: E402
from ldv.checks._fixtures import find_corpus                # noqa: E402
from ldv.tools import usage_store as US                      # noqa: E402

FAIL = 0


def chk(tag: str, what: str, good: bool, detail: str) -> None:
    global FAIL
    if not good:
        FAIL += 1
    print(f"  [{'过' if good else '红'}] {tag} {what}\n        {detail}")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


ENTRY = REPO / "entry.py"
USTORE = REPO / "ldv" / "tools" / "usage_store.py"
#: 回退锚点 —— **按真源码逐字写**（改源码时这里会失配 ⇒ 探针自己报红，不静默）
ENTRY_WIRED = "                                  propensity=rec.propensity, snapshot=snap)"
ENTRY_BARE = "                                  propensity=rec.propensity)"
US_WIRED = '    snap_line = f"snapshot: {snapshot}\\n" if snapshot else ""'
US_BARE = '    snap_line = ""'


def drill_once(nodes: Path, corpus_root: Path) -> dict:
    """搭一个新 App、查一次、钻一支，返回那条记录的 snapshot（空串 = 没接上）。"""
    import os

    os.environ["LDV_CORPUS"] = str(nodes)
    US.clear(corpus_root)
    app = E.App(None)
    r = app.query({"require": []})
    dids = [n["did"] for n in (r.get("nodes") or [])]
    if not dids:
        return {"_err": "没有可下钻的支"}
    app.drill(dids[0])
    rows = {x["did"]: x for x in US.load(corpus_root)}
    row = rows.get(dids[0]) or {}
    return {"did": dids[0], "snapshot": row.get("snapshot", ""),
            "n_records": len(rows)}


def main() -> int:
    builtin = find_corpus()
    if builtin is None:
        print("⊘ 内置语料不在 —— 跳过（判据侧同样报跳过）")
        return 0
    tmp = Path(tempfile.mkdtemp(prefix="ldv-snaprev-"))
    nodes = tmp / "_data" / "shop" / "nodes"
    nodes.mkdir(parents=True)
    for f in sorted(builtin.glob("*.md")):
        shutil.copy2(f, nodes / f.name)
    corpus_root = tmp / "_data" / "shop"

    entry0, us0 = ENTRY.read_bytes(), USTORE.read_bytes()
    sha_e0, sha_u0 = _sha(ENTRY), _sha(USTORE)
    print("=" * 78)
    print("§I7 接线反向对照｜回退真源码 ⇒ 判据 [接入] ⑨-① 必须红")
    print("=" * 78)
    try:
        # ── V1 现状：接线在 ⇒ 快照在 ────────────────────────────────────
        r1 = drill_once(nodes, corpus_root)
        chk("V1", "现状（接线在）：下钻 ⇒ 记录带 snapshot",
            bool(r1.get("snapshot")) and '"did"' in r1.get("snapshot", ""),
            f"{r1.get('did')}｜快照 {len(r1.get('snapshot', ''))} 字节")

        # ── V2 回退 entry 的接线 ⇒ 必须没有 ────────────────────────────
        src = ENTRY.read_text(encoding="utf-8")
        if ENTRY_WIRED not in src:
            chk("V2", "回退 entry 接线", False,
                f"锚点失配（entry.py 改过了？）—— 找不着：{ENTRY_WIRED.strip()[:50]}")
        else:
            ENTRY.write_text(src.replace(ENTRY_WIRED, ENTRY_BARE, 1), encoding="utf-8")
            importlib.reload(E)
            r2 = drill_once(nodes, corpus_root)
            chk("V2", "回退 entry 接线 ⇒ 记录**没有** snapshot（判据会红）",
                r2.get("snapshot", "x") == "" and r2.get("n_records") == 1,
                f"{r2.get('did')}｜快照 {len(r2.get('snapshot', ''))} 字节｜"
                f"记录 {r2.get('n_records')} 条")

        # ── V3 回退格式行（entry 接线还在）⇒ 同样没有 ──────────────────
        ENTRY.write_bytes(entry0)                 # 先还 entry
        importlib.reload(E)
        src = USTORE.read_text(encoding="utf-8")
        if US_WIRED not in src:
            chk("V3", "回退 usage_store 格式行", False,
                "锚点失配（usage_store.py 改过了？）")
        else:
            USTORE.write_text(src.replace(US_WIRED, US_BARE, 1), encoding="utf-8")
            importlib.reload(US)
            r3 = drill_once(nodes, corpus_root)
            chk("V3", "回退格式行 ⇒ 记录**没有** snapshot（判据会红）",
                r3.get("snapshot", "x") == "" and r3.get("n_records") == 1,
                f"{r3.get('did')}｜快照 {len(r3.get('snapshot', ''))} 字节｜"
                f"记录 {r3.get('n_records')} 条")
    finally:
        ENTRY.write_bytes(entry0)
        USTORE.write_bytes(us0)
        importlib.reload(E)
        importlib.reload(US)
        shutil.rmtree(tmp, ignore_errors=True)

    # ── V4 还原验字节 ────────────────────────────────────────────────
    chk("V4", "还原后两个文件 sha256 与开头逐字相同",
        _sha(ENTRY) == sha_e0 and _sha(USTORE) == sha_u0,
        f"entry {_sha(ENTRY)[:12]} vs {sha_e0[:12]}｜"
        f"usage_store {_sha(USTORE)[:12]} vs {sha_u0[:12]}")

    print(f"\n{'✓ 全过' if not FAIL else f'✗ 红 {FAIL} 条'}（V1–V4）")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
