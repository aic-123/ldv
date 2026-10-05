"""B1–B6 —— 契约类检查（§I1–§I6）。

六条都只做一件事：**把接口条款变成会红的判据**。

    B1 假阴     §K8  —— 假阴禁止。**唯一破坏正确性的那条**
    B2 合并覆盖 §I2  —— 粗方向不许拒掉细方向收下的东西
    B3 代价可比 §I3  —— 同层内两两可比（NaN / 非数都算不可比）
    B4 劈开划分 §I4  —— 不重不漏（**内核的构造性质**）
    B5 解码覆盖 §I5  —— `解码(编码(d)) ⊒ d`
    B6 信号不折叠 §I6 —— 「无信号」与「负信号」必须分得开

⚠️ 覆盖类（B2 / B5）用**行为式**判据，不用集合相等：

    覆盖 = 细方向说「是」的查询，粗方向**不许说「否」**

理由：`§I1` 允许假阳，所以「集合相等」这个判据根本立不住 ——
粗方向本来就允许比细方向多收。**能立住的只有单向的那一半**：
细方向收下的，粗方向不许拒。这与 §K8 的不对称契约是同一个形状。
"""

from __future__ import annotations

from typing import Any, Sequence

from ..core.direction import ORIGIN_SPLIT, Direction
from ..core.interfaces import call_hit, call_penalty
from ..core.tri import Tri
from ._framework import Report

_QUERY_CAP = 40     # 每步最多探这么多查询 —— 够红，且不把报告淹掉


def _wrap(payload: Any, did: str, rank: int) -> Direction:
    """把插件产出的 payload 包成一个**合成方向**，只用来调 `命中`。"""
    return Direction(did=did, rank=rank, payload=payload, witness=(),
                     origin=ORIGIN_SPLIT, parent=None)


# --- §K8 的另一半：假阳计量 --------------------------------------------------

def false_positive_profile(kernel: Any, plugin: Any, queries: Sequence[Any]) -> dict[str, Any]:
    """**假阳计量** —— §K8 是「假阴禁止 / **假阳计量**」，两半都要有。

    `B1` 管前一半（假阴禁止，红了就阻断）；这里管后一半：
    假阳**允许**，但要**量出来**。

    为什么必须量：假阳的代价是性能，而性能是**会累积**的。
    一个假阳率 90% 的过滤器不违反任何一条检查，却让整套结构白建 ——
    所有方向都说「是」，等于没有方向。

    ⇒ 所以它是**度量，不是判据**：进 `report()`，不进退出码（继承 dce）。
    """
    yes_total = 0
    fp_total = 0
    worst: tuple[int, str] = (0, "")
    per_direction: dict[str, tuple[int, int]] = {}
    for d in kernel.all_directions():
        mem = kernel.members_of(d)
        if not mem:
            continue
        y = f = 0
        for q in queries:
            if call_hit(plugin, d, q) is not Tri.YES:
                continue
            y += 1
            if not (mem & q.ideal):
                f += 1
        if y:
            per_direction[d.did] = (y, f)
            if f > worst[0]:
                worst = (f, d.did)
        yes_total += y
        fp_total += f
    rate = (fp_total / yes_total) if yes_total else 0.0
    return {
        "判「是」总数": yes_total,
        "其中假阳": fp_total,
        "假阳率": rate,
        "最差方向": worst[1] or "—",
        "有判「是」的方向数": len(per_direction),
    }


def _yes_directions(kernel: Any, plugin: Any, queries: Sequence[Any]) -> dict[str, list[Any]]:
    """每个方向说「是」的查询 —— B2 / B5 的公共输入。"""
    out: dict[str, list[Any]] = {}
    for d in kernel.all_directions():
        out[d.did] = [q for q in queries if call_hit(plugin, d, q) is Tri.YES]
    return out


