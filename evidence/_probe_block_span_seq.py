"""探针 4′：视图块**横跨树层**吗？一次查询的前沿有多大？（**维护路径版**，2026-10-13）

⚠️ 与 `_probe_block_span.py` 的差别只有一处，但是**必须**的一处：
   老探针用 `batch_kernel` 建树，而声明 `view_spec.json` 的 `universe` 是**维护路径**
   编的号（v0.1.21 起方向 `sequence`：批建 16 ≠ 维护 8）⇒ 按 `did` 查 rank 会**串号**。
   ⇒ 这里用 `maintenance_kernel`，与 `run_checks.view_report` / `retrieval_report` 同源。

结论仍是一句话：块若**横跨树层**，它在**任何单层前沿**上都只占一部分 ⇒ 块级剪枝无处安放。
"""
from __future__ import annotations

import io
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent / "repo"
sys.path.insert(0, str(REPO))

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from ldv.checks._fixtures import load  # noqa: E402
from ldv.checks.abstraction import spec_for  # noqa: E402
from ldv.core.views import coarsest_stable_refinement  # noqa: E402
from ldv.run_checks import maintenance_kernel, load_spec_file, corpus_fingerprint  # noqa: E402

loaded = load()
nodes, edges, _ = loaded
doc = load_spec_file()
which = str(doc.get("方向") or "")
spec, _ = spec_for(doc, which, corpus_fingerprint(nodes, edges))
kernel = maintenance_kernel(which, nodes, edges)
q = coarsest_stable_refinement(spec)

rank = {d.did: d.rank for d in kernel.all_directions()}

print(f"方向 {which}｜|U| = {len(spec.universe)}｜|Q| = {len(q)} 块")
print()
print("每块的：成员数 / 成员**占几个 rank** / rank 分布")
for b in q:
    rs = Counter(rank[d] for d in b)
    print(f"  {len(b):3d} 个成员｜{len(rs)} 个 rank｜{dict(sorted(rs.items()))}")
print()
print("★ 若「几个 rank」列 > 1 ⇒ 该块**横跨树层** ⇒ 任何**单层前沿**上都只看得到它的一部分")
print()

lvl = Counter(rank[d] for d in spec.universe)
print(f"{len(spec.universe)} 个方向按 rank 分布：{dict(sorted(lvl.items()))}")

# 逐层前沿：用真查询跑一遍，看 trace 里遍历到的方向按 rank 计数
from ldv.run_checks import batch_kernel as _bk  # noqa: E402

queries = _bk(which, nodes, edges)[2]
q0 = queries[0]
res = kernel.query(q0)
per_layer = Counter(rank[did] for did, _v in res.trace)
print()
print(f"一次查询（{q0.label}）：layers_visited = {res.layers_visited}")
print(f"  遍历过的方向按 rank 计数：{dict(sorted(per_layer.items()))}")
print(f"  即：每层的「前沿规模」上限 ≈ 各 rank 的计数 —— "
      f"最大 {max(per_layer.values()) if per_layer else 0}")
print()
print(f"⇒ 视图块平均 {len(spec.universe) / len(q):.1f} 个成员，而单层前沿最大 "
      f"{max(per_layer.values()) if per_layer else 0} 个 ⇒ "
      f"块在前沿上**装不下**")
