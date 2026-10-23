"""`B` 判据的**合成对照**（2026-10-23）。

## 为什么要有这一层

一条**永远红**的判据和一条**永远绿**的判据，在「跑一遍真实语料」里长得一样。
所以每条判据都得回答同一个问题：**它会不会红？**

做法：造一个**已知违反**的输入，要求判据报红；再造一个**干净的**，要求报绿。
两个结果**必须不同** —— 都落在同一态，这个对照就什么都没证明。

## 与 `b17_known_answer` / `b20_known_answer` 的关系

同一套做法，只是那两条写在各自的检查文件里，这里集中放新增的。
⚠️ 集中不等于可以偷懒：每条都要**真造违反**，不许「跑一遍看它绿」。

## 边界

合成对照证明的是「**这条判据的代码路径能报红**」，
**不是**「真实语料上不存在这种违反」。后者是 `run_checks` 在真语料上的事。
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from ..core.direction import (ORIGIN_EXOGENOUS, ORIGIN_SPLIT, Direction,
                              Ledger)
from ..core.tri import Tri
from ._framework import Report, result_of


# --- 公用：造一个只提供「查得动」的最小内核 --------------------------------

class StubKernel:
    """只实现检查用到的那几个方法 —— **不模拟内核**，只喂形状。

    ⚠️ 刻意不实现 `insert` / `expand`：这个 stub 只用来喂**坏结构**，
       多一个方法就多一处「和真内核行为不一致」的机会。
    """

    def __init__(self, dirs: list[Direction],
                 children: dict[str, tuple[str, ...]] | None = None,
                 members: dict[str, frozenset[str]] | None = None,
                 stayed: dict[str, frozenset[str]] | None = None) -> None:
        self._dirs = dirs
        self._children = children or {}
        self._members = members or {}
        self._stayed = stayed or {}
        self.ledger = Ledger()

    def all_directions(self) -> list[Direction]:
        return list(self._dirs)

    def direction(self, did: str) -> Direction:
        for d in self._dirs:
            if d.did == did:
                return d
        raise KeyError(did)

    def children_of(self, d: Direction) -> tuple[Any, ...]:
        return tuple(self.direction(x) for x in self._children.get(d.did, ()))

    def members_of(self, d: Direction) -> frozenset[str]:
        return frozenset(self._members.get(d.did, ()))

    def stayed_of(self, d: Direction) -> frozenset[str]:
        return frozenset(self._stayed.get(d.did, ()))


def _d(did: str, rank: int, parent: str | None,
       witness: tuple[str, ...] = (), origin: str = ORIGIN_SPLIT) -> Direction:
    return Direction(did=did, rank=rank, payload=frozenset({did}),
                     witness=witness, origin=origin, parent=parent)


def _tri(fn: Any, code: str, *args: Any, **kw: Any) -> Tri:
    """跑一个检查，取它给 `code` 的三态。"""
    rep = Report(plugin="(合成对照)")
    fn(*args, rep, **kw)
    return result_of(rep, code)


def _tri_src(fn: Any, code: str, extra: list[Path]) -> Tri:
    """B12 / B14 的签名是 `(rep, extra_sources)` —— 与 `_tri` 的顺序相反。"""
    rep = Report(plugin="(合成对照)")
    fn(rep, extra)
    return result_of(rep, code)


def _pair(label: str, bad: Tri, good: Tri, want_bad: Tri = Tri.NO) -> list[str]:
    """一对结果的自检：坏输入必须是 `want_bad`，好输入必须是「是」，且**两者不同**。"""
    fails: list[str] = []
    if bad is not want_bad:
        fails.append(f"{label}：坏输入期望 {want_bad}，实测 {bad}")
    if good is not Tri.YES:
        fails.append(f"{label}：好输入期望 是，实测 {good}")
    if bad is good:
        fails.append(f"{label}：两个结果相同（{bad}）—— 这个对照证明不了什么")
    return fails


# --- B4 劈开成划分 ---------------------------------------------------------

def b4_known_answer() -> list[str]:
    """B4 的合成对照：**子方向重叠** 与 **漏了但账上没有**，两种都要红。"""
    from .contract import b4_split_is_partition

    root = _d("D0", 1, None, origin=ORIGIN_EXOGENOUS)
    c1 = _d("D1", 2, "D0", ("D0",))
    c2 = _d("D2", 2, "D0", ("D0",))

    # ① 坏：D1={a,b}、D2={b,c} ⇒ b 重叠；且 c 不在父里
    bad_k = StubKernel([root, c1, c2], {"D0": ("D1", "D2")},
                       {"D0": frozenset({"a", "b"}),
                        "D1": frozenset({"a", "b"}),
                        "D2": frozenset({"b", "c"})})
    # ② 好：D1={a}、D2={b}，父={a,b}，无滞留
    good_k = StubKernel([root, c1, c2], {"D0": ("D1", "D2")},
                        {"D0": frozenset({"a", "b"}),
                         "D1": frozenset({"a"}),
                         "D2": frozenset({"b"})})
    # ③ 坏（第二种）：漏了 c，但账上没有它
    leak_k = StubKernel([root, c1, c2], {"D0": ("D1", "D2")},
                        {"D0": frozenset({"a", "b", "c"}),
                         "D1": frozenset({"a"}),
                         "D2": frozenset({"b"})})
    fails = _pair("B4 重叠", _tri(b4_split_is_partition, "B4", bad_k),
                  _tri(b4_split_is_partition, "B4", good_k))
    leak = _tri(b4_split_is_partition, "B4", leak_k)
    if leak is not Tri.NO:
        fails.append(f"B4 漏项未记账：期望 否，实测 {leak}")
    return fails


# --- B7 见证完备 -----------------------------------------------------------

def b7_known_answer() -> list[str]:
    """B7 的合成对照：非根方向**没有见证** ⇒ 红；根没有见证 ⇒ 仍然绿（设计如此）。"""
    from .structure import b7_witness_complete

    root = _d("D0", 1, None, origin=ORIGIN_EXOGENOUS)
    bad_k = StubKernel([root, _d("D1", 2, "D0", ())], {"D0": ("D1",)})
    good_k = StubKernel([root, _d("D1", 2, "D0", ("D0",))], {"D0": ("D1",)})
    return _pair("B7 无见证", _tri(b7_witness_complete, "B7", bad_k),
                 _tri(b7_witness_complete, "B7", good_k))


# --- B9 账本只追加 ---------------------------------------------------------

def b9_known_answer() -> list[str]:
    """B9 的合成对照：**改写一条已记录的事件** ⇒ 红；干净账本 ⇒ 绿。

    ⚠️ 改的是 `_events[0].detail` —— `Ledger` 留了 `_digest` 快照，
       所以「改过」和「没改过」在 `verify_append_only()` 上**必须**不同。
    """
    from ..core.direction import Event
    from .structure import b9_ledger_append_only

    def _kernel(tamper: bool) -> StubKernel:
        k = StubKernel([_d("D0", 1, None, origin=ORIGIN_EXOGENOUS)])
        k.ledger.append("born", "D0", rank=1)
        k.ledger.append("born", "D1", rank=2, parent="D0")
        if tamper:
            old = k.ledger._events[0]                     # noqa: SLF001
            k.ledger._events[0] = Event(                  # noqa: SLF001
                seq=old.seq, kind=old.kind, did=old.did,
                detail={**old.detail, "rank": 99})
        return k

    return _pair("B9 账本改写", _tri(b9_ledger_append_only, "B9", _kernel(True)),
                 _tri(b9_ledger_append_only, "B9", _kernel(False)))


# --- B12 无全局标量分 ------------------------------------------------------

def b12_known_answer() -> list[str]:
    """B12 的合成对照：源码里出现 `score` 这类名字 ⇒ 红；`n_expanded` ⇒ 绿。"""
    from .source import b12_no_global_scalar

    with tempfile.TemporaryDirectory(prefix="ldv-b12-") as td:
        bad_p = Path(td) / "bad.py"
        bad_p.write_text("total_score = 1\n", encoding="utf-8")
        good_p = Path(td) / "good.py"
        good_p.write_text("n_expanded = 1\n", encoding="utf-8")
        return _pair("B12 全局标量",
                     _tri_src(b12_no_global_scalar, "B12", [bad_p]),
                     _tri_src(b12_no_global_scalar, "B12", [good_p]))


# --- B13 良基树 -----------------------------------------------------------

def b13_known_answer() -> list[str]:
    """B13 的合成对照：**父链成环** / **一个方向两个父** ⇒ 红；正常树 ⇒ 绿。"""
    from .structure import b13_rank_well_founded

    root = _d("D0", 1, None, origin=ORIGIN_EXOGENOUS)

    # ① 坏：D1 与 D2 互认父 ⇒ 成环
    cyc = StubKernel([root, _d("D1", 2, "D2", ("D0",)),
                      _d("D2", 3, "D1", ("D0",))], {"D0": ("D1",)})
    # ② 坏：D3 同时是 D1、D2 的子 ⇒ 两个父
    multi = StubKernel([root, _d("D1", 2, "D0", ("D0",)),
                        _d("D2", 2, "D0", ("D0",)),
                        _d("D3", 3, "D1", ("D1",))],
                       {"D0": ("D1", "D2"), "D1": ("D3",), "D2": ("D3",)})
    # ③ 好
    good = StubKernel([root, _d("D1", 2, "D0", ("D0",))], {"D0": ("D1",)})

    fails = _pair("B13 成环", _tri(b13_rank_well_founded, "B13", cyc),
                  _tri(b13_rank_well_founded, "B13", good))
    m = _tri(b13_rank_well_founded, "B13", multi)
    if m is not Tri.NO:
        fails.append(f"B13 多父：期望 否，实测 {m}")
    return fails


# --- B14 外生边界 ---------------------------------------------------------

def b14_known_answer() -> list[str]:
    """B14 的合成对照：自优化侧 `import` 了插件 ⇒ 红；不 import ⇒ 绿。"""
    from .source import b14_exogenous_boundary

    with tempfile.TemporaryDirectory(prefix="ldv-b14-") as td:
        bad_p = Path(td) / "bad.py"
        bad_p.write_text("from ldv.plugins.keyset import KeysetPlugin\n",
                         encoding="utf-8")
        good_p = Path(td) / "good.py"
        good_p.write_text("X = 1\n", encoding="utf-8")
        return _pair("B14 越界 import",
                     _tri_src(b14_exogenous_boundary, "B14", [bad_p]),
                     _tri_src(b14_exogenous_boundary, "B14", [good_p]))


ALL = (b4_known_answer, b7_known_answer, b9_known_answer,
       b12_known_answer, b13_known_answer, b14_known_answer)