def cover_nesting_profile(kernel: Any, plugin: Any,
                          queries: Sequence[Any]) -> dict[str, Any]:
    """**覆盖嵌套** —— 子方向说「是」的查询，父方向不许说「否」。

        `覆盖(子) ⊆ 覆盖(父)`  ⟺  `命中(子,q) = 是 ⇒ 命中(父,q) ≠ 否`

    ## 为什么这是**度量**，不是判据

    §1 的硬要求表里**没有**这一条（硬的是「成员层 `members(子) ⊆ members(父)`」
    与「健全性 `members ⊆ 覆盖`」）。所以违反它**不破坏正确性** ——
    剪枝仍然安全（父的「否」只在父的覆盖之外才下）。

    但它**不是无害的**：子方向收下的东西被父否掉，说明**父的过滤器比子的更松**，
    于是遍历在父那一层**白走一趟**才到子。这正是 §K8 说的「假阳的代价是性能，
    而性能会累积」的结构形态 ⇒ 进输出，不进退出码。

    ## 它为什么值得单独量（与假阳率的分工）

        假阳率      说「说了多少句不该说的**是**」—— **行为**侧
        覆盖嵌套    说「子说的『是』，父接不接得住」—— **结构**侧

    实测（36 项语料，A 方向，12 个已展开方向）：`劈开` 拿不到父方向时，
    子方向把父的 `forb` 丢了 ⇒ **越界 14 次**；把父的 `forb` 续上之后 **0 次**。
    """
    bad: list[str] = []
    checked = 0
    for d in kernel.all_directions():
        kids = kernel.children_of(d)
        if not kids:
            continue
        checked += 1
        for k in kids:
            for q in queries:
                if call_hit(plugin, k, q) is not Tri.YES:
                    continue
                if call_hit(plugin, d, q) is Tri.NO:
                    bad.append(f"{k.did} 说「是」的 {q.label}，被父 {d.did} 判「否」")
                    break
    # ⚠️ `越界次数` 是**次**数（一个方向可能有多个子方向越界），不是方向数。
    return {"已展开方向数": checked, "越界次数": len(bad), "例子": bad[:2]}


# --- §1 硬要求表第三行：健全性（**必须两条路径都量**） ------------------------

def soundness_profile(kernel: Any, plugin: Any) -> dict[str, Any]:
    """`members(d) ⊆ 覆盖(d)` —— §1 硬要求表**第三行**，按方向统计。

        members(d) ⊆ { x : 代价(d, x) = 0 }

    ## 为什么它必须**单独**量，而且必须**两条路径都量**

    `B16` 查的就是这一条，但 `run_checks` 只拿**一次建完**的内核去查它。
    而这一条在两条路径上**不是同一件事**：

        一次建完     每个方向的成员集在**第一次展开时就是完整的**
                     ⇒ 子方向的 payload 从这批成员算出来，一定盖得住它们 ⇒ 天然成立
        维护路径     成员是**后来才长起来的**，而 payload 在劈开时就**冻结**了
                     ⇒ 子方向收不住「它没见过的项」 ⇒ 破了也没人查

    ⇒ 实测（36 项语料，本轮修 `keyset.split` 之前）：

        方向      一次建完            先建 2 维护 34
        ────────────────────────────────────────────────
        keyset    0 / 159            123 / 227     且 42/385 条假阴
        reach     0 / 224            313 / 385     且 44/384 条假阴
        sequence  0 / 122             68 / 210     且 25/154 条假阴

    **一次建完全绿、维护路径全红** —— 这就是「空转与通过长得一模一样」在
    **流程**上的形态：检查存在、是绿的，但它从没跑在出问题的那条路上。

    修好 A 之后（先建 6 维护 30），把 `B1` 也搬到维护路径上跑：

        方向        维护路径越界        B1（维护路径上的假阴）
        ────────────────────────────────────────────────────────
        keyset      0 / 232            **是**（411 个样本，0 条假阴）
        reach       246 / 328          **否**（30/367 条假阴）
        sequence    36 / 176           **否**（17/141 条假阴）

    ⇒ 「健全性破了」与「真的产生假阴」是同一件事的两面：
      `members(d) ⊄ 覆盖(d)` ⇒ `命中` 的「否」证不住 ⇒ 假阴（§K8 禁）。

    ⚠️ 这是**硬要求**（§K8 禁假阴的结构来源），所以它**不是**「无害趋势」。
       当前它以**度量**形式报出而不是进退出码，理由只有一条，且要说清：
       B / C 两个方向的修法**需要先裁定**（见 `C10` §4 与设计文档 §10.2），
       而一条基线就红的判据**过不了注入验证**（注入验证要求基线绿）。
       ⇒ 裁定之后，这一条要升级成判据（进退出码）。
    """
    bad: dict[str, tuple[int, int]] = {}
    members = 0
    outside = 0
    for d in kernel.all_directions():
        mem = kernel.members_of(d)
        if not mem:
            continue
        members += len(mem)
        out = 0
        for iid in sorted(mem):
            try:
                cost = call_penalty(plugin, d, kernel.items[iid])
            except Exception:  # noqa: BLE001 - 插件是外部代码
                cost = float("nan")
            if cost != 0.0:
                out += 1
        if out:
            bad[d.did] = (out, len(mem))
            outside += out
    return {
        "方向数": len(kernel.all_directions()),
        "成员数": members,
        "越界成员数": outside,
        "越界方向": bad,
        "最大越界方向": max(bad, key=lambda k: bad[k][0]) if bad else "—",
    }


