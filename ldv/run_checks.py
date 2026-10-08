"""跑 B1–B20 —— 设计文档 §7。

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

    判据（进退出码）   B1–B20。**全量上最贵的是 `B3`**（见下），
                       其次是 `B15`（3 次整建）与 `B8`（n 次插入）
    探针（只报不判）   十个度量。贵的是 `规范重建`（O(n) 次重建 × 每次 O(n)）

⚠️ 别把「判据贵」全记在 `B8` / `B15` 头上：实测 281 项上两者合计 **2.9 s**，
   而 `--no-probes` 整趟（三个方向）是 **7.2 s** ⇒ 它们约占 40%，其余分散在
   十几条判据里。**要指名道姓得先逐块计时**（`outputs/_measure_b8_scaling.py`
   与 `outputs/_measure_cover_cost.py` 记了两次「形状对得上但因果不成立」）。

⚠️ **全量上 `B3` 是最大的一块**（3907 项 / reach）：它的**建表**那半是
   `层内方向数 × 项数` 次 `call_penalty` —— 7813 × 3907 = **30.5M 次**，
   而每次 `reach.penalty` 内部跑一遍 BFS。
   ⇒ 它的**三重循环**那半（`O(L²n)`，全量 **379 亿次**迭代）**已经删掉** ——
   那一段证明上不可能命中，见 `ldv/MEASUREMENTS.md` 结果八；
   **剩下的建表那半**是「插件对同一 payload 反复重算」那个病，位置在**插件里**。

⚠️ 覆盖族（健全性 / `B16` / 覆盖不漏 / 细化量）也调同一个 oracle，但它**已经不是大头**：
   3907 项上整族 **0.69 s**（改前 49.71 s —— 那个 oracle 按**正向**闭包逐项扫，
   且 85% 的调用在**重算**）。读数、根因与「**渐近没修**」那条边界见
   `ldv/MEASUREMENTS.md` 结果七。
   ⚠️ 它的**探针**（`覆盖 oracle（度量…）`那一行）报 `调用 / 命中 / 不同 payload` ——
   只报一个「快了 N 倍」不够：一个「从来没命中」的缓存与一个「全命中」的缓存，
   在**判据的结论**上长得一模一样。

⇒ **关探针是安全的**：它们**不进退出码**，关掉它们**不可能把红变成绿**。
   实测（281 项语料，`outputs/_profile_runone_prod.py` —— 它**包生产 `run_one`**，
   不抄一份序列，所以剖面不会跟生产漂）：

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
import tempfile
from pathlib import Path

from .checks._fixtures import (
    build_incremental,
    build_keyset,
    build_reach,
    build_sequence,
    coverage_of,
    equiv_classes,
    keyset_queries,
    label_bearing,
    labels,
    load,
    make_builder,
    make_keyset_root,
    make_reach_root,
    make_sequence_root,
    reach_queries,
)
from .core.tri import Tri
from .checks._framework import Report
from .checks.abstraction import (
    VIEW_CODES,
    a1_soundness,
    a2_stable,
    a3_coarsest,
    a4_category,
    a5_ledger,
    a6_roundtrip,
    build_views,
    ledger_entries,
    load_spec_file,
    reading_ctx,
    render_views,
    spec_for,
    subtree_of,
    view_profile,
    warranted_of,
)
from .core.views import view_parts
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
from .checks.refinement import (
    b20_semantic_refinement,
    refinement_profile,
    render_refinement,
)
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
                "B13", "B15", "B16", "B17", "B18", "B19", "B20")
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

    # 覆盖不漏（§1 表第四行，⬜ 2026-10-07 已降级）**只在批建路径上是判据**：
    # 维护路径 baseline 里有既存违规（sequence 16 漏 / 2 对）。⚠️ 那**不是**「另一条
    # 机制」—— 实测漏项与滞留项是**同一批**（16 == 16，双向差 0）⇒ 它与「滞留 == 0」
    # 是**同一个条件**，而后者已被出路 (4) 用「代价在划分」换掉 ⇒ 降级为
    # 「度量 + baseline 守卫」。见 §10.2 B 末。
    rep.note(render_soundness(soundness_profile(kernel, cover),
                              soundness_profile(inc, cover), which))
    leak_b, leak_i = cover_leak_profile(kernel, cover), cover_leak_profile(inc, cover)
    rep.note(render_cover_leak(leak_b, leak_i, which))
    # ⚠️ **别在这里再调 `leak_obs`** —— 它会把上面两个 profile 重算一遍。
    #   那正是本仓库在防的形状的一种：多算一遍**看不出**（结果一模一样），
    #   只看得见它慢。`leak_obs` 现在只留给 `--write-cover-leak-baseline` 用。
    b18_cover_leak_baseline({f"{which}|batch": leak_b, f"{which}|maintenance": leak_i},
                            rep, corpus=corpus_fingerprint(nodes, edges))

    # B19 —— 变细守卫。**跨次数**的性质：反复声称能分、却连续 N 次没让覆盖变细。
    # 与 `§K2 判空`（单次性质）不是一回事，不能合并。
    prog = progress_profile(kernel, cover)
    rep.note(render_progress(prog, which))
    b19_progress_guard(prog, rep)

    # B20 —— **语义**变细守卫。`§K2` 第二半的外生判据：内核没有语义，
    # 所以语义**只能从语料的标注来**（`_fixtures.labels`，不调插件）。
    # 与 `B19` 成对：**一条守覆盖，一条守标签**；两条都进退出码，但**判的范围不同**。
    # ⚠️ 判据只在**标签轴**上落（`label_bearing`，由语料决定）：
    #   `reach` / `sequence` 的 payload 与标签无关 ⇒ 只报不判（报「未展开」）。
    labs = labels(nodes)
    ref = refinement_profile(kernel, labs)
    rep.note(render_refinement(ref, which))
    b20_semantic_refinement(ref, rep, judged=label_bearing(which, nodes))

    # ★ 覆盖 oracle 的**代价读数**（度量，不进退出码）。
    #   ⚠️ **只报一个「快了 N 倍」不够**：一个「从来没命中」的缓存与一个「全命中」的
    #   缓存，在**判据的结论**上长得一模一样 —— 那正是本仓库一直在防的形状。
    #   所以两个数都报：去重有没有真的发生、代价落到内存上多少。
    #   为什么这一条重要：覆盖族是本仓库**唯一**随语料超线性的地方（见 `_fixtures`
    #   的「代价的形状」那段），而它贵在**同一个 payload 被反复要**。
    _cs = cover.stats
    rep.note(f"覆盖 oracle（度量，不进退出码）：调用 {_cs['调用']} 次，"
             f"去重命中 {_cs['命中']} 次（不同 payload {_cs['不同 payload']} 个，"
             f"缓存元素 {_cs['缓存元素数']} 个）")

    # B17 —— 「叶 = 不可分等价类」。**外生 oracle**，不调插件（否则共享盲点）。
    # 它同时是 `C8` §7 第 2 步的实测：判空点上有没有可搬的区分信息。
    # ⚠️ 必须把 `which` 传进去：(b) 那一半在 `reach` 上**只报不判**（§7.2）。
    classes = equiv_classes(which, nodes, edges)
    b17_leaf_is_equivalence_class(kernel, classes, rep, which)

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


def view_report(loaded, targets: list[str]) -> Report:
    """流程 E 的 `§A1`–`§A6` —— **单独一组**，与插件无关。

    为什么单独一组、且**只跑一次**：

        视图层的输入是「**方向 id**」的集合（`core/views.ViewSpec.universe`），
        而方向 id 是**每个内核各自从 0 开始**的（`D0` 在 keyset 里与在 reach 里
        是**两个不同的方向** —— `_fixtures.Cover` 的 docstring 专门记了这条）。
        ⇒ 外生声明里必须写清它属于哪条方向 ⇒ 判据也就只对那一条方向跑。

    ## 三种「跳过」各有各的话

        文件不在            还没人声明 `P` / `E`
        指纹对不上          声明是**别的语料**上写的
        `方向` 不是这一条    声明是**别的方向**的

    ⚠️ 三条都报「**跳过**」而不是「过」—— 设计稿 §10 停止条件第 1 条：
       `P` / `E` 必须外生，**猜一个就是替人做 `§K9` 的决定**。
       跳过 ≠ 通过：它明说「这一层在本趟**什么都没查**」。
    """
    nodes, edges, _ = loaded
    corpus = corpus_fingerprint(nodes, edges)
    rep = Report(plugin="(视图)", expects=VIEW_CODES)
    doc = load_spec_file()

    if not doc:
        _skip_views(rep,
                    "外生项**未声明**（`docs/prior-art/…` 之外的 `ldv/checks/view_spec.json` "
                    "不在）—— `P` / `E` 必须**外生**（设计稿 §3 / §K9 / `B14`），"
                    "猜一个就是替人做决定（§10 停止条件 1）。"
                    "**跳过 ≠ 通过**：这一层本趟什么都没查。")
        return rep

    which = str(doc.get("方向") or "")
    if which not in targets:
        _skip_views(rep,
                    f"外生声明写的是 `{which}`，而本趟跑的是 {targets} "
                    f"⇒ 本方向**判不了**（跳过 ≠ 通过）")
        return rep

    spec, why = spec_for(doc, which, corpus)
    if spec is None:
        _skip_views(rep, why + " —— 判不了，**不是通过**")
        return rep

    kernel, plugin, _, _ = batch_kernel(which, nodes, edges)
    cover = coverage_of(which, nodes, edges)
    vs = build_views(kernel, spec, cover, plugin)
    a1_soundness(vs, rep)
    a2_stable(spec, vs.q, rep)
    a3_coarsest(spec, vs.q, rep)

    # ★ `§A4` 的「部件」= `reach(块)` 的一个划分（`view_parts`）。视图划分**会横跨树**，
    #   所以「下层」**不是**「`reach` 里的那些块」—— 那不是嵌套，恒等式根本不成立。
    #   见 `view_parts` 的 docstring 与 `§A4` 那一段注释。
    parts = view_parts(vs.q, lambda d: subtree_of(kernel, d))
    a4_category(parts, reading_ctx(kernel, spec), doc.get("读数") or [], rep)

    a5_ledger(ledger_entries(kernel, spec), vs,
              lambda did, item: warranted_of(kernel, did, item),
              frozenset(kernel.items), rep)

    # ⚠️ `§A6` 要一个**真的落盘**才判得了。这里给一个临时文件 ——
    #   所以生产路径上这条**是跑的**；「没有落盘 ⇒ 跳过」那条路由测试单独验。
    with tempfile.TemporaryDirectory() as _td:
        a6_roundtrip(vs, kernel, cover, plugin, rep, path=Path(_td) / "views.json")

    prof = view_profile(vs)
    rep.note(f"{why}；方向 `{which}`")
    rep.note(render_views(vs, prof, which))
    return rep


def _skip_views(rep: Report, why: str) -> None:
    """六条一起跳过 —— 用一个函数，免得六条的**理由**各写一遍、写着写着就不一样了。"""
    rep.add("A1", "视图健全性：具体化 ⊇ ∪成员", Tri.UNEXPANDED, why)
    rep.add("A2", "视图稳定：B₁ ⊆ E⁻¹(B₂) 或 B₁ ∩ E⁻¹(B₂) = φ", Tri.UNEXPANDED, why)
    rep.add("A3", "视图最粗：不存在更粗的稳定划分", Tri.UNEXPANDED, why)
    rep.add("A4", "视图类别：distributive 的 `G` / holistic 的见证都要实测成立",
            Tri.UNEXPANDED, why)
    rep.add("A5", "视图账：每条都要指得到具体方向 + 项，且独立重算下成立",
            Tri.UNEXPANDED, why)
    rep.add("A6", "视图落盘-读回：权威边逐字相同、存档不带派生边、读回后 §A1 仍成立",
            Tri.UNEXPANDED, why)


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
        print("   ⇒ **关它不可能把红变成绿**：判据（B1–B20）一条不少地全跑。")
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

    # ★ 流程 E 的 `§A1`–`§A6` —— **视图侧**，同样与插件无关，只跑一次。
    #   它的外生输入（`P` / `E`）必须由人声明；没声明就三条都**跳过**并印原因
    #   （设计稿 §10 停止条件 1）。见 `view_report` 的 docstring。
    view_rep = view_report(loaded, targets)
    reps.append(view_rep)
    print(view_rep.render())
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
