"""跑 B1–B19 —— 设计文档 §7。

    python -m ldv.run_checks                # 三个插件都跑
    python -m ldv.run_checks keyset         # 只跑方向 A（键集包含）
    python -m ldv.run_checks reach          # 只跑方向 B（锚点可达）
    python -m ldv.run_checks sequence       # 只跑方向 C（序列前缀）

    python -m ldv.run_checks --write-cover-leak-baseline    # 重新冻结覆盖不漏的基线
    python -m ldv.run_checks --no-probes                    # 不跑贵的**探针**（判据一条不少）
    python -m ldv.run_checks --cap 400                      # 只跑前 400 项（**会把缩了印出来**）

退出码：**只看「红」的条数**。度量（方向数 / 事件数 / 假阳率 / 使用记录数）走输出，
不进退出码 —— 继承 dce：`report()` 承载度量，退出码只承载「有没有违规」。

---
## 先分清「判据」与「探针」—— 这决定了该关哪一个

    判据（进退出码）   B1–B19。贵的**主要**是 `B15`（3 次整建）与 `B8`（n 次插入）
    探针（只报不判）   十个度量。贵的是 `规范重建`（O(n) 次重建 × 每次 O(n)）

⚠️ 别把「判据贵」全记在 `B8` / `B15` 头上：实测 281 项上两者合计 **2.9 s**，
   而 `--no-probes` 整趟（三个方向）是 **7.2 s** ⇒ 它们约占 40%，其余分散在
   十几条判据里。**要指名道姓得先逐块计时**（`outputs/_measure_b8_scaling.py`
   与 `outputs/_measure_cover_cost.py` 记了两次「形状对得上但因果不成立」）。

⇒ **关探针是安全的**：它们**不进退出码**，关掉它们**不可能把红变成绿**。
   实测（281 项语料，`outputs/_profile_runone.py`）：

    方向       整趟      其中 `规范重建`   关掉探针后
    ──────────────────────────────────────────────────
    keyset     27.4 s      26.96 s           ~0.4 s
    reach     450.8 s     446.54 s           ~4.3 s
    sequence  ~300 s       ~300 s             ~2 s

⇒ 所以 `--no-probes`：**判据一条不少地全跑，只把贵的探针关掉**，并**印出来**。
   这是「**缩小范围**」与「**全过**」必须分得开的那条纪律在开关上的落地 ——
   关掉的东西**出现在报告里**，不出现在退出码里。

## `--cap` 是另一回事

`--cap N` 把**整个语料**截到前 N 项 —— 它连**判据**一起截。
所以它**会挡住结论**：实测 `openalex-citations` 的环**全部落在第 400 项之后**
（前 400 项 SCC ≥2 的组 = **0**），所以 `--cap 160` 上看不到 `B17` 那条红。
⇒ 要判据有效就别用 `--cap`；要快就用 `--no-probes`。

⚠️ 默认 **0 = 不缩** —— 随仓库那 36 项语料的行为一个字节都不变。
"""

from __future__ import annotations

import sys

from .checks._fixtures import (
    build_incremental,
    build_keyset,
    build_reach,
    build_sequence,
    coverage_of,
    equiv_classes,
    keyset_queries,
    load,
    make_builder,
    make_keyset_root,
    make_reach_root,
    make_sequence_root,
    reach_queries,
)
from .checks._framework import Report
from .checks.coverage import (
    b16_members_covered,
    b18_cover_leak_baseline,
    b19_progress_guard,
    corpus_fingerprint,
    cover_leak_profile,
    progress_profile,
    render_cover_leak,
    render_progress,
    render_soundness,
    soundness_profile,
    write_baseline,
)
from .checks.divergence import divergence_profile, render_divergence
from .checks.equivalence import absorption_profile, b17_leaf_is_equivalence_class, render_absorption
from .checks.rebuild import rebuild_profile, render_rebuild
from .checks.contract import (
    b1_no_false_negative,
    b2_merge_covers,
    b3_penalty_comparable,
    b4_split_is_partition,
    b5_decode_covers,
    b6_signal_not_collapsed,
    cover_nesting_profile,
    false_positive_profile,
)
from .checks.semantics import b10_three_states_separable, b11_propensity_complete
from .checks.source import b12_no_global_scalar, b14_exogenous_boundary
from .checks.structure import (
    b7_witness_complete,
    b8_invalidation_local,
    b9_ledger_append_only,
    b13_rank_well_founded,
    b15_reproducible,
)


