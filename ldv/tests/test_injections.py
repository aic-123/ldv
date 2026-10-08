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

## 但「全绿」自己也得有个**范围**

上面的「每条检查都验过了」是个**全称句** —— 它说的是「**声明的每一个编号**」。
新加一条检查、忘了配注入，套件照样报「全绿」，而那个「全绿」对新增的编号
**一个字节的信息都没有**。同一句话，范围悄悄缩了，读起来一模一样。

⇒ 所以套件自己第 0 条就是**注册表自检**：`CASES` 的键 ∪ 副判据的编号，
必须**恰好等于** `run_checks` 声明要跑的那批（两个方向都报）。见 `_registry_gap`。

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

import itertools
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ldv.checks._fixtures import (  # noqa: E402
    build_incremental,
    build_keyset,
    build_reach,
    build_sequence,
    coverage_of,
    keyset_queries,
    load,
    items as make_items,
    make_builder,
    reach_queries,
    sequences,
)
from ldv.checks._framework import Report  # noqa: E402
from ldv.checks.abstraction import (  # noqa: E402
    READINGS,
    SUMMARIES,
    ReadingCtx,
    ViewSet,
    a1_soundness,
    a2_stable,
    a3_coarsest,
    a4_category,
    a5_ledger,
    a6_roundtrip,
    build_views,
    reading_ctx,
    subtree_of,
    warranted_of,
)
from ldv.core.views import view_parts  # noqa: E402
from ldv.checks.contract import (  # noqa: E402
    b1_no_false_negative,
    b2_merge_covers,
    b3_penalty_comparable,
    b4_split_is_partition,
    b5_decode_covers,
    b6_signal_not_collapsed,
)
from ldv.checks.coverage import (  # noqa: E402
    b16_members_covered,
    b18_cover_leak_baseline,
    b19_progress_guard,
    corpus_fingerprint,
    cover_leak_profile,
    progress_profile,
    soundness_profile,
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
from ldv.core.direction import EVENT_STAYED, ORIGIN_SPLIT, Direction  # noqa: E402
from ldv.core.interfaces import call_penalty  # noqa: E402
from ldv.core.kernel import Kernel, QueryResult  # noqa: E402
from ldv.core.tri import Tri  # noqa: E402
from ldv.core.views import ViewSpec, partition_of  # noqa: E402
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
                         keyset_queries(nodes), rep, path="批建")
    return rep


def inj_b1_maintenance(nodes, edges, injected: bool) -> Report:
    """`B1` 的**第二条**判据（维护路径）：ground truth 是「**维护之后**的成员 ∩ 查询」。

    ⚠️ 为什么不能只跑一条：`B1` 的 ground truth 里**有成员集**，而成员集在两条路径上
       不同（一次建完时它一开始就完整，维护时是后来才长起来的）⇒ 同一批查询
       问的其实是**另一个**成员集。「只跑一条路」与「两条路都跑」在汇总里长得一样。
    """
    all_items = make_items(nodes)
    ids = sorted(nodes)
    init = ids[:6]                                  # 与 `run_checks.MAINT_INIT` 一致
    plug = KeysetPlugin()
    k = Kernel(plug, {i: all_items[i] for i in init})
    k.build(plug.merge([]))
    for i in init:
        k.insert(i)
    for i in ids[6:]:
        k.insert(i, all_items[i])
    rep = _rep()
    b1_no_false_negative(k, _HitAllNo() if injected else plug,
                         keyset_queries(nodes), rep, path="维护")
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


def inj_b4_stay_unaccounted(nodes, edges, injected: bool) -> Report:
    """`B4` 的**第二条**判据（§10.2 出路 (4)）：**漏的必须条条有账**。

    ⚠️ 上面那条 `inj_b4` 注入的是「重叠」；这一条注入的是**账**。

    ## 为什么必须单列一条

    裸的 `∪members(子) == members(父)`（等式）在出路 (4) 之下必然破 —— 项可以
    **停在父方向**。所以判据是「`⊆` **且** 漏的恰好是账上那些」。

    带上 `⊆` 就**放宽了** —— 而放宽的东西必须**另有东西钉住**，否则
    「漏」会重新变得看不见（那正是本设计反复要消灭的形状）。
    ⇒ 这一条注入的就是「**放宽之后漏了但没记账**」：拿一个真的有滞留的结构，
      把账**抹掉**。

    判据两侧都要成立才算数：
        基线（账在）   漏 == 账 ⇒ **绿**
        注入（账抹掉） 漏 ≠ 账 ⇒ **红**

    ⚠️ 它**只**能在「真有滞留」的结构上验 —— 在批建路径上 `miss` 恒为空集，
       抹账与不抹账**都是绿的**（那正是「基线就绿的注入验证毫无信息量」）。
       ⇒ 所以这里用**先建 2 维护 34** 的 `sequence`：实测滞留 32 项。
    """
    ids = sorted(nodes)
    k = build_incremental(make_builder("sequence", nodes, edges), nodes, ids[:2], ids[2:])
    if injected:
        # 把「留在这一层」的账抹掉 —— 滞留项仍在父方向里，但**没有账**。
        # （只动 `_events`；`B4` 不查账本完整性，那是 `B9` 的事。）
        k.ledger._events = [e for e in k.ledger._events if e.kind != EVENT_STAYED]  # noqa: SLF001
    rep = _rep()
    b4_split_is_partition(k, rep)
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


def inj_b13_two_parents(nodes, edges, injected: bool) -> Report:
    """`B13` 的**第二条**判据：**树性**（每方向恰好一个父）。

    注入的是**最贴近判据的那条边** —— `_children` 是唯一写「父 → 子」的地方，
    所以把一个已有的子方向**再挂到第二个父**下面，就造出了一个 **DAG**：

        语义入度  1 → 2

    ⚠️ **这条注入是实测挑出来的**：改之前 `B13` 对这种结构**照样报「是」**
       （`outputs/_probe_b13_gap.py` 的读数），而 `§10.2 D` 的持久化路线
       正押在「入度 = 1」上 ⇒ 前提**没有任何检查守着**。

    ⚠️ **注入必须造在「父→子」这条边上，不能只改 `parent` 字段**：
       只改 `parent` 会让两条边**打架**（那是另一组断言），
       而入度本身没变 —— 那就测不到树性。
    """
    kernel, _ = build_keyset(nodes)
    if injected:
        parents = [d for d in kernel.all_directions() if kernel.children_of(d)]
        p1, p2 = parents[0], parents[1]
        victim = kernel.children_of(p2)[0]
        kernel._children[p1.did] = tuple(kernel._children[p1.did]) + (victim.did,)  # noqa: SLF001
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


