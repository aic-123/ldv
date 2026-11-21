"""探针：三个阶段 × 三个方向 × 多份语料，把 `B4` 与 `D2` 的**四种口径**并排量出来。

四种 D2 口径：

    D2 裸        覆盖(父) − ∪覆盖(子)                              ← 现在 `cli` 用的
    D2 收域      (覆盖(父) − ∪覆盖(子)) ∩ 树的项集                 ← 对齐 oracle 与树的**论域**
    D2 记账      (覆盖(父) − ∪覆盖(子)) − 账上滞留                  ← 「每一条漏都有证明」
    D2 收域+记账 (覆盖(父) − ∪覆盖(子)) ∩ 树 − 账上滞留(∩树)        ← 两条一起

`B4` 同时量两种：现写法（账上滞留不筛）与筛过活项的写法。

用法（cwd = repo）：python ../outputs/_probe_d2_four.py
"""
import os
import sys

sys.path.insert(0, ".")

from ldv.checks._fixtures import load
from ldv.checks.contract import b4_split_is_partition
from ldv.checks._framework import Report
from ldv.cli import _assemble
from ldv.flow import insert_items, remove_items

CORPORA = ["openalex-n50", "openalex-n100", "openalex-n150", "openalex-b280"]
WHICHS = ["keyset", "reach", "sequence"]
HOLDOUT = 12


def d2_four(kernel, cover):
    """四种口径的 D2 漏项数 + 逐对明细。"""
    raw = dom = acct = both = 0
    live = set(kernel.items)
    detail = []
    for d in kernel.all_directions():
        kids = kernel.children_of(d)
        if not kids:
            continue
        cp = set(cover(d.payload))
        uc = set()
        for k in kids:
            uc |= set(cover(k.payload))
        miss = cp - uc
        if not miss:
            continue
        stay = set(kernel.stayed_of(d))
        raw += len(miss)
        m_dom = miss & live
        m_acct = miss - stay
        m_both = m_dom - (stay & live)
        dom += len(m_dom)
        acct += len(m_acct)
        both += len(m_both)
        detail.append((d.did, len(miss), len(m_dom), len(m_acct), len(m_both),
                       sorted(miss)[:3]))
    return raw, dom, acct, both, detail


def b4_now(kernel):
    rep = Report("B4")
    b4_split_is_partition(kernel, rep)
    a = rep.assertions[0]
    return a.result.name, a.detail


print(f"{'语料':<14}{'方向':<10}{'段':<10}"
      f"{'D2裸':>6}{'收域':>6}{'记账':>6}{'收域+记账':>10}  B4")
print("─" * 90)
rows = []
for corpus in CORPORA:
    os.environ["LDV_CORPUS"] = corpus
    loaded = load()
    if loaded is None:
        print(f"{corpus:<14} 语料找不到 ⇒ 跳过（**不是通过**）")
        continue
    nodes, edges, _ = loaded
    for which in WHICHS:
        kernel, queries, new, removable, cover = _assemble(
            which, nodes, edges, HOLDOUT, HOLDOUT)
        for tag, act in (("A 批建", None), ("B 维护", "ins"), ("B′ 删除", "del")):
            if act == "ins":
                insert_items(kernel, new)
            elif act == "del":
                remove_items(kernel, removable, cover=cover)
            raw, dom, acct, both, detail = d2_four(kernel, cover)
            b4r, b4d = b4_now(kernel)
            print(f"{corpus:<14}{which:<10}{tag:<10}"
                  f"{raw:>6}{dom:>6}{acct:>6}{both:>10}  {b4r}")
            if detail:
                for did, r, dm, a, b, ex in detail[:2]:
                    print(f"{'':<34}· {did}: 裸{r} 收域{dm} 记账{a} 收域+记账{b} 例{ex}")
            rows.append((corpus, which, tag, raw, dom, acct, both, b4r, b4d))

print("\n═══ 汇总 ═══")
red_b4 = [r for r in rows if r[7] != "YES"]
print(f"B4 红/跳的行：{len(red_b4)} / {len(rows)}")
for r in red_b4[:6]:
    print(f"   {r[0]} / {r[1]} / {r[2]}: {r[7]}｜{r[8][:110]}")
print(f"四种 D2 口径的**总漏项数**：裸 {sum(r[3] for r in rows)}"
      f"、收域 {sum(r[4] for r in rows)}、记账 {sum(r[5] for r in rows)}"
      f"、收域+记账 {sum(r[6] for r in rows)}")
