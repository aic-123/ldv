"""视图层的内核 —— **最粗稳定细化**（流程 E，`docs/分层方向视图-抽象层.md` §3）。

    python -m ldv.run_checks            # 判据在 `(视图)` 那一组里

本模块是**纯算法**：不认识内核、不认识插件、不 import 本包的任何别的东西。
视图层要的那个对象在这里定义，判据在 `checks/abstraction.py`。

---

## 它解决的那一个问题

「把已建成的结构重新编码成几张视图」听起来像**摘要**，而摘要文献的默认形状是
**最小化一个目标函数**（MDL 代价 / 重建误差 / 社区保持度）。那条路在本设计里
**走不通** —— 一旦引入标量分，系统即在实质上充当裁判（E-1 / §4.3 / `B12`）。

出路不是「不要视图」，是**换一个对象**：不问「哪套视图更好」，而问
「给定一组外生约束，**有没有一个不用挑的视图集合**」。

**有，而且是唯一的。** Paige & Tarjan 1986（Princeton TR-038）§1 给的
*relational coarsest partition problem* 是：

> given a partition `P` of a set `U` and a relation `E` on `U`, find the **coarsest
> refinement `Q` of `P`** such that for each pair of blocks `B₁, B₂` of `Q`,
> either `B₁ ⊆ E⁻¹(B₂)` or `B₁ ∩ E⁻¹(B₂) = φ`.

（⚠️ 该文**没有文字层**，上面这段是**读渲染图**取到的：PDF p.4 = 论文 p.2。
取法与页码见 `docs/prior-art/README.md`；引用它的地方必须显式声明不经过
`verify_quotes`。见 `docs/分层方向视图-抽象层.md` §3。）

**最粗稳定细化唯一** ⇒ 视图集合不用挑 ⇒ **不需要目标函数**。
⇒ 这一条把「不许打分」从一条**纪律**（要靠 `B12` 扫源码守）变成一条**定理**
（唯一性来自划分的格结构，不来自任何优化）。

---

## 三条硬边界落在这个模块上的样子

    E-1 不许有全局目标函数   ⇒ 本模块**没有** `cost` / `score` / `objective` 参数，
                               也没有任何跨视图可比的数。`coarsest_stable_refinement`
                               是**确定性**的：同一个 `(P, E)` 只可能有一个答案。
    E-2 不许排序 / 打分 / 匹配 ⇒ 返回的是**块的无序集合**（按最小元素规范化排序，
                               只为可复现 §8.2，不是「按好坏排」）
    E-3 不许改外生项          ⇒ 本模块**不持有**任何东西：`ViewSpec` 是入参，
                               `Q` 是返回值。没有全局状态、没有缓存、不写盘。

---

## ⚠️ 为什么 `P` 和 `E` 必须**外生**

§3 的两条边界，逐字：

    ① 它是 `P` 的**细化**，不是任意划分 ⇒ 视图只会比初始划分更细，
       不会把两个初始块合成一个 ⇒ 初始划分是**外生的**（§K9）：
       由人声明「哪些方向同属一组」。
    ② 「稳定」是**对关系 `E`** 说的。`E` 选什么，视图就跟着变 ——
       所以 `E` 也必须外生声明，**不许由使用记录驱动**（§K9；`B14`）。

⇒ 本模块**没有默认值**。`ViewSpec` 三个字段全是必填，少一个就 `TypeError`。
   「猜一个 `P`」不是「先跑起来再说」，是**替人做 §K9 的决定**
   （`docs/分层方向视图-抽象层.md` §10 停止条件第 1 条）。

---

## 复杂度与「够不够用」

`n = |U|`，`m = |E|`。细化那一层是朴素写法：每轮对每个块求 `E⁻¹(B)`（O(m)）
并按它切一遍当前划分，直到不动点。

    ⚠️ **不引 Paige–Tarjan 的 O(m log n)** —— 理由是本项目一贯的那一条：
       checker 要**简单**（McConnell 等 2011 §5.1 的 Simplicity）。
       这里 `n` 是**一层里的方向数**，实测 36 项语料上单层最多几十个；
       而「简单」买到的是「读得懂、能注入、能对已知答案」。
       ⇒ 这是**有意的**取舍，不是没想到。要换算法时，`stable()` 与
          `coarser_stable_exists()` 这两个**独立**的 oracle 不用动 ——
          它们就是换算法时的对照。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import partial
from itertools import islice
from typing import Callable, Iterable, Iterator, Sequence

__all__ = [
    "ViewSpec",
    "coarsest_stable_refinement",
    "propagate",
    "propagate_naive",
    "extend_q",
    "restrict_spec",
    "stable",
    "refines",
    "refines_partition",
    "coarser_stable_exists",
    "partition_of",
    "reach_of",
    "view_parts",
    "MAX_COARSENING_CANDIDATES",
]

#: 暴力搜「更粗的稳定划分」时，枚举上限 —— 超了就**报跳过**（不是报过）。
#: Bell 数长得快（B(8)=4140、B(10)=115975），所以这里必须有个闸。
#: ⚠️ **闸门本身也要印出来**：「搜不完所以跳过」与「搜完了没有」必须分得开。
MAX_COARSENING_CANDIDATES = 200_000


# --- 外生项 -------------------------------------------------------------------

@dataclass(frozen=True)
class ViewSpec:
    """外生的三件东西：`U` / `P` / `E`。**全部必填，没有默认值**（见模块开头）。

        `universe`    `U` —— 参与这一层视图的**方向 id**（有序元组，只为可复现）
        `partition`   `P` —— `U` 的一个划分（「哪些方向同属一组」，人声明）
        `relation`    `E` —— `U` 上的二元关系（「稳定」是对它说的，人声明）

    `__post_init__` 做**三条**检查，一条都不省：

        `P` 真的是 `U` 的划分      不重、不漏、都在 `U` 里
        `E` 的两端都在 `U` 里      落在 `U` 外的边是**声明错了**，不是「多给了」
        `U` 里没有重复            重复会让「块」与「集合」两套计数对不上

    ⚠️ 三条都**抛异常**而不是「自动修正」。自动补一个缺失的元素进 `P`
       会让「声明错了」与「声明对了」长得一模一样 —— 正是本仓库一直在防的形状。
    """

    universe: tuple[str, ...]
    partition: tuple[frozenset[str], ...]
    relation: frozenset[tuple[str, str]]

    def __post_init__(self) -> None:
        u = set(self.universe)
        if len(u) != len(self.universe):
            dup = sorted({x for x in self.universe if self.universe.count(x) > 1})
            raise ValueError(f"`universe` 里有重复元素：{dup[:5]}")
        seen: set[str] = set()
        for b in self.partition:
            if not b:
                raise ValueError("`partition` 里有空块 —— 空块不是划分的一部分")
            if seen & b:
                raise ValueError(f"`partition` 的块互相重叠：{sorted(seen & b)[:5]}")
            seen |= b
        if seen != u:
            miss = sorted(u - seen)[:5]
            extra = sorted(seen - u)[:5]
            raise ValueError(
                f"`partition` 不是 `universe` 的划分：缺 {miss}、多 {extra}")
        bad = sorted({x for pair in self.relation for x in pair} - u)[:5]
        if bad:
            raise ValueError(f"`relation` 的端点不在 `universe` 里：{bad}")

    def as_dict(self) -> dict[str, object]:
        """给落盘 / 报告用 —— **块与边都排序**，让同一个 spec 只有一个写法（§8.2）。"""
        return {
            "universe": list(self.universe),
            "partition": [sorted(b) for b in sorted(self.partition, key=min)],
            "relation": sorted(self.relation),
        }


def partition_of(blocks: Iterable[Iterable[str]]) -> tuple[frozenset[str], ...]:
    """把「一堆块」规范化成**可复现**的元组：块按最小元素排、块内元素排序。

    ⚠️ **排序不是为了「按好坏排」**（E-2 禁排序），只是为了让同一个划分
       只有一种写法 —— §8.2 的可复现性要求同一个输入产出**逐字相同**的元组。
       所以这里排的是**元素 id**，不是任何度量。
    """
    out = [frozenset(b) for b in blocks if b]
    return tuple(sorted(out, key=lambda b: (min(b), len(b))))


# --- 核心算法 -----------------------------------------------------------------

def _preimage(relation: frozenset[tuple[str, str]],
              block: frozenset[str]) -> frozenset[str]:
    """`E⁻¹(block) = {x : ∃y ∈ block, (x, y) ∈ E}`。

    ⚠️ **方向不要写反。** Paige–Tarjan 的 `E⁻¹(B₂)` 是「**能走到** `B₂` 的元素」，
       不是「`B₂` 能走到的」。写成后者会得到一个**看着也在跑、答案是另一个划分**
       的实现 —— 而两者在输出上只有一个块的区别，不逐条对**看不出来**。
       注入 `A2`（把 `Q` 粗化一格）与 `A3` 的暴力 oracle 都依赖这个方向是对的。
    """
    return frozenset(x for (x, y) in relation if y in block)


def _refine(blocks: list[frozenset[str]],
            relation: frozenset[tuple[str, str]]) -> list[frozenset[str]]:
    """把 `blocks` 对 `relation` 细到**不动点** —— `coarsest_stable_refinement`
    与 `propagate` **共用的那一层循环**。

        反复：对每个块 `B`，把每个块 `S` 切成 `S ∩ E⁻¹(B)` 与 `S \\ E⁻¹(B)`
              两半都非空才切
        到「一轮下来一个块都没被切开」为止

    终止：每次切**严格增加**块数，块数 ≤ `|U|`。

    ⚠️ **共用这一层不影响「两个 oracle 不共享盲点」那条纪律。**
       共享的是**被判对象**的构造（本来就只有一条路），不是 oracle：
       `stable()` 逐块对验定义、`coarser_stable_exists()` 暴力枚举粗化 ——
       两者都不走这里一行。
    """
    while True:
        changed = False
        # 快照：本轮的 splitter 是**这一轮开始时**的块。新切出来的块
        # 下一轮才当 splitter —— 外层 `while` 会一直跑到它们都试过为止。
        for splitter in list(blocks):
            inv = _preimage(relation, splitter)
            new: list[frozenset[str]] = []
            for s in blocks:
                inside = s & inv
                if inside and inside != s:
                    new.append(inside)
                    new.append(s - inv)
                    changed = True
                else:
                    new.append(s)
            blocks = new
        if not changed:
            return blocks


def coarsest_stable_refinement(
    spec: ViewSpec,
) -> tuple[frozenset[str], ...]:
    """`P` 对 `E` 的**最粗稳定细化** `Q`。**唯一**（§3）。

    正确性：任何**稳定**的 `P` 的细化 `R` 都被 `_refine` 的每一步保持
    （`R` 的块要么整个落在 `E⁻¹(B)` 里，要么整个在外面），
    所以极限细化每一个稳定细化 ⇒ 它就是**最粗**的那个。∎

    ⚠️ **确定性**：切分的**顺序**会影响中间划分，但**不影响**极限 ——
       这正是「唯一性」那条定理的内容。为了让输出**逐字可复现**（§8.2），
       返回值按 `partition_of` 规范化（排元素 id，不排任何度量）。
    """
    return partition_of(_refine(list(spec.partition), spec.relation))


# --- `E′` 增量传播（§7） -------------------------------------------------------
#
# 视图不是一次算完的：结构一动（流程 B），视图要么重算、要么**传播**。
# 设计稿 §7 把这条定成 `E′`，并且**只定接口不定实现** —— 这里把它落下来。
#
# ⚠️ **本设计特有的那条约束**（§7 逐字）：`§10.2 B` 已裁定「增量 ≡ 全量」**不成立**，
#    且**不成立不破坏任何一条检查** ⇒ 传播**不许**声称「传播后的视图 ≡ 重算的视图」。
#    能声称的只有「传播后的视图仍**稳定**、仍是 `P′` 的**细化**」。
#    而「更细」这件事不是观察，是**定理** —— 见 `propagate` 的 docstring。

def restrict_spec(spec: ViewSpec, drop: frozenset[str]) -> ViewSpec:
    """把 `spec` 投影到 `U ∖ drop` 上 —— **外生项照原样投影，不重新声明**。

    `P` 的每个块减掉 `drop` 里落在它里面的元素，空掉的块去掉；
    `E` 只留两端都还在的边。

    ⚠️ 这个函数**不做**任何「补一个元素进去」的事 —— 那会让「声明错了」
       与「声明对了」长得一模一样（`ViewSpec.__post_init__` 的 ⚠️ 同一条）。
       投影之后仍然要过 `ViewSpec.__post_init__` 那三条检查。
    """
    keep = frozenset(spec.universe) - drop
    if not keep:
        raise ValueError("投影之后 `universe` 空了 —— 那不是一份声明")
    part = tuple(b & keep for b in spec.partition if b & keep)
    rel = frozenset((x, y) for (x, y) in spec.relation if x in keep and y in keep)
    return ViewSpec(universe=tuple(d for d in spec.universe if d in keep),
                    partition=part, relation=rel)


def refines_partition(
    coarse: Sequence[frozenset[str]],
    fine: Sequence[frozenset[str]],
) -> bool:
    """`fine` 是不是 `coarse` 的**细化** —— 每个 `coarse` 块恰好被若干 `fine` 块盖住。

    ⚠️ **「恰好」是逐字的**：既要盖满（不许漏）、又要不越界（不许把两个
       `coarse` 块并进同一个 `fine` 块）。只查「盖满」会让**合并**蒙混过关 ——
       而合并正是 `E′` 最要防的那一种（它是信息丢失，`§K8` 的假阴那一侧）。
    """
    for b in coarse:
        cover = [f for f in fine if f & b]
        merged = frozenset().union(*cover) if cover else frozenset()
        if merged != b:
            return False
    return True


def extend_q(
    old_q: Sequence[frozenset[str]],
    spec: ViewSpec,
) -> tuple[frozenset[str], ...]:
    """`Q_ext` = 旧 `Q` ∪ { `spec.universe` 里旧 `Q` **没覆盖到**的方向，各自成块 }。

    ⚠️ **`Q_ext` 的定义是 `E′` 的一部分，不是实现细节。** `§A7` 的判据 ②
       （传播只许**细分**）判的就是「结果细化 `Q_ext`」—— 所以 `Q_ext` 必须
       由**一个**函数定义，判据与实现**共用**它，否则两边各写一份、
       写着写着就不一样了（`test_injections` 第 0 条防的是同一类事）。
    """
    known = frozenset().union(*old_q) if old_q else frozenset()
    return partition_of(list(old_q) + [frozenset({d}) for d in spec.universe
                                        if d not in known])


def propagate(
    old_q: Sequence[frozenset[str]],
    spec: ViewSpec,
) -> tuple[frozenset[str], ...]:
    """`E′`：从**旧划分** `old_q` 出发，把 `spec.universe` 里的**新方向**
    （`old_q` 没覆盖到的那些）各自成块并进去，再跑到不动点。

    ## ★ 传播的结果**不一定**等于重算的结果 —— 而且这是**定理**，不是观察

        `Q_ext` = `old_q` ∪ { 每个新方向一块 }
        `Q′`    = `csr(Q_ext)`        ← 本函数
        `Q*`    = `csr(P′)`           ← 重算（`coarsest_stable_refinement`）

    `old_q` 是 `P` 的细化 ⇒ `Q_ext` 是 `P′` 的细化（新方向按 `P′` 落位）。
    `Q′` 稳定且细化 `P′` ⇒ 由**最粗**的定义，`Q′` 细化 `Q*`：

        Q′ ⊑ Q*        传播出来的只会**更细**（或相等），不会更粗

    ⚠️ **所以「传播后 == 重算后」是一句会被实测打脸的话**（§7 逐字），
       而**打脸不破坏任何一条检查** —— `§10.2 B` 已裁定。
       ⇒ 这条**只报不判**：块数差进输出，不进退出码（`abstraction.a7_reading`）。

    ⚠️ **反向也成立，而且它才是判据**：`Q′` 细化 `old_q`（**传播只细分，不合并**）。
       合并 = 信息丢失 ⇒ `§K8` 的假阴那一侧 ⇒ 那是**红**，不是读数。
       `refines_partition` 逐块查的就是这一条。
    """
    return partition_of(_refine(list(extend_q(old_q, spec)), spec.relation))


def propagate_naive(
    old_q: Sequence[frozenset[str]],
    spec: ViewSpec,
) -> tuple[frozenset[str], ...]:
    """`E′` 的**对照**实现 —— 把新方向挂到**父方向所在的那一块**上，**不重跑不动点**。

    ⚠️ **这是对照，不是备选方案。** 它复现的是「传播 = 把新东西挂上去」这个
       最自然的错觉。错觉的代价在这一条上：新边 `(父, 新)` 把 `父` 塞进了
       `E⁻¹({新})`，于是 `{新}` 那一块**跨在** `E⁻¹({新})` 的内外
       （`父` 在里面、`父` 的同块兄弟在外面）⇒ 划分**不再稳定**。

    ⇒ `§A7` 的注入就是它：真 `propagate` ⇒ 过，`propagate_naive` ⇒ 红。
       （`abstraction.a7_known_answer`。）
    """
    blocks = [set(b) for b in old_q]
    owner = {d: i for i, b in enumerate(blocks) for d in b}
    for d in spec.universe:
        if d in owner:
            continue
        parent = next((x for (x, y) in sorted(spec.relation) if y == d), None)
        home = owner.get(parent) if parent is not None else None
        if home is None:
            owner[d] = len(blocks)
            blocks.append({d})
        else:
            blocks[home].add(d)
            owner[d] = home
    return partition_of(blocks)


# --- 两个**独立**的 oracle -----------------------------------------------------
#
# 判据要能红，就必须有一个**与被判对象不同路**的参照物（本仓库的老纪律：
# 拿被测对象自己的运算记录当见证不算 certifying，McConnell 等 2011 §5.5）。
#
# `stable()` 与 `coarser_stable_exists()` 是**两个不同的问题**：
#
#     stable()                  这个划分**稳定吗**       —— 逐个块对验定义，O(k²)
#     coarser_stable_exists()   还有**更粗的**稳定划分吗 —— 暴力枚举粗化，与
#                               `coarsest_stable_refinement` **不共用代码**
#
# ⇒ `§A3` 用的是后者。用「再跑一遍 `coarsest_stable_refinement` 比一比」
#   当 oracle 会与被判对象**共享盲点**（两边同时错、判据永远绿）——
#   那正是 `false-green` 形状 3。

def stable(spec: ViewSpec, q: Sequence[frozenset[str]]) -> tuple[bool, list[str]]:
    """`Q` 对 `E` 稳定吗 —— 逐字按定义验，返回 `(稳定?, 反例)`。

        对每对块 `B₁, B₂`：`B₁ ⊆ E⁻¹(B₂)` 或 `B₁ ∩ E⁻¹(B₂) = φ`

    ⇒ 「存在一块**跨**在 `E⁻¹(B₂)` 内外」就是反例。返回的反例写成
      「`B₁` 有 x 在 `E⁻¹(B₂)` 里、y 不在」—— 这样它**指得出**是哪两个元素。
    """
    inv = {b: _preimage(spec.relation, b) for b in q}
    bad: list[str] = []
    for b1 in q:
        for b2 in q:
            inter = b1 & inv[b2]
            if inter and inter != b1:
                inside = sorted(inter)[:1]
                outside = sorted(b1 - inv[b2])[:1]
                bad.append(f"{sorted(b1)[:3]} 里 {inside} 走得到 {sorted(b2)[:3]}，"
                           f"而 {outside} 走不到")
    return (not bad), bad


def refines(spec: ViewSpec, q: Sequence[frozenset[str]]) -> bool:
    """`Q` 是不是 `P` 的**细化** —— 每块 `Q` 都落在**某一个** `P` 块里。

    ⚠️ 这一条**不是形式**：§3 边界 ① 说视图**只会比初始划分更细**，
       不会把两个初始块合成一个。少了它，一个「把两个初始块并起来」的
       `Q` 照样可以**稳定**（见 `coarser_stable_exists` 里那段），
       于是 `§A2` 绿而它根本不是 `P` 的细化。
    """
    for b in q:
        if not any(b <= p for p in spec.partition):
            return False
    return True


def _set_partitions(n: int) -> Iterator[tuple[tuple[int, ...], ...]]:
    """枚举 `0..n-1` 的**所有集合划分**，每个划分是一串「组」，每组是一串下标。

    `n` 个元素 ⇒ 产出 `Bell(n)` 个划分。⚠️ **不是** `2^n`：
       「把块合并成哪些组」是划分问题，不是子集问题 —— 写成子集枚举会
       把同一个粗化数很多遍（`{A,B}` 与 `{B,A}`），而**重复枚举看不出来**
       （结论一样），只看得见它慢。

    ⚠️ **组号必须留下来。** 早先的写法产出「每个元素一个组号」然后只按下标重排，
       于是**两个不同的划分渲染成同一个东西**（都退化成恒等划分）——
       后果是「更粗的稳定划分」永远搜不到，`§A3` 变成一条**永远绿**的判据。
       实测踩到：`E = ∅`、`P = {U}` 上明明没有更粗的稳定划分，却报「有」。
    """
    if n <= 0:
        yield ()
        return
    groups: list[list[int]] = [[0]]

    def rec(i: int) -> Iterator[tuple[tuple[int, ...], ...]]:
        if i == n:
            yield tuple(tuple(g) for g in groups)
            return
        for g in groups:              # 放进一个已有的组
            g.append(i)
            yield from rec(i + 1)
            g.pop()
        groups.append([i])            # 或者自己开一个新组
        yield from rec(i + 1)
        groups.pop()

    yield from rec(1)


def _bell(n: int) -> int:
    """`Bell(n)` —— `n` 个元素的**集合划分数**。只**数**，不建。

        B(0)=1, B(n+1) = Σ_{k=0..n} C(n,k)·B(k)

    ⚠️ **为什么单列一个「只数不建」的函数**：枚举上限必须在**动手之前**就判。
       **先物化、再拿物化出来的 `len` 去比上限**的写法在 `k = 25` 时
       **不是报跳过，是挂住** —— `Bell(25) ≈ 4.6e18`，那个 `list` 一辈子建不完
       （`outputs/_probe_cap.py`：20 秒超时、`timeout` 退出码 124）。

       「**搜不完**」与「**还在搜**」在只看输出的时候长得一模一样 ——
       这正是本仓库一直在防的形状，只不过这一次它出现在**闸门自己**身上。
       所以闸门的判据不能是「跑完再比」，只能是「**先算规模再跑**」。
       `test_views` ⑥ 段守着这一条（大组 ⇒ 毫秒级报 `None`，且不许一律跳过）。
    """
    row = [1]
    for i in range(n):
        row.append(sum(math.comb(i, k) * row[k] for k in range(i + 1)))
    return row[n]


def _coarsenings(
    bs: Sequence[frozenset[str]],
) -> Iterator[tuple[frozenset[str], ...]]:
    """`bs` 的全部**粗化** —— 每个粗化是一串「合并后的块」。

    ⚠️ 按**组号**合并：只按下标重排（丢掉组号）会让每个划分都退化成恒等 ——
       见 `_set_partitions` 的 ⚠️。
    """
    for grp in _set_partitions(len(bs)):
        yield tuple(frozenset().union(*[bs[i] for i in g]) for g in grp)


class _LazyPool:
    """**可重复迭代、但不物化**的池子 —— 每次 `iter()` 现算一遍。

    ⚠️ 两个性质**缺一不可**，而 `list` 与生成器各只有一半：

        生成器   不物化，但**一次性** —— 笛卡尔积的外层每换一个元素都要
                 **重头**取内层池子，第二次取就空了
        `list`   可重复迭代，但**物化** —— 那就又绕回「闸门拦不住内存」

    ⇒ 要的是「可重复迭代 **且** 不物化」，所以这里自己写一个。
       （`_product` 的 docstring 早就在说这件事，只是原先的调用方传的是 `list`。）
    """

    def __init__(self, make: Callable[[], Iterator]) -> None:
        self._make = make

    def __iter__(self) -> Iterator:
        return self._make()


def coarser_stable_exists(
    spec: ViewSpec,
    q: Sequence[frozenset[str]],
    cap: int = MAX_COARSENING_CANDIDATES,
) -> tuple[bool | None, str, int]:
    """**存在**比 `Q` 更粗的、`P` 的稳定细化吗 —— 暴力枚举。

    返回 `(有没有, 说明, 枚举了多少个候选)`；`None` = **搜不完**（超 `cap`）。

    ## 为什么是暴力，而不是「再算一遍最粗稳定细化比一比」

    后者与被判对象**共用同一段代码** ⇒ 两边**同时错**时判据照样绿
    （`false-green` 形状 3「共享盲点」）。暴力枚举走的是**另一条路**：
    它不依赖 `coarsest_stable_refinement` 的任何一行。

    ## 枚举的是什么

    `Q` 比 `Q*` 粗（`Q*` = 最粗稳定细化）⟺ `Q*` 由 `Q` **合并若干块**得到。
    而合并**只允许发生在同一个 `P` 块内部**（否则就不再是 `P` 的细化了）。

    ⇒ 按 `P` 块分组，对每一组里的 `Q` 块枚举**集合划分**（`Bell(k)` 个），
      笛卡尔积就是全部候选。**恒等划分**（每组都是单块）就是 `Q` 自己，
      要排除掉 —— 否则「`Q` 稳定」会把自己报成「存在更粗的稳定划分」。

    ## ⚠️ 顺序：**先算规模，再动手**（这条是判据的一部分）

    规模 `n_cand = ∏ Bell(组内 Q 块数)` 用 `_bell` **只数不建**地先算出来，
    超 `cap` 立刻返回 `None`；过了这一关才开始枚举，而且枚举走 `_LazyPool`
    （可重复迭代、不物化）。

    ⇒ 「搜不完 ⇒ 报跳过」这句话**在时间与内存上都成立**。
      「先物化、再比 `cap`」的写法在**大组上挂住**（不是跳过）—— 见 `_bell` 的 ⚠️。

    ⚠️ **`cap` 撞上时返回 `None`，调用方必须报「跳过」而不是「过」。**
       搜不完与搜完了没有，在只看 `bool` 的时候**长得一模一样**。
    """
    groups: dict[frozenset[str], list[frozenset[str]]] = {p: [] for p in spec.partition}
    for b in q:
        home = next((p for p in spec.partition if b <= p), None)
        if home is None:
            # `Q` 不是 `P` 的细化 ⇒ 「更粗的稳定划分」这个问题**问不出来**。
            # 说清是这一条，不要退化成「没有」—— 那是两件事。
            return None, "`Q` 不是 `P` 的细化（有块跨出了 `P` 的块）⇒ 搜不了", 0
        groups[home].append(b)

    # ★ 先算规模。⚠️ 这个计数器**不叫** `total` —— `B12`（`checks/source.py`）按
    #    **名字**扫内核源码，`total` 在它的禁用表里（`score|objective|utility|global|importance|total`）。
    #    这里数的是「**枚举了多少个候选粗化**」，与跨方向评分毫无关系 ⇒ 那是**假红**。
    #    处置按纪律：**改代码不改扫描器**（扫描器是唯一守 E-1 的东西，放宽它才是真损失）。
    n_cand = 1
    for p in spec.partition:
        n_cand *= _bell(len(groups[p]))
        if n_cand > cap:
            return None, f"候选数 {n_cand} 超过上限 {cap} ⇒ **没搜完**", n_cand

    pools = [_LazyPool(partial(_coarsenings, sorted(groups[p], key=min)))
             for p in spec.partition]

    q_norm = partition_of(q)
    seen = 0
    for combo in _product(pools):
        seen += 1
        # `combo` 是「每个 `P` 块一个粗化」，每个粗化是一串**合并后的块**。
        # ⚠️ 恒等判定要拿**规范化后的块集合**比，不能去数「组里有几个块」——
        #    后者会把 `frozenset({'a'})` 当成「一个元素、长度为 1」，
        #    于是**每一个**候选都被当成恒等跳过，结论正好反了。
        #    （实测踩到：`E = ∅` 上 `Q = {U}` 明明是**没有**更粗的稳定划分，
        #      却报「有」。）
        merged = partition_of([b for part in combo for b in part])
        if merged == q_norm:
            continue                      # 恒等 = `Q` 自己（它当然稳定，但不是「更粗的」）
        if not refines(spec, merged):
            continue
        ok, _ = stable(spec, merged)
        if ok:
            return True, (f"把 {len(q)} 块并成 {len(merged)} 块之后**仍然稳定**："
                          f"{[sorted(b)[:3] for b in merged[:3]]} …"), seen
    return False, f"枚举了 {seen} 个粗化，没有一个稳定", seen


def _product(pools: Sequence[Iterable]) -> Iterator[tuple]:
    """笛卡尔积 —— 手写一份，避免 `itertools.product` 在超大池上先物化。

    `itertools.product` 会**先把每个池子转成 tuple**（它就是这么写的），
    于是「`cap` 拦住了枚举」这件事在内存上**没有**被拦住。这里逐层递归，
    池子按需取。

    ⚠️ **`pools[i]` 必须可重复迭代**：外层每换一个元素都要重头取内层池子
       （笛卡尔积的定义）。传一次性生成器会在第二次取时拿到空池子 ——
       而「少枚举了一些」与「枚举完了」在结论上**看不出来**。
       ⇒ 不物化又可重复迭代的池子见 `_LazyPool`。
    """
    if not pools:
        yield ()
        return

    def rec(i: int, acc: tuple) -> Iterator[tuple]:
        if i == len(pools):
            yield acc
            return
        for x in islice(pools[i], 0, None):
            yield from rec(i + 1, acc + (x,))

    yield from rec(0, ())


# --- 视图的「下界」与「部件」 --------------------------------------------------
#
# `§A4`（类别不许说错）要问的是「这张视图的读数能不能**只从下层算出来**」。
# 那需要先定义「下层」是什么。这里两个函数就是那个定义。
#
# ⚠️ **不许把「下层」直接定义成「块 ⊆ reach(本块) 的那些块」。** 视图划分
#    **会横跨树**：实测（36 项语料、`P = {U}`）块 `{D10,D15,D22,D3}` 里 `D22`
#    不在 `D2` 的子树里 ⇒ 那个关系**不构成嵌套**，按它取「极大子块」会漏掉
#    横跨的那些 ⇒ 恒等式根本不成立（实测 `|block| = 1` 而 `Σ|子块| = 24`）。
#    ⇒ 改成**截断成部件**：部件恰好**划分** `reach`，恒等式按构造成立。


def reach_of(
    block: Iterable[str],
    subtree_of: Callable[[str], frozenset[str]],
) -> frozenset[str]:
    """一块方向的**下界** `reach(B) = ∪{ 子树(d) : d ∈ B }`。

    `subtree_of` 由调用方给（内核侧是「`d` 及其全部后代」）。
    ⚠️ 本模块**不认识内核**，所以树是**参数**，不是 import。
    """
    out: set[str] = set()
    for d in block:
        out |= subtree_of(d)
    return frozenset(out)


def view_parts(
    q: Sequence[frozenset[str]],
    subtree_of: Callable[[str], frozenset[str]],
) -> dict[frozenset[str], tuple[frozenset[str], ...]]:
    """每块 `B` 的**部件**：`B` 自己 + 其余块与 `reach(B)` 的交（非空的那些）。

    这些部件**恰好划分** `reach(B)` —— 不是巧合，是「块两两不交、并集是 `U`」
    的直接推论（`ViewSpec` 已经保证）。所以

        reach(B) = B ⊎ p₁ ⊎ p₂ ⊎ …

    是**恒等式**，任何「在集合上可下推」的读数都必须满足它。

    ⚠️ 于是这条恒等式**不能**当成判据用（恒真的东西不提供信息）——
       `§A4` 判的是**声称的那个合成函数对不对**：声称 `distributive` 就要给出
       `G`，实测 `读数(reach) == G(读数(B), 读数(p₁), …)`。`G` 给错了就红。
       这正是设计稿 §9 那句「声称 distributive 但**实测 ≠** ⇒ 红」。
    """
    out: dict[frozenset[str], tuple[frozenset[str], ...]] = {}
    for b in q:
        reach = reach_of(b, subtree_of)
        parts = [b]
        for other in q:
            if other is b:
                continue
            cut = other & reach
            if cut:
                parts.append(cut)
        out[b] = tuple(parts)
    return out