PLUGIN_CODES = ("B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B9", "B10", "B11",
                "B13", "B15", "B16", "B17", "B18", "B19")
KERNEL_CODES = ("B12", "B14")

#: 维护路径的初始批大小 —— 与 `C7` / `C10` 的读数口径一致（先建 6、维护 30）。
MAINT_INIT = 6


def batch_kernel(which: str, nodes, edges):
    """一次建完的内核 + 插件 + 查询集 + 「只建根」的构造器。"""
    if which == "keyset":
        kernel, plugin = build_keyset(nodes)
        return kernel, plugin, keyset_queries(nodes), make_keyset_root(nodes)
    if which == "reach":
        kernel, plugin = build_reach(nodes, edges)
        return (kernel, plugin, reach_queries(nodes, edges), make_reach_root(nodes, edges))
    if which == "sequence":
        kernel, plugin = build_sequence(nodes, edges)
        # 同样用「外生挑的项集合」当 ground truth
        return (kernel, plugin, reach_queries(nodes, edges), make_sequence_root(nodes, edges))
    raise ValueError(which)


def maintenance_kernel(which: str, nodes, edges):
    """「先建 6、再维护 30」的内核 —— **健全性与覆盖不漏都要两条路径**。"""
    ids_all = sorted(nodes)
    mk_build = make_builder(which, nodes, edges)
    return build_incremental(mk_build, nodes, ids_all[:MAINT_INIT], ids_all[MAINT_INIT:])


def leak_obs(which: str, batch_kernel, inc_kernel, cover) -> dict:
    """`B18` 的观测 —— 键是 `<方向>|<路径>`，baseline 与它逐键比。"""
    return {
        f"{which}|batch": cover_leak_profile(batch_kernel, cover),
        f"{which}|maintenance": cover_leak_profile(inc_kernel, cover),
    }