def render_soundness(batch: dict[str, Any], inc: dict[str, Any],
                     which: str = "") -> str:
    """把两条路径的健全性读数渲染成**一行**。"""
    head = f"健全性（{which}）" if which else "健全性"
    b, i = batch["越界成员数"], inc["越界成员数"]
    if not b and not i:
        return (f"{head}：一次建完 {b}/{batch['成员数']}、维护 {i}/{inc['成员数']} "
                f"个成员落在覆盖外（两条路径都成立）")
    return (f"★ {head}**被破坏**（§1 硬要求表第三行）："
            f"一次建完 {b}/{batch['成员数']}、**维护 {i}/{inc['成员数']}** 个成员落在覆盖外"
            f"（最差方向 {inc['最大越界方向']}）"
            f"—— §K8 的结构来源，不是无害趋势；修法见设计文档 §10.2")


# --- B1 ---------------------------------------------------------------------

def b1_no_false_negative(kernel: Any, plugin: Any, queries: Sequence[Any], rep: Report) -> None:
    bad: list[str] = []
    checked = 0
    for d in kernel.all_directions():
        mem = kernel.members_of(d)
        if not mem:
            continue
        for q in queries:
            truth = mem & q.ideal
            if not truth:
                continue
            checked += 1
            if call_hit(plugin, d, q) is Tri.NO:
                bad.append(f"{d.did}×{q.label}（实际有 {sorted(truth)[:3]}）")
    if checked == 0:
        rep.add("B1", "假阴", Tri.UNEXPANDED, "没有一条「实际有命中」的样本可查")
        return
    rep.add("B1", "假阴：命中说否而实际有命中",
            Tri.NO if bad else Tri.YES,
            f"{len(bad)}/{checked} 条假阴：{bad[:3]}" if bad else f"{checked} 个「实际有命中」样本全部未被判否")


# --- B2 ---------------------------------------------------------------------

def b2_merge_covers(plugin: Any, kernel: Any, queries: Sequence[Any], rep: Report) -> None:
    """`合并(P)` 不许拒掉 P 里任一方向说「是」的查询。"""
    yes_of = _yes_directions(kernel, plugin, queries)
    groups: list[tuple[Direction, ...]] = []
    seen: set[tuple[str, ...]] = set()
    for d in kernel.all_directions():
        kids = kernel.children_of(d)
        if len(kids) >= 2:
            key = tuple(k.did for k in kids)
            if key not in seen:
                seen.add(key)
                groups.append(kids)
    # 再补上「全层一起合」
    for rank in sorted({d.rank for d in kernel.all_directions()}):
        layer = tuple(d for d in kernel.all_directions() if d.rank == rank)
        if len(layer) >= 2:
            key = tuple(k.did for k in layer)
            if key not in seen:
                seen.add(key)
                groups.append(layer)

    bad: list[str] = []
    checked = 0
    for group in groups:
        merged = _wrap(plugin.merge(list(group)), f"M({group[0].did}..)", group[0].rank - 1)
        for d in group:
            for q in yes_of.get(d.did, [])[:_QUERY_CAP]:
                checked += 1
                if call_hit(plugin, merged, q) is Tri.NO:
                    bad.append(f"{d.did} 说「是」的 {q.label}，被 合并({len(group)} 个) 判「否」")
    if checked == 0:
        rep.add("B2", "合并覆盖", Tri.UNEXPANDED, "没有可合的分组，或没有任何方向说「是」")
        return
    rep.add("B2", "合并覆盖：粗方向不许拒掉细方向收下的",
            Tri.NO if bad else Tri.YES,
            f"{len(bad)}/{checked} 条被拒：{bad[:2]}" if bad else f"{len(groups)} 组 × {checked} 次判定全过")


# --- B3 ---------------------------------------------------------------------

