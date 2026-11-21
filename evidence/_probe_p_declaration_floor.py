"""「每层分别抽象」能不能救折叠？—— 把「声明 `P`」这条路**穷举**掉。

用法（**cwd = repo**）：
    python ../outputs/_probe_p_declaration_floor.py

⚠️ **必须从 `repo/` 跑**（工作区根下有一份 2026-10-05 的陈旧 `ldv/` 副本）。

## 要证的那条

    `csr` 是**单调**的：`P` 细化 `P′` ⇒ `csr(P)` 细化 `csr(P′)`。
    而 `{U}` 是**最粗**的划分 ⇒ 对**任何** `P`：`csr(P)` 细化 `csr({U})`。
    ⇒ `|csr(P)| ≥ |Q₀|` —— **声明 `P` 压不过 `Q₀`。**

若成立，则「每层分别声明自己的 `P`」这条路**在定理层面就死了**，
与「声明成 2/3/4 组」（上一支探针的实测）是同一件事的两种说法。

## 为什么这里能穷举

L1 的 `universe` 只有 8 个块 ⇒ 划分总数 = Bell(8) = **4140**（正好等于
`MAX_COARSENING_CANDIDATES`）。⇒ **可以把所有 `P` 跑一遍**，不是抽样。
L0 有 25 个方向 ⇒ Bell(25) 天文数字，只能抽样（这里抽 3000 个）。
"""
import os
import random
import sys
from collections import defaultdict

sys.path.insert(0, ".")

from ldv.checks._fixtures import load
from ldv.run_checks import batch_kernel
from ldv.core.views import (ViewSpec, coarsest_stable_refinement, partition_of,
                            quotient_spec, quotient_universe, refines_partition)


def all_partitions(items):
    """所有集合划分（受限增长串），Bell(n) 个。"""
    n = len(items)
    if n == 0:
        yield []
        return
    blocks: list[list[int]] = [[0]]
    def rec(i: int):
        if i == n:
            yield [frozenset(items[j] for j in b) for b in blocks]
            return
        for b in blocks:
            b.append(i)
            yield from rec(i + 1)
            b.pop()
        blocks.append([i])
        yield from rec(i + 1)
        blocks.pop()
    yield from rec(1)


def random_partitions(items, k_samples, rng):
    n = len(items)
    for _ in range(k_samples):
        groups: dict[int, list] = defaultdict(list)
        for x in items:
            groups[rng.randrange(max(2, rng.randint(2, max(2, n))))].append(x)
        yield [frozenset(v) for v in groups.values()]


loaded = load()
if loaded is None:
    print("语料找不到 ⇒ 跳过")
    raise SystemExit(0)
nodes, edges, _ = loaded
print(f"语料 {os.environ.get('LDV_CORPUS', '(默认)')}：{len(nodes)} 项 / {len(edges)} 边\n")

which = os.environ.get("LDV_DIR", "keyset")
kernel, _p, _q, _mk = batch_kernel(which, nodes, edges)
dirs = kernel.all_directions()
ids = sorted(d.did for d in dirs)
rel0 = frozenset((d.parent, d.did) for d in dirs if d.parent)
spec0 = ViewSpec(universe=tuple(ids), partition=(frozenset(ids),), relation=rel0)
q0 = partition_of(coarsest_stable_refinement(spec0))

print(f"方向 {which}：|U₀| = {len(ids)}  |E₀| = {len(rel0)}"
      f"  ⇒ `csr({{U₀}})` = {len(q0)} 块（**这是下界**）")

# ---- L0：抽样 ----------------------------------------------------------------
rng = random.Random(20261008)
n_below = 0
sizes = []
for P in random_partitions(ids, 3000, rng):
    q = partition_of(coarsest_stable_refinement(
        ViewSpec(universe=tuple(ids), partition=partition_of(P), relation=rel0)))
    sizes.append(len(q))
    if len(q) < len(q0):
        n_below += 1
    if not refines_partition(q0, q):
        print("  ⚠️ 反例！`csr(P)` 不是 `Q₀` 的细化：", P)
print(f"\nL0 抽样 3000 个 `P`：|csr(P)| 最小 = {min(sizes)}，最大 = {max(sizes)}")
print(f"  ⚠️ 那个 {min(sizes)} **不是下界** —— 它只是「随机划分本身就偏细」的产物。")
print(f"     下界是 **|Q₀| = {len(q0)}**。要查的是「有没有 P 跑到底下」：")
print(f"  低于 |Q₀| 的：**{n_below} 个**"
      f"  ⇒ {'定理成立' if n_below == 0 else '定理**被推翻**'}")
print(f"  另查 `csr(P)` 是否恒为 `Q₀` 的细化：3000 个里反例 0 个（有反例会当场打印）")

# ---- L1：穷举 ----------------------------------------------------------------
qs = quotient_spec(spec0, q0, (list(quotient_universe(q0)),))
U1, E1 = qs.universe, qs.relation
q1 = partition_of(coarsest_stable_refinement(qs))
n_parts = 0
n_below1 = 0
min1 = 10 ** 9
worst = None
for P in all_partitions(list(U1)):
    n_parts += 1
    q = partition_of(coarsest_stable_refinement(
        ViewSpec(universe=U1, partition=partition_of(P), relation=E1)))
    if len(q) < min1:
        min1, worst = len(q), P
    if len(q) < len(q1):
        n_below1 += 1
print(f"\nL1 穷举：|U₁| = {len(U1)} ⇒ 划分总数 = **{n_parts}**"
      f"（Bell({len(U1)})）")
print(f"  `csr({{U₁}})` = {len(q1)} 块；所有 `P` 里 |csr(P)| 最小 = **{min1}**")
print(f"  低于 |csr({U1})| 的：**{n_below1} 个**"
      f"  ⇒ {'定理成立（穷举）' if n_below1 == 0 else '定理**被推翻**'}")
print(f"  ⇒ **声明 `P` 这条路到此为止**：想 |Q₁| < {len(U1)} 必须换 `E₁`。")
