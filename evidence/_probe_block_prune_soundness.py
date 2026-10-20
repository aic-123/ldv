"""探针 3：**块级剪枝在真语料上成立吗？** 以及它能省多少？

定理候选（若成立，`§3.1` 的「信任链」论证就是**归错因**了）：

    §I2 要求 `覆盖(merge(S)) ⊇ ⋃_{d∈S} 覆盖(d)`
    `hit(·,q)=否` ⟺ `覆盖(·)` 里没有能匹配 q 的项
    ⇒ 合并说「否」⇒ 覆盖(合并) 里没有 ⇒ 每个成员的覆盖里也没有 ⇒ **每个成员都说「否」**

⇒ 那么这个推论只用 **§I2**（插件契约，`B2` 守），**不用 `§A1`**。
   本探针在**真语料 × 真插件 × 全部查询**上逐块验它，并算出能省多少次 `hit`。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "C:/Users/19253/WorkBuddy AI/2026-10-05-14-16-35/repo")

from ldv.checks._fixtures import load, keyset_queries  # noqa: E402
from ldv.checks.abstraction import spec_for           # noqa: E402
from ldv.core.views import coarsest_stable_refinement  # noqa: E402
from ldv.run_checks import batch_kernel, load_spec_file, corpus_fingerprint  # noqa: E402
from ldv.core.tri import Tri                          # noqa: E402
from ldv import retriever as R                        # noqa: E402

loaded = load(); nodes, edges, _ = loaded
doc = load_spec_file(); which = str(doc.get("方向") or "")
spec, _ = spec_for(doc, which, corpus_fingerprint(nodes, edges))
kernel, plugin, queries, _ = batch_kernel(which, nodes, edges)
q_all = list(queries)

# 所有层级的划分：roots 层的 q + 每层的 q（这里用最粗的那层：spec 的 csr）
q = coarsest_stable_refinement(spec)

print(f"方向 {which}｜|Q| = {len(q)} 块｜查询 {len(q_all)} 条")
print()

viol = 0
saved_total = 0
checked_total = 0
block_no_total = 0
pruned_members = 0

for qi, query in enumerate(q_all):
    for block in q:
        ids = sorted(block, key=R._did_order)
        dirs = [kernel.direction(d) for d in ids]
        payload = plugin.merge(dirs)
        carrier = R._carrier(kernel, block, plugin)
        blk = plugin.hit(carrier, query)
        per = [plugin.hit(d, query) for d in dirs]
        checked_total += 1
        if blk is Tri.NO:
            block_no_total += 1
            if any(v is not Tri.NO for v in per):
                viol += 1
                if viol <= 3:
                    print(f"  ✗ 反例：query#{qi} 块 {ids[:3]}…")
                    print(f"      合并=否，而成员 = {[str(v) for v in per]}")
            else:
                pruned_members += len(dirs)
                saved_total += len(dirs) - 1        # 一次合并+一次 hit 代替 |dirs| 次 hit
        _ = payload

print(f"块级判定总数      {checked_total}")
print(f"其中「合并说否」  {block_no_total}")
print(f"★ 反例（合并=否 而某成员 ≠ 否）  {viol}")
print(f"   ⇒ 推论{'成立' if viol == 0 else '不成立'}")
print()
print(f"若按块剪枝：被整块砍掉的成员方向数 {pruned_members}")
print(f"           省下的 `hit` 调用数     {saved_total}"
      f"（占全部 hit {len(q_all) * len(spec.universe)} 次的比例 "
      f"{saved_total / max(1, len(q_all) * len(spec.universe)):.1%}）")