def b3_penalty_comparable(kernel: Any, plugin: Any, rep: Report) -> None:
    """同一层内，`代价` 的返回必须**两两可比**。

    ⚠️ 插件抛异常要**记成违例**，不许让检查崩掉。
       检查崩掉 = 这一条静默消失 = 「空转与通过长得一模一样」。
    """
    bad: list[str] = []
    checked = 0
    for rank in sorted({d.rank for d in kernel.all_directions()}):
        layer = [d for d in kernel.all_directions() if d.rank == rank]
        if len(layer) < 2:
            continue
        table: dict[str, list[float]] = {}
        for d in layer:
            vals: list[float] = []
            for i in sorted(kernel.items):
                try:
                    vals.append(call_penalty(plugin, d, kernel.items[i]))
                except Exception as exc:  # noqa: BLE001 - 插件是外部代码
                    bad.append(f"{d.did} 的代价抛异常：{type(exc).__name__}: {exc}")
                    vals.append(float("nan"))
            table[d.did] = vals
            checked += len(vals)
            if any(v != v for v in vals):       # NaN
                bad.append(f"{d.did} 的代价里有 NaN（不可比）")
        # 两两可比 = 任意两列逐项都满足 <、==、> 恰好一个
        ids = sorted(table)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                for a, b in zip(table[ids[i]], table[ids[j]]):
                    if not (a < b or a == b or a > b):
                        bad.append(f"{ids[i]} vs {ids[j]} 的一项不可比：{a} / {b}")
                        break
    if checked == 0:
        rep.add("B3", "代价可比", Tri.UNEXPANDED, "没有任何一层有 ≥2 个方向")
        return
    rep.add("B3", "代价可比：同层内两两可比",
            Tri.NO if bad else Tri.YES,
            f"{len(bad)} 处不可比：{bad[:2]}" if bad else f"{checked} 个代价值全部可比")


# --- B4 ---------------------------------------------------------------------

def b4_split_is_partition(kernel: Any, rep: Report) -> None:
    """`劈开` 的结果必须构成**划分**。

    ⚠️ 查的是**内核的归属分配**，不是插件返回的 payload。
       理由见 `core/kernel.py` 开头：`命中` 允许假阳 ⇒ 重叠永远看不出来，
       所以「不重不漏」只能由内核的分配来保证，也只能查那里。
    """
    bad: list[str] = []
    checked = 0
    for d in kernel.all_directions():
        kids = kernel.children_of(d)
        if len(kids) < 2:
            continue
        checked += 1
        parent = kernel.members_of(d)
        union: set[str] = set()
        for k in kids:
            mk = set(kernel.members_of(k))
            dup = union & mk
            if dup:
                bad.append(f"{d.did} 的子方向重叠：{sorted(dup)[:3]}")
            union |= mk
        if union != set(parent):
            miss = sorted(set(parent) - union)
            extra = sorted(union - set(parent))
            bad.append(f"{d.did} 的子方向不覆盖父：漏 {miss[:3]} / 多 {extra[:3]}")
    if checked == 0:
        rep.add("B4", "劈开成划分", Tri.UNEXPANDED, "没有任何方向被展开过")
        return
    rep.add("B4", "劈开成划分：不重不漏",
            Tri.NO if bad else Tri.YES,
            f"{len(bad)} 处破划分：{bad[:2]}" if bad else f"{checked} 个已展开方向的子集构成划分")


# --- B5 ---------------------------------------------------------------------

def b5_decode_covers(plugin: Any, kernel: Any, queries: Sequence[Any], rep: Report) -> None:
    """`解码(编码(d))` 必须**覆盖** `d`（可以更粗，不可以更细）。"""
    yes_of = _yes_directions(kernel, plugin, queries)
    bad: list[str] = []
    checked = 0
    for d in kernel.all_directions():
        blob = plugin.encode(d)
        if not isinstance(blob, (bytes, bytearray)):
            bad.append(f"{d.did} 的 §I5 编码不是 bytes：{type(blob).__name__}")
            continue
        back = _wrap(plugin.decode(bytes(blob)), f"dec({d.did})", d.rank)
        for q in yes_of.get(d.did, [])[:_QUERY_CAP]:
            checked += 1
            if call_hit(plugin, back, q) is Tri.NO:
                bad.append(f"{d.did} 说「是」的 {q.label}，被 解码(编码(d)) 判「否」")
    if checked == 0:
        rep.add("B5", "解码覆盖", Tri.UNEXPANDED, "没有任何方向说「是」")
        return
    rep.add("B5", "解码覆盖：解码(编码(d)) ⊒ d",
            Tri.NO if bad else Tri.YES,
            f"{len(bad)}/{checked} 条被拒：{bad[:2]}" if bad else f"{checked} 次往返判定全过")


# --- B6 ---------------------------------------------------------------------

def render_usage(rec: Any) -> str:
    """使用记录的**输出形态** —— B6 查的就是这里。"""
    if rec is None:
        return "无信号"
    return f"有信号 outcome={rec.outcome:+.2f} 倾向={rec.propensity:.3f}"


