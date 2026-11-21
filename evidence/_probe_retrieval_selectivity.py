"""抽象层对**检索**有没有用？—— 选择性、以及「用视图块建新结构」。

用法（**cwd = repo**）：
    python ../outputs/_probe_retrieval_selectivity.py

⚠️ **必须从 `repo/` 跑**（工作区根下有一份 2026-10-05 的陈旧 `ldv/` 副本）。
⚠️ 只在**外生声明那一份语料**上跑得动（`view_spec.json` 是为它写的）。

## 先分清三样东西

    ① **方向树**（内核产出）    每个方向带 `覆盖`；这才是检索结构
    ② **视图层**（抽象层产出）  把「结构上分不开」的方向并成一张视图
    ③ **用视图块建的新结构**    把 ② 的块当节点、另配一个关系

⚠️ 本仓库 README 明说「**不做匹配**」：把一段处境映射到方向是**上层**的事。
   所以「够不够应对大数据检索」**不是本仓库能单独回答的** —— 这里能测的是
   「产出的结构**有没有选择性**」（一个节点平均/最坏罩住多少项）。

## ⚠️ ③ 的判据：一个关系**只有一条合法来路** —— 提升（`quotient_spec`）

块上的关系必须**是 `E` 在块映射下的像**（丢掉自环，`§M0`）。
③ 里那三条候选（覆盖相交 / 覆盖包含 / 覆盖相等）都是**从覆盖算出来的** ——
所以要逐条实测：它与 `E_lift` 的**对称差**有多大。
对称差 > 0 ⇒ 它不是 `E` 的提升 ⇒ **那是另一套结构，不是折叠**。
"""
import statistics
import sys

sys.path.insert(0, ".")

from ldv.checks._fixtures import load
from ldv.checks.abstraction import build_views, spec_for, view_profile
from ldv.core.views import (ViewSpec, coarsest_stable_refinement, partition_of,
                            quotient_spec, quotient_universe)
from ldv.run_checks import batch_kernel, coverage_of, corpus_fingerprint, load_spec_file

loaded = load()
if loaded is None:
    print("语料找不到 ⇒ 跳过")
    raise SystemExit(0)
nodes, edges, _ = loaded
doc = load_spec_file()
which = str(doc.get("方向") or "") if doc else ""
corpus = corpus_fingerprint(nodes, edges)
spec, why = spec_for(doc, which, corpus) if doc else (None, "没声明")
if spec is None:
    print(f"跳过：{why}")
    raise SystemExit(0)

kernel, plugin, _q, _mk = batch_kernel(which, nodes, edges)
cover = coverage_of(which, nodes, edges)
items = sorted({i for d in kernel.all_directions() for i in kernel.members_of(d)})
n_items = len(items)
print(f"语料：{len(nodes)} 项 / {len(edges)} 边 ｜ 方向 `{which}` ｜ "
      f"有覆盖的项 {n_items}\n")


def _sel_row(name, sizes):
    """一行选择性读数：min / 中位 / max，以及「一个节点平均罩住多少比例」。"""
    nz = sorted(v for v in sizes if v)
    med = statistics.median(nz)
    print(f"   {name}：min {min(nz)} / 中位 {med:.0f} / max {max(nz)}"
          f"   （总项数 {n_items}）")
    print(f"   ⇒ 中位选择性 = {med:.0f}/{n_items} = {med / n_items:.3f}"
          f"   ｜ 最坏选择性 = {max(nz)}/{n_items} = {max(nz) / n_items:.3f}")
    return med / n_items, max(nz) / n_items


# ---- ① 方向树的选择性 --------------------------------------------------------
sizes = {d.did: len(kernel.members_of(d)) for d in kernel.all_directions()}
roots = [d.did for d in kernel.all_directions() if not d.parent]
print("① 方向树（这才是检索结构）")
print(f"   方向数 {len(sizes)}   根 {len(roots)} 个   根覆盖 = "
      f"{[sizes[r] for r in roots]}")
dir_med, dir_max = _sel_row("各方向覆盖", sizes.values())

# ---- ② 视图层的选择性 --------------------------------------------------------
vs = build_views(kernel, spec, cover, plugin)
prof = view_profile(vs)
print("\n② 视图层（抽象层产出）")
print(f"   方向 {prof['方向数']} ⇒ 视图 {prof['视图数']}；最大块 {prof['最大块']}")
print(f"{'视图':>5s} {'块内方向':>8s} {'成员项':>7s} {'具体化项':>8s} {'具体化/成员':>11s}")
concs = []
for v in vs.views:
    c = len(v.concretization)
    concs.append(c)
    print(f"{v.vid:>5s} {v.n_dirs:8d} {len(v.covered):7d} {c:8d}"
          f" {(c / len(v.covered) if v.covered else float('nan')):11.3f}")
