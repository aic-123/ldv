"""量「膨胀」到底真不真：**方向数涨的时候，视图数涨不涨？**

用法（**cwd = repo**）：
    python ../outputs/_probe_view_growth.py

⚠️ **必须从 `repo/` 跑**（工作区根下有一份 2026-10-05 的陈旧 `ldv/` 副本）。

判据不是「视图数会不会涨」（那是读数），而是：
  **如果视图数在规模涨 10 倍时只涨 2 倍，那「多层折叠折不动」就不是问题**
  —— 抽象层在第一层就已经把膨胀收住了，第二层没东西可折。

三列：方向数 | 视图数（P={U} 的最粗稳定划分）| 收缩比。
"""
import sys

sys.path.insert(0, ".")

from ldv.checks._fixtures import load
from ldv.run_checks import batch_kernel
from ldv.core.views import (ViewSpec, coarsest_stable_refinement, partition_of,
                            quotient_spec, quotient_universe)

loaded = load()
if loaded is None:
    print("语料找不到 ⇒ 跳过")
    raise SystemExit(0)
nodes, edges, _ = loaded
print(f"语料：{len(nodes)} 项 / {len(edges)} 边\n")
print(f"{'方向':10s} {'方向数':>7s} {'视图数':>7s} {'收缩比':>8s}   L1 视图数 / 收缩比")

for which in ("keyset", "reach", "sequence"):
    kernel, _p, _q, _mk = batch_kernel(which, nodes, edges)
    dirs = kernel.all_directions()
    ids = sorted(d.did for d in dirs)
    rel = frozenset((d.parent, d.did) for d in dirs if d.parent)
    spec = ViewSpec(universe=tuple(ids), partition=(frozenset(ids),), relation=rel)
    q0 = partition_of(coarsest_stable_refinement(spec))
    s0 = len(q0) / len(ids)
    # L1：把 q0 当 P，商规格再用 P={U}
    qs = quotient_spec(spec, q0, (list(quotient_universe(q0)),))
    q1 = partition_of(coarsest_stable_refinement(qs))
    s1 = len(q1) / len(qs.universe)
    print(f"{which:10s} {len(ids):7d} {len(q0):7d} {s0:8.3f}   {len(q1):7d} / {s1:.3f}")

print("\n（`reach` 的方向数 = 2n−1 ⇒ 它是唯一可能真膨胀的方向。）")