class _NoRefusal(Kernel):
    """`B16` **维护路径**那条判据的注入：**关掉两条出路**。

        §10.2 出路 (1) 根记账        `insert` 先问 `命中(根, {x})`，只有「否」才不塞进结构
        §10.2 出路 (4) 项留在父方向   每个子方向都**证明**不收它 ⇒ 停在父这一层

    两者都走 `_refuses` ⇒ 一个 override 同时关掉，`insert` 退化成**改前**的行为：
    不看 `命中`，按最小代价一路塞到底。

    ⚠️ **必须用方向 C（`sequence`），不能用 A**：`keyset` 的劈开是**结构上**完备的
       （`x` 满足父 ⇒ 要么有 `best` 落子 1、要么没有落子 2），两条出路**从不触发**
       ⇒ 关掉它们什么也不改，注入红不了。
       `sequence` 是 k 叉，父的前缀后面那个符号来自**无穷字母表** ⇒ 两条出路真的会触发。

    ⚠️ **不能用「选代价最大的子方向」那种注入**（`_WorstChild` 的形态）：实测在维护
       路径上它会让方向数**指数增长**（36 项跑到 100 万个方向），是病态而不是缺陷。
       关出路就够 —— 病灶清楚、代价有界，而且**正好**是这一行要守的那件事。
    """

    def _refuses(self, d: Any, item_id: str) -> bool:  # noqa: ARG002
        return False


def inj_b16(nodes, edges, injected: bool) -> Report:
    plug = KeysetPlugin()
    k = (_WorstChild if injected else Kernel)(plug, make_items(nodes))
    k.build(plug.merge([]))
    for nid in sorted(nodes):
        k.insert(nid)
    rep = _rep()
    b16_members_covered(k, coverage_of("keyset", nodes, edges), rep, path="批建")
    return rep


def inj_b16_maintenance(nodes, edges, injected: bool) -> Report:
    """`B16` 的**第二条**判据（维护路径）：与批建那条**各判一次、各注入一次**。

    ⚠️ 为什么不能只跑一条：两条路径**不是同一件事** ——
       一次建完时，子方向的 payload 从**当时完整的**成员集算出来 ⇒ 天然盖得住；
       维护时成员是**后来才长起来的**，payload 在劈开时就冻结了 ⇒ 盖不住。
       「只跑一条路」与「两条路都跑」在汇总里长得**一模一样**，所以两条各占一行。

    ⚠️ 也不能只注入一条：批建那行的注入（`_WorstChild`）在维护路径上**不红** ——
       维护路径的问题**不是「分配写反了」，是「后来的项没人接」**。同一句话，
       两条路径上的形态不同 ⇒ 两条注入也不共用。
    """
    from ldv.plugins.sequence import SequencePlugin

    all_items = make_items(nodes)
    ids = sorted(nodes)
    init = ids[:6]                                  # 与 `run_checks.MAINT_INIT` 一致
    plug = SequencePlugin(sequences(nodes, edges))
    k = (_NoRefusal if injected else Kernel)(plug, {i: all_items[i] for i in init})
    k.build(frozenset({()}))                        # 根 = 空前缀：覆盖一切
    for i in init:
        k.insert(i)
    for i in ids[6:]:
        k.insert(i, all_items[i])
    rep = _rep()
    b16_members_covered(k, coverage_of("sequence", nodes, edges), rep, path="维护")
    return rep


# ═══ B18 ═════════════════════════════════════════════════════════════════════

class _DropBucket:
    """`B18` 注入：**只报前两个桶**，把其余的桶丢掉。

    ⚠️ 为什么用方向 C 而不是 A：`keyset` 的二分是**结构上**不漏的
       （`x` 满足父 ⇒ 要么有 `best` 落子 1、要么没有落子 2，两半合起来恰好是父的覆盖）
       ⇒ 想让它漏只能造假 payload，而那会立刻撞上 `§K2` 的「两侧非空」。
       `sequence` 是 k 叉，**少报几个桶**就真的漏 —— 这也正是 §10.2 对 C 描述的那条机制
       （父的覆盖是 `{x : p ⊑ seq(x)}`，而子方向只枚举了**当时见过的符号**）。
    """

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.name = inner.name

    def __getattr__(self, k: str) -> Any:
        return getattr(self.inner, k)

    def split(self, parent, items):  # noqa: ANN001, ANN201
        got = self.inner.split(parent, items)
        if got is None or len(got) <= 2:
            return got
        return got[:2]                      # ← 注入：丢掉其余的桶


def _drop_bucket_plugin(nodes, edges, injected: bool):
    from ldv.plugins.sequence import SequencePlugin

    seqs = sequences(nodes, edges)
    inner = SequencePlugin(seqs)
    return _DropBucket(inner) if injected else inner


def inj_b18(nodes, edges, injected: bool) -> Report:
    plug = _drop_bucket_plugin(nodes, edges, injected)
    k = Kernel(plug, make_items(nodes))
    k.build(frozenset({()}))
    for nid in sorted(nodes):
        k.insert(nid)
    cover = coverage_of("sequence", nodes, edges)
    obs = {f"sequence|batch": cover_leak_profile(k, cover)}
    # baseline 取**当前**文件 —— 批建路径三个方向都是 0 漏，所以任何一处漏都是「新增」。
    # ⚠️ 指纹必须一起给：`B18` 的第 0 条会先比语料指纹，对不上就**跳过**。
    #    注入验证要验的是**判据**，不是「跳过」—— 所以这里必须造出**真跑时会出现的**
    #    那种配置（基线冻在本语料上），否则验的是一个生产里不存在的形态。
    fp = corpus_fingerprint(nodes, edges)
    rep = _rep()
    b18_cover_leak_baseline(obs, rep, fp,
                            {"语料": fp,
                             "基线": {"sequence|batch": {"漏项数": 0, "漏的对数": 0}}})
    return rep