def b6_signal_not_collapsed(kernel: Any, plugin: Any, rep: Report) -> None:
    """「没有观测到使用」与「使用了但为负」必须**长得不一样**。

    arena 那条「0 和『没有』分不开，就不许印成 0」在这里的形态。

    ⚠️ 生效的是 **`kernel.plugin`**，不是传进来的 `plugin`：
       `record_usage` 是**内核方法**，它调的是内核自己持有的那个插件。
       这一点是**注入验证抓出来的** —— 当时传进来的插件被换成「把 None 折成 0」，
       检查照样绿，因为那个插件**压根没被用到**。
       ⇒ 所以这里显式要求两者是同一个对象，不一致就报「未展开」而不是装作通过。
    """
    if plugin is not kernel.plugin:
        rep.add("B6", "信号不折叠", Tri.UNEXPANDED,
                "传入插件 ≠ 内核持有的插件 —— 这样测的是错对象，宁可不测")
        return
    d = kernel.all_directions()[0]
    none_rec = kernel.record_usage(d, {"outcome": None, "note": "没被点"})
    neg_rec = kernel.record_usage(d, {"outcome": -1.0, "note": "点了但没用"})
    r_none, r_neg = render_usage(none_rec), render_usage(neg_rec)
    bad: list[str] = []
    if none_rec is not None:
        bad.append(f"「没有观测到使用」被折成了记录：{r_none}")
    if neg_rec is None:
        bad.append("「使用了但为负」被折成了「无信号」")
    if r_none == r_neg:
        bad.append(f"两者渲染相同：{r_none!r}")
    if neg_rec is not None and not (0.0 < neg_rec.propensity <= 1.0):
        bad.append(f"倾向权重越界：{neg_rec.propensity}")
    rep.add("B6", "信号不折叠：无信号 ≠ 负信号",
            Tri.NO if bad else Tri.YES,
            "；".join(bad) if bad else f"「{r_none}」 ≠ 「{r_neg}」")


# --- B16 --------------------------------------------------------------------

def b16_members_covered(kernel: Any, plugin: Any, rep: Report) -> None:
    """**成员必须落在自己方向的覆盖里** —— §K8 的结构版本，**与查询集无关**。

        members(d)  ⊆  { x : 代价(d, x) = 0 }

    为什么这条要紧：`命中` 说「否」是在**证明**「这个方向里没有命中」，
    而它手里的依据只有**方向自己**（payload）。若某个成员根本不在方向的覆盖里，
    那个证明就**管不到它** —— 它可能命中，而 `命中` 已经说了「否」。
    ⇒ 这就是假阴的**结构来源**。

    ## 与 `B1` 的分工（实测过，不是推理）

        B1   行为式：拿**查询集**的 ground truth 比 —— 只查得到被查询覆盖到的方向
        B16  结构式：拿**代价**比 —— 不看查询集，一次全查

    实测：同一个「插入时选**代价最大**的子方向」的坏内核（健全性破坏 102 个成员），

        B1  在 40 条查询下判「否」  ← 查到了
        B1  在  3 条查询下判「是」  ← **绿了**
        B16                        ← 无论查询集怎么变，都是「否」

    ⇒ 查询集一换，`B1` 就可能瞎；`B16` 不会。**两个都留**：
      `B1` 管「插件**说**得准不准」，`B16` 管「内核**分**得对不对」。
    """
    bad: list[str] = []
    checked = 0
    for d in kernel.all_directions():
        mem = kernel.members_of(d)
        if not mem:
            continue
        checked += len(mem)
        outside: list[str] = []
        for iid in sorted(mem):
            try:
                cost = call_penalty(plugin, d, kernel.items[iid])
            except Exception as exc:  # noqa: BLE001 - 插件是外部代码
                bad.append(f"{d.did} 的代价抛异常：{type(exc).__name__}: {exc}")
                break
            if cost != 0.0:
                outside.append(iid)
        if outside:
            bad.append(f"{d.did} 有 {len(outside)}/{len(mem)} 个成员落在覆盖外"
                       f"（{outside[:3]}）")
    if checked == 0:
        rep.add("B16", "过滤器健全", Tri.UNEXPANDED, "没有任何方向有成员")
        return
    rep.add("B16", "过滤器健全：成员 ⊆ 覆盖（§K8 的结构版本，与查询集无关）",
            Tri.NO if bad else Tri.YES,
            f"{len(bad)} 个方向有成员越界：{bad[:2]}" if bad
            else f"{checked} 个成员全部落在自己方向的覆盖里（代价均为 0）")