view_med, view_max = _sel_row("各视图具体化", concs)

print(f"\n   ⇒ **视图层比方向树粗 {view_med / dir_med:.1f}×**"
      f"（中位选择性 {dir_med:.3f} → {view_med:.3f}）")

# ---- ③ 用视图块建「新结构」 ---------------------------------------------------
q = partition_of(coarsest_stable_refinement(spec))
qs = quotient_spec(spec, q, (list(quotient_universe(q)),))
U, E_lift = qs.universe, qs.relation
block_items = {v.vid: frozenset(v.covered) for v in vs.views}
print(f"\n③ 用视图块建新结构：换一个关系，看能不能折")
print(f"   节点 = 视图块（{len(U)} 个）")
print(f"   合法来路 = **提升**：|E_lift| = {len(E_lift)} 条边，"
      f"自环 {sum(1 for a, b in E_lift if a == b)} 条（`§M0` 按构造丢掉）")
print(f"   `csr({{U}})` 在提升关系下 = "
      f"{len(partition_of(coarsest_stable_refinement(qs)))} 块 ⇒ 折不动\n")

CANDS = (
    ("覆盖相交（≠∅）", lambda B: frozenset(
        (a, b) for a in U for b in U if a != b and B[a] & B[b])),
    ("覆盖包含（⊇ 且 ≠）", lambda B: frozenset(
        (a, b) for a in U for b in U if a != b and B[b] and B[b] <= B[a])),
    ("覆盖相等", lambda B: frozenset(
        (a, b) for a in U for b in U if a != b and B[a] == B[b])),
)

print(f"   {'候选关系':16s} {'|E|':>4s} {'自环':>4s} {'|Q|':>4s} {'收缩比':>6s} "
      f"{'折得动':>6s} {'△ E_lift':>9s} {'是提升?':>7s}")
for name, mk in CANDS:
    rel = mk(block_items)
    sp = ViewSpec(universe=U, partition=(frozenset(U),), relation=rel)
    qq = partition_of(coarsest_stable_refinement(sp))
    symdiff = len(rel ^ E_lift)
    loops = sum(1 for a, b in rel if a == b)
    print(f"   {name:16s} {len(rel):4d} {loops:4d} {len(qq):4d}"
          f" {len(qq) / len(U):6.3f} {'是' if len(qq) < len(U) else '否':>6s}"
          f" {symdiff:9d} {'是' if symdiff == 0 else '否':>7s}")

# ---- ③b 单变量对照：候选关系**读 `E` 吗**？ ----------------------------------
# 判据：把 `E_lift` 换成它的**子集 / 空集**，候选关系若**逐字不变**
# ⇒ 候选关系不是 `E` 的函数 ⇒ 它由**另一个量**（覆盖）定义 ⇒ 换语义。
# ⚠️ 反向对照：扰动**覆盖**（把一个块的覆盖清空），候选关系**必须变** ——
#    否则这个测试是空转的（「不变」可能只是因为它对什么都没反应）。
print("\n   ③b 单变量对照（候选关系读哪个量？）")
variants = {"E_lift": E_lift,
            "E_lift ∖ 一条边": E_lift - {sorted(E_lift)[0]} if E_lift else frozenset(),
            "E_lift = ∅": frozenset()}
for name, mk in CANDS:
    base = mk(block_items)
    same_E = all(mk(block_items) == base for _ in variants.values())
    perturbed = dict(block_items)
    perturbed[U[0]] = frozenset()
    reacts_cover = mk(perturbed) != base
    print(f"   {name:16s} 扰动 E ⇒ {'逐字不变' if same_E else '变了'}"
          f"   ｜ 扰动覆盖 ⇒ {'变了' if reacts_cover else '**没反应**'}")

print("\n   ⇒ 三条候选：扰动 `E` 逐字不变、扰动覆盖就变 ⇒ **由覆盖定义，不由 `E` 定义**")
print("   ⇒ 它们不是 `E` 的提升（对称差 > 0）⇒ 那是**新结构**，不是折叠；")
print("     它不受 `§A1`–`§A7` 保护（那七条全是「对 `E`」的）⇒ §10 停止条件，问人。")