# ═══ B19 ═════════════════════════════════════════════════════════════════════

class _SplitOffOne:
    """`B19` 注入：**每次只切掉一个**锚点，其余全给另一侧。

    ⇒ 两侧的覆盖**都不比父小**（环上任意非空子集都撑满整个环）
    ⇒ 每次展开都「没变细」，而链长 = 项数 − 1（**不靠平衡二分**）。

    ## ⚠️ 两条**走不通**的注入路，写在这里免得下一个人重走

        路一  「**一个**子退回父 payload」（**原注入**）
              ⇒ 另一侧的子仍然**严格更小** ⇒ 新谓词下**不算**没变细 ⇒ 注入不红
        路二  「**每个**子都退回父 payload」
              ⇒ 两侧的代价都是 0 ⇒ 内核把成员全判给**靠前**那个
              ⇒ 另一侧为空 ⇒ `§K2` 判「分不开」⇒ **这一层根本不建** ⇒ 判据「未展开」

    路二的根因值得记住：`reach` 的 `代价 == 0 ⟺ 项 ∈ payload`（**不是** ∈ 覆盖）。
    所以「两侧覆盖都 ⊇ 父」时，成员在两侧**都**是 0 分 ⇒ 必然平手。

    ⇒ 能红的形状只能是「**两侧代价可区分，但覆盖都不变小**」——
      这正是本类：一侧拿 1 个锚点、另一侧拿其余，各自的成员在自己那侧都是 0 分。
    """

    def __init__(self, inner):  # noqa: ANN001
        self.inner = inner
        self.name = inner.name

    def __getattr__(self, k):  # noqa: ANN201
        return getattr(self.inner, k)

    def split(self, parent, items):  # noqa: ANN001, ANN201
        ids = sorted(str(it["id"]) for it in items)
        if len(ids) < 2:
            return None
        return (frozenset({ids[0]}), frozenset(ids[1:]))


def _cycle(n: int = 16):
    """一个 `n` 项的**有向环**：`c00 → c01 → … → c{n-1} → c00`。

    ⚠️ **为什么 `B19` 的夹具必须是环、不能是链**（2026-10-07 改；实测见
       `outputs/_probe_b19_injection.py`）：

        链   反向锥逐级**严格嵌套** ⇒ 任何真子集的覆盖都严格更小
             ⇒ 「没变细」**按构造不可能出现** ⇒ 判据在这个夹具上**永远绿**
        环   每一项的反向锥都 = **整个环**（强连通）
             ⇒ 任意非空锚点集都撑满同一个覆盖 ⇒ 「没变细」可以**连续成立**

    ⇒ 旧夹具是一条 16 项的**链**，那是给**旧谓词**（`细化量 ≤ 0`）造的 ——
      旧谓词量的是「切得多**不匀**」，链上到处都是（实测 31/31）；
      新谓词量的是「有没有真**变细**」，链上一次都没有（实测 **0/31**）。
      **谓词换了聚合（`max` → `min`），夹具必须跟着换。** 两者是一对。
    """
    from ldv.corpus.loader import Node

    ids = [f"c{i:02d}" for i in range(n)]
    nodes = {i: Node(id=i, fields={"type": i}, keys=frozenset({i})) for i in ids}
    edges = {ids[k]: frozenset({ids[(k + 1) % n]}) for k in range(n)}
    return nodes, edges


def inj_b19(nodes, edges, injected: bool) -> Report:
    """**基线绿 + 注入红**（同一个环、同一个方向、同一个 oracle，只有 `split` 不同）：

        基线（原插件，平衡二分）      最长没变细连续段 = log2(16) = **4**  < 10 ⇒ 绿
        注入（每次只切一个）          最长没变细连续段 = 16 − 1   = **15** ≥ 10 ⇒ 红
    """
    from ldv.plugins.reach import ReachPlugin

    dn, de = _cycle(16)
    inner = ReachPlugin(de)
    plug = _SplitOffOne(inner) if injected else inner
    k = Kernel(plug, make_items(dn))
    k.build(frozenset(dn))
    for nid in sorted(dn):
        k.insert(nid)
    rep = _rep()
    b19_progress_guard(progress_profile(k, coverage_of("reach", dn, de)), rep)
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
    b17_leaf_is_equivalence_class(k, equiv_classes("keyset", nodes, edges), rep, "keyset")
    return rep


# ═══ B20 ═════════════════════════════════════════════════════════════════════
#
# `B20` 的谓词是**精确整数**判据：`count_child[l]·|父| == count_parent[l]·|子|`
# ⇒ 「空转」= 子方向是父方向的**等比子样本**。
#
# 这条判据有个副作用，它决定了夹具**只能怎么造**：
#
#     **父一旦是纯标签**，子必然也是纯标签 ⇒ 那一刀起往下**每一刀都空转**
#     ⇒ 真插件切出纯标签子树之后，**基线自己就红** ⇒ 对照失效
#
# 实测过、被否掉的三种夹具（都栽在这一条，见 `outputs/_probe_b20_injection.py`）：
#
#     成对夹具（每对 1×L0 + 1×L1，靠 `pair` 键切）
#         基线先挑 `type=L0` ⇒ 切出纯标签子树 ⇒ 基线自己红；
#         想让基线不挑 `type`，就得把标签做成不均衡，而不均衡又凑不出等比块
#     纯标签子树做成「键完全相同」的叶子
#         真插件在那里 `candidates` 为空 ⇒ 确实不空转；
#         但注入方**也没有键可用了** ⇒ 注入切不动
#     标签取**高位**位
#         前几刀的子方向与父**同分布**（各标签等比稀释）⇒ 基线自己就空转
#
# ⇒ 结论：**「同一个夹具 + 真插件」当基线，与「在同一个夹具上做一次标签盲的
#   切分」这两件事互斥** —— 基线要不红，就必须永远切不出纯标签方向；
#   而「永远切不出纯标签方向」就等于「标签与切分轴正交」，
#   正交又正是注入方要利用的东西。
#
# ⇒ 所以夹具、插件、树**全不动**，只换 **oracle**。`B20` 判的是
#   「展开有没有在**标签**上产出东西」，能让它红的本来就不是某一种 `split`，
#   而是「标签与切分轴无关」这件事本身。


