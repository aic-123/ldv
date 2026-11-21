"""把「让折叠继续」的**候选杠杆**逐个试一遍，看哪个真能把收缩比压到阈值以下。

用法（**cwd = repo**）：
    python ../outputs/_probe_fold_levers.py
    LDV_CORPUS=openalex-small python ../outputs/_probe_fold_levers.py

⚠️ **必须从 `repo/` 跑**（工作区根下有一份 2026-10-05 的陈旧 `ldv/` 副本）。

每一个杠杆都要过**三条硬边界**（抽象层 §0.2 / §K9）：
    E-1 不许引入全局目标函数   E-2 不许排序 / 打分   §K9 不许把尺度偷偷内生化
「能不能折」只是必要条件；**能不能过边界**才是充分条件。
"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, ".")

from ldv.checks._fixtures import load
from ldv.run_checks import batch_kernel
from ldv.core.views import (ViewSpec, coarsest_stable_refinement, partition_of,
                            quotient_spec, quotient_universe, block_namer,
                            refines_partition)

loaded = load()
if loaded is None:
    print("语料找不到 ⇒ 跳过")
    raise SystemExit(0)
nodes, edges, _ = loaded
which = os.environ.get("LDV_DIR", "keyset")
print(f"语料 {len(nodes)} 项 / {len(edges)} 边 ｜ 方向 {which}\n")

kernel, _p, _q, _mk = batch_kernel(which, nodes, edges)
dirs = kernel.all_directions()
ids = sorted(d.did for d in dirs)
rel0 = frozenset((d.parent, d.did) for d in dirs if d.parent)
spec = ViewSpec(universe=tuple(ids), partition=(frozenset(ids),), relation=rel0)
q0 = partition_of(coarsest_stable_refinement(spec))
qs = quotient_spec(spec, q0, (list(quotient_universe(q0)),))

U = qs.universe
E = qs.relation
print(f"L0：{len(ids)} 方向 ⇒ {len(q0)} 视图（收缩比 {len(q0)/len(ids):.3f}）")
print(f"L1：|U₁| = {len(U)}  |E₁| = {len(E)}  阈值 0.5 ⇒ 要折下去得 |Q₁| ≤ {len(U)//2}\n")

adj = defaultdict(set)
for a, b in E:
    adj[a].add(b)
members = {name: b for b, name in block_namer(q0).items()}


def csr_of(rel, partition=None):
    s = ViewSpec(universe=U, partition=partition or (frozenset(U),),
                 relation=frozenset(rel))
    return partition_of(coarsest_stable_refinement(s))


def report(label, rel, partition=None, boundary="?"):
    q = csr_of(rel, partition)
    s = len(q) / len(U)
    mark = "折得动" if s <= 0.5 else "仍停"
    print(f"  {label:34s} |E| = {len(rel):3d} ⇒ |Q| = {len(q):2d}  {s:6.3f}  {mark}"
          f"   [{boundary}]")
    return q


print("① 诱导关系的几种选法（P = {U}，全部**由 E 诱导**，不引入新声明）")
report("原样：may（提升）", E, boundary="过边界")
# must：∀a∈A ∃b∈B
must = set()
for A in U:
    for B in U:
        if A == B:
            continue
        if all(any(b in adj[a] for b in members[B]) for a in members[A]):
            must.add((A, B))
report("must（∀a∈A ∃b∈B）", must, boundary="过边界")
# 传递闭包
closure = set()
for v in U:
    seen, stack = set(), [v]
    while stack:
        x = stack.pop()
        for y in adj.get(x, ()):
            if y not in seen:
                seen.add(y)
                stack.append(y)
    closure |= {(v, y) for y in seen}
report("可达闭包（transitive closure）", closure, boundary="过边界")
report("对称闭包（无向化）",
       E | {(b, a) for a, b in E}, boundary="过边界")
report("反向（E⁻¹）", {(b, a) for a, b in E}, boundary="过边界")

print("\n② 把「共同入邻 / 共同出邻」当关系（**这是换语义，不是换诱导**）")
same_out = {(A, B) for A in U for B in U
            if A != B and {b for a in members[A] for b in adj[a]}
            == {b for a in members[B] for b in adj[a]}}
report("共同出邻（同签名 ⇒ 相关）", same_out, boundary="⚠️ 换语义")
report("共同出邻 ∪ 共同入邻", same_out | {(b, a) for a, b in same_out},
       boundary="⚠️ 换语义")

print("\n③ 换操作符：MGP 的**极大匹配**（合并 ⇒ 收缩有结构保证）")
# 确定性极大匹配：按名字序，配一个还没配上的邻居
matched: dict[str, str] = {}
for v in U:
    if v in matched:
        continue
    for w in sorted(adj.get(v, ())):
        if w not in matched:
            matched[v] = w
            matched[w] = v
            break
merged = len({frozenset({v, w}) for v, w in matched.items()})
print(f"  极大匹配：{len(matched)}/{len(U)} 个视图被配上 ⇒ {merged} 对合并"
      f" ⇒ |Q| = {len(U) - merged}  收缩比 {(len(U)-merged)/len(U):.3f}"
      f"  {'折得动' if (len(U)-merged)/len(U) <= 0.5 else '仍停'}"
      f"   [⚠️ 匹配**不唯一** ⇒ 破坏「视图唯一」]")

print("\n④ 换 P：P = 离散（= 文档 §3.2 的字面「Q == P」）")
report("P = 离散", E, partition=tuple(frozenset({v}) for v in U),
       boundary="过边界（但那是终止层）")

print("\n⑤ 松阈值：把 MIN_SHRINK_RATIO 从 0.5 放宽")
for t in (0.95, 0.99, 1.0):
    q = csr_of(E)
    s = len(q) / len(U)
    print(f"  阈值 {t:4.2f} ⇒ 当前收缩比 {s:.3f}  "
          f"{'继续' if s <= t else '仍停'}")

print("\n⑥ 换 P：声明**更粗**的 P（k 组，按名字序均分）"
      "—— 理论上 csr 只细化，Q ⊇ P ⇒ 不该变")
for k in (2, 3, 4, 8):
    order = list(U)
    chunks = [order[i::k] for i in range(k)]          # 轮转均分 ⇒ 大小最均匀
    P = tuple(frozenset(c) for c in chunks if c)
    q = csr_of(E, partition=P)
    s = len(q) / len(U)
    # ⚠️ 「Q 细化 P」的判据是**每个 P 块被若干 Q 块恰好盖住**，
    #    不是 `set(q) >= set(P)`（后者只在 P 本身就是 Q 的一个子集时才真）。
    print(f"  P = {k} 组（实际 {len(P)} 组）⇒ |Q| = {len(q):2d}  {s:6.3f}"
          f"  {'折得动' if s <= 0.5 else '仍停'}"
          f"   [Q 细化 P：{refines_partition(P, q)}]")
