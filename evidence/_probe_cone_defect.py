"""探针：`Cone(x)`（`_path[x]`）与「当前真的持有 x 的方向」是否一致，
以及 `stayed_of(d)` 与「子方向的成员」是否互相矛盾。

    (a) 持有集 ≠ 锥       ⇒ `remove` 只从锥上摘 ⇒ 子方向留**幽灵成员** ⇒ `B4` 报
                           「子方向多出父没有的项」
    (b) 滞留项 ∈ 某个子方向的成员 ⇒ `B4` 的 `漏` 里没有它、而账上有它 ⇒ 报「滞留与账不符」

还要量「根覆盖之外」的项数 —— 那批项在 `members(根)` 里但 `_path[x] = ()`。

用法（cwd = repo）：python ../outputs/_probe_cone_defect.py
"""
import os
import sys

sys.path.insert(0, ".")

from ldv.checks._fixtures import load
from ldv.cli import _assemble
from ldv.flow import insert_items

CORPORA = ["openalex-n50", "openalex-n100", "openalex-n150", "openalex-b280"]
WHICHS = ["keyset", "reach", "sequence"]
HOLDOUT = 12


def holders(kernel, x):
    return {d.did for d in kernel.all_directions() if x in set(kernel.members_of(d))}


def audit(kernel, tag):
    a_bad, b_bad = [], []
    for x in sorted(kernel.items):
        h = holders(kernel, x)
        c = set(kernel.cone(x))
        if h != c:
            a_bad.append((x, sorted(h - c), sorted(c - h)))
    for d in kernel.all_directions():
        stay = set(kernel.stayed_of(d))
        if not stay:
            continue
        kids = kernel.children_of(d)
        um = set()
        for k in kids:
            um |= set(kernel.members_of(k))
        inter = sorted(stay & um)
        if inter:
            b_bad.append((d.did, inter[:3]))
    oos = [x for x in sorted(kernel.items) if x in set(kernel.members_of(kernel.root))
           and not kernel.cone(x)]
    print(f"   [{tag}] (a) 持有集≠锥：{len(a_bad)} 项"
          + (f"，例 {a_bad[0][0]}：只被这些方向持有而锥上没有 {a_bad[0][1][:3]}"
             if a_bad else "")
          + f"｜(b) 滞留项落在子成员里：{len(b_bad)} 个方向"
          + (f"，例 {b_bad[0]}" if b_bad else "")
          + f"｜根成员里但锥为空：{len(oos)} 项")
    return len(a_bad), len(b_bad)


print(f"{'语料':<14}{'方向':<10}{'段':<12}{'(a) 持有集≠锥':>14}{'(b) 滞留∈子成员':>16}")
print("─" * 70)
tot = [0, 0]
for corpus in CORPORA:
    os.environ["LDV_CORPUS"] = corpus
    loaded = load()
    if loaded is None:
        print(f"{corpus:<14} 语料找不到 ⇒ 跳过（不是通过）")
        continue
    nodes, edges, _ = loaded
    for which in WHICHS:
        kernel, queries, new, removable, cover = _assemble(which, nodes, edges, HOLDOUT, 0)
        a, b = audit(kernel, "A 批建")
        print(f"{corpus:<14}{which:<10}{'A 批建':<12}{a:>14}{b:>16}")
        tot[0] += a
        tot[1] += b
        insert_items(kernel, new)
        a, b = audit(kernel, "B 维护")
        print(f"{corpus:<14}{which:<10}{'B 维护':<12}{a:>14}{b:>16}")
        tot[0] += a
        tot[1] += b

print(f"\n合计：(a) {tot[0]} 项、(b) {tot[1]} 个方向")
