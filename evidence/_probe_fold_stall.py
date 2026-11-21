"""诊断：为什么 L1 的商**不缩**（收缩比 1.000）。

用法（**cwd = repo**）：
    python ../outputs/_probe_fold_stall.py

⚠️ **必须从 `repo/` 跑**（工作区根下有一份 2026-10-05 的陈旧 `ldv/` 副本）。

要看的四件事：
  ① L0 的 `Q`（8 块）与**提升后的关系** `E₁`（8 个视图之间到底有几条边）
  ② `csr(P={U}, E₁)` 给出几个块 —— 它为什么不是 1 块
  ③ `E₁` 里**每个视图的出度 / 入度**：谁把谁区分开了
  ④ 换一个 `E₁`（把自环允许 / 用可达闭包 / 用传递约简）会不会缩
"""
import sys
from collections import Counter

sys.path.insert(0, ".")

from ldv.checks._fixtures import load, coverage_of
from ldv.checks.abstraction import load_spec_file, spec_for
from ldv.checks.coverage import corpus_fingerprint
from ldv.run_checks import batch_kernel
from ldv.core.views import (ViewSpec, coarsest_stable_refinement, partition_of,
                            quotient_spec, quotient_universe, block_namer,
                            n_coarsening_candidates)

loaded = load()
if loaded is None:
    print("语料找不到 ⇒ 跳过")
    raise SystemExit(0)
nodes, edges, _ = loaded

doc = load_spec_file()
which = str(doc.get("方向") or "keyset")
spec, why = spec_for(doc, which, corpus_fingerprint(nodes, edges))
print(f"方向 {which}｜{why}")
if spec is None:
    print("⇒ 本趟判不了（跳过）")
    raise SystemExit(0)

kernel, plugin, _q, _mk = batch_kernel(which, nodes, edges)

q0 = partition_of(coarsest_stable_refinement(spec))
print(f"\n① L0：|U| = {len(spec.universe)}  |Q| = {len(q0)}")
print(f"   声明的关系 |E| = {len(spec.relation)} 条")
namer = block_namer(q0)
for b, name in sorted(namer.items(), key=lambda kv: int(kv[1][1:])):
    print(f"   {name} = {sorted(b)}")

# 提升后的关系
qspec = quotient_spec(spec, q0, (list(quotient_universe(q0)),))
print(f"\n② 商规格：|U₁| = {len(qspec.universe)}  |P₁| = {len(qspec.partition)} 块"
      f"  |E₁| = {len(qspec.relation)} 条")
print(f"   E₁ = {sorted(qspec.relation)}")

q1 = partition_of(coarsest_stable_refinement(qspec))
print(f"   csr(P₁={{U}}, E₁) ⇒ |Q₁| = {len(q1)} 块 ⇒ 收缩比 {len(q1)/len(qspec.universe):.3f}")
for b in sorted(q1, key=lambda s: sorted(s)):
    print(f"     {sorted(b)}")

# 出度 / 入度
out = Counter(a for a, _ in qspec.relation)
inn = Counter(b for _, b in qspec.relation)
print("\n③ E₁ 上每个视图的出度 / 入度（**出度为 0 的会让 {U} 不稳定**）")
for v in qspec.universe:
    print(f"   {v}  出 {out.get(v, 0)}  入 {inn.get(v, 0)}")

# 单点检查：{U} 稳不稳
U = frozenset(qspec.universe)
pre = frozenset(a for (a, _b) in qspec.relation)   # E₁⁻¹(U)
print(f"\n   E₁⁻¹(U) = {sorted(pre)}  （{len(pre)}/{len(U)} 个）")
print(f"   U ⊆ E₁⁻¹(U) ? {U <= pre}   ← 若否，{{U}} 不稳 ⇒ 必须细分")
print(f"   U ∩ E₁⁻¹(U) = φ ? {not (U & pre)}")

# ④ 换几种 E₁ 会不会缩
def try_rel(label, rel):
    s = ViewSpec(universe=qspec.universe, partition=qspec.partition,
                 relation=frozenset(rel))
    qq = partition_of(coarsest_stable_refinement(s))
    print(f"   {label:26s} |E| = {len(rel):3d} ⇒ |Q| = {len(qq):2d}"
          f"  收缩比 {len(qq)/len(qspec.universe):.3f}")

print("\n④ 换 E₁ 的几种选法")
try_rel("原样（提升）", qspec.relation)
try_rel("去掉自环（本来就没有）",
        {(a, b) for a, b in qspec.relation if a != b})
# 可达闭包
adj: dict[str, set[str]] = {}
for a, b in qspec.relation:
    adj.setdefault(a, set()).add(b)
closure = set()
for v in qspec.universe:
    seen, stack = set(), [v]
    while stack:
        x = stack.pop()
        for y in adj.get(x, ()):
            if y not in seen:
                seen.add(y)
                stack.append(y)
    closure |= {(v, y) for y in seen}
try_rel("可达闭包", closure)
# 只保留「有向边」的反向（E 的方向反过来）
try_rel("E 反向", {(b, a) for a, b in qspec.relation})

print(f"\n   §A3 的候选数（当前 Q₁）= {n_coarsening_candidates(qspec, q1)}")
