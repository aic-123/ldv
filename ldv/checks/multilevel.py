"""`§M0`–`§M6` —— **视图层（L0）的声明判据** + **「若继续折」的契约判据**。

    `docs/分层方向视图-多层抽象-前作核验.md` §三 是这份实现的**来源**；
    `ldv/core/views.py` 的折叠那一段是**被判对象**（`quotient_spec` / `fold_until`）。

---

## ⚠️ 先读这一段：折叠**不是目标**，产物是第 0 层

本设计的产物是**视图层**：`csr(spec)` 给的那个划分。它的用途是让检索器在使用这套
结构时**先得到一个模糊掌握**（先看粗、再看细），而不是一上来就在 25 个方向里精确挑。
**一层就够了。**

实测（36 项语料 / 三个方向，`MEASUREMENTS.md` 结果二十）：**真折 0 层** ——
第 1 层的商不缩（`|Q₁| == |U₁|`）⇒ 折叠在此终止。而这不是缺陷：
膨胀已被第 0 层收住（`reach` 方向数涨 5.7× 而视图数只涨 1.6×）。

⇒ 这七条按**守谁**分两拨，名字里**不再出现「多层」**：

| 编号 | 守谁 | 守的东西 | 红形态 |
|---|---|---|---|
| `§M0` | **L0** | 视图关系**无自环** | 去掉 `A ≠ B` 过滤 ⇒ 出现 `(V, V)` |
| `§M1` | **L0** | 视图层**真的在缩**（收缩比 ≤ 阈值） | 喂离散划分（块数 == 方向数） |
| `§M4` | **L0** | 验证代价 `n_cand ≤ cap`（**折叠之前**判） | 一层 `Bell(K) > 200000` |
| `§M2` | 折叠 | 层数不超过 `MAX_LEVELS` | 调用方把 `max_levels` 抬到声明值之上 |
| `§M3` | 折叠 | 总代价有界（`Σ|U_k| ≤ 2·|U_0|`） | 手造一个「每层不缩」的层叠 |
| `§M5` | 折叠 | 终止**只由 `§M1` / `§M2` 触发** | 末层 `stopped` 为空（跑到不动点） |
| `§M6` | 折叠 | 顶层账 **⊇** 各层丢掉的并（账必须**被携带**） | 折叠时不携带账 |

⚠️ **「一层就够」不等于「折叠的契约可以不要」。** `fold_until` 仍在跑，它的刹车、
   它的账、它的出口都还是**契约** ⇒ 后四条照旧守。**但它们守的是机制，不是产物。**
   （⚠️ 旧标题里的「且不止一层」是**反需求**的：它把「折叠没折下去」写成红，
   而本设计的产物恰恰就是第 0 层。已删 —— 见 `m1_shrink` / `m5_termination`。）

⚠️ **七条里有四条的红形态在契约层** —— `§M3` / `§M6` / `§M5` 与 `§M1` 的 `bad` 那一支。
   它们是**守卫**，不是会开火的判据（与删除路径的 `D2`/`D3` 同一处境）：
   `fold_until` 的每一层都缩一半以上 ⇒ `Σ|U_k| < 2|U_0|` 是**几何级数的推论**；
   账 `ledger_k = ∪_{j≤k} lost_j` **按构造相等** ⇒ `§M6` 恒真；
   而它的循环**只有两个出口**，都带非空 `stopped` ⇒ `§M5` 的正向半**不可达**；
   一超阈值就**立刻停** ⇒ 超阈值的层只可能是末层 ⇒ `§M1` 的 `bad` **恒空**。
   四条对 `fold_until` 的输出**恒真**。它们守的是**契约**：另一个实现若允许
   「不缩的层继续折」/「跑到不动点就停」/「不携带账」，它们就会开火。
   所以注入是**手造层叠**。
   这一条必须写在这里 —— 否则「恒真」与「没在判」长得一模一样。
   ⚠️ **同一个事实在 `test_injections.py` 的 L0 节首也写着一份**（两处都是散文，
   漂移了没有任何东西看得出来）⇒ **改一处要改两处**。能**真跑红**的是另外四条：
   `§M0`（塞自环）/ `§M1`（喂离散划分）/ `§M2`（拆掉 `MAX_LEVELS`）/ `§M4`（一层 11 块）。

⚠️ **`§M4` 与 `§A3` 必须分得开**：
   `§A3` 跳过 = 「**这一层判不了**」（读数层面）；`§M4` 红 = 「**这一层压根不该这么设计**」
   （设计层面，阻止交付）。两者在只看布尔值时**长得一模一样** ⇒ `§M4` 在**折叠之前**判，
   且注入必须**同时观察到**「`§M4` 红 + `§A3` 仍跳过」，否则「分得开」这句话是空的。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable, Sequence

from ..core.views import (
    MAX_COARSENING_CANDIDATES,
    MAX_LEVELS,
    MIN_SHRINK_RATIO,
    Level,
    ViewSpec,
    coarsest_stable_refinement,
    fold_until,
    quotient_spec,
)
from ._framework import Report
from ..core.tri import Tri

#: 这一组要跑的编号。**只列已经实现了的** —— 没实现的不许写进来
#: （写进来就等于声称「套件全绿」覆盖了它，而它根本没跑）。
#:
#: ⚠️ 名字里的 `M` 是 `multilevel` 的残留（这一组原来是按「多层」写的）。
#:    代码**不改名** —— 改名要动注册表、注入、文档四处，而编号本身
#:    对「这一组守什么」一个字节的信息都没有。**改的是主语**（标题与文档）。
MULTILEVEL_CODES = ("M0", "M1", "M2", "M3", "M4", "M5", "M6")


def _title_m2(n: int) -> str:
    return f"折叠：层数 ≤ `MAX_LEVELS`（{n}）"


def _title_m4(cap: int) -> str:
    return f"每层：验证代价 `n_cand ≤ {cap}`（`§A3` 的**前置门**）"


#: 七条的**标题** —— **一处定义，两处用**（真跑时 `rep.add`、跳过时 `skip_all`）。
#:
#: ⚠️ **分开写两份会各自漂移**，而两边都只是字符串 ⇒ 漂移了**没有任何东西看得出来**
#:    ——「跳过时印的标题」与「真跑时印的标题」不一致时，读起来像**两条不同的判据**。
#:    `run_tests.test_multilevel` ⑥ 段**逐字对照**两边（并核 `set(TITLES) == set(CODES)`）。
#:
#: ⚠️ `M2` / `M4` 的标题带**参数**（`max_levels` / `cap`），所以它们是**函数**：
#:    形参不是默认值时标题要跟着动，否则输出里印的那个数就是**假的**
#:    （而「印错了数」与「印对了数」在只看红绿时长得一模一样）。
TITLES: dict[str, str] = {
    "M0": "视图层：`E` 无自环（自环让「稳定」退化成恒真）",
    "M1": "视图层：`shrink = |Q₀|/|U₀| ≤ 阈值`"
          "（视图层**真的在缩** —— 这一层就是产物）",
    "M2": _title_m2(MAX_LEVELS),
    "M3": "折叠：总代价 `Σ|U_k| ≤ 2·|U_0|`（几何递减的推论 —— 守卫）",
    "M4": _title_m4(MAX_COARSENING_CANDIDATES),
    "M5": "折叠：终止只由 `§M1`（收缩比）/ `§M2`（层数）触发 —— 守卫",
    "M6": "折叠：顶层账 ⊇ 各层丢掉的并（账必须**被携带**，不许现推）",
}


# --- §M0 ----------------------------------------------------------------------

def m0_no_self_loop(levels: Sequence[Level], rep: Report) -> None:
    """`§M0` —— 商关系**无自环**：每一层的 `relation` 里不许有 `(x, x)`。

    ## 为什么自环要单独防

    Paige–Tarjan 的稳定条件是对 `E⁻¹(B₂)` 说的。若 `E` 里有 `(x, x)`，
    那么 `x ∈ E⁻¹({x})` **恒成立** ⇒ 「`B₁ ⊆ E⁻¹(B₂)` 或 `B₁ ∩ E⁻¹(B₂) = φ`」
    在某些块上**自动为真** ⇒ 判据退化成**空转**。这正是本仓库最忌的形状，
    而它出在**被判对象**（`E`）身上，不是出在判据身上 —— 所以只能在这里挡。

    ⚠️ 第 0 层是**人写的** `E`，不是 `quotient_spec` 产的。这一条**照样判它**：
       人写进来的自环与商诱导出来的自环，对「稳定」的破坏**完全一样**。
       分开报会让「人写错了」与「商算错了」长得一样。
    """
    bad = [(i, a) for i, lv in enumerate(levels)
           for (a, b) in lv.spec.relation if a == b]
    rep.add("M0", TITLES["M0"],
            Tri.NO if bad else Tri.YES,
            (f"{len(bad)} 处自环：{[(i, x) for i, x in bad[:3]]}"
             f"（第 0 层来自人写的 `E`，之后各层来自 `quotient_spec`）") if bad
            else (f"{len(levels)} 层，"
                  f"{sum(len(lv.spec.relation) for lv in levels)} 条边，无自环"))


# --- §M1 ----------------------------------------------------------------------

def m1_shrink(levels: Sequence[Level], rep: Report,
              min_ratio: float = MIN_SHRINK_RATIO) -> None:
    """`§M1` —— **视图层真的在缩**：`shrink(L0) = |Q₀| / |U₀| ≤ min_ratio`。

        `inner` = 除**末层**外的每一层（末层是终止层，按定义会超阈值）
        `len(levels) == 1` 时 `inner` 就是那一层本身 —— **它不豁免**

    ## 为什么末层豁免

    `fold_until` 的终止条件**就是**「这一层缩不动了」⇒ 末层按定义会超阈值。
    把它算进去的话 `§M1` 在**任何**输入上都红 —— 一条常驻的红等于没人再看红。

    ## ⚠️ 旧的「且层数 ≥ 2」已删（2026-10-08）—— 它把两件事混成了一件

        ① 「视图层没缩够」  ← **真判据**：`|Q₀| == |U₀|` ⇒ 这一层没兑现「粗」
        ② 「折叠没折下去」  ← **反需求**：本设计的产物就是第 0 层，**一层就够**

    而 `len(levels) == 1` 这个**读数**恰好**只**由 ① 造成 —— `fold_until` 的循环在
    `shrink > min_ratio` 时**立刻返回**，所以「只有一层」⇔「`L0` 没缩够」。
    ⇒ 判据直接写成 ① 本身**更强也更准**：它指着那一层的两个数（`|U₀|` / `|Q₀|`），
      而不是绕道去数层数。红形态**不变**（同一个条件），只是说法对了。

    ⚠️ 旧 docstring 还写着「`Q == P`（第一层就不缩）」—— 那句**把两个极端写反了**：

        `Q == P` = `{U}` = **1 块**   ⇒ `shrink = 1/|U|` ⇒ **缩得最狠**
        `|Q| == |U|` = 离散            ⇒ `shrink = 1.0`   ⇒ **一点没缩**

    （同一条更正在 `test_injections.inj_m1` 里也有一份；实现按 `|Q|/|U|` 的定义走。）

    ## ⚠️ `bad` 那一支是**守卫**（对 `fold_until` 的输出恒真）

    `fold_until` 只要 `shrink > min_ratio` 就**立刻停** ⇒ 超阈值的层**只可能是末层**
    ⇒ `levels[:-1]` 全 ≤ 阈值 ⇒ `bad` 恒空。红形态只能**手造**：另一个实现若允许
    「不缩的层继续折」，它才开火。理由与 `§M3` 相同 —— 守的是**契约**。
    """
    inner = levels[:-1] if len(levels) > 1 else list(levels)
    bad = [(i, round(lv.shrink, 3)) for i, lv in enumerate(inner)
           if lv.shrink > min_ratio]
    rep.add("M1", TITLES["M1"],
            Tri.NO if bad else Tri.YES,
            (f"没缩够：{[(f'L{i}', r) for i, r in bad[:3]]}（阈值 {min_ratio}）—— "
             f"`|Q| == |U|` ⇒ 这一层**没有兑现「粗」**，检索器拿不到模糊掌握") if bad
            else (f"L0：`|U₀| = {len(levels[0].spec.universe)}` ⇒ "
                  f"`|Q₀| = {len(levels[0].q)}`，收缩比 {levels[0].shrink:.3f} "
                  f"≤ {min_ratio}"
                  + (f"；另有 {len(inner) - 1} 个中间层也全 ≤ {min_ratio}"
                     if len(inner) > 1 else "")
                  + ("（只有一层：它自己就是终止层）" if len(levels) == 1 else "")))


# --- §M2 ----------------------------------------------------------------------

def m2_levels(levels: Sequence[Level], rep: Report,
              max_levels: int = MAX_LEVELS) -> None:
    """`§M2` —— 层数不超过 `MAX_LEVELS`。

    ⚠️ `fold_until` 自己按 `max_levels` 停 ⇒ 拿**默认参数**跑出来的层数
       永远 `≤ MAX_LEVELS` ⇒ 这一条在基线上恒绿。**它守的是配置**：
       调用方把 `max_levels` 抬到声明值之上时，它必须开火 ——
       「有人把刹车拆了」与「没超」在只看层数时**长得一模一样**。
    """
    n = len(levels)
    rep.add("M2", _title_m2(max_levels),
            Tri.NO if n > max_levels else Tri.YES,
            (f"折了 {n} 层，超过声明上限 {max_levels} —— "
             f"最后一层的 `stopped` 是 {levels[-1].stopped!r}") if n > max_levels
            else f"折了 {n} 层 ≤ {max_levels}；末层停因 {levels[-1].stopped!r}")


# --- §M3 ----------------------------------------------------------------------

def m3_cost(levels: Sequence[Level], rep: Report) -> None:
    """`§M3` —— 总代价有界：`Σ_k |U_k| ≤ 2 · |U_0|`。

    ## ⚠️ 这是**守卫**，不是会开火的判据（读之前先读这句）

    `fold_until` 的每一层都缩掉一半以上（`§M1` 管着）⇒

        Σ|U_k| ≤ |U_0| · (1 + 1/2 + 1/4 + …) < 2·|U_0|

    是**几何级数的推论**，对 `fold_until` 的输出**恒真**。所以它在基线上绿，
    而「绿」的**理由**是「这个不变量被实现了」还是「这条判据没在判」——
    **长得一模一样**。

    ⇒ 它守的是**契约**：另一个实现若允许「不缩的层继续折」（比如把
      `fold_until` 的刹车拆了），层数就会线性涨、总代价跟着涨。那时它开火。
      所以它的注入是**手造层叠**（`inj_m3`），不是真跑 `fold_until`。

    ⚠️ 这一条与删除路径的 `D2`/`D3` 是同一个处境（「它们是守卫，不是会开火的判据」），
       处置也一样：**照配，并写明它为什么不常开火**。
    """
    n0 = len(levels[0].spec.universe)
    cost = sum(len(lv.spec.universe) for lv in levels)
    rep.add("M3", TITLES["M3"],
            Tri.NO if cost > 2 * n0 else Tri.YES,
            (f"Σ|U_k| = {cost} > 2·|U_0| = {2 * n0}（{len(levels)} 层）"
             f" —— 有层**不缩还继续折**") if cost > 2 * n0
            else f"Σ|U_k| = {cost} ≤ 2·|U_0| = {2 * n0}（{len(levels)} 层）")


# --- §M4 ----------------------------------------------------------------------

def m4_verifiable(levels: Sequence[Level], rep: Report,
                  cap: int = MAX_COARSENING_CANDIDATES) -> None:
    """`§M4` —— **每一层**的验证代价 `n_cand ≤ cap`。

    与 `§A3` 的跳过**必须分得开**（模块开头那段）：
    `§A3` 是「跑到那一层才发现判不了」，`§M4` 是「折叠**之前**就知道这一层不该这么设计」。
    ⇒ 判据在 `fold_until` 之前/之中就该有结论，而不是等 `§A3` 报跳过。
    """
    bad = [(i, lv.n_cand) for i, lv in enumerate(levels) if lv.n_cand > cap]
    rep.add("M4", _title_m4(cap),
            Tri.NO if bad else Tri.YES,
            (f"{len(bad)} 层超上限：{bad[:3]} —— `§A3` 那时只会报**跳过**，"
             f"而跳过不阻止交付") if bad
            else (f"{len(levels)} 层的 `n_cand` = "
                  f"{[lv.n_cand for lv in levels]}，全 ≤ {cap}"))


# --- §M5 ----------------------------------------------------------------------

def m5_termination(levels: Sequence[Level], rep: Report) -> None:
    """`§M5` —— 折叠终止**只许**由 `§M1`（收缩比）或 `§M2`（层数）触发。

        末层的 `stopped` **非空**，且取值在 `{"M1 收缩比", "M2 层数"}` 里

    ## ⚠️ 这是**守卫**（读之前先读这句）

    `fold_until` 的循环**只有两个出口**，都带非空 `stopped` ⇒ 这一条对它的输出
    **恒真**，红形态只能**手造**。它守的是**契约**：另一个实现若「跑到不动点就停」
    （没有刹车），它开火。理由与 `§M3` / `§M6` 相同。

    ## ⚠️ 旧的「反向半：层数 > 1」已删（2026-10-08）—— 两条理由，各自都够

        ① 它是**反需求**：本设计的产物是第 0 层，**一层就够**。
           「第一层就停」**不是误停**，是期望的结局。
        ② 它与 `§M1` **判同一件事**：`len(levels) < 2` ⇔ `L0` 没缩够
           （`fold_until` 的循环在 `shrink > min_ratio` 时立刻返回）——
           而那正是 `§M1` 现在**直接**判的。留着它会让「红」分不清是谁的
           （本仓库的一条纪律：两条判同一件事，红就指不出病因）。

    ⇒ 删掉它**没有**丢掉覆盖：那个条件仍被 `§M1` 判着，而且判得更准。
    """
    legal = {"M1 收缩比", "M2 层数"}
    last = levels[-1].stopped
    why: list[str] = []
    if last not in legal:
        why.append(f"末层停因 {last!r} 不在 {sorted(legal)} 里"
                   f"（空 = 没有刹车，那是「跑到不动点」，本设计不许）")
    rep.add("M5", TITLES["M5"],
            Tri.NO if why else Tri.YES,
            "；".join(why) if why
            else (f"末层停因 {last!r} 合法（共 {len(levels)} 层）"))


# --- §M6 ----------------------------------------------------------------------

def m6_ledger(levels: Sequence[Level], rep: Report) -> None:
    """`§M6` —— 顶层视图的账 **⊇** 各层丢掉的并。

        `levels[-1].ledger  ⊇  ∪_k levels[k].lost`

    ## 为什么这条是「误差不许优化掉」的载体

    折叠让视图变粗 ⇒ 会**丢**掉一些原本答得出的原始项（`lost(k)`）。
    Hendrickson 的 `The price paid` 与 Fortunato 的 `resolution limit`
    说的是同一件事的两面：**封顶之后，阈值以下的东西永久不可见。**

    ldv 对这件事的现有机制是**账**（抽象层 §6）。多层里，把账在向上一层时
    **丢掉**，等价于**偷偷把误差优化掉了** —— 正是设计稿 §1 花一整节挡掉的那条路。

    ## ⚠️ 为什么 `ledger` 必须是**字段**，不能现推

    现推（`∪_k lost(k)`）的话，「账被丢了」与「账本来就是空的」**长得一模一样** ——
    两者都是「现推出来是个空集/非空集」。所以 `Level` 里 `lost` 与 `ledger`
    **分开存**，判据比的是「携带下来的」与「真的丢了的」。
    """
    lost_union: set[str] = set()
    for lv in levels:
        lost_union |= set(lv.lost)
    top = set(levels[-1].ledger)
    miss = sorted(lost_union - top)
    rep.add("M6", TITLES["M6"],
            Tri.NO if miss else Tri.YES,
            (f"顶层账少了 {len(miss)} 项：{miss[:3]} —— 有层**丢了账没往上带**"
             f"（等价于把误差优化掉）") if miss
            else (f"{len(levels)} 层共丢 {len(lost_union)} 项，"
                  f"顶层账携带 {len(top)} 项，逐项覆盖"))


# --- 已知答案对照 --------------------------------------------------------------

#: 合成对照用的一张小规格：`U = {a,b,c,d}`、`P = {U}`、`E = {(a,b),(c,d)}`。
#: 与 `abstraction.py` 里 `§A7` 那张手推图**同源**，便于交叉核对。
CONTROL_SPEC = ViewSpec(
    universe=("a", "b", "c", "d"),
    partition=(frozenset({"a", "b", "c", "d"}),),
    relation=frozenset({("a", "b"), ("c", "d")}),
)


def known_answer_controls() -> list[str]:
    """`§M0`–`§M6` 的**合成对照** —— 每条都要「该绿时绿、该红时红」。

    返回失败清单（空 = 全过）。**每条都造一个会红的输入**，
    否则「这条判据在判」与「这条判据恒真」长得一模一样。
    """
    fails: list[str] = []

    def judge(fn: Callable[[Sequence[Level], Report], None],
              levels: Sequence[Level]) -> Tri:
        rep = Report(plugin="(多层对照)", expects=MULTILEVEL_CODES)
        fn(levels, rep)
        return rep.assertions[0].result

    # ── `§M0`：真商无自环；去掉 `A ≠ B` 过滤就出现自环 ─────────────────────
    def quotient_with_self_loops(spec, q, partition):
        """对照实现：**不**过滤 `A == B`。"""
        from ..core.views import block_namer, partition_of

        namer = block_namer(q)
        home = {x: namer[b] for b in partition_of(q) for x in b}
        relation = frozenset((home[a], home[b]) for (a, b) in spec.relation)
        uni = tuple(sorted(set(home.values()), key=lambda s: int(s[1:])))
        return ViewSpec(universe=uni, partition=partition_of(partition),
                        relation=relation)

    # 造一个「同一块里有一条 `E` 边」的规格：`E = {(a,b)}` 而 `Q = {U}`
    spec_loop = ViewSpec(universe=("a", "b"),
                         partition=(frozenset({"a", "b"}),),
                         relation=frozenset({("a", "b")}))
    q_loop = (frozenset({"a", "b"}),)
    good = Level(spec=quotient_spec(spec_loop, q_loop, [("V0",)]),
                 q=(frozenset({"V0"}),), shrink=1.0, n_cand=1)
    bad = Level(spec=quotient_with_self_loops(spec_loop, q_loop, [("V0",)]),
                q=(frozenset({"V0"}),), shrink=1.0, n_cand=1)
    if judge(m0_no_self_loop, [good]) is not Tri.YES:
        fails.append("§M0 真商被判红")
    if judge(m0_no_self_loop, [bad]) is not Tri.NO:
        fails.append("§M0 自环没被抓住（去掉 A≠B 过滤的对照实现应当是红的）")

    # ── `§M1`：视图层缩够了绿；视图层**不缩**（`|Q₀| == |U₀|`，离散）红 ────
    ok_levels = [Level(spec=CONTROL_SPEC, q=(frozenset({"a", "b"}), frozenset({"c", "d"})),
                       shrink=0.5, n_cand=1),
                 Level(spec=CONTROL_SPEC, q=(frozenset({"a", "b", "c", "d"}),),
                       shrink=1.0, n_cand=1, stopped="M1 收缩比")]
    # ⚠️ 「不缩」= **离散**（4 块 / 4 元素 ⇒ `shrink = 1.0`），
    #    **不是** `Q == P`（1 块 / 4 元素 ⇒ `shrink = 0.25`，那是**缩得最狠**）。
    #    前作核验 §3.2 把这两个极端写反了 —— 见 `test_injections.inj_m1` 的 ⚠️。
    flat = [Level(spec=CONTROL_SPEC, q=tuple(frozenset({x}) for x in ("a", "b", "c", "d")),
                  shrink=1.0, n_cand=1, stopped="M1 收缩比")]
    if judge(m1_shrink, ok_levels) is not Tri.YES:
        fails.append("§M1 视图层缩够了被判红")
    if judge(m1_shrink, flat) is not Tri.NO:
        fails.append("§M1 视图层没缩够（`|Q₀| == |U₀|`，离散）没被抓住")
    # ★ 「一层就够」是本设计的**产物**，所以「只有一层」本身不许是红 ——
    #   红的是「那一层没缩够」。这两件事在只数层数时长得一模一样。
    one_ok = [Level(spec=CONTROL_SPEC, q=(frozenset({"a", "b"}), frozenset({"c", "d"})),
                    shrink=0.5, n_cand=1, stopped="M2 层数")]
    if judge(m1_shrink, one_ok) is not Tri.YES:
        fails.append("§M1 只有一层但**缩够了**（`max_levels = 1` 那一侧）被判红 —— "
                     "「一层就够」是产物，不许当成红")

    # ── `§M2`：默认层数绿；把 `max_levels` 抬过声明值红 ───────────────────
    many = [replace(ok_levels[0], stopped="") for _ in range(MAX_LEVELS + 1)]
    if judge(m2_levels, ok_levels) is not Tri.YES:
        fails.append("§M2 正常层数被判红")
    if judge(m2_levels, many) is not Tri.NO:
        fails.append(f"§M2 折了 {len(many)} 层（> {MAX_LEVELS}）没被抓住")

    # ── `§M3`：几何递减绿；「每层不缩」红（**手造**，见 docstring） ────────
    flat_many = [Level(spec=ViewSpec(universe=("a", "b", "c", "d"),
                                     partition=(frozenset({"a", "b", "c", "d"}),),
                                     relation=frozenset()),
                       q=(frozenset({"a"}), frozenset({"b"}), frozenset({"c"}),
                          frozenset({"d"})),
                       shrink=1.0, n_cand=1) for _ in range(3)]
    if judge(m3_cost, ok_levels) is not Tri.YES:
        fails.append("§M3 几何递减被判红")
    if judge(m3_cost, flat_many) is not Tri.NO:
        fails.append("§M3 「每层不缩」没被抓住（Σ = 12 > 2·4 = 8）")

    # ── `§M4`：小 `n_cand` 绿；`Bell(11) > 200000` 红 ──────────────────────
    big = [replace(ok_levels[0], n_cand=678_570)]      # `Bell(11)`
    if judge(m4_verifiable, ok_levels) is not Tri.YES:
        fails.append("§M4 小 `n_cand` 被判红")
    if judge(m4_verifiable, big) is not Tri.NO:
        fails.append("§M4 `Bell(11)=678570 > 200000` 没被抓住")

    # ── `§M5`：合法停因绿；停因为空（跑到不动点）红 ────────────────────────
    #    ⚠️ 旧的第三例（`flat`：只折一层 ⇒ 红）已删 —— 那条判的是 `§M1` 的事，
    #       而且「一层就够」是本设计的产物。见 `m5_termination` 的 docstring。
    if judge(m5_termination, ok_levels) is not Tri.YES:
        fails.append("§M5 合法停因被判红")
    if judge(m5_termination, [replace(ok_levels[0], stopped=""),
                              replace(ok_levels[1], stopped="")]) is not Tri.NO:
        fails.append("§M5 末层停因为空（跑到不动点）没被抓住")
    if judge(m5_termination, one_ok) is not Tri.YES:
        fails.append("§M5 只有一层但停因合法被判红 —— 「一层就够」是产物，不许当成红")

    # ── `§M6`：账携带齐绿；丢了账红 ───────────────────────────────────────
    carried = [replace(ok_levels[0], lost=frozenset({"x"}), ledger=frozenset({"x"})),
               replace(ok_levels[1], lost=frozenset({"y"}),
                       ledger=frozenset({"x", "y"}))]
    dropped = [carried[0], replace(carried[1], ledger=frozenset())]
    if judge(m6_ledger, carried) is not Tri.YES:
        fails.append("§M6 账携带齐被判红")
    if judge(m6_ledger, dropped) is not Tri.NO:
        fails.append("§M6 「下层有账、上层账为空」没被抓住")
    return fails


# --- 真跑一遍：从 committed 规格出发折叠 --------------------------------------

def run_multilevel(spec: ViewSpec, kernel: object, cover: Callable[[object], frozenset[str]],
                   plugin: object, rep: Report,
                   *, q0: Sequence[frozenset[str]] | None = None,
                   next_partition: Callable[[int, tuple[str, ...]], Sequence] | None = None,
                   max_levels: int = MAX_LEVELS,
                   min_shrink_ratio: float = MIN_SHRINK_RATIO) -> list[Level]:
    """在**真内核**上跑一遍**视图层**（并按需继续折），判 `§M0`–`§M6`。

    ⚠️ **产物是第 0 层**（`levels[0]`）。`fold_until` 会再折下去直到刹车，
       但实测**真折 0 层**（`n_folds`）—— 后几层只是**机制**，不是交付物。

    `next_partition` 默认给 `P = {U}`（信息量最低那一档）——
    ⚠️ **那仍然是一次「人声明」**，只是由调用方在这里替人写下来。
       它必须能被调用方替换，否则尺度就被偷偷内生化（违反 `§K9`）。

    `q0` 默认 `csr(spec)`（生产口径）。⚠️ **留这个缝是为了注入**：
       `§M1` 的红形态是「**视图层**不缩」，而那要 `q0` = **离散划分**
       （`|Q₀| == |U₀|` ⇒ `shrink = 1.0`；⚠️ **不是** `q0 == spec.partition`，
       那是 1 块 ⇒ 缩得**最狠**）；
       `§M4` 的红形态要一个 11 块以上的 `q0`。生产路径**不传**它。

    ⚠️ `§M2` 判的是**声明的** `MAX_LEVELS`，不是这里的 `max_levels` 形参 ——
       形参是「调用方能不能把刹车拆掉」的那条缝，判据必须看得见它。
    """
    if next_partition is None:
        def next_partition(_k: int, universe: tuple[str, ...]) -> Sequence:
            return (list(universe),)

    def answer(s: ViewSpec, q: Sequence[frozenset[str]],
               down: dict[str, frozenset[str]]) -> frozenset[str]:
        """这一层**答得出**的原始项 = 各视图 `覆盖(合并(块内方向))` 的并。

        ⚠️ `down` 把第 `k` 层的视图 id 映回**第 0 层**的方向 id ——
           没有它，第 1 层起拿到的是 `V0` 这种名字，`kernel.direction` 会
           `KeyError`（实测踩到）。**折叠的含义就是「把块内的方向合起来」**，
           所以这里展平成第 0 层的方向再去 `merge`。

        ⚠️ **不走 `abstraction.build_views`**：那一层要 `View` 对象、要 `§A1` 的
           两栏分开存。这里只要「答得出哪些项」这一个数，多绕一圈没有信息增量。
           `merge` 抛异常时按「答不出」处理（返回空），与 `build_views` 的处置一致。
        """
        out: set[str] = set()
        for block in q:
            dirs = [kernel.direction(d)                          # type: ignore[attr-defined]
                    for vid in sorted(block) for d in sorted(down.get(vid, ()))]
            if not dirs:
                continue
            try:
                out |= set(cover(plugin.merge(dirs)))            # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001 - payload 由插件产出
                continue
        return frozenset(out)

    levels = fold_until(spec, q0 if q0 is not None else coarsest_stable_refinement(spec),
                        next_partition, answer,
                        max_levels=max_levels, min_shrink_ratio=min_shrink_ratio)
    m0_no_self_loop(levels, rep)
    m1_shrink(levels, rep, min_ratio=min_shrink_ratio)
    # ⚠️ `§M2` 用**声明的** `MAX_LEVELS` 判，不用形参 —— 见 docstring。
    m2_levels(levels, rep, max_levels=MAX_LEVELS)
    m3_cost(levels, rep)
    m4_verifiable(levels, rep)
    m5_termination(levels, rep)
    m6_ledger(levels, rep)
    return levels


def n_folds(levels: Sequence[Level]) -> int:
    """**真折层数** —— 视图层（`L0`）与**末层**（终止层）都**不算**。

        levels = [L0, L1, ..., L_{m-1}]
                 ↑    └──────┬──────┘   ↑
              视图层      真折的层     终止层

    ⚠️ **为什么必须把它单独数出来**：`len(levels) == 2` 这件事**同时**对应两种
       完全不同的情形 —— 「折了一层」与「一层都没折、第 1 层立刻撞阈值」。
       两者在只印 `len(levels)` 的时候**长得一模一样**（都是「2 层」），
       而后者正是本项目最忌的「空转与通过长得一模一样」。

    ⚠️ **`len(levels) == 1` 时返回 0，而且那时没有终止层** —— 那一层就是
       视图层自己（它自己撞了刹车）。旧的标题把它写成
       「第 0 层 + 真折 0 层 + **1 个终止层**」，等于把同一个东西数了两遍。
       见 `render_levels`。

    实测（2026-10-08，36 项 / `openalex-small` / `openalex-n50`…，三个方向）：
       **真折层数恒为 0** —— 末层永远是第 1 层。见 `MEASUREMENTS.md` 结果二十。
       ⇒ 它是**读数**，不是判据：拿它当判据会**基线就红**，
          而「基线就红的判据过不了注入验证」（本仓库的一条纪律）。
       ⇒ 它只进 `render_levels` 的标题，**不进退出码**。
    """
    return max(0, len(levels) - 2)


def render_levels(levels: Sequence[Level]) -> str:
    """把**产物**与**折叠读数**分开印 —— 产物是第 0 层（视图层），折叠是机制。

    ⚠️ **第一行必须先说这一层是什么**（「模糊掌握」层）。只印层数的话，
       「这东西做出来干什么用的」在输出里**读不出来** —— 而那正是这一层
       后引入的原因（抽象层 §0.0）。

    ⚠️ **第二行不许只写「N 层」**：那会让「真折 0 层」与「真折 1 层」共用一行
       （见 `n_folds`）。真折层数必须与总层数**分开印**。
    """
    n = n_folds(levels)
    l0 = levels[0]
    head = (f"视图层（L0）：|U| = {len(l0.spec.universe)} ⇒ |Q| = {len(l0.q)}"
            f"（收缩比 {l0.shrink:.3f}）—— **这就是「模糊掌握」层**")
    if len(levels) == 1:
        # ⚠️ 停因**照抄 `l0.stopped`**，不许在这里推断「是不是没缩够」——
        #    只有一层也可能是**层数上限先到**（`max_levels = 1`），
        #    而那时写「撞了 `§M1`」是**假红**：两者在只印层数时长得一模一样。
        fold = (f"折叠：只跑了 1 层（停因 {l0.stopped or '—'}）"
                f" ⇒ **真折 {n} 层** —— 视图层自己就是终止层")
    else:
        fold = (f"折叠：共 {len(levels)} 层 = 视图层（L0）+ 中间 {n} 层"
                f" + 终止层（L{len(levels) - 1}）⇒ **真折 {n} 层**")
    lines = [head, fold]
    for i, lv in enumerate(levels):
        lines.append(
            f"    L{i}  |U| = {len(lv.spec.universe):>3}  |Q| = {len(lv.q):>3}"
            f"  收缩比 {lv.shrink:.3f}  n_cand {lv.n_cand:>8}"
            f"  答得出 {len(lv.ans):>4}  丢 {len(lv.lost):>4}  账 {len(lv.ledger):>4}"
            + (f"  停因 {lv.stopped}" if lv.stopped else ""))
    return "\n".join(lines)


def skip_all(rep: Report, why: str) -> None:
    """七条一起跳过 —— 与 `run_checks._skip_views` 同一个理由：理由只写一遍。

    ⚠️ 标题取自 `TITLES`（**与真跑时同一个来源**）。分开写两份的话，
       「跳过时印的标题」与「真跑时印的标题」会各自漂移 —— 而两边都只是字符串，
       漂移了**没有任何东西看得出来**。`test_multilevel` ⑥ 段逐字对照两边。
    """
    for code in MULTILEVEL_CODES:
        rep.add(code, TITLES[code], Tri.UNEXPANDED, why)
