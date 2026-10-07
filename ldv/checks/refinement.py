"""B20 —— **语义变细守卫**（`§K2` 第二半的外生**语义**判据）。

    python -m ldv.run_checks            # 三个插件都跑（只在标签轴上判）

---

## 这条检查补的是哪一个洞

`§K2` 的字面是两句话：

    展开必须产出**下层表达不出的东西**，否则不建这一层
    └────────── 第二半 ──────────┘   └─ 第一半 ─┘

内核实现的是**第一半**（`劈开后一侧为空 ⇒ 不建`）。第二半是**语义**问题，
而**内核没有语义**（§4.1）—— 它手上只有 `§I3 代价`，而代价**允许假阳**。

已经有的一条外生检查是 `B17`，但它判的是**结构**等价：

    不可分 ⟺ 「互相可达」（`reach`）/「键集相同」（`keyset`）/「序列相同」（`sequence`）

那个等价关系是**从方向自己的谓词**复算出来的 —— 它说的是「这个方向**分不开**」，
**不是**「这两项**意思不同**」。所以 `B17` 仍然不含语义。

**要判「真的更细」，需要一个不来自本系统的语义。** 本模块用**语料的标注**当那个语义。

## 判据是什么

    语义增益(d) = H(标签 | 被劈的那部分) − Σ_k (|m_k|/|base|)·H(标签 | m_k)

    `语义增益 = 0`  ⟺  每个子的标签分布都与父**一模一样**
                    ⟺  这次展开**在标签上什么都没说** ⟺ **语义空转**

⇒ **`B20`**：连续 `N = 10` 次展开的语义增益都为 0 ⇒ 红。

⚠️ **谓词用「每个子的标签分布与父完全相同」这条*精确*判据，不用 `增益 ≤ ε`。**
   理由是 `B19` 那次的教训：浮点阈值会引入一个**拍出来的 ε**，而这里存在一个
   **精确的整数判据** ——

       子与父「同分布」  ⟺  ∀ 标签 l： `count_child[l] · |父| == count_parent[l] · |子|`

   全整数，没有 ε。增益只作**度量**报趋势（与 `B19` 的「细化量」同一个分工）。

## 与 `B19` 成对：一条守**覆盖**，一条守**标签**

    `B19`  结构变细   `|覆盖(父)| − min_k |覆盖(子)| ≤ 0` ⇒ 没变细
    `B20`  语义变细   每个子的标签分布 == 父的              ⇒ 没分开

⚠️ **两条必须分开报，不许合并。** 一次展开可以「覆盖确实变小了」而「标签分布没变」
（切掉的是同一类里的项），也可以反过来。合并它们就会把「**分开了东西**」与
「**分开的是有意思的东西**」当成同一件事 —— 而那正是 `§K2` 两半的区别。

## ⚠️ 判据只在**标签轴**上落，其余方向**只报不判**

哪条方向是标签轴，**由语料决定，不由声明决定**：标签的取值键（`type=<值>`）
在不在这条方向的**键空间**里（`_fixtures.label_bearing`）。

    `keyset`   payload 是键集 ⇒ 标签的取值键在它的键空间里 ⇒ **是标签轴** ⇒ 判
    `reach`    payload 是锚点集 ⇒ 与标签无关            ⇒ 只报不判
    `sequence` payload 是前缀   ⇒ 与标签无关            ⇒ 只报不判

这与 `B17`(b) 在 `reach` 上降级是**同一个形状**：判据的**适用范围**由「它在这条
方向上有没有内容」决定。⚠️ **`只报不判` 报的是「未展开」（跳过），不是「过」** ——
「这条判据在这条方向上什么都没查」与「查过了没问题」必须分得开。

## ⚠️ 这条判据**必须靠注入证明自己能红**

真实语料上它很可能是绿的，而绿的**理由**可能是「真的没空转」，也可能是
「**这个语料的标签恰好与方向轴重合**」—— **两者长得一模一样**。

注入的方式不是「换一种 `split`」，而是**换 oracle**（`test_injections.inj_b20`）。
原因是这条谓词本身逼出来的一个矛盾：

    「同一个夹具 + 真插件」当基线，与「在同一个夹具上做一次标签盲的切分」
    这两件事**互斥** —— 基线要不红，就必须永远切不出纯标签方向；
    而「永远切不出纯标签方向」就等于「标签与切分轴正交」，
    正交又正是注入方要利用的东西。

（细节与被否掉的三种夹具见 `ldv/tests/test_injections.py` 的 `B20` 那一段。）

所以注入**只换标签的指派方式，夹具、插件、树全不动**：

    基线  `type = L{i mod 64}`（低 6 位）   切分轴与标签相关 ⇒ 只在纯标签子树里空转
    注入  `type = L{popcount(i) mod 2}`    每个方向都恰好 50/50 ⇒ **每一刀都空转**

⚠️ 这**不是**「改判据去迁就测试」—— 判的是同一件事（展开有没有在标签上产出东西），
   只是把「标签与切分轴无关」这一件事直接构造出来。键集在两次里**完全不动**。
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

from ..core.tri import Tri
from ._framework import Report

#: 连续多少次「语义空转」就报错。与 `B19` 同一个数，同一个前例
#: （SP-GiST `longValuesOK` 的 "within ten cycles"）。
REFINEMENT_N = 10


def _mix(counter: Counter, n: int) -> dict[str, float]:
    return {k: v / n for k, v in counter.items()} if n else {}


def _entropy(counter: Counter, n: int) -> float:
    if n <= 0:
        return 0.0
    return -sum((v / n) * math.log2(v / n) for v in counter.values() if v)


def _same_mix(p: Counter, pn: int, c: Counter, cn: int) -> bool:
    """子与父**标签分布完全相同** —— 精确的整数判据（无 ε，见模块 docstring）。"""
    if cn <= 0 or pn <= 0:
        return True
    return all(c.get(l, 0) * pn == p.get(l, 0) * cn
               for l in set(p) | set(c))


def refinement_profile(kernel: Any, labels: dict[str, str]) -> dict[str, Any]:
    """每个**已展开**方向的语义增益，以及「连续语义空转」的最长段。

    ## 父的参照物是**被劈的那一部分**（`∪ 子`），不是 `members(父)`

    `§10.2` 出路 (4) 之后项可以**停在父这一层**（滞留）。滞留的项**没有参与**这次劈开，
    所以把它们算进「父的标签分布」会让一次正常的劈开显得像空转。
    ⇒ 参照物取 `base = ∪_k members(k)`；批建路径上 `滞留 == 0`，两者相等。
    滞留量单独报出来（`滞留`），免得读者以为它们不存在。

    ## 空方向怎么算

    某个子的成员集为空 ⇒ 它对分布没有贡献（`cn == 0` 视为同分布，不计入空转判定）。
    """
    rows: list[dict[str, Any]] = []
    flat: dict[str, bool] = {}
    for d in kernel.all_directions():
        kids = kernel.children_of(d)
        if not kids:
            continue
        child_members = [set(kernel.members_of(k)) for k in kids]
        base = set().union(*child_members) if child_members else set()
        base = {i for i in base if i in labels}
        if not base:
            continue
        pc: Counter = Counter(labels[i] for i in base)
        pn = len(base)
        h_p = _entropy(pc, pn)
        h_c = 0.0
        all_same = True
        for ms in child_members:
            ms = {i for i in ms if i in labels}
            cc: Counter = Counter(labels[i] for i in ms)
            if not _same_mix(pc, pn, cc, len(ms)):
                all_same = False
            h_c += (len(ms) / pn) * _entropy(cc, len(ms))
        flat[d.did] = all_same
        rows.append({"方向": d.did, "秩": d.rank, "标签数": len(pc),
                     "成员": pn,
                     # ⚠️ 夹在 0 以上：**信息增益在数学上非负**，负值只可能来自
                     #    浮点（子加权熵比父熵大 1e-16）。判据用的是整数判据，
                     #    不受影响；但读数印出 `-0.0000` 会被读成「增益是负的」。
                     "语义增益": max(0.0, h_p - h_c),
                     "父熵": h_p, "子加权熵": h_c, "子数": len(kids)})

    def chain(flag: dict[str, bool]) -> int:
        """沿**根→叶**的最长连续段（与 `B19` 同一条纪律：按 `flag` 数，不按标签）。"""
        best = 0

        def walk(d: Any, run: int) -> None:
            nonlocal best
            run = run + 1 if flag.get(d.did, False) else 0
            best = max(best, run)
            for k in kernel.children_of(d):
                walk(k, run)

        walk(kernel.root, 0)
        return best

    gains = [r["语义增益"] for r in rows]
    flatrows = [r for r in rows if flat.get(r["方向"], False)]
    return {
        "标签数": len(set(labels.values())),
        "已标注项": len(labels),
        "已展开方向数": len(rows),
        # —— 判据那一条（`B20` 的谓词）
        "语义空转展开": len(flatrows),
        "最长语义空转连续段": chain(flat),
        "空转的": [r["方向"] for r in flatrows][:4],
        # —— 度量那两条（只报不判）
        "最小语义增益": min(gains) if gains else 0.0,
        "平均语义增益": (sum(gains) / len(gains)) if gains else 0.0,
        "N": REFINEMENT_N,
    }


def render_refinement(prof: dict[str, Any], which: str = "") -> str:
    head = f"语义增益（{which}）" if which else "语义增益"
    if prof["已标注项"] == 0:
        return f"{head}：这份语料**没有标注** ⇒ 判不了（不是通过）"
    if prof["已展开方向数"] == 0:
        return f"{head}：没有已展开的方向（未展开，**不是**通过）"
    return (f"{head}：{prof['标签数']} 个标签 / {prof['已标注项']} 项｜"
            f"{prof['已展开方向数']} 次展开｜**语义空转** {prof['语义空转展开']} 次（判据），"
            f"最长连续段 {prof['最长语义空转连续段']}（阈值 N={prof['N']}）｜"
            f"最小增益 {prof['最小语义增益']:.4f}、平均 {prof['平均语义增益']:.4f}"
            f"（度量，不进退出码）")


def b20_semantic_refinement(prof: dict[str, Any], rep: Report,
                            judged: bool = True) -> None:
    """**反复声称能分、却连续 N 次在标签上什么都没分开 ⇒ 红。**

    与 `§K2 判空`、`B19` 的关系：

        §K2 判空   判「**这次**分不开」                    —— 单次性质
        B19        判「反复声称，却连续 N 次**覆盖没变细**」 —— 跨次数，**结构**
        B20        判「反复声称，却连续 N 次**标签没分开**」 —— 跨次数，**语义**

    `judged=False`（这条方向不是标签轴）⇒ 报「**未展开**」并把理由印出来。
    ⚠️ **不许报「过」** —— 那会让「这条判据在这条方向上没查」与「查过了没问题」
       长得一模一样（`B19` 的 docstring 里那条教训的同一形态）。
    """
    if prof["已标注项"] == 0:
        rep.add("B20", "语义变细守卫", Tri.UNEXPANDED,
                "这份语料没有标注（标签取自 `type` 字段）—— 判不了，不是通过")
        return
    if prof["已展开方向数"] == 0:
        rep.add("B20", "语义变细守卫", Tri.UNEXPANDED, "没有任何方向被展开过")
        return
    if not judged:
        rep.add("B20", "语义变细守卫", Tri.UNEXPANDED,
                f"本方向**不是标签轴**（payload 与标签无关）⇒ 只报不判"
                f"（{prof['已展开方向数']} 次展开的语义空转 {prof['语义空转展开']} 次"
                f"仍印在读数里）—— **跳过 ≠ 通过**")
        return
    over = prof["最长语义空转连续段"] >= prof["N"]
    rep.add("B20", "语义变细守卫（连续 N 次展开都必须真的分开标签）",
            Tri.NO if over else Tri.YES,
            (f"连续 {prof['最长语义空转连续段']} 次展开的标签分布与父**完全相同**"
             f"（≥ N={prof['N']}）—— 这些层在语义上什么都没产出；"
             f"例：{prof['空转的']}") if over
            else f"{prof['已展开方向数']} 次展开中语义空转 {prof['语义空转展开']} 次，"
                 f"最长连续段 {prof['最长语义空转连续段']} < N={prof['N']}")
