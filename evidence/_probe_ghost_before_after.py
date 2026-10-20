"""单变量对照：`_expand_inner` 里那一段「把项追加进自己的锥」拿掉 vs 留着。

**为什么要这个探针**：`kernel._expand_inner` 的修复前读数（「(a) 49 项」）
是在**修复前的代码**上量的。那份代码已经不在了 ⇒ 直接引那个数字等于
引一个**自己重跑不出来**的读数。所以这里用一个**最小的单变量**把修复前的
行为**重建出来**：

    `_expand_inner` 里对 `_path` 的写入**只有**那一段追加（第 597–600 行），
    所以「进函数前快照 `_path`、返回前还原」与「把那段追加删掉」**逐字等价**。

两臂跑同一批语料、同一个装配、同一个审计函数 ⇒ 差别只有一个变量。

用法（**cwd = repo**）：
    python ../outputs/_probe_ghost_before_after.py

⚠️ **必须从 `repo/` 跑**。工作区根下有一份 2026-10-05 的 `ldv/` 陈旧副本，
从那里跑会**静默**导入旧副本（症状：`_assemble` 参数个数对不上，或读数
看起来合理但其实是旧代码）。
"""
import os
import sys

sys.path.insert(0, ".")

from ldv.checks._fixtures import load
from ldv.cli import _assemble
from ldv.core.kernel import Kernel
from ldv.flow import insert_items

CORPORA = ["openalex-n50", "openalex-n100", "openalex-n150", "openalex-b280"]
WHICHS = ["keyset", "reach", "sequence"]
HOLDOUT = 12


def holders(kernel, x):
    return {d.did for d in kernel.all_directions() if x in set(kernel.members_of(d))}


def audit(kernel):
    """(a) 持有集 ≠ 锥 的项数 —— 修复前 = 幽灵成员数。"""
    n = 0
    for x in sorted(kernel.items):
        if holders(kernel, x) != set(kernel.cone(x)):
            n += 1
    return n


# ── 修复前的行为：把 `_expand_inner` 对 `_path` 的追加撤销掉 ────────────────
_orig_expand_inner = Kernel._expand_inner


def old_expand_inner(self, d):
    before = dict(self._path)
    kids = _orig_expand_inner(self, d)
    self._path = before          # ← 唯一变量：撤销那段追加
    return kids


def sweep(tag, patch: bool):
    if patch:
        Kernel._expand_inner = old_expand_inner
    else:
        Kernel._expand_inner = _orig_expand_inner
    tot = {"A": 0, "B": 0}
    try:
        for corpus in CORPORA:
            os.environ["LDV_CORPUS"] = corpus
            loaded = load()
            if loaded is None:
                print(f"  {corpus}: 语料找不到 ⇒ 跳过（不是通过）")
                continue
            nodes, edges, _ = loaded
            for which in WHICHS:
                kernel, queries, new, removable, cover = _assemble(
                    which, nodes, edges, HOLDOUT, 0)
                tot["A"] += audit(kernel)
                insert_items(kernel, new)
                tot["B"] += audit(kernel)
    finally:
        Kernel._expand_inner = _orig_expand_inner
    print(f"  {tag:<12} 批建后 {tot['A']:>4} 项 ｜ 维护 12 项后 {tot['B']:>4} 项")
    return tot


print("单变量对照：(a) 「持有集 ≠ 锥」的项数  （4 份语料 × 3 个方向 = 12 行）")
print("─" * 72)
after = sweep("修复后（现行）", patch=False)
before = sweep("修复前（重建）", patch=True)
print("─" * 72)
print(f"  差：批建 {before['A']} → {after['A']}；维护 {before['B']} → {after['B']}")
