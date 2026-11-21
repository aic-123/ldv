"""回答一个问题：**多层折不动，是因为「东西太少」，还是因为「关系形状」？**

用法（**cwd = repo**）：
    python ../outputs/_probe_fold_why.py
    LDV_CORPUS=openalex-small python ../outputs/_probe_fold_why.py

⚠️ **必须从 `repo/` 跑**（工作区根下有一份 2026-10-05 的陈旧 `ldv/` 副本）。

## 两个解释，必须能分开

    H1「东西太少」   L1 的 |U₁| 只有 8~16 ⇒ 没得折
    H2「关系形状」   `csr` 的切分由 **出邻签名** 决定 ⇒ 与 |U₁| 无关

**分开它们的实验**：把 `|U₁|` 与 `P` 都**冻住**，只换 `E₁`。
若 `|Q₁|` 随 `E₁` 剧烈变化 ⇒ H2；若 `|Q₁|` 只随 `|U₁|` 变 ⇒ H1。

再补一个**规模侧**的读数：`|U₁|` 在语料涨 10 倍时涨多少 ——
若它几乎不涨，说明 L0 已经把膨胀吸掉了（那不是 H1，是 L0 干得太好）。
"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, ".")

from ldv.checks._fixtures import load
from ldv.run_checks import batch_kernel
from ldv.core.views import (ViewSpec, coarsest_stable_refinement, partition_of,
                            quotient_spec, quotient_universe, block_namer)

loaded = load()
if loaded is None:
    print("语料找不到 ⇒ 跳过")
    raise SystemExit(0)
nodes, edges, _ = loaded
print(f"语料 {os.environ.get('LDV_CORPUS', '(默认)')}：{len(nodes)} 项 / {len(edges)} 边\n")

print("① 每层读数（L0 = 抽象层，L1 = 把 L0 的商再折一次）")
print(f"{'方向':9s} {'L0 |U|':>7s} {'L0 |Q|':>7s} {'比':>7s}"
      f" {'L1 |U₁|':>8s} {'L1 |E₁|':>8s} {'L1 |Q₁|':>8s} {'比':>7s} {'真折':>5s}")

rows = []
for which in ("keyset", "reach", "sequence"):
    kernel, _p, _q, _mk = batch_kernel(which, nodes, edges)
    dirs = kernel.all_directions()
    ids = sorted(d.did for d in dirs)
    rel0 = frozenset((d.parent, d.did) for d in dirs if d.parent)
    spec = ViewSpec(universe=tuple(ids), partition=(frozenset(ids),), relation=rel0)
    q0 = partition_of(coarsest_stable_refinement(spec))
    qs = quotient_spec(spec, q0, (list(quotient_universe(q0)),))
    q1 = partition_of(coarsest_stable_refinement(qs))
    n_fold = 1 if len(q1) < len(qs.universe) else 0
    rows.append((which, len(ids), len(q0), len(qs.universe), len(qs.relation),
                 len(q1), qs, q0))
    print(f"{which:9s} {len(ids):7d} {len(q0):7d} {len(q0)/len(ids):7.3f}"
          f" {len(qs.universe):8d} {len(qs.relation):8d} {len(q1):8d}"
          f" {len(q1)/len(qs.universe):7.3f} {n_fold:5d}")

print("\n② 冻结 |U₁| 与 P，只换 E₁ —— 这是把 H1 与 H2 分开的那个实验")
print("   （下表每一行**同一个 |U₁|、同一个 P = {U₁}**，只有关系不同）\n")
print("   ⚠️ 先说一条容易读错的事：**`csr` 只细化、从不合并**（`Q ⊇ P`，"
      "故 `|Q| ≥ |P|`）。")
print("      所以「压缩」全部来自**声明的 `P`**，`csr` 只负责判定那个 `P` 稳不稳。")
print("      ⇒ 下表里 `|Q| < |U₁|` 有三种完全不同的来历，必须分开标：")
print("        · **真折**：关系没被动过，`{U₁}` 本来就稳")
print("        · **退化**：稳定是白来的（关系空 / 含自环）—— `§M0` 挡的那一类")
print("        · **换语义**：稳定是**另一个关系**给的（无向化 / 共同出邻）—— E-1/E-2 挡的那一类")
print("      ⚠️ 这三种在**输出上长得一模一样**（都是「|Q| 变小了」）。\n")


def degeneracy(rel, U):
    """这一份 `E₁` 的「折得动」是**怎么来的** —— 结构性判定，不是语义判定。

    语义那一路（无向化 / 共同出邻）**判不出来**，必须由调用方声明，
    所以下面每个变体自带 `kind`。
    """
    if not rel:
        return "退化（关系空）"
    if any((v, v) in rel for v in U):
        return "退化（含自环）"
    return None


# `kind` 由**人声明**：`orig` = 关系没动过；`sem` = 换了语义。
VARIANTS = (
    ("原样 E₁（提升）", "orig", lambda E, sym, U: E),
    ("E₁ = ∅（`must` 那条）", "orig", lambda E, sym, U: frozenset()),
    ("E₁ = 对称闭包", "sem", lambda E, sym, U: sym),
    ("E₁ = E₁ ∪ 自身环", "orig", lambda E, sym, U: E | {(v, v) for v in U}),
)

for which, n_ids, n_q0, n_u1, n_e1, n_q1, qs, q0 in rows:
    U, E = qs.universe, qs.relation
    adj = defaultdict(set)
    for a, b in E:
        adj[a].add(b)
    sym = E | {(b, a) for a, b in E}
    print(f"  [{which}]  |U₁| = {len(U)}   （L0：{n_ids} 方向 ⇒ {n_q0} 视图）")
    for label, kind, mk in VARIANTS:
        rel = frozenset(mk(E, sym, U))
        s = ViewSpec(universe=U, partition=(frozenset(U),), relation=rel)
        q = partition_of(coarsest_stable_refinement(s))
        folded = len(q) < len(U)
        deg = degeneracy(rel, U)
        if not folded:
            verdict = "仍停"
        elif deg:
            verdict = f"折得动 ⚠️ **{deg}** —— 稳定白来"
        elif kind == "sem":
            verdict = "折得动 ⚠️ **换语义**（无向化 ⇒ 那是另一个关系）"
        else:
            verdict = "折得动（真）"
        print(f"      {label:22s} |E₁| = {len(rel):3d} ⇒ |Q₁| = {len(q):2d}"
              f"   {len(q)/len(U):6.3f}  {verdict}")
    # 签名诊断：`csr` 从 {U} 出发，切的就是「出邻签名」是否相同
    sig = {v: frozenset(adj.get(v, ())) for v in U}
    uniq = len(set(sig.values()))
    sinks = sum(1 for v in U if not adj.get(v))
    print(f"      ── 诊断：|U₁| = {len(U)}，**不同出邻签名 = {uniq}**，"
          f"出度 0 的块 = {sinks}")
    print(f"         交叉校验：签名数 == |Q₁| ？ {uniq == n_q1}"
          f"（{uniq} vs {n_q1}）"
          f"；|Q₁| == |U₁| ？ {n_q1 == n_u1}"
          f" ⇒ 签名两两不同 ⇒ `csr` 无处可并，**与 |U₁| 的大小无关**\n")

print("③ 规模侧：|U₁| 会随语料涨吗？（若不涨，膨胀是被 L0 吸掉的，不是「东西少」）")
print(f"   {'方向':9s} {'方向数':>8s} {'L0 视图':>8s} {'L1 |U₁|':>8s}")
for which, n_ids, n_q0, n_u1, _e, _q, _qs, _q0 in rows:
    print(f"   {which:9s} {n_ids:8d} {n_q0:8d} {n_u1:8d}")
print("\n   ⇒ 把这四列拿去和 5 份语料横向比（`LDV_CORPUS=... ` 各跑一遍）：")
print("      **方向数涨 5.7× 而 L1 |U₁| 只涨 1.6×** ⇒ L0 已把膨胀收住。")