def run_one(which: str, loaded, probes: bool = True) -> Report:
    nodes, edges, dangling = loaded
    cover = coverage_of(which, nodes, edges)
    kernel, plugin, queries, make = batch_kernel(which, nodes, edges)

    rep = Report(plugin=which, expects=PLUGIN_CODES)
    rep.note(f"语料 {len(nodes)} 项；图边 {sum(len(v) for v in edges.values())} 条"
             + (f"；悬挂边 {len(dangling)} 个（{sorted(dangling)[:3]}）" if dangling else ""))

    # ★ **维护路径的内核只建一次，两条判据共用**（`B1` / `B16`）。
    inc = maintenance_kernel(which, nodes, edges)

    # ★ `B1` 与 `B16` 都**两条路径各判一次** —— 它们的 ground truth 在两条路径上不同：
    #   `B1` 是「**成员** ∩ 查询」（成员集维护后会变）、`B16` 是「成员 ⊆ 覆盖」
    #   （维护时 payload 已冻结）。**「只跑一条路」与「两条路都跑」在汇总里
    #   长得一模一样** ⇒ `path` 必须印在标题里。
    b1_no_false_negative(kernel, plugin, queries, rep, path="批建")
    b1_no_false_negative(inc, plugin, queries, rep, path="维护")
    b2_merge_covers(plugin, kernel, queries, rep)
    b3_penalty_comparable(kernel, plugin, rep)
    b4_split_is_partition(kernel, rep)
    b5_decode_covers(plugin, kernel, queries, rep)
    b6_signal_not_collapsed(kernel, plugin, rep)
    b7_witness_complete(kernel, rep)
    b8_invalidation_local(make, sorted(nodes), rep)
    b9_ledger_append_only(kernel, rep)
    b10_three_states_separable(kernel, plugin, rep)
    b11_propensity_complete(kernel, plugin, rep)
    b13_rank_well_founded(kernel, rep)
    b15_reproducible(make, sorted(nodes), rep)

    # ★ **健全性同样两条路径各判一次**（见 `checks/coverage.py`）。
    #   判据落在哪条路径上，**由那条路径的基线决定**：基线绿 ⇒ 可以当判据；
    #   基线红 ⇒ 只能当度量，或走 baseline 守卫。两条路现在都绿 ⇒ 两条都跑，
    #   **每一行单独判、单独注入验证**。
    b16_members_covered(kernel, cover, rep, path="批建")
    b16_members_covered(inc, cover, rep, path="维护")

    # 覆盖不漏**只在批建路径上是判据**：维护路径 baseline 里有既存违规
    # （sequence 16 漏 / 2 对），那是**另一条机制**（§1 硬要求表第四行），
    # 根记账 / 项留在父方向修的是健全性，不是它 —— 所以走 baseline 守卫。
    rep.note(render_soundness(soundness_profile(kernel, cover),
                              soundness_profile(inc, cover), which))
    leak_b, leak_i = cover_leak_profile(kernel, cover), cover_leak_profile(inc, cover)
    rep.note(render_cover_leak(leak_b, leak_i, which))
    b18_cover_leak_baseline(leak_obs(which, kernel, inc, cover), rep,
                            corpus=corpus_fingerprint(nodes, edges))

    # B19 —— 进步量守卫。**跨次数**的性质：反复声称能分、却连续 N 次没让覆盖变细。
    # 与 `§K2 判空`（单次性质）不是一回事，不能合并。
    prog = progress_profile(kernel, cover)
    rep.note(render_progress(prog, which))
    b19_progress_guard(prog, rep)

    # B17 —— 「叶 = 不可分等价类」。**外生 oracle**，不调插件（否则共享盲点）。
    # 它同时是 `C8` §7 第 2 步的实测：判空点上有没有可搬的区分信息。
    classes = equiv_classes(which, nodes, edges)
    b17_leaf_is_equivalence_class(kernel, classes, rep)

    st = kernel.stats()
    by_rank: dict[int, int] = {}
    for d in kernel.all_directions():
        by_rank[d.rank] = by_rank.get(d.rank, 0) + 1
    rep.note(f"结构：{st}")
    rep.note(f"各层方向数 " + "/".join(str(by_rank[r]) for r in sorted(by_rank)))
    # ⚠️ 这里**不报**「空权威层比例」（`方向 − 空权威层`）：§K2 按**字面**
    #    （「否则不建这一层」）实现 ⇒ 被判「分不开」的那一层根本没建出来，
    #    **没有空壳可数**。
    #    代价没有消失，只是**换了地方** —— 从「多出来的空壳」搬到了「叶容量」：
    #    这一层不建 ⇒ 那些项只能留在这个叶里 ⇒ 分辨率下降。
    #    所以这里报两个数：`§K2 判空`（形状与接口贴合度的**信号**）
    #    与 `最大/平均叶容量`（**代价**信号）。两者都进输出、不进退出码。
    rep.note(f"§K2 判空 {st['§K2 判空']} 次（判「这一层不建」）；"
             f"叶容量 最大 {st['最大叶容量']} / 平均 {st['平均叶容量']}"
             f"（{st['叶']} 个叶）")
    rep.note("§K2 判空与叶容量都是**度量**，不是判据（不进退出码）—— "
             "形状越贴合接口，两个数越小")

    # §K8 的另一半：假阳**计量**（不是判据 —— 进输出，不进退出码）
    fp = false_positive_profile(kernel, plugin, queries)
    rep.note(
        f"假阳计量（§K8）：判「是」{fp['判「是」总数']} 次，其中假阳 {fp['其中假阳']} 次，"
        f"假阳率 {fp['假阳率']:.1%}（最差方向 {fp['最差方向']}）"
    )

    # **覆盖嵌套**（`§I4` 带上父方向买到的那个东西）—— 同样只报不判。
    # §1 的硬要求表里**没有**这一条 ⇒ 违反它不破坏正确性（剪枝仍安全），
    # 但它说明「子说的『是』，父接不住」⇒ 遍历白走一趟 ⇒ 纯假阳。
    cn = cover_nesting_profile(kernel, plugin, queries)
    rep.note(
        f"覆盖嵌套（§I4 父方向）：{cn['已展开方向数']} 个已展开方向，"
        f"子覆盖越出父覆盖 {cn['越界次数']} 次"
        + (f"（例：{cn['例子'][0]}）" if cn["例子"] else "（子覆盖 ⊆ 父覆盖）")
        + "（度量，不进退出码）"
    )

    # 「增量 ≡ 全量」—— 第 4 个度量（`C8` §7 第 1 步）。
    # ⚠️ **只报不判**：实测它**不成立**，进退出码会让套件常红、红成噪声。
    #    它是 `C8` §7 第 2 / 3 步的决策依据：哪几个 k 同构、哪几个不同构。
    #
    # ★ 探针可以关（`--no-probes`）。**关掉是安全的**：度量不进退出码，
    #   所以关掉它**不可能把红变成绿** —— 只会让报告少一行读数。
    #   但**必须印出来**：「没跑」与「跑了但没话说」不能长得一样。
    if probes:
        prof = divergence_profile(make_builder(which, nodes, edges), nodes, sorted(nodes))
        rep.note(render_divergence(prof, which))
    else:
        rep.note("增量 ≡ 全量（度量）：**本趟未跑**（`--no-probes`）—— 它是探针，不进退出码")

    # `C8` §7 第 2 步的读数：判空点上**有没有**可搬的区分信息。
    # 实测三个方向都是 0 —— 这不是「还没做」，是**没有东西可搬**（见 equivalence.py）。
    rep.note(render_absorption(absorption_profile(kernel, classes), which))

    # `C8` §7 第 3 步的读数：规范重建**值不值得做**。
    # 结论：同构买得到，但代价是 §K3（接口级不变量）—— 所以**不做**。
    # 与上一条一样：**只报不判**（`B8` 才是那条判据）。
    # ⚠️ 这是**最贵的一条**（O(n) 次重建 × 每次 O(n)）：281 项上 reach 要 446 s，
    #    3907 项上是**小时级**。所以它是 `--no-probes` 关掉的第一条。
    if probes:
        rep.note(render_rebuild(rebuild_profile(which, nodes, edges), which))
    else:
        rep.note("规范重建（度量）：**本趟未跑**（`--no-probes`）—— 它是探针，不进退出码")
    return rep


