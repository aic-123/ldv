"""注入验证 —— **每条检查都必须能红**。

    跑法：python -m ldv.tests.test_injections

---

## 为什么这一步不能省

「全过」这句话**本身不提供任何信息** —— 一个永远返回「过」的检查
也会给出同样的结果。arena 那边把这件事说得很直白：

> **空转与通过长得一模一样。**

所以每条检查都要**成对**验一次：

    未注入 ⇒ 绿（基线）
    注入   ⇒ 红（判据真的在判）

**只验一半不行**：只验「注入后红」会被「本来就红」骗过；
只验「未注入绿」会被「永远绿」骗过。

---

## 注入要**改在最靠近判据的地方**

比如 `B1`（假阴）注入的是**返回假「否」的插件**，不是「让检查看不见」。
改在检查自己身上，验的是检查的 bug，不是判据的 bug。

---

## 注入还得**选对**：看起来该红的改法未必真红

`B15` 是实例。三种「该让 B15 变红」的改法里，只有一种真的变红：

    去掉 `build()` 的预装         → 根变叶，退化成一个方向  → B15 仍「过」（空转！）
    `expand()` 劈之前不排序       → 同一集合迭代序一致       → B15 仍「过」
    方向攒够容量才劈（上溢分裂）   → 真的顺序相关             → B15 **红** ← 用它

**「想当然的注入」和「有效的注入」长得一样**，所以注入本身也要挑：
挑不出来，说明这条判据的真实内容还没被说清。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ldv.checks._fixtures import (  # noqa: E402
    build_keyset,
    build_reach,
    keyset_queries,
    load,
    items as make_items,
    reach_queries,
)
from ldv.checks._framework import Report  # noqa: E402
from ldv.checks.contract import (  # noqa: E402
    b1_no_false_negative,
    b2_merge_covers,
    b3_penalty_comparable,
    b4_split_is_partition,
    b5_decode_covers,
    b6_signal_not_collapsed,
    b16_members_covered,
)
from ldv.checks.semantics import b10_three_states_separable, b11_propensity_complete  # noqa: E402
from ldv.checks.source import b12_no_global_scalar, b14_exogenous_boundary  # noqa: E402
from ldv.checks.structure import (  # noqa: E402
    b7_witness_complete,
    b8_invalidation_local,
    b9_ledger_append_only,
    b13_rank_well_founded,
    b15_reproducible,
)
from ldv.core.direction import ORIGIN_SPLIT, Direction  # noqa: E402
from ldv.core.interfaces import call_penalty  # noqa: E402
from ldv.core.kernel import Kernel, QueryResult  # noqa: E402
from ldv.core.tri import Tri  # noqa: E402
from ldv.plugins.keyset import KeysetPlugin  # noqa: E402


def _rep() -> Report:
    return Report(plugin="注入", expects=())


def _tmp_source(body: str) -> Path:
    fh = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8")
    fh.write(body)
    fh.close()
    return Path(fh.name)


# ═══ B1 ══════════════════════════════════════════════════════════════════════

class _HitAllNo(KeysetPlugin):
    """假阴注入：不问青红皂白一律说「否」。"""

    def hit(self, d: Direction, query: Any) -> Tri:
        return Tri.NO


def inj_b1(nodes, edges, injected: bool) -> Report:
    kernel, good = build_keyset(nodes)
    rep = _rep()
    b1_no_false_negative(kernel, _HitAllNo() if injected else good,
                         keyset_queries(nodes), rep)
    return rep


# ═══ B2 ══════════════════════════════════════════════════════════════════════

class _MergeTooNarrow(KeysetPlugin):
    """合并注入：把合并结果收窄 —— 于是粗方向会拒掉细方向收下的东西。"""

    def merge(self, ds):
        req, forb = super().merge(ds)
        return (req | {"type=概念"}, forb)


def inj_b2(nodes, edges, injected: bool) -> Report:
    kernel, good = build_keyset(nodes)
    rep = _rep()
    b2_merge_covers(_MergeTooNarrow() if injected else good, kernel,
                    keyset_queries(nodes), rep)
    return rep


# ═══ B3 ══════════════════════════════════════════════════════════════════════

class _PenaltyNaN(KeysetPlugin):
    """代价注入：某些方向返回 NaN —— NaN 与任何数都不可比。"""

    def penalty(self, d: Direction, item: Any) -> float:
        if d.rank >= 2:
            return float("nan")
        return super().penalty(d, item)


def inj_b3(nodes, edges, injected: bool) -> Report:
    kernel, good = build_keyset(nodes)
    rep = _rep()
    b3_penalty_comparable(kernel, _PenaltyNaN() if injected else good, rep)
    return rep


# ═══ B4 ══════════════════════════════════════════════════════════════════════

def inj_b4(nodes, edges, injected: bool) -> Report:
    kernel, _ = build_keyset(nodes)
    if injected:
        # 把两个子方向的归属**弄重叠** —— 这正是行为式判据查不出来的那一半
        for d in kernel.all_directions():
            kids = kernel.children_of(d)
            if len(kids) >= 2:
                kernel._members[kids[0].did] |= set(kernel._members[kids[1].did])  # noqa: SLF001
                break
    rep = _rep()
    b4_split_is_partition(kernel, rep)
    return rep


# ═══ B5 ══════════════════════════════════════════════════════════════════════

class _DecodeLossy(KeysetPlugin):
    """解码注入：往返时**丢**掉 require —— 于是解码结果不再覆盖原方向。"""

    def decode(self, blob: bytes):
        req, forb = super().decode(blob)
        return (frozenset(), forb | ({"type=概念"} if req else frozenset()))


def inj_b5(nodes, edges, injected: bool) -> Report:
    kernel, good = build_keyset(nodes)
    rep = _rep()
    b5_decode_covers(_DecodeLossy() if injected else good, kernel,
                     keyset_queries(nodes), rep)
    return rep


# ═══ B6 ══════════════════════════════════════════════════════════════════════

class _SignalFolds(KeysetPlugin):
    """信号注入：把「没有观测到使用」折成「outcome=0 的记录」。"""

    def signal(self, d: Direction, interaction: Any):
        from ldv.core.interfaces import UsageSignal

        if interaction.get("outcome") is None:
            return UsageSignal(did=d.did, outcome=0.0, note="折成 0 了")
        return super().signal(d, interaction)


def inj_b6(nodes, edges, injected: bool) -> Report:
    # ⚠️ 注入必须发生在**内核持有的那个插件**上 —— `record_usage` 用的是它。
    #    换的是**传给检查的参数**的话，检查照样绿：那个参数压根没被用到。
    plug = _SignalFolds() if injected else KeysetPlugin()
    kernel = Kernel(plug, make_items(nodes))
    kernel.build(plug.merge([]))
    for nid in sorted(nodes):
        kernel.insert(nid)
    rep = _rep()
    b6_signal_not_collapsed(kernel, plug, rep)
    return rep


# ═══ B7 ══════════════════════════════════════════════════════════════════════

def inj_b7(nodes, edges, injected: bool) -> Report:
    kernel, _ = build_keyset(nodes)
    if injected:
        kernel._dirs["DX"] = Direction(  # noqa: SLF001
            did="DX", rank=2, payload=(frozenset(), frozenset()),
            witness=(), origin=ORIGIN_SPLIT, parent=kernel.root.did,
        )
    rep = _rep()
    b7_witness_complete(kernel, rep)
    return rep


# ═══ B8 ══════════════════════════════════════════════════════════════════════

class _LeakyKernel(Kernel):
    """失效注入：插入之后**顺手**动一个路径外的方向。"""

    def insert(self, item_id: str):
        path = super().insert(item_id)
        pathset = set(path)
        for d in self.all_directions():
            if d.did not in pathset and (d.parent is None or d.parent not in pathset):
                self._members.setdefault(d.did, set()).add(item_id)  # noqa: SLF001
                break
        return path


def inj_b8(nodes, edges, injected: bool) -> Report:
    def make() -> Kernel:
        plug = KeysetPlugin()
        k = (_LeakyKernel if injected else Kernel)(plug, make_items(nodes))
        k.build(plug.merge([]))
        return k

    rep = _rep()
    b8_invalidation_local(make, sorted(nodes), rep)
    return rep


# ═══ B9 ══════════════════════════════════════════════════════════════════════

def inj_b9(nodes, edges, injected: bool) -> Report:
    kernel, _ = build_keyset(nodes)
    if injected:
        from ldv.core.direction import Event

        saved = kernel.ledger._events[1]                        # noqa: SLF001
        kernel.ledger._events[1] = Event(                       # noqa: SLF001
            seq=saved.seq, kind=saved.kind, did=saved.did,
            detail={**saved.detail, "改写": True},
        )
    rep = _rep()
    b9_ledger_append_only(kernel, rep)
    return rep


# ═══ B10 ═════════════════════════════════════════════════════════════════════

def inj_b10(nodes, edges, injected: bool) -> Report:
    kernel, plug = build_keyset(nodes)
    orig = QueryResult.render
    if injected:
        QueryResult.render = lambda self: "同一个输出"          # noqa: ARG005
    try:
        rep = _rep()
        b10_three_states_separable(kernel, plug, rep)
        return rep
    finally:
        QueryResult.render = orig


# ═══ B11 ═════════════════════════════════════════════════════════════════════

def inj_b11(nodes, edges, injected: bool) -> Report:
    kernel, plug = build_keyset(nodes)
    if injected:
        kernel.usage.append(SimpleNamespace(did=kernel.root.did, propensity=None))
    rep = _rep()
    b11_propensity_complete(kernel, plug, rep)
    return rep


# ═══ B12 ═════════════════════════════════════════════════════════════════════

def inj_b12(nodes, edges, injected: bool) -> Report:
    extra = []
    if injected:
        extra = [_tmp_source(
            "class K:\n"
            "    def __init__(self):\n"
            "        self.score = 0.0\n"
            "        self.global_weight = 1.0\n"
            "    def rank_score(self, ds):\n"
            "        return sum(ds) / len(ds)\n"
        )]
    rep = _rep()
    b12_no_global_scalar(rep, extra_sources=extra)
    return rep


# ═══ B13 ═════════════════════════════════════════════════════════════════════

def inj_b13(nodes, edges, injected: bool) -> Report:
    kernel, _ = build_keyset(nodes)
    if injected:
        kernel._dirs["DX"] = Direction(  # noqa: SLF001
            did="DX", rank=2, payload=(frozenset(), frozenset()),
            witness=("DX",), origin=ORIGIN_SPLIT, parent=kernel.root.did,
        )
    rep = _rep()
    b13_rank_well_founded(kernel, rep)
    return rep


# ═══ B14 ═════════════════════════════════════════════════════════════════════

def inj_b14(nodes, edges, injected: bool) -> Report:
    extra = []
    if injected:
        extra = [_tmp_source(
            "from ldv.plugins.keyset import KeysetPlugin\n"
            "\n"
            "def sneak(params):\n"
            "    params.exogenous['outermost_intent'] = '改了'\n"
            "    return KeysetPlugin\n"
        )]
    rep = _rep()
    b14_exogenous_boundary(rep, extra_sources=extra)
    return rep


# ═══ B15 ═════════════════════════════════════════════════════════════════════

class _SplitOnOverflow(Kernel):
    """结构注入：**边插边长**（B-tree 的上溢分裂）——

        方向攒够 `CAP` 项才劈，而且劈的时候只看到**当时已经有的成员**
        （没满就返回 `()`，并且**不标记已展开**）

    于是「先来的是谁」决定第一次分组 ⇒ 同数据、不同历史 ⇒ 不同结构。

    ⚠️ 这条注入是**实测挑出来的**。另外两种「看起来该红」的改法都不红：

        · 去掉 `build()` 的预装  ⇒ 根第一次展开只看到 1 项 ⇒ 根变叶，
          整棵树**退化成一个方向**；三种顺序仍然「同构」⇒ B15 照样绿。
          （正因为这个盲区，`B15` 现在加了一道非退化前提。）
        · `expand()` 劈之前不排序 ⇒ 同一集合的迭代序一致 ⇒ 看不出差别。

    只有「攒够才劈」真的让先来后到决定分组。
    """

    CAP = 6

    def build(self, root_payload: Any):
        root = super().build(root_payload)
        self._members[root.did] = set()        # 根从空开始，随插入长大
        return root

    def expand(self, d: Direction) -> tuple[Direction, ...]:
        if d.did in self._expanded:
            return self.children_of(d)
        if len(self._members.get(d.did, ())) < self.CAP:
            return ()                          # 未满：不劈，也**不标记已展开**
        return super().expand(d)


def inj_b15(nodes, edges, injected: bool) -> Report:
    def make() -> Kernel:
        plug = KeysetPlugin()
        k = (_SplitOnOverflow if injected else Kernel)(plug, make_items(nodes))
        k.build(plug.merge([]))
        return k

    rep = _rep()
    b15_reproducible(make, sorted(nodes), rep)
    return rep


# ═══ B16 ═════════════════════════════════════════════════════════════════════

class _WorstChild(Kernel):
    """健全性注入：插入时选**代价最大**的子方向（本该选最小）。

    这不是硬凑的坏代码 —— 它是「分配那一步写反了」的形态，
    而分配正是 `members ⊆ 覆盖` 唯一可能被破坏的地方。

    ⚠️ 这条注入同时打红 `B1`（40 条查询下）。**那是对的**：两个检查在这一点上重叠。
       它证明的是 `B16` **不看查询集** —— 把查询集缩到 3 条，`B1` 就绿了，`B16` 照红。
    """

    def insert(self, item_id: str):
        if item_id not in self.items:
            raise KeyError(item_id)
        path: list[str] = []
        d = self.root
        while True:
            path.append(d.did)
            kids = self.expand(d)
            if not kids:
                break
            nxt = max(kids, key=lambda k: (call_penalty(self.plugin, k, self.items[item_id]),
                                           k.did))
            d = nxt
        self._members.setdefault(d.did, set()).add(item_id)
        for did in path:
            self._members.setdefault(did, set()).add(item_id)
        self._path[item_id] = tuple(path)
        return tuple(path)


def inj_b16(nodes, edges, injected: bool) -> Report:
    plug = KeysetPlugin()
    k = (_WorstChild if injected else Kernel)(plug, make_items(nodes))
    k.build(plug.merge([]))
    for nid in sorted(nodes):
        k.insert(nid)
    rep = _rep()
    b16_members_covered(k, plug, rep)
    return rep


# ═══ B17 ═════════════════════════════════════════════════════════════════════

class _GiveUpEarly(KeysetPlugin):
    """`§K2` 判空注入：**能分也判分不开** —— 小组（≤ `LIMIT` 项）直接放弃。

    这不是硬凑的坏代码，它是「判据写得比实际能力弱」的形态：
    插件**有能力**分开那一组（组里混着不同键集），却**谎报**「分不开」。
    ⇒ 那些项只能挤进同一个叶 ⇒ 叶里混进多个不可分等价类。

    ⚠️ **为什么不用「内核平手时乱定序」当注入**：试过了，**不红**。
       `expand()` 的分配用 `(代价, 候选序号)` 取最小 —— 平手判给**靠前**那个，
       而候选的顺序由插件自己的返回顺序定，内核**不看** `did`。
       所以「让 `did` 变」动不了这个判据。
       **(b) 那一半只能被「代价依赖语义之外的东西」打破**，
       而那个形态在**合环图**上才出现（见 `run_tests.test_equivalence` ⑤）。
    """

    LIMIT = 4

    def split(self, parent, items):  # noqa: ANN001, ANN201
        if len(items) <= self.LIMIT:
            return None                     # 谎报：能分也**声明**分不开
        return super().split(parent, items)


def inj_b17(nodes, edges, injected: bool) -> Report:
    from ldv.checks._fixtures import equiv_classes
    from ldv.checks.equivalence import b17_leaf_is_equivalence_class

    plug = _GiveUpEarly() if injected else KeysetPlugin()
    k = Kernel(plug, make_items(nodes))
    k.build(plug.merge([]))
    for nid in sorted(nodes):
        k.insert(nid)
    rep = _rep()
    b17_leaf_is_equivalence_class(k, equiv_classes("keyset", nodes, edges), rep)
    return rep


# ═══ B11b ════════════════════════════════════════════════════════════════════
#: B11 有**两条**判据（完备 + 位置敏感），所以注入也要两条。
#: 只验「缺倾向会红」的话，把曝光模型退回均匀**不会有任何东西变红** ——
#: 而那正是最容易被悄悄改回去的一处。

def inj_b11_uniform(nodes, edges, injected: bool) -> Report:
    """把曝光模型退回「按个数」：所有位次的倾向都相同。"""
    from ldv.core.interfaces import WeightedRecord

    orig = Kernel.record_usage
    if injected:
        def uniform(self, d, interaction):
            sig = self.plugin.signal(d, interaction)
            if sig is None:
                return None
            rec = WeightedRecord(did=d.did, outcome=float(sig.outcome), propensity=1.0)
            self.usage.append(rec)
            return rec

        Kernel.record_usage = uniform
    try:
        kernel, plug = build_keyset(nodes)
        rep = _rep()
        b11_propensity_complete(kernel, plug, rep)
        return rep
    finally:
        Kernel.record_usage = orig


# ═══ 度量也要能变（不是 B 系列） ══════════════════════════════════════════════
#
# B 系列是**判据**：基线绿、注入红。
# 「增量 ≡ 全量」（`checks/divergence.py`）是**度量**：它没有红不红，只有读数。
# ⇒ 它的注入验证问的是另一件事：**读数会不会跟着系统变？**
#    一个永远报同一个数的度量，和一条永远绿的检查一样没有信息量。
#
# ⚠️ 这条注入在「同构」那一列上**看不出来** —— 基线与注入**都是不同构**。
#    它只在**尺度**那一列上露出来（方向数 / 叶容量）。这正是「度量必须报多个数」
#    的理由：只报一个布尔，这个缺陷就被度量放过去了。


class _LeafIsTerminal(Kernel):
    """注入：把「分不开」写成**永久标记** —— 原来那处缺陷的形状。

    一旦某个方向被判过「分不开」，就**永远**是叶，即使成员集后来变了。
    ⇒ 树只在第一批项上长过层，之后插进来的项全堆进已有叶。
    """

    def expand(self, d: Any):  # noqa: ANN201
        if d.did in self._children:
            return self.children_of(d)
        if d.did in self._tried:            # ← 永久：不再随成员集变化重试
            return ()
        return super().expand(d)


def metric_scale_matches(nodes: Any, edges: Any, injected: bool) -> bool:
    """「增量与全量的**尺度**是否逐项相同」—— 基线 True，注入 False。"""
    from ldv.checks.divergence import divergence_profile

    cls = _LeafIsTerminal if injected else Kernel
    all_items = make_items(nodes)

    def build(subset_ids: list[str]):
        plug = KeysetPlugin()
        sub = sorted(set(subset_ids))
        k = cls(plug, {i: all_items[i] for i in sub})
        k.build(plug.merge([]))
        for i in sub:
            k.insert(i)
        return k, plug

    prof = divergence_profile(build, nodes, sorted(nodes), ks=(2, 6, 12))
    f = prof["全量"]
    return all((r["方向数"], r["最大叶容量"]) == (f["方向数"], f["最大叶容量"])
               for r in prof["各 k"].values())


# 「分批维护不许改变**分辨率**」—— 这是 `test_emergence` ③ 收窄之后留下的那条判据。
# ⚠️ 只查**叶容量**，不查方向数：`§I4` 允许插件定扇出之后，扇出在**首次展开时定形**
#    （§K3：已分配的项不许挪走）⇒ 「首次展开时组里有几个符号」取决于初始批次
#    ⇒ **方向数（记账）会变**。叶容量才是分辨率（§7.1）。
# 这条注入证明**收窄之后判据仍然抓得到那处旧缺陷**（叶是终态 ⇒ 项全堆进叶 ⇒ 叶容量爆）。

def metric_resolution_stable(nodes: Any, edges: Any, injected: bool) -> bool:
    """「分批维护是否不改变**最大叶容量**」—— 基线 True，注入 False。"""
    from ldv.checks._fixtures import sequences
    from ldv.plugins.reach import ReachPlugin
    from ldv.plugins.sequence import SequencePlugin

    cls = _LeafIsTerminal if injected else Kernel
    all_items = make_items(nodes)
    ids = sorted(nodes)

    cases = {
        "keyset": (lambda: KeysetPlugin(), lambda p: p.merge([])),
        "reach": (lambda: ReachPlugin(edges), lambda p: frozenset(nodes)),
        "sequence": (lambda: SequencePlugin(sequences(nodes, edges)), lambda p: frozenset({()})),
    }

    def leaf_cap(k: Any) -> int:
        return max((len(k.members_of(d)) for d in k.all_directions()
                    if not k.children_of(d)), default=0)

    for mk_plug, mk_root in cases.values():
        full_plug = mk_plug()
        full = cls(full_plug, {i: all_items[i] for i in ids})
        full.build(mk_root(full_plug))
        for i in ids:
            full.insert(i)
        inc_plug = mk_plug()
        inc = cls(inc_plug, {i: all_items[i] for i in ids[:6]})
        inc.build(mk_root(inc_plug))
        for i in ids[:6]:
            inc.insert(i)
        for i in ids[6:]:
            inc.insert(i, all_items[i])
        if leaf_cap(inc) != leaf_cap(full):
            return False
    return True


# 「覆盖嵌套」也是**度量**（`§I4` 带上父方向买到的那个东西）。
# 注入：A 的 `劈开` **不续父的 `forb`** —— 于是子方向重新收下父已排除的项。
# 基线 `覆盖(子) ⊆ 覆盖(父)`，注入后**越界**（实测 0 → 14 次）。

class _ForgetsParentForbid(KeysetPlugin):
    """注入：子方向把父的 `forb` 丢了（= 拿不到父方向时的必然结果）。"""

    def split(self, parent, items):  # noqa: ANN001, ANN201
        keysets = [frozenset(it["keys"]) for it in items]
        common = frozenset.intersection(*keysets) if keysets else frozenset()
        universe = frozenset().union(*keysets) if keysets else frozenset()
        cand = sorted(universe - common)
        if not cand:
            return None
        n = len(items)
        best = min(cand, key=lambda k: (abs(sum(1 for ks in keysets if k in ks) / n - 0.5), k))
        return ((common | {best}, frozenset()), (common, frozenset({best})))


def metric_cover_nesting(nodes: Any, edges: Any, injected: bool) -> bool:
    """「覆盖嵌套是否成立」—— 基线 True，注入 False。"""
    from ldv.checks.contract import cover_nesting_profile

    plug = _ForgetsParentForbid() if injected else KeysetPlugin()
    k = Kernel(plug, make_items(nodes))
    k.build(plug.merge([]))
    for i in sorted(nodes):
        k.insert(i)
    from ldv.checks._fixtures import keyset_queries

    return cover_nesting_profile(k, plug, keyset_queries(nodes))["越界次数"] == 0


# 「健全性」也是**度量**，而且它**必须在维护路径上量** —— 这是本设计里
# 「空转与通过长得一模一样」在**流程**上的那一例。
#
# 注入：A 的 `劈开` 把「当前成员的键集之交」（`common`）掺进子方向的 `req`。
#
# ⚠️ **这条注入在「一次建完」那条路上看不出来** —— 每个方向第一次展开时成员集
#    就是完整的，`common` 一定盖得住它们（实测 0/159 与基线相同）。
#    它只在**维护路径**上露出来（实测 123/224）：成员后来才长起来，
#    而 `req` 在劈开时就**冻结**了 ⇒ 子方向收不住「它没见过的项」。
# ⇒ 所以这个度量函数**只量维护路径**。若它去量一次建完，基线与注入都是绿的，
#    这条注入验证会**通过而毫无信息量** —— 那正是它要防的东西。

class _CommonInReq(KeysetPlugin):
    """注入：子方向的 `req` 掺进「当前成员」算出来的 `common`。"""

    def split(self, parent, items):  # noqa: ANN001, ANN201
        p_req, p_forb = parent.payload
        keysets = [frozenset(it["keys"]) for it in items]
        common = frozenset.intersection(*keysets) if keysets else frozenset()
        universe = frozenset().union(*keysets) if keysets else frozenset()
        cand = sorted(universe - common)
        if not cand:
            return None
        n = len(items)
        best = min(cand, key=lambda k: (abs(sum(1 for ks in keysets if k in ks) / n - 0.5), k))
        req = p_req | common                       # ← 注入：掺进 common
        return ((req | {best}, p_forb), (req, p_forb | {best}))


def metric_soundness(nodes: Any, edges: Any, injected: bool) -> bool:
    """「健全性在**维护路径**上是否成立」—— 基线 True，注入 False。"""
    from ldv.checks.contract import soundness_profile

    plug = _CommonInReq() if injected else KeysetPlugin()
    all_items = make_items(nodes)
    ids = sorted(nodes)
    k = Kernel(plug, {i: all_items[i] for i in ids[:6]})
    k.build(plug.merge([]))
    for i in ids[:6]:
        k.insert(i)
    for i in ids[6:]:
        k.insert(i, all_items[i])
    return soundness_profile(k, plug)["越界成员数"] == 0


# ═══ 驱动 ════════════════════════════════════════════════════════════════════

CASES: dict[str, Callable] = {
    "B1": inj_b1, "B2": inj_b2, "B3": inj_b3, "B4": inj_b4, "B5": inj_b5,
    "B6": inj_b6, "B7": inj_b7, "B8": inj_b8, "B9": inj_b9, "B10": inj_b10,
    "B11": inj_b11, "B12": inj_b12, "B13": inj_b13, "B14": inj_b14, "B15": inj_b15,
    "B16": inj_b16, "B17": inj_b17,
}

#: 同一条检查的**第二条**判据。键是标签，值是 `(判据编号, 注入函数)`。
#: 标签不参与 B1–B17 的注册表核对。
EXTRA_CASES: dict[str, tuple[str, Callable]] = {
    "B11·位置敏感": ("B11", inj_b11_uniform),
}


def _result(rep: Report, code: str) -> Tri:
    for a in rep.assertions:
        if a.code == code:
            return a.result
    return Tri.UNEXPANDED


def main() -> int:
    loaded = load()
    if loaded is None:
        print("⚠ 没有语料 —— 注入验证跑不了")
        return 0
    nodes, edges, _ = loaded

    print("═══ 注入验证：每条检查都要能红 ═══")
    failures: list[str] = []
    cases: list[tuple[str, str, Callable]] = [(c, c, fn) for c, fn in CASES.items()]
    cases += [(label, code, fn) for label, (code, fn) in EXTRA_CASES.items()]
    for label, code, fn in cases:
        try:
            base = _result(fn(nodes, edges, False), code)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{label}: 基线就抛异常 {type(exc).__name__}: {exc}")
            print(f"  ✗ {label}  基线抛异常：{type(exc).__name__}: {exc}")
            continue
        try:
            shot = _result(fn(nodes, edges, True), code)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{label}: 注入后抛异常 {type(exc).__name__}: {exc}")
            print(f"  ✗ {label}  注入后抛异常：{type(exc).__name__}: {exc}")
            continue

        ok = (base is Tri.YES) and (shot is Tri.NO)
        why = ""
        if base is not Tri.YES:
            why += f"基线不是绿的（{base}）"
        if shot is not Tri.NO:
            why += ("；" if why else "") + f"注入后没变红（{shot}）"
        print(f"  {'✓' if ok else '✗'} {label}  基线={base}  注入={shot}"
              + (f"   ← {why}" if why else ""))
        if not ok:
            failures.append(f"{label}: {why}")

    print()
    n = len(cases)

    # ── 度量那一条：问的不是「红不红」，是「读数会不会变」 ──────────────────
    for label, fn in (("度量·增量≡全量", metric_scale_matches),
                      ("度量·分辨率不变", metric_resolution_stable),
                      ("度量·覆盖嵌套", metric_cover_nesting),
                      ("度量·健全性（维护路径）", metric_soundness)):
        try:
            m_base = fn(nodes, edges, False)
            m_shot = fn(nodes, edges, True)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{label}: 抛异常 {type(exc).__name__}: {exc}")
            print(f"  ✗ {label}  抛异常：{type(exc).__name__}: {exc}")
            continue
        m_ok = m_base and not m_shot
        why = ""
        if not m_base:
            why += "基线就不一致（度量本来该说一致）"
        if m_shot:
            why += ("；" if why else "") + "注入后读数没变（度量对缺陷不敏感）"
        print(f"  {'✓' if m_ok else '✗'} {label}  基线一致={m_base}  "
              f"注入后仍一致={m_shot}" + (f"   ← {why}" if why else ""))
        if not m_ok:
            failures.append(f"{label}: {why}")
        n += 1

    if failures:
        print(f"═══ {len(failures)}/{n} 条没通过 ═══")
        for f in failures:
            print(f"  · {f}")
        return 1
    print(f"═══ {n}/{n} 条：基线绿、注入红 ═══")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
