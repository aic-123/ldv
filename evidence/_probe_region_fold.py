"""把下一层的 `U` **按区域**划（而不是按出邻签名），能不能折？代价是什么？

用法（**cwd = repo**）：
    python ../outputs/_probe_region_fold.py
    LDV_CORPUS=openalex-small python ../outputs/_probe_region_fold.py

⚠️ **必须从 `repo/` 跑**（工作区根下有一份 2026-10-05 的陈旧 `ldv/` 副本）。

## 两个提法，其实是**两种不同的 `P`**

    ① 把整层结构当成**一个对象**   ⇒ `P = {U₁}`（1 块）
    ② **分区域**                    ⇒ `P = WCC / SCC / …`

而 `csr` 的契约是 `Q = csr(P) ⊇ P`，且（已证）对任何 `P` 都有
`csr(P)` 细化 `csr({U})`。⇒ **只要照常跑 `csr`，两条都压不过 `Q₀`。**

## 所以真正要问的是：**能不能跳过 `csr`，直接把区域当 `Q`？**

`§A3` 的 docstring 已经点名两种会红的形状：

    · `Q` **不是 `P` 的细化**（「把两个初始块并起来」）—— 设计稿 §3 边界 ①
    · `Q` **自己不稳定** ⇒ 也红（补出来的那条）

本探针就量这两条：区域划分**折到几块**、**稳定吗**、**并了多少对不同签名的块**。
"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, ".")

from ldv.checks._fixtures import load
from ldv.run_checks import batch_kernel
from ldv.core.views import (ViewSpec, coarsest_stable_refinement, partition_of,
                            quotient_spec, quotient_universe, stable, refines)


def wcc(nodes, rel):
    """弱连通分量 —— 把有向边当无向。**纯结构、无阈值、无目标函数。**"""
    parent = {n: n for n in nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in rel:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    groups: dict[str, list] = defaultdict(list)
    for n in nodes:
        groups[find(n)].append(n)
    return [frozenset(v) for v in groups.values()]


def scc(nodes, rel):
    """强连通分量（迭代 Tarjan）。同样是纯结构。"""
    adj = defaultdict(list)
    for a, b in rel:
        adj[a].append(b)
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on: dict[str, bool] = {}
    stack: list[str] = []
    out: list[frozenset[str]] = []
    counter = [0]
    for root in nodes:
        if root in index:
            continue
        work = [(root, 0)]
        while work:
            v, pi = work[-1]
            if pi == 0:
                index[v] = low[v] = counter[0]
                counter[0] += 1
                stack.append(v)
                on[v] = True
            recurse = False
            for i in range(pi, len(adj[v])):
                w = adj[v][i]
                if w not in index:
                    work[-1] = (v, i + 1)
                    work.append((w, 0))
                    recurse = True
                    break
                if on.get(w):
                    low[v] = min(low[v], index[w])
            if recurse:
                continue
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on[w] = False
                    comp.append(w)
                    if w == v:
                        break
                out.append(frozenset(comp))
            work.pop()
            if work:
                u = work[-1][0]
                low[u] = min(low[u], low[v])
    return out


loaded = load()
if loaded is None:
    print("语料找不到 ⇒ 跳过")
    raise SystemExit(0)
nodes, edges, _ = loaded
print(f"语料 {os.environ.get('LDV_CORPUS', '(默认)')}：{len(nodes)} 项 / {len(edges)} 边\n")

for which in ("keyset", "reach", "sequence"):
    kernel, _p, _q, _mk = batch_kernel(which, nodes, edges)
    dirs = kernel.all_directions()
    ids = sorted(d.did for d in dirs)
    rel0 = frozenset((d.parent, d.did) for d in dirs if d.parent)
    spec0 = ViewSpec(universe=tuple(ids), partition=(frozenset(ids),), relation=rel0)
    q0 = partition_of(coarsest_stable_refinement(spec0))
    qs = quotient_spec(spec0, q0, (list(quotient_universe(q0)),))
    U, E = qs.universe, qs.relation
    q1 = partition_of(coarsest_stable_refinement(qs))

    adj = defaultdict(set)
    for a, b in E:
        adj[a].add(b)
    sig = {v: frozenset(adj.get(v, ())) for v in U}

    print(f"══ {which} ══  |U₁| = {len(U)}  |E₁| = {len(E)}"
          f"  ⇒ `csr({{U₁}})` = {len(q1)} 块")

    for name, part in (("① 整层当对象", [frozenset(U)]),
                       ("② 弱连通分量", wcc(U, E)),
                       ("② 强连通分量", scc(U, E))):
        P = partition_of(part)
        sp = ViewSpec(universe=U, partition=P, relation=E)
        qc = partition_of(coarsest_stable_refinement(sp))
        ok_stable, bad = stable(sp, P)
        n_twin_viol = sum(1 for b in P if len({sig[x] for x in b}) > 1)
        print(f"   [{which}] {name:10s} P={len(P):2d} 块"
              f" | csr⇒{len(qc):2d} {'折' if len(qc) < len(U) else '停'}"
              f" | 直接当Q⇒{len(P):2d} {'折' if len(P) < len(U) else '停'}"
              f" §A2={ok_stable}({len(bad)}对) 混签名={n_twin_viol}/{len(P)}")
    print()


def all_partitions(items):
    """所有集合划分（受限增长串）—— Bell(n) 个。"""
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


EXHAUSTIVE_CAP = 9

print("═══ 穷举：把 `U₁` 的**全部**划分按「稳定 / 不稳」分成两组 ═══")
print(f"（`Bell(k)` 长得快 ⇒ 只在 `|U₁| ≤ {EXHAUSTIVE_CAP}` 上穷举，超了印「没跑完」）\n")
print(f"{'方向':10s} {'|U₁|':>5s} {'划分总数':>10s} {'稳定个数':>8s}"
      f" {'稳定组 |Q|':>12s} {'不稳组 |Q|':>12s}")
for which in ("keyset", "reach", "sequence"):
    kernel, _p, _q, _mk = batch_kernel(which, nodes, edges)
    dirs = kernel.all_directions()
    ids = sorted(d.did for d in dirs)
    rel0 = frozenset((d.parent, d.did) for d in dirs if d.parent)
    spec0 = ViewSpec(universe=tuple(ids), partition=(frozenset(ids),), relation=rel0)
    q0 = partition_of(coarsest_stable_refinement(spec0))
    qs = quotient_spec(spec0, q0, (list(quotient_universe(q0)),))
    U, E = qs.universe, qs.relation
    if len(U) > EXHAUSTIVE_CAP:
        print(f"{which:10s} {len(U):5d} {'—':>10s} {'没跑完':>8s}"
              f" {'（Bell 太大）':>12s} {'—':>12s}")
        continue
    spec = ViewSpec(universe=U, partition=(frozenset(U),), relation=E)
    ok_sizes, bad_sizes, n_ok = [], [], 0
    for P in all_partitions(list(U)):
        ok, _bad = stable(spec, P)
        if ok:
            n_ok += 1
            ok_sizes.append(len(P))
        else:
            bad_sizes.append(len(P))
    tot = len(ok_sizes) + len(bad_sizes)
    print(f"{which:10s} {len(U):5d} {tot:10d} {n_ok:8d}"
          f" {min(ok_sizes):6d} ~ {max(ok_sizes):<5d}"
          f" {min(bad_sizes):6d} ~ {max(bad_sizes):<5d}")
print()
print("⇒ **二分，没有第三种**：")
print("   · **稳定** 的划分 ⇒ `|Q| ≥ |csr({U₁})|` ⇒ **折不动**（那正是 csr 求的东西）")
print("   · **折得动** 的划分 ⇒ 必然**不稳** ⇒ `§A2` 直接红")
print("   而「区域」这条判据给出的划分，**不会**自动落进第一组的最粗那个点。")