def cap_corpus(loaded, cap: int):
    """把语料**截到前 `cap` 项**（按 id 升序）—— 见模块开头 `--cap`。

    返回 `(新语料, 被截掉的项数)`。`cap <= 0` 或本来就不够大 ⇒ 原样返回。
    边跟着节点一起收：留下的项之间的边才留。
    """
    nodes, edges, dangling = loaded
    if cap <= 0 or len(nodes) <= cap:
        return loaded, 0
    keep = set(sorted(nodes)[:cap])
    cut = len(nodes) - len(keep)
    new_nodes = {i: n for i, n in nodes.items() if i in keep}
    new_edges = {i: frozenset(d for d in v if d in keep)
                 for i, v in edges.items() if i in keep}
    return (new_nodes, new_edges, dangling), cut


def main(argv: list[str]) -> int:
    # ⚠️ `--cap 400` 里的 `400` **不是**方向名 —— 所以先摘掉带值的选项再取位置参数。
    cap = 0
    probes = True
    args: list[str] = []
    i = 1
    while i < len(argv):
        if argv[i] == "--cap":
            cap = int(argv[i + 1])
            i += 2
            continue
        if argv[i] == "--no-probes":
            probes = False
            i += 1
            continue
        args.append(argv[i])
        i += 1
    which = [a for a in args if not a.startswith("-")]
    targets = which or ["keyset", "reach", "sequence"]

    loaded = load()
    if loaded is None:
        print("⚠ 找不到语料 —— 全部检查**未展开**（跳过），这不是通过。")
        print("  设 LDV_CORPUS 或把语料放到 ldv/corpus/nodes/")
        return 0

    loaded, cut = cap_corpus(loaded, cap)
    if cut:
        print(f"⚠ `--cap {cap}`：语料 **{cut + cap} 项 ⇒ 本趟只用前 {cap} 项**，"
              f"其余 {cut} 项**本趟什么也没说**。")
        print("   ⚠️ `--cap` **连判据一起截** —— 它会挡住结论。实测 `openalex-citations`")
        print("      的环全在第 400 项之后 ⇒ 前 400 项上看不到 `B17` 那条红。")
        print("      要判据有效就别用 `--cap`；要快用 `--no-probes`。")
        print()

    if not probes:
        print("⚠ `--no-probes`：**贵的那几条探针本趟未跑**（`增量≡全量` / `规范重建`）。")
        print("   为什么关：`规范重建` 是 O(n) 次重建 × 每次 O(n)，281 项上 reach 要 446 s，")
        print("   3907 项上是小时级 —— 而它**只报不判**，不进退出码。")
        print("   ⇒ **关它不可能把红变成绿**：判据（B1–B19）一条不少地全跑。")
        print("   报告里会写明哪几行未跑 —— 「没跑」与「跑了但没话说」不共用一行。")
        print()

    if "--write-cover-leak-baseline" in args:
        nodes, edges, _ = loaded
        entries: dict[str, dict] = {}
        for w in targets:
            cover = coverage_of(w, nodes, edges)
            entries.update(leak_obs(w, batch_kernel(w, nodes, edges)[0],
                                    maintenance_kernel(w, nodes, edges), cover))
        entries = {k: {"漏项数": v["漏项数"], "漏的对数": v["漏的对数"]}
                   for k, v in sorted(entries.items())}
        write_baseline(entries, corpus=corpus_fingerprint(nodes, edges))
        print("已冻结覆盖不漏基线：")
        print(f"  语料指纹 {corpus_fingerprint(nodes, edges)}")
        for k, v in entries.items():
            print(f"  {k:24s} {v['漏项数']} 漏 / {v['漏的对数']} 对")
        return 0

    reps: list[Report] = []
    for w in targets:
        rep = run_one(w, loaded, probes=probes)
        reps.append(rep)
        print(rep.render())
        print()

    # B12 / B14 是**内核侧**的，与插件无关 —— 只跑一次
    kernel_rep = Report(plugin="(内核)", expects=KERNEL_CODES)
    b12_no_global_scalar(kernel_rep)
    b14_exogenous_boundary(kernel_rep)
    reps.append(kernel_rep)
    print(kernel_rep.render())
    print()

    total_red = sum(len(r.red) for r in reps)
    total = sum(len(r.assertions) for r in reps)
    skipped = sum(len(r.skipped) for r in reps)
    missing = [c for r in reps for c in r.missing_codes() + r.unexpected_codes()]
    print("═══ 汇总 ═══")
    print(f"  断言 {total} 条：红 {total_red}，跳过 {skipped}")
    if skipped:
        print(f"  跳过的是：{[a.code for r in reps for a in r.skipped]}")
    if missing:
        print(f"  ‼ 注册表不符：{missing}")
    print(f"  退出码 {1 if total_red else 0}")
    return 1 if total_red else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
