"""底层节点变多时，抽象层的**视图**会变大吗？—— 两条读法分开量。

用法（**cwd = repo**）：
    python ../outputs/_probe_view_size.py
    LDV_CORPUS=openalex-small python ../outputs/_probe_view_size.py

⚠️ **必须从 `repo/` 跑**（工作区根下有一份 2026-10-05 的陈旧 `ldv/` 副本）。

## 「视图过大」有两条读法，别混

    读法甲：**块内成员太多** —— 一张视图里塞了几百个方向 ⇒ 它就不算摘要了
    读法乙：**视图数量太多** —— 视图张数随底层节点数一起涨 ⇒ 摘要没起到压缩作用

两条各有自己的量：甲 = 最大块 / 中位块；乙 = `|Q|`。
**而 `§M4` 那道闸卡的是乙**（`P = {U}` 时 `n_cand = Bell(|Q|)`，
`MAX_COARSENING_CANDIDATES = 200_000` ⇒ `Bell(|Q|) ≤ 2e5` ⇒ **`|Q| ≤ 10`**）。
"""
import os
import statistics
import sys

sys.path.insert(0, ".")

from ldv.checks._fixtures import load
from ldv.run_checks import batch_kernel
from ldv.core.views import (MAX_COARSENING_CANDIDATES, ViewSpec,
                            coarsest_stable_refinement, n_coarsening_candidates,
                            partition_of)

loaded = load()
if loaded is None:
    print("语料找不到 ⇒ 跳过")
    raise SystemExit(0)
nodes, edges, _ = loaded
print(f"语料 {os.environ.get('LDV_CORPUS', '(默认)')}：{len(nodes)} 项 / {len(edges)} 边")
print(f"`MAX_COARSENING_CANDIDATES` = {MAX_COARSENING_CANDIDATES:,}"
      f"  ⇒ `Bell(|Q|) ≤ 2e5` ⇒ **`|Q| ≤ 10`**（Bell(10)=115975，Bell(11)=678570）\n")

print(f"{'方向':9s} {'|U|':>6s} {'|Q|':>5s} {'|Q|/|U|':>8s}"
      f" {'块内 max':>8s} {'中位':>5s} {'max/|U|':>8s} {'n_cand':>12s} {'§M4':>6s}")
rows = []
for which in ("keyset", "reach", "sequence"):
    kernel, _p, _q, _mk = batch_kernel(which, nodes, edges)
    dirs = kernel.all_directions()
    ids = sorted(d.did for d in dirs)
    rel0 = frozenset((d.parent, d.did) for d in dirs if d.parent)
    spec = ViewSpec(universe=tuple(ids), partition=(frozenset(ids),), relation=rel0)
    q = partition_of(coarsest_stable_refinement(spec))
    sizes = sorted(len(b) for b in q)
    n_cand = n_coarsening_candidates(spec, q)
    m4 = "绿" if n_cand <= MAX_COARSENING_CANDIDATES else "**红**"
    # ★ 机制猜想：最大块 == **出度为 0 的方向**（签名都是 ∅ ⇒ 必然并成一块）
    has_out = {a for a, _b in rel0}
    sinks = frozenset(d for d in ids if d not in has_out)
    biggest = max(q, key=len)
    rows.append((which, len(ids), len(q), sizes, n_cand, sinks, biggest))
    print(f"{which:9s} {len(ids):6d} {len(q):5d} {len(q)/len(ids):8.3f}"
          f" {max(sizes):8d} {statistics.median(sizes):5.0f}"
          f" {max(sizes)/len(ids):8.3f} {n_cand:12,d} {m4:>6s}")

print("\n① 块内成员（读法甲）—— 最大块占底层的比例")
for which, n_u, n_q, sizes, _nc, _s, _b in rows:
    top = sizes[-3:][::-1]
    print(f"   {which:9s} max = {max(sizes):4d} / {n_u:4d}"
          f" = {max(sizes)/n_u:.3f}   前三块 {top}")

print("\n② 视图张数（读法乙）—— 以及 `§M4` 那道闸")
for which, n_u, n_q, _s, n_cand, _sk, _b in rows:
    print(f"   {which:9s} |Q| = {n_q:3d}  n_cand = {n_cand:>14,d}"
          f"  {'过闸' if n_cand <= MAX_COARSENING_CANDIDATES else '**撞闸（§M4 红）**'}")

print("\n③ 机制：最大块是不是**出度为 0 的方向**（签名都是 ∅ ⇒ 必然并成一块）？")
for which, n_u, _nq, sizes, _nc, sinks, biggest in rows:
    print(f"   {which:9s} 出度 0 的方向 = {len(sinks):4d} / {n_u:4d}"
          f"（{len(sinks)/n_u:.3f}）"
          f"   最大块 = {len(biggest):4d}"
          f"   **相等？ {frozenset(biggest) == sinks}**")

print("\n⇒ 两条读法的答案**不一样**，见上面的表。")
