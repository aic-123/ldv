"""`T1`–`T3` —— **检索器层（流程 T）的三条判据**。

    python -m ldv.run_checks            # 在 `(检索)` 那一组里跑

被判对象是 `ldv/retriever.py`（流程 T 的算法）。三条的来源是 `基线§7` 那张表。

| 编号 | 守谁 | 守的东西 | 红形态 |
|---|---|---|---|
| `T1` | 流程 T | **候选集(步骤 T3) ≡ 候选集（不用视图、直接按 `R0` 遍历）** | `步骤 T3` **漏跑了「后看」的块**（早停 / 忘记跑） |
| `T2` | 流程 T | **认识必须是当前的**：用到的 `spec` 指纹 == 当前结构的 `spec` 指纹 | 结构动了（流程 B），那份认识**没跟着变** |
| `T3` | 流程 T | **解释指得到**：`步骤 T4` 的每条理由都指得到实际结果 | 解释里出现一个成分，而它**不在**任何候选方向的覆盖里 |

★ **`T1` 是承重的那一条**：它守住「**没漏**」（`§K8` 假阴禁止）。

## ★★ 先说清楚：**本层的收益是 0**（2026-10-09 实测）

    `T2` 算出的顺序**无处可达** —— `kernel.query` / `flow.run_query` 只收 `q`，
    从 `root` 遍历整棵树；而视图块**横跨树**（不是子树）
    ⇒ 「只扫先看的块」**做不到**（不能传参，也不能复制流程 A 的循环）。
    ⇒ `T3` 的循环是个**空转循环**（`ldv/retriever.py::run_in_order`）。

    三个探针（`outputs/_probe_retriever_inert.py`，顺序**反转** / 换成**空元组** /
    换成**离散划分**）⇒ 输出**逐项相同**。

⇒ **所以这一组今天守的不是「顺序对不对」，是「不许因为顺序而漏东西」** ——
  而后者在今天**成立得很彻底**（顺序根本不影响任何东西）。
⚠️ **「收益 = 0」必须印出来**（`run_retrieval` 的 note 会印）——
   不印的话，「收益 0」与「有收益」在只看数字时**长得一模一样**。
   本层将来的走向（要不要让它生效、怎么生效）写在 `基线§13 13.0`，**要人拍**。

---

★ **三条都是守卫**（`§C3`）—— **正确实现下恒绿**，红形态**只能手造**。
   这不是「判据没在判」，是它们的红本来就落在**契约层**（同 `§M3` / `§M5` 的处境）：
   它们守的是**接口的承诺**，不是**这一份实现**。⇒ 三条都必须配注入，而且注入是手造的。
   这条必须写在这里 —— 否则「恒真」与「没在判」在只看布尔值时**长得一模一样**。

⚠️ **`T3` 判的**不是**「解释暴露了遍历顺序」** —— 那样反而错了：遍历顺序**不许**进输出
   （`基线§3.2`）。`T3` 判的是解释与**结果**对不对得上。

---

## ★ 一处**实测出来的**夹具限制（写在这里，免得下一轮重新推一遍）

`T1` 的注入要造出「**后看的块里真有命中**」（`§C3` 的 ⚠️），否则它恒绿。
**但这条夹具在「满足 `§I2 覆盖契约」的插件上造不出来** —— 有一条证明：

    `覆盖(合并(S)) ⊇ ⋃_{d∈S} 覆盖(d)`          （`§I2` 的契约，`B2` 守它）
    `命中(·, q) = 否`  ⟺  `覆盖(·) ∩ ideal = ∅`
    ⇒ 合并说「否」 ⇒ 覆盖(合并) ∩ ideal = ∅ ⇒ 每个成员的覆盖也 ∩ ideal = ∅ ⇒ 每个成员都「否」

⇒ **后看的块里不可能有「是」的成员**（本仓库的真插件都满足 `§I2`）⇒ 早停**丢不了候选**
   ⇒ 这条判据在真插件上**永远绿**。

⇒ 夹具只能**手造一个 `合并` 会丢成员的插件**（`_LosingMerge`）—— 而那恰恰是
   `基线§3.1` 描述的那个情形（「`合并` **实测**可以丢」）。
   ★ 这不是绕过判据：判据判的是「检索器有没有漏」，
     而「漏得掉」的前提正是**合并会丢**。夹具造的就是这个前提。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Mapping, Sequence

from ..core.tri import Tri
from ..core.views import ViewSpec, coarsest_stable_refinement
from ..retriever import (
    Retrieval,
    as_bar,
    render_reading,
    render_reason,
    retrieve,
    spec_fingerprint,
)
from ._framework import Report

#: 检索器层的判据编号 —— 与插件侧 / 内核侧 / 视图侧 / CLI 侧**并列**，不是它们的一部分。
#: ⚠️ **只列**已经实现了的（写进来就等于声称「套件全绿」覆盖了它）。
RETRIEVER_CODES = ("T1", "T2", "T3")

#: 三条的**标题** —— **一处定义，两处用**（真跑时 `rep.add`、跳过时 `skip_all`）。
#:
#: ⚠️ **分开写两份会各自漂移**，而两边都只是字符串 ⇒ 漂移了**没有任何东西看得出来**
#:    ——「跳过时印的标题」与「真跑时印的标题」不一致时，读起来像**两条不同的判据**。
#:    `run_tests.test_retriever` ⑥ 段**逐字对照**两边（并核 `set(TITLES) == set(RETRIEVER_CODES)`）。
TITLES: dict[str, str] = {
    "T1": "等价：候选集(步骤 T3) ≡ 候选集（不用视图、直接按 R0 遍历）",
    "T2": "当前：用到的认识的 `spec` 指纹 == 当前结构的 `spec` 指纹",
    "T3": "解释指得到：`步骤 T4` 的每条理由都指得到实际结果",
}


# --- `T1` ---------------------------------------------------------------------

def t1_equivalence(rep: Report, r: Retrieval, kernel: Any) -> None:
    """`T1` —— 候选集(步骤 T3) ≡ 候选集（**不用视图、直接按 `R0` 遍历**）。

    ⚠️ 右边那次遍历是 `kernel.query(q)` —— 流程 A 的 `R0`–`R4`，**不经视图**。
       两边都是**真跑**出来的，不是把左边抄一遍。

    跳过：需求为空 / 没有可判的方向（`§C3`）。
    """
    q = r.query
    if not (q.ideal or q.require or q.forbid):
        rep.add("T1", TITLES["T1"], Tri.UNEXPANDED,
                "需求**为空**（`ideal` / `require` / `forbid` 三样都没有）"
                "⇒ 没有可判的方向。**跳过 ≠ 通过**")
        return
    expected = frozenset(kernel.query(q).yes)
    got = r.candidates
    missing = sorted(expected - got)
    extra = sorted(got - expected)
    bad = bool(missing or extra)
    rep.add("T1", TITLES["T1"], Tri.NO if bad else Tri.YES,
            (f"候选集**不等**：漏 {len(missing)} 个（{missing[:3]}）、"
             f"多 {len(extra)} 个（{extra[:3]}）—— `步骤 T3` 把**块**当成了终止依据？"
             f"（基线§8 停止条件 2：改实现，**不许**放宽本判据）") if bad
            else (f"{len(got)} 个候选 ≡ 不用视图直接按 `R0` 遍历的结果"
                  # ⚠️ 这里原来写「视图只改了代价，没改结论」——
                  #    **前半句是假的**（代价也没改，顺序无处可达）：2026-10-09 更正。
                  f"（**不改变结论**这一半成立；「改变代价」那一半本层还做不到 —— "
                  f"基线§4 T3 ②，收益 = 0）"))


# --- `T2` ---------------------------------------------------------------------

def t2_current(rep: Report, r: Retrieval, spec: ViewSpec) -> None:
    """`T2` —— 取到的认识**必须是当前的**（`基线§6`）。

    跳过：**盘上没有任何认识** ⇒ 没得比（`§C3`）。**跳过 ≠ 通过。**
    """
    rec = r.recognition
    if not rec.on_disk:
        rep.add("T2", TITLES["T2"], Tri.UNEXPANDED,
                "盘上**没有任何认识** ⇒ 没有可比的版本（跳过 ≠ 通过）")
        return
    now = spec_fingerprint(spec)
    bad = rec.fingerprint != now
    rep.add("T2", TITLES["T2"], Tri.NO if bad else Tri.YES,
            (f"用到的是**陈旧的**那份：指纹 {rec.fingerprint} ≠ 当前 {now}"
             f"（来源：{rec.source}）—— 结构动了，那份认识没跟着变") if bad
            else f"指纹相符 `{now}`（来源：{rec.source}）")


# --- `T3` ---------------------------------------------------------------------

def t3_reasons(rep: Report, r: Retrieval, kernel: Any) -> None:
    """`T3` —— `步骤 T4` 的每条理由都指得到**实际结果**。

        每条 = (一个**候选**方向, 一个项)，且那个项**在该方向的覆盖里**。

    跳过：没有任何候选（或候选都没有成员）⇒ 没有可指的东西（`§C3`）。
    """
    if not r.reasons:
        rep.add("T3", TITLES["T3"], Tri.UNEXPANDED,
                "没有任何候选（或候选都没有成员）⇒ 没有可指的东西（跳过 ≠ 通过）")
        return
    bad: list[str] = []
    for did, item in r.reasons:
        if did not in r.candidates:
            bad.append(f"{render_reason((did, item))} —— 方向**不在候选里**")
            continue
        if item not in kernel.members_of(kernel.direction(did)):
            bad.append(f"{render_reason((did, item))} —— 项**不在该方向的覆盖里**")
    rep.add("T3", TITLES["T3"], Tri.NO if bad else Tri.YES,
            (f"{len(bad)} 条指不到实际结果：{bad[:2]}") if bad
            else (f"{len(r.reasons)} 条理由都指得到实际结果"
                  f"（每条 = 一个候选方向 + 它覆盖里的一个项）"))


# --- 跑一遍再判 ----------------------------------------------------------------

def run_retrieval(kernel: Any, plugin: Any,
                  need: Mapping[str, Mapping[str, object]], spec: ViewSpec,
                  rep: Report, *, recognition: Any = None) -> Retrieval:
    """走一遍流程 T（`T0`–`T4`）再逐条判 `T1`–`T3`。

    ⚠️ **判据读的是同一份 `Retrieval`**（`T1` 读候选集、`T2` 读认识的指纹、`T3` 读解释）
       —— 三条不各自跑一遍，否则「红」会分不清是三条里的哪一条造成的。
    """
    r = retrieve(kernel, plugin, need, spec=spec, recognition=recognition)
    t1_equivalence(rep, r, kernel)
    t2_current(rep, r, spec)
    t3_reasons(rep, r, kernel)
    rep.note(f"认识：{r.recognition.source}；指纹 {r.recognition.fingerprint}")
    rep.note(render_reading(r))
    # ⚠️ 这一句**必须**印（模块开头那段）：不印的话，「收益 0」与「有收益」
    #    在只看数字时长得一模一样 —— 而本层今天的收益**确实是 0**。
    rep.note("⚠️ **本层当前是「预备」的**（`基线§13 13.0`）：`T2` 的顺序**无处可达**"
             "（`kernel.query` 只收 `q`、从 root 遍历整棵树，而块横跨树）⇒ 上面每一个数"
             "都**只是描述**，没有一个是收益。⇒ 这一组今天守的是「不许因为顺序而漏东西」，"
             "而它在今天成立得很彻底（顺序不影响任何东西）。要让它**生效**：见 §13 13.0，"
             "**要人拍**。")
    return r


def skip_all(rep: Report, why: str) -> None:
    """三条一起跳过 —— 与 `run_checks._skip_views` 同一个理由：理由只写一遍。

    ⚠️ 标题取自 `TITLES`（**与真跑时同一个来源**）。分开写两份的话，
       「跳过时印的标题」与「真跑时印的标题」会各自漂移。
    """
    for code in RETRIEVER_CODES:
        rep.add(code, TITLES[code], Tri.UNEXPANDED, why)


# ═══ 已知答案对照 ═════════════════════════════════════════════════════════════
#
# `T1`–`T3` 的红形态只能**手造**（见模块开头）。下面每条都造一个「该红的输入」，
# 否则「这条判据在判」与「这条判据恒真」长得一模一样。


class _LosingMerge:
    """一个 **`合并` 会丢成员**的键集插件 —— `T1` 夹具的**前提**。

    见模块开头那条证明：**满足 `§I2` 契约的插件上，后看的块里不可能有「是」**
    ⇒ 早停丢不了候选 ⇒ `T1` 永远绿。要造出「后看的块里真有命中」，
    就必须有一个**真的会丢**的合并 —— 而这正是 `基线§3.1` 说的那个情形。

    只改 `merge` 一件事：取**最细的那个成员**的 payload（`rank` 最大），
    而不是分量取交 ⇒ 覆盖收窄 ⇒ 别的成员的命中会被**合并**这一层遮住。
    """

    name = "keyset"

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def merge(self, ds: Sequence[Any]) -> Any:
        if not ds:
            return self._inner.merge([])
        return max(ds, key=lambda d: d.rank).payload

    def hit(self, d: Any, query: Any) -> Tri:
        return self._inner.hit(d, query)

    def penalty(self, d: Any, item: Any) -> float:
        return self._inner.penalty(d, item)


def _synth(with_losing_merge: bool) -> tuple[Any, Any, Any]:
    """手造的小内核 + 插件 + 一个「后看的块里有真命中」的查询。

    返回 `(kernel, plugin, query)`。查的是「有 `k2`、没有 `k1`」——
    项 `c` 恰好满足 ⇒ `ideal = {c}`。
    """
    from ..core.kernel import Kernel, Query
    from ..plugins.keyset import KeysetPlugin

    inner = KeysetPlugin()
    plug: Any = _LosingMerge(inner) if with_losing_merge else inner
    items = {
        "a": {"id": "a", "keys": ["k1"]},
        "b": {"id": "b", "keys": ["k1", "k2"]},
        "c": {"id": "c", "keys": ["k2"]},
        "d": {"id": "d", "keys": ["k3"]},
    }
    k = Kernel(inner, items)          # 内核照常拿真插件（`hit` / `penalty` 都是真的）
    k.build(inner.merge([]))
    for iid in sorted(items):
        k.insert(iid)
    q = Query(ideal=frozenset({"c"}), require=frozenset({"k2"}),
              forbid=frozenset({"k1"}), label="有 k2 无 k1")
    return k, plug, q


def _synth_spec(kernel: Any) -> ViewSpec:
    """手造内核的 `spec`：`P = {U}`（信息量最低那一档），`E` = 树边。"""
    dirs = kernel.all_directions()
    u = tuple(sorted(d.did for d in dirs))
    edges = frozenset((d.parent, d.did) for d in dirs if d.parent)
    return ViewSpec(universe=u, partition=(frozenset(u),), relation=edges)


def _early_stop(r: Retrieval) -> Retrieval:
    """注入：**「先看的块跑完就收工」的可观测形态**。

    早停的后果 = 后看的块**没跑到** ⇒ 那些块里的命中**不在候选集里**。
    而「漏掉的候选」与「本来就没有」**长得一模一样** —— 正是本项目从头到尾在防的形状
    （`基线§3.1` 末）。所以注入**造的就是这个可观测形态**。
    """
    keep = frozenset().union(*r.first) if r.first else frozenset()
    return replace(r, candidates=frozenset(d for d in r.candidates if d in keep))


def known_answer_controls() -> list[str]:
    """`T1`–`T3` 的**合成对照** —— 每条都要「该绿时绿、该红时红」。

    返回失败清单（空 = 全过）。
    """
    fails: list[str] = []

    def judge(fn: Callable[[Report], None]) -> Tri:
        rep = Report(plugin="(检索对照)", expects=RETRIEVER_CODES)
        fn(rep)
        return rep.assertions[0].result

    # ── `T1`：真实现 ⇒ 绿；「先看跑完就收工」⇒ 红 ─────────────────────────
    kernel, plugin, q = _synth(with_losing_merge=True)
    spec = _synth_spec(kernel)
    need = {"keyset": as_bar(q)}
    full = retrieve(kernel, plugin, need, spec=spec)
    if not full.late:
        # 夹具前提：必须**真有一个「后看」的块**，否则这条对照什么都没验。
        fails.append("§T1 夹具不成立：没有「后看」的块")
    else:
        covered = frozenset().union(*full.late)
        lost = sorted(full.candidates & covered)
        if not lost:
            fails.append("§T1 夹具不成立：「后看」的块里**没有真命中** "
                         "⇒ 这条判据恒绿（等于没做）")
        else:
            if judge(lambda rep: t1_equivalence(rep, full, kernel)) is not Tri.YES:
                fails.append("§T1 真实现被判红")
            if judge(lambda rep: t1_equivalence(rep, _early_stop(full), kernel)) is not Tri.NO:
                fails.append("§T1「先看跑完就收工」没被抓住（漏的候选应当是红的）")

    # `T1` 的跳过：需求为空 ⇒ **跳过**，不是过、也不是红
    empty = replace(full, query=q.__class__(ideal=frozenset(), label="空需求"))
    if judge(lambda rep: t1_equivalence(rep, empty, kernel)) is not Tri.UNEXPANDED:
        fails.append("§T1 空需求没被判「跳过」（跳过 ≠ 通过）")

    # ── `T2`：指纹相符 ⇒ 绿；用了陈旧那份 ⇒ 红；盘上什么都没有 ⇒ 跳过 ─────
    from .abstraction import ViewSet

    other = ViewSpec(universe=spec.universe,
                     partition=tuple(frozenset({d}) for d in spec.universe),
                     relation=spec.relation)
    stale = ViewSet(spec=other, q=coarsest_stable_refinement(other), views=())
    ok_r = retrieve(kernel, plugin, need, spec=spec, recognition=stale)
    if ok_r.recognition.fingerprint != spec_fingerprint(spec):
        fails.append("§T2 真实现没用当前那份（陈旧认识应当被丢掉、重算）")
    if judge(lambda rep: t2_current(rep, ok_r, spec)) is not Tri.YES:
        fails.append("§T2 指纹相符（重算后）被判红")
    trusting = replace(ok_r, recognition=replace(ok_r.recognition,
                                                fingerprint=spec_fingerprint(other),
                                                source="盘上（直接用）"))
    if judge(lambda rep: t2_current(rep, trusting, spec)) is not Tri.NO:
        fails.append("§T2「不比对、直接用盘上那份」没被抓住")
    none_r = replace(ok_r, recognition=replace(ok_r.recognition, on_disk=False))
    if judge(lambda rep: t2_current(rep, none_r, spec)) is not Tri.UNEXPANDED:
        fails.append("§T2 盘上没有任何认识时没被判「跳过」（跳过 ≠ 通过）")

    # ── `T3`：真实现的解释 ⇒ 绿；写死成一句固定的 ⇒ 红 ───────────────────
    if not full.reasons:
        fails.append("§T3 夹具不成立：真实现一条理由都没有")
    else:
        if judge(lambda rep: t3_reasons(rep, full, kernel)) is not Tri.YES:
            fails.append("§T3 真实现的解释被判红")
        fixed = replace(full, reasons=(("__写死__", "__写死__"),))
        if judge(lambda rep: t3_reasons(rep, fixed, kernel)) is not Tri.NO:
            fails.append("§T3「解释写死成一句固定的」没被抓住")

    return fails