def _labeled_bits(bits: int, label_bits: int):
    """`2**bits` 项；每项带 `bits` 个位键 `b{k}`；`type` 按 `label_bits` 取低若干位。

    `b{k}` 的每个取值 = 「下标 ≡ 某个值 mod 2^{k+1}」的那批项 ⇒
    `KeysetPlugin` 选「最接近 1/2 的键」，所有位键都在 1/2 ⇒ 它**逐位二分**，
    树是深度 `bits` 的平衡二叉树。

    ⚠️ 键集里**始终**带 `type=L0…`（两份 oracle 都认得出这是标签轴，
       见 `_fixtures.label_bearing`）—— 这正是要验的东西：判据的**适用范围**
       不该因为标签怎么指派而变。
    """
    from ldv.corpus.loader import Node

    mod = 1 << label_bits
    nodes: dict[str, Node] = {}
    for i in range(1 << bits):
        nid = f"n{i:04d}"
        keys = {f"type=L{i % mod}"} | {f"b{k}={((i >> k) & 1)}" for k in range(bits)}
        nodes[nid] = Node(id=nid, fields={"type": f"L{i % mod}", "i": i},
                          keys=frozenset(keys))
    return nodes, {nid: frozenset() for nid in nodes}


def inj_b20(nodes, edges, injected: bool) -> Report:
    """**基线绿 + 注入红**（同一个夹具、同一个插件、同一棵树 —— 只有 oracle 不同）：

        基线  `type = L{i mod 64}`（低 6 位）     最长语义空转连续段 =  **4**  < 10 ⇒ 绿
        注入  `type = L{popcount(i) mod 2}`      最长语义空转连续段 = **11** ≥ 10 ⇒ 红

    注入那版为什么**每一刀都空转**：位键 `b{k}` 的每个取值都是
    「下标 ≡ 某个值 mod 2^{k+1}」，这种集合里剩下的自由位至少还有一个，
    而「自由位的 popcount 奇偶」正好各占一半 ⇒ 任何方向都恰好 50/50
    ⇒ 子与父**精确同分布** ⇒ 空转。

    ⚠️ 注入那版的最长连续段 = **树深 − 1**：最底下那一刀是「2 项父 → 2 个单项子」，
       单项子的分布与 50/50 的父**不同分布** ⇒ 那一刀不空转，链在它那里断掉。
       所以树深必须 ≥ 12 才能把连续段推到 ≥ 10 —— 这是 `bits = 12` 的理由。
    """
    from ldv.checks._fixtures import label_bearing, labels as _labels
    from ldv.checks.refinement import b20_semantic_refinement, refinement_profile

    dn, _de = _labeled_bits(12, 6)
    if injected:
        for nd in dn.values():
            nd.fields["type"] = f"L{bin(nd.fields['i']).count('1') % 2}"

    plug = KeysetPlugin()
    k = Kernel(plug, make_items(dn))
    k.build(plug.merge([]))
    for nid in sorted(dn):
        k.insert(nid)
    rep = _rep()
    prof = refinement_profile(k, _labels(dn))
    b20_semantic_refinement(prof, rep, judged=label_bearing("keyset", dn))
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
    """注入：把「分不开」写成**永久标记** —— 「一个布尔看不出来」的那种形状。

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
    plug = _CommonInReq() if injected else KeysetPlugin()
    all_items = make_items(nodes)
    ids = sorted(nodes)
    k = Kernel(plug, {i: all_items[i] for i in ids[:6]})
    k.build(plug.merge([]))
    for i in ids[:6]:
        k.insert(i)
    for i in ids[6:]:
        k.insert(i, all_items[i])
    return soundness_profile(k, coverage_of("keyset", nodes, edges))["越界成员数"] == 0


# 「覆盖不漏」（§1 表**第四行**，⬜ 2026-10-07 已降级）也是度量。注入：`sequence` 的 `劈开`
# **少报桶** —— 父的覆盖里那些「下一个符号没被枚举到」的项，任何子方向都收不住。
# ⇒ 这正是 §10.2 对 C 描述的那条机制。
# ⚠️ 但它**不是**「与健全性并列的另一条独立机制」：实测漏项与滞留项是**同一批**
#    （16 == 16，双向差 0，见 `outputs/_probe_leak_vs_stay.py`）⇒ 两者是同一条件的两种处置。
#    这里注入的「少报桶」**同时**造出漏与滞留，正是这一点的直接证据。

def metric_cover_leak(nodes: Any, edges: Any, injected: bool) -> bool:
    """「覆盖不漏是否成立」—— 基线 True，注入 False。"""
    plug = _drop_bucket_plugin(nodes, edges, injected)
    k = Kernel(plug, make_items(nodes))
    k.build(frozenset({()}))
    for nid in sorted(nodes):
        k.insert(nid)
    return cover_leak_profile(k, coverage_of("sequence", nodes, edges))["漏项数"] == 0


# 「细化量」也是度量：**每次展开的覆盖细化量**。注入：每次只切掉一个锚点。
# ⚠️ 夹具是**环**不是链 —— 链上「没变细」按构造不可能出现（见 `_cycle`）。
#    读数在链上恒为 0，注入也动不了它 ⇒ 那会是一条**假绿**的度量对照。

def metric_progress(nodes: Any, edges: Any, injected: bool) -> bool:
    """「最长没变细连续段」这个读数 —— 基线 **4**（平衡二分的深度 `log2(16)`），注入 **15**。"""
    from ldv.plugins.reach import ReachPlugin

    dn, de = _cycle(16)
    inner = ReachPlugin(de)
    plug = _SplitOffOne(inner) if injected else inner
    k = Kernel(plug, make_items(dn))
    k.build(frozenset(dn))
    for nid in sorted(dn):
        k.insert(nid)
    return progress_profile(k, coverage_of("reach", dn, de))["最长没变细连续段"] == 4


# ═══ §A1–§A6（流程 E · 抽象层）════════════════════════════════════════════════
#
# 判据本体在 `checks/abstraction.py`，算法在 `core/views.py`。
# 注入的形态都一样：**在真链路上装出视图集合，然后只动那个视图集合的一处**。
#
# ## ⚠️ 这里的 `P` / `E` 是**测试自己写的** —— 这不违反 §K9
#
# §K9 / `B14` 禁的是「**系统**替人猜 `P` / `E`」：生产路径只能从
# `ldv/checks/view_spec.json` 读（见 `run_checks.view_report`，读不到就**跳过**）。
# **测试就是人的代言**，所以测试里构造 `P` / `E` 不但允许，而且是**必须**的 ——
# 否则这六条判据的注入就挂在「那个配置文件在不在」上：
# 文件一没，基线就塌，注入验证报出来的是**配置缺失**，不是**判据能不能红**。
#
# ⇒ 分工写清楚，免得下一个人以为哪边漏了：
#
#     注入验证（本段）          「判据能不能红」        —— 自带 `P` / `E` / 读数声明 / 账
#     `run_tests.test_view_spec_file`  「committed 的声明成不成立」—— 读那个文件
#
# ## `P` / `E` 取什么
#
#     `E = 父→子`（内核自己的树边）
#     `P = {U}`（人**没有先验意见**：所有方向同属一组）
#
# ⚠️ **`E` 的方向**：`E⁻¹(B)` 是「**能走到** `B` 的方向」，所以 `(父, 子) ∈ E`
#    读作「父能走到子」。写反了**不会报错**，只会得到一个「看着也在跑、
#    答案是另一个划分」的实现（`core/views._preimage` 的 ⚠️ 专门记了这条）。
#
# `P = {U}` 是 `(P, E)` 里信息量**最低**的一档 —— 正因为如此它最能说明
# 「`Q` 唯一 ⇒ 视图集合不用挑」：一点先验都不给，答案照样是唯一的。


def _view_chain(nodes: Any, edges: Any, head: int | None = None):
    """从**真内核**读出方向与树边，装成 `(spec, kernel, plugin, cover)`。

    `head` 只取前 `head` 个方向 —— 给 `§A3` 那条**走暴力 oracle** 的注入用：
    `coarser_stable_exists` 的候选数是 `∏ Bell(组内 Q 块数)`，全量 25 个方向时
    `Bell(25) ≈ 4.6e18` ⇒ 撞上限**跳过**（那是对的，见 `core/views._bell`）。
    想让它真的**搜完**并且**搜出结果**，`universe` 必须小到 `Bell` 撑得住。
    """
    kernel, plugin = build_keyset(nodes)
    dirs = kernel.all_directions()
    U = tuple(d.did for d in dirs)
    if head is not None:
        U = U[:head]
    keep = set(U)
    rel = frozenset((d.did, c.did) for d in dirs if d.did in keep
                    for c in kernel.children_of(d) if c.did in keep)
    spec = ViewSpec(universe=U, partition=(frozenset(U),), relation=rel)
    return spec, kernel, plugin, coverage_of("keyset", nodes, edges)


def _view_report(
    vs: ViewSet, *, kernel: Any = None, cover: Any = None, plugin: Any = None,
    decls: list[dict[str, Any]] | None = None,
    entries: list[tuple[str, str, str]] | None = None,
    a6kw: dict[str, Any] | None = None,
) -> Report:
    """跑视图侧的判据。`kernel` / `decls` / `entries` / `a6kw` 不给就**不跑**那一条。

    ⚠️ 不给就**不跑**，而不是「跑成跳过」：注入要验的是**那一条判据**，
       把别的判据也塞进来会让「谁红的」分不清。驱动只读 `code` 那一条。
    """
    rep = _rep()
    a1_soundness(vs, rep)
    a2_stable(vs.spec, vs.q, rep)
    a3_coarsest(vs.spec, vs.q, rep)
    if kernel is not None and decls is not None:
        a4_category(view_parts(vs.q, lambda d: subtree_of(kernel, d)),
                    reading_ctx(kernel, vs.spec), decls, rep)
    if kernel is not None and entries is not None:
        a5_ledger(entries, vs, lambda did, item: warranted_of(kernel, did, item),
                  frozenset(kernel.items), rep)
    if a6kw is not None:
        with tempfile.TemporaryDirectory() as td:
            a6_roundtrip(vs, kernel, cover, plugin, rep,
                         path=Path(td) / "views.json", **a6kw)
    return rep


def inj_a1(nodes: Any, edges: Any, injected: bool) -> Report:
    """`§A1` 注入：把某张视图的**具体化掐掉一项**。

        基线（真最粗稳定细化 + 真 `合并`）  具体化 ⊇ 声明收着的 ⇒ **绿**
        注入（掐掉一个成员）                那一项没了       ⇒ **红**

    ⚠️ 掐的必须是 `covered` 里的项 —— 掐一个本来就不在 `covered` 里的项
       **不会**造成违规（`§A1` 只查「少了」，不查「多了」）。
       所以这里从 `covered` 里挑，挑不到就**报错**（不是静默跳过）：
       那说明基线本身已经不对了，是另一回事。

    ⚠️ 这条注入是**判据的独立内容**的直接证据：同样的内核、同样的 `P`/`E`，
       `B16` 在这一处**照样绿**（它逐方向判，不看合并）—— 见
       `run_tests.test_view_a1_not_implied_by_b16`。
    """
    spec, kernel, plugin, cover = _view_chain(nodes, edges)
    vs = build_views(kernel, spec, cover, plugin)
    if injected:
        host = max(vs.views, key=lambda v: (len(v.covered), v.vid))
        if not host.covered:
            raise AssertionError("基线里没有任何视图声明收着原始项 ⇒ 掐一项掐不出违规")
        victim = sorted(host.covered)[0]
        vs = replace(vs, views=tuple(
            replace(v, concretization=v.concretization - {victim})
            if v.vid == host.vid else v for v in vs.views))
    return _view_report(vs)


def inj_a2(nodes: Any, edges: Any, injected: bool) -> Report:
    """`§A2` 注入：把 `Q` **粗化一格**（合并两块）⇒ 有一块跨在 `E⁻¹(B₂)` 内外 ⇒ 红。

    ⚠️ **合并哪两块不影响结论** —— 实测在全量 spec 上「稳定合并对」是**空集**
       （8 块两两合并、28 对，没有一对稳定）。⇒ 这条注入不是**挑出来的**，
       是**任意一对**都红。挑出来的注入只能证明「存在一个红」，
       证明不了「判据真的在判」。

    ⚠️ `§A3` 在这条注入上也红（不稳定 ⇒ 不是那个唯一解）。**那是对的**，
       两条的**重叠**与各自**额外的覆盖面**写在 `a3_coarsest` 的 docstring 里。
    """
    spec, kernel, plugin, cover = _view_chain(nodes, edges)
    vs = build_views(kernel, spec, cover, plugin)
    if injected:
        if len(vs.q) < 2:
            raise AssertionError(f"基线 `Q` 只有 {len(vs.q)} 块 ⇒ 「合并两块」做不出来")
        vs = replace(vs, q=partition_of([vs.q[0] | vs.q[1]] + list(vs.q[2:])))
    return _view_report(vs)


def inj_a3(nodes: Any, edges: Any, injected: bool) -> Report:
    """`§A3` 注入**甲**：喂一个**未到不动点**的 `Q`（`Q := P`，迭代 **0 轮**）。

        基线（真不动点）      没有更粗的稳定划分 ⇒ **绿**
        注入（`Q := P`）      `{U}` 里混着叶方向 ⇒ 它**自己就不稳定** ⇒ **红**

    设计稿 §9 那张表的 `§A3` 行写的就是这一条（「喂一个未到不动点的 `Q`」）。

    ⚠️ 它走的是 `§A3` 的**第二条**红（`Q` 自己不稳定），而这条**`§A2` 也红**。
       能验出 `§A3` **独立内容**的是下一条（`inj_a3_not_coarsest`）。
    """
    spec, kernel, plugin, cover = _view_chain(nodes, edges)
    vs = build_views(kernel, spec, cover, plugin)
    if injected:
        vs = replace(vs, q=partition_of(spec.partition))
    return _view_report(vs)


def inj_a3_not_coarsest(nodes: Any, edges: Any, injected: bool) -> Report:
    """`§A3` 注入**乙**：喂一个**稳定但更细**的 `Q`（离散划分）⇒ 走「存在更粗的稳定划分」。

    ## ★ 为什么非有这一条不可

    设计稿 §9 那张表里，`§A3` 的**红条件**列写的是「存在更粗的稳定划分 ⇒ 红」，
    而**注入**列写的是「未到不动点」—— 那一条走的是**另一个**红分支
    （`Q` 自己不稳定），`§A2` 也红。⇒ 照字面只配那一条注入的话，
    **表里声明的那个红条件一次都没被验过**，而套件照样报「全绿」。

    能且只能验到那个分支的形态是「**稳定**、但**不是最粗**」：

        离散划分**总是稳定**（小定理：单元素块对任何 `E⁻¹(B)` 要么整个在里面、
        要么整个在外面）⇒ `§A2` **绿**，而 `§A3` **红**。

    ⇒ 这条注入是「`§A3` 有独立于 `§A2` 的内容」的**唯一**证据。

    ## 为什么用受限 `universe`

    暴力 oracle 要**搜完**才能给出「有」；`Bell(25) ≈ 4.6e18` 会撞上限
    （那时报**跳过**，不是红 —— 那是对的，但不是这条注入要验的东西）。
    ⇒ 取前 6 个方向：`|Q| = 4`、`Bell(4) = 15` ⇒ 毫秒级搜完。
    ⚠️ 若基线的 `Q` 恰好**就是**离散划分，这条注入**红不了**（两个划分重合）
       ⇒ 那种情况下**报错**，不静默放过。
    """
    spec, kernel, plugin, cover = _view_chain(nodes, edges, head=6)
    vs = build_views(kernel, spec, cover, plugin)
    if len(vs.q) >= len(spec.universe):
        raise AssertionError(
            f"受限 spec 上基线 `Q` 已经是离散划分（{len(vs.q)} 块 / "
            f"{len(spec.universe)} 个方向）⇒ 离散注入与基线重合，红不了")
    if injected:
        vs = replace(vs, q=partition_of([frozenset({x}) for x in spec.universe]))
    return _view_report(vs)


# ═══ §A4 ═════════════════════════════════════════════════════════════════════

def _median_witness(ctx: ReadingCtx) -> tuple[list[str], list[str]]:
    """从**真数据**里搜一组「定长摘要相同、中位数不同」的方向 —— 见证不许编。

    ⚠️ 编一个见证（照抄别处的方向 id）在真链路上可能**根本不成立**，
       那时 `§A4` 报红是**对的**，而注入会以为「基线该是绿的」——
       于是失败被记在判据头上，真正的原因（见证是编的）看不见。

    ⚠️ 只搜**三元素**集合：两个元素的集合只要和相同，中位数必然相同
       （`(a+b)/2`），永远给不出见证。`C(25,3) = 2300` ⇒ 毫秒级。
    """
    f, _ = READINGS["中位数"]
    sf = SUMMARIES["计数与和"]
    buckets: dict[Any, list[frozenset[str]]] = {}
    for combo in itertools.combinations(sorted(ctx.value_of), 3):
        S = frozenset(combo)
        buckets.setdefault(sf(S, ctx), []).append(S)
    for group in buckets.values():
        for A, B in itertools.combinations(group, 2):
            if abs(float(f(A, ctx)) - float(f(B, ctx))) > 1e-9:
                return sorted(A), sorted(B)
    raise AssertionError("真链路上找不到中位数的见证 ⇒ 这条注入做不出来（报错，不静默跳过）")


def inj_a4(nodes: Any, edges: Any, injected: bool) -> Report:
    """`§A4` 注入：把 `中位数` 从 `holistic` **改标成** `distributive`。

        基线  三类各一条（`计数` distributive / `平均` algebraic /
              `中位数` holistic + **真数据里搜出来的**见证）  ⇒ **绿**
        注入  `中位数` 标成 distributive（`G=取平均`）        ⇒ **红**

    设计稿 §9 那张表的 `§A4` 行逐字就是这一条：「把 `Median` 标成 distributive ⇒ 红」。

    ⚠️ 这条注入只改**一条**声明，别的三条一个字没动 ⇒ 红只可能来自那一条
       （`a4_category` 的 detail 会印出是哪条、实测多少、声称多少）。
       若把基线整个换掉，就分不清「红」是注入造成的还是基线本来就不行。
    """
    spec, kernel, plugin, cover = _view_chain(nodes, edges)
    vs = build_views(kernel, spec, cover, plugin)
    ctx = reading_ctx(kernel, spec)
    A, B = _median_witness(ctx)
    decls: list[dict[str, Any]] = [
        {"名": "计数", "类别": "distributive", "G": "取和"},
        {"名": "平均", "类别": "algebraic", "摘要": "计数与和", "H": "和除计数"},
        {"名": "中位数", "类别": "holistic", "摘要": "计数与和",
         "见证": {"摘要": "计数与和", "A": A, "B": B}},
    ]
    if injected:
        decls[-1] = {"名": "中位数", "类别": "distributive", "G": "取平均"}
    return _view_report(vs, kernel=kernel, decls=decls)


# ═══ §A5 ═════════════════════════════════════════════════════════════════════

def _a5_account(kernel: Any, spec: ViewSpec) -> list[tuple[str, str, str]]:
    """一份**条条成立**的账：每个方向的**结构停留项** = `members(d) \\ ∪ members(子)`。

    ⚠️ **不直接用 `ledger_entries`**：批建路径上账**本来就是空的**
       （`滞留 == 0`、`根覆盖之外 == 0`）⇒ `§A5` 报**跳过**，而注入验证要求
       **基线绿**。跳过不是绿。⇒ 由测试（人的代言）给一份非空且条条成立的账。

    ⚠️ 这份账是不是「恰好该有那些条」是 **`B4` 的事**，`§A5` 只判**可指认**。
       两条判同一件事会让「红」分不清是谁的（设计稿 §9 的 `§A5` 行写了这条边界）。
    """
    out: list[tuple[str, str, str]] = []
    for did in spec.universe:
        d = kernel.direction(did)
        kids: set[str] = set()
        for c in kernel.children_of(d):
            kids |= set(kernel.members_of(c))
        for item in sorted(set(kernel.members_of(d)) - kids):
            out.append((did, item, "结构停留：子方向都不收它"))
    return out


def inj_a5(nodes: Any, edges: Any, injected: bool) -> Report:
    """`§A5` 注入：账里有一条的**方向指不到任何视图**。

        基线  一份条条成立的账                              ⇒ **绿**
        注入  把第一条的方向改成 `ZZ`（不在任何视图的块里）   ⇒ **红**

    ⚠️ 判据有三个**互不替代**的红分支，这一条只走 ①；另外两条各有各的注入行
       （`A5·项不是语料的项` / `A5·独立重算不成立`）。
       只配一条的话，「账逐条可指认」这句话的另外两个分支**一次都没被验过**。
    """
    spec, kernel, plugin, cover = _view_chain(nodes, edges)
    vs = build_views(kernel, spec, cover, plugin)
    entries = _a5_account(kernel, spec)
    if not entries:
        raise AssertionError("真链路上结构停留项为空 ⇒ 造不出一份非空的账（基线就绿不了）")
    if injected:
        entries = [("ZZ", entries[0][1], "编的")] + entries[1:]
    return _view_report(vs, kernel=kernel, entries=entries)


def inj_a5_bad_item(nodes: Any, edges: Any, injected: bool) -> Report:
    """`§A5` 注入②：账里有一条的**项不是语料里的项**（方向是对的）。"""
    spec, kernel, plugin, cover = _view_chain(nodes, edges)
    vs = build_views(kernel, spec, cover, plugin)
    entries = _a5_account(kernel, spec)
    if not entries:
        raise AssertionError("真链路上结构停留项为空 ⇒ 造不出一份非空的账")
    if injected:
        entries = [(entries[0][0], "这个项不存在", "编的")] + entries[1:]
    return _view_report(vs, kernel=kernel, entries=entries)


def inj_a5_unwarranted(nodes: Any, edges: Any, injected: bool) -> Report:
    """`§A5` 注入③：账里有一条**独立重算不成立**（项在语料里，但不在那个方向上）。"""
    spec, kernel, plugin, cover = _view_chain(nodes, edges)
    vs = build_views(kernel, spec, cover, plugin)
    entries = _a5_account(kernel, spec)
    if not entries:
        raise AssertionError("真链路上结构停留项为空 ⇒ 造不出一份非空的账")
    if injected:
        did = entries[0][0]
        mine = set(kernel.members_of(kernel.direction(did)))
        other = sorted(set(kernel.items) - mine)
        if not other:
            raise AssertionError(f"`{did}` 的成员集就是整个语料 ⇒ 造不出「不在它上面」的项")
        entries = [(did, other[0], "编的")] + entries[1:]
    return _view_report(vs, kernel=kernel, entries=entries)


# ═══ §A6 ═════════════════════════════════════════════════════════════════════

def inj_a6(nodes: Any, edges: Any, injected: bool) -> Report:
    """`§A6` 注入：换成「**存了派生边** + **读回时信任它**」那一对**对照实现**。

        基线  真实现（盘上只有权威边，派生边读回现算）      ⇒ **绿**
        注入  两个对照都上 ⇒ 盘上带派生栏、读回时信任它     ⇒ **红**

    ⚠️ **必须两个都上**。只上「存派生边」的那一个 ⇒ 读回时照样重算，
       漂移没了；只上「信任派生边」的那一个 ⇒ 盘上根本没有派生栏，
       退回重算，漂移也没了。⇒ 单独上任何一个都**造不出漂移**。

    ⚠️ 于是这一条注入里红的是 `§A6` 的**分支②**（存档带了派生边），
       **不是分支③**（漂移）。分支③ 只有在**盘上的派生值本身就是错的**时候才亮，
       而那要求基线视图自己就先违反 `§A1` —— 那会让**基线就红**，
       违反「基线绿 + 注入红」。⇒ 分支③ 由 `run_tests` 的
       `test_view_a6_branch_split` 在**合成**的坏视图上单独验，两条路各管一段。
    """
    from ldv.core import view_persist as vp

    spec, kernel, plugin, cover = _view_chain(nodes, edges)
    vs = build_views(kernel, spec, cover, plugin)
    kw: dict[str, Any] = {}
    if injected:
        kw = {"to_d": vp._to_dict_storing_derived,
              "from_d": vp._from_dict_trusting_derived}
    return _view_report(vs, kernel=kernel, cover=cover, plugin=plugin, a6kw=kw)


# ═══ 驱动 ════════════════════════════════════════════════════════════════════

CASES: dict[str, Callable] = {
    "B1": inj_b1, "B2": inj_b2, "B3": inj_b3, "B4": inj_b4, "B5": inj_b5,
    "B6": inj_b6, "B7": inj_b7, "B8": inj_b8, "B9": inj_b9, "B10": inj_b10,
    "B11": inj_b11, "B12": inj_b12, "B13": inj_b13, "B14": inj_b14, "B15": inj_b15,
    "B16": inj_b16, "B17": inj_b17, "B18": inj_b18, "B19": inj_b19, "B20": inj_b20,
    "A1": inj_a1, "A2": inj_a2, "A3": inj_a3,
    "A4": inj_a4, "A5": inj_a5, "A6": inj_a6,
}

#: 同一条检查的**第二条**判据。键是标签，值是 `(判据编号, 注入函数)`。
#: 标签是给人看的，不参与注册表核对 —— 核对比的是**编号**（见 `_registry_gap`）。
EXTRA_CASES: dict[str, tuple[str, Callable]] = {
    "B1·维护路径": ("B1", inj_b1_maintenance),
    "B11·位置敏感": ("B11", inj_b11_uniform),
    "B4·滞留要记账": ("B4", inj_b4_stay_unaccounted),
    "B16·维护路径": ("B16", inj_b16_maintenance),
    "B13·树性": ("B13", inj_b13_two_parents),
    "A3·稳定但更细": ("A3", inj_a3_not_coarsest),
    "A5·项不是语料的项": ("A5", inj_a5_bad_item),
    "A5·独立重算不成立": ("A5", inj_a5_unwarranted),
}


def _result(rep: Report, code: str) -> Tri:
    for a in rep.assertions:
        if a.code == code:
            return a.result
    return Tri.UNEXPANDED


def _registry_gap() -> tuple[list[str], list[str]]:
    """注册表对不上的地方 —— 返回 `(声明了但没验, 验了但没声明)`。

    比的两个集合都**外生给定**，不看任何一边的自我声明：

        左边  `run_checks.PLUGIN_CODES | KERNEL_CODES | abstraction.VIEW_CODES`
              —— 声明要跑的编号
        右边  `CASES` 的键 ∪ `EXTRA_CASES` 的值          —— 真被验过的编号

    ⚠️ **`VIEW_CODES` 必须一起比进来**，不能只比插件与内核那两批：
       视图那六条是**另一组**（`Report(plugin="(视图)")`），漏掉它的话
       「新增一条视图判据、忘了配注入」这件事**照样报全绿** ——
       而这句话对新增的编号一个字节的信息都没有。这正是本节开头那个形状。

    ⚠️ 反过来也要比：`VIEW_CODES` **只列已经实现了的**。
       少实现一条就**不写进来** —— 于是「套件全绿」这句话**不覆盖它**，
       而且这里是**双向**核对，多写一个编号也会红。

    两个方向都要报，因为它们**症状不同**：

        声明了但没验   新加一条检查、忘了配注入 ⇒ 套件报「全绿」，
                       而那个「全绿」对新增的编号一个字节的信息都没有
        验了但没声明   注入还在、检查被删了 ⇒ 注入在验一条没人跑的判据

    ## 为什么这条自检非有不可

    上面第一种正是本仓库一直在防的形状（「空转与通过长得一模一样」），
    只不过它这次出现在**注册表**上。别处没有任何东西守它，所以守在这里。
    """
    from ldv.checks.abstraction import VIEW_CODES
    from ldv.run_checks import KERNEL_CODES, PLUGIN_CODES

    declared = set(PLUGIN_CODES) | set(KERNEL_CODES) | set(VIEW_CODES)
    covered = set(CASES) | {code for code, _ in EXTRA_CASES.values()}
    return sorted(declared - covered), sorted(covered - declared)


def main() -> int:
    loaded = load()
    if loaded is None:
        print("⚠ 没有语料 —— 注入验证跑不了")
        return 0
    nodes, edges, _ = loaded

    print("═══ 注入验证：每条检查都要能红 ═══")
    failures: list[str] = []

    # ── 第 0 条：注册表自检 —— 「全绿」这句话覆盖到了哪些编号 ────────────────
    undeclared, unverified = _registry_gap()
    if undeclared or unverified:
        if undeclared:
            print(f"  ✗ 注册表  {undeclared}  **声明了要跑，却没有任何注入验过它**"
                  f" —— 上面的「全绿」管不到它们")
            failures.append(f"注册表: {undeclared} 未被注入覆盖（新增检查忘了配注入？）")
        if unverified:
            print(f"  ✗ 注册表  {unverified}  **有注入，却不在 `run_checks` 声明的编号里**"
                  f" —— 注入在验一条没人跑的判据")
            failures.append(f"注册表: {unverified} 有注入但不在声明里（检查删了、注入没删？）")
    else:
        print(f"  ✓ 注册表  {len(CASES)} 个编号 + "
              f"{len(EXTRA_CASES)} 条副判据 —— 与 `run_checks` 声明的那批**恰好相等**")
    n = 1  # 上面这条也算一条

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
    n += len(cases)

    # ── 度量那一条：问的不是「红不红」，是「读数会不会变」 ──────────────────
    for label, fn in (("度量·增量≡全量", metric_scale_matches),
                      ("度量·分辨率不变", metric_resolution_stable),
                      ("度量·覆盖嵌套", metric_cover_nesting),
                      ("度量·健全性（维护路径）", metric_soundness),
                      ("度量·覆盖不漏", metric_cover_leak),
                      ("度量·细化量", metric_progress)):
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
