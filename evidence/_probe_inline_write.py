"""把「随行写 ⇒ 与批建不同构」从**散文**变成**实测** —— 手递手为什么做不了。

## 背景

`core/locking.py` 说手递手（crabbing）**没做**；`Kernel.insert` 的 docstring 给了
**理由**：末尾那一笔（记成员）写在下降**结束之后**，而这不是随手写的顺序 ——

    随行写   走到 d 时先 `members(d) += x`，再 `expand(d)`
             ⇒ **祖先**会看见这个新项 ⇒ 祖先**可能因为这次插入而分裂**
    末尾写   走到 d 时 `members(d)` 里**还没有** x
             ⇒ 祖先的分裂**不受这次插入影响**

⚠️ 这条**因果**在 kernel.py / 设计文档 / MEASUREMENTS / 工作流程 / README **五处**
   都写着，但**没有对照**在实测它。本脚本补上。

## 怎么测

`_InlineWrite` **不抄** `insert`（抄会漂），只**覆写 `expand`**：在每次 `expand(d)`
之前先把「当前这一项」加进 `d` 的成员集 —— 这正是「随行写」的语义。

然后对每个方向各量三组：

    A  批建        一次建完
    B  维护        先建 6、再逐项后添
    比较的读数     方向数 / 最大叶容量 / 结构指纹

    基线（`Kernel`，末尾写）   A 与 B 的读数**应逐项相同**（`test_emergence` ③ 钉的）
    对照（`_InlineWrite`）      A 与 B **应不同** —— 那就是手递手的代价

跑法：
    python outputs/_probe_inline_write.py            # 默认语料
    LDV_CORPUS=snap-email-eu python outputs/_probe_inline_write.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent / "repo"
sys.path.insert(0, str(REPO))

from ldv.checks._fixtures import (  # noqa: E402
    build_incremental, items, load, make_builder,
)
from ldv.core.kernel import Kernel  # noqa: E402


class _InlineWrite(Kernel):
    """对照：把「记成员」改成**随行写**。

    ⚠️ 只覆写 `expand` —— 不抄 `insert`。`insert` 里末尾那一笔仍然会写，
       但那时成员**已经**写过了（`set.add` 幂等）⇒ 唯一变的是**时机**。

    `hits` 是**前提读数**：覆写到底**真的**往成员集里加过几次新成员。
    `hits == 0` ⇒ 这个对照根本没生效，测出来的「没差别」不算数。
    """

    hits = 0

    def insert(self, item_id, item=None):          # noqa: ANN001
        self._inline_item = item_id
        try:
            return super().insert(item_id, item)
        finally:
            self._inline_item = None

    def expand(self, d):                            # noqa: ANN001
        cur = getattr(self, "_inline_item", None)
        if cur is not None:
            before = len(self._members.get(d.did, ()))
            self._members.setdefault(d.did, set()).add(cur)   # ← 随行写
            if len(self._members[d.did]) != before:
                type(self).hits += 1
        return super().expand(d)


def stats_of(k: Kernel) -> dict:
    return k.stats()


def shape(k: Kernel) -> tuple:
    """**只比结构** —— 方向 / 子边 / 成员集。

    ⚠️ 不拿 `persist._fingerprint` 比：它还含 `_expanded` / `_tried` / `_path`，
       那三样是**历史相关**的（§K3 只保证**接口级**不变量）⇒ 用它们比会把
       「时机不同」误报成「结构不同」。
    """
    dirs = tuple(sorted((d.did, d.rank, repr(d.payload), d.witness, d.parent)
                        for d in k.all_directions()))
    kids = tuple(sorted((p, tuple(v)) for p, v in k._children.items()))  # noqa: SLF001
    mem = tuple(sorted((d.did, tuple(sorted(k.members_of(d))))
                       for d in k.all_directions()))
    return (dirs, kids, mem)


def max_leaf(k: Kernel) -> int:
    """与 `test_emergence` ③ **逐字同一个口径**（判据是叶容量，不是方向数）。"""
    return max((len(k.members_of(d)) for d in k.all_directions()
                if not k.children_of(d)), default=0)


def build_batch(which, nodes, edges, cls):
    """一次建完 —— 与 `_fixtures.build_*` 同装配，只换内核类。"""
    all_items = items(nodes)
    sub = sorted(nodes)
    if which == "keyset":
        from ldv.plugins.keyset import KeysetPlugin
        plug = KeysetPlugin()
        root = plug.merge([])
    elif which == "reach":
        from ldv.plugins.reach import ReachPlugin
        plug = ReachPlugin(edges)
        root = frozenset(sub)
    else:
        from ldv.plugins.sequence import SequencePlugin
        from ldv.checks._fixtures import sequences
        plug = SequencePlugin(sequences(nodes, edges))
        root = frozenset({()})
    k = cls(plug, {i: all_items[i] for i in sub})
    k.build(root)
    for i in sub:
        k.insert(i)
    return k


def main() -> int:
    loaded = load()
    if loaded is None:
        print("⚠ 找不到语料 —— 设 `LDV_CORPUS`")
        return 0
    nodes, edges, _ = loaded
    ids = sorted(nodes)
    print(f"语料 {len(nodes)} 项 / {sum(len(v) for v in edges.values())} 边")
    print()

    from ldv.core import persist as P

    def variant(which, mode, cls):
        """`mode` = `"batch"` 或 `"maint"`；`cls` = 内核类。"""
        if cls is Kernel and mode == "batch":
            return mk(ids)[0]
        if cls is Kernel and mode == "maint":
            return build_incremental(mk, nodes, ids[:6], ids[6:])
        if mode == "batch":
            return build_batch(which, nodes, edges, cls)
        # 维护臂：只建前 6、再逐项后添（与 `build_incremental` 同切分）
        all_items = items(nodes)
        sub6 = ids[:6]
        if which == "keyset":
            from ldv.plugins.keyset import KeysetPlugin
            plug = KeysetPlugin()
            root = plug.merge([])
        elif which == "reach":
            from ldv.plugins.reach import ReachPlugin
            plug = ReachPlugin(edges)
            root = frozenset(sub6)
        else:
            from ldv.plugins.sequence import SequencePlugin
            from ldv.checks._fixtures import sequences
            plug = SequencePlugin(sequences(nodes, edges))
            root = frozenset({()})
        k = cls(plug, {i: all_items[i] for i in sub6})
        k.build(root)
        for i in sub6:
            k.insert(i)
        for i in ids[6:]:
            k.insert(i, all_items[i])
        return k

    for which in ("keyset", "reach", "sequence"):
        mk = make_builder(which, nodes, edges)
        for mode in ("batch", "maint"):
            a = variant(which, mode, Kernel)          # 末尾写（现行）
            b = variant(which, mode, _InlineWrite)    # 随行写（对照）
            print(f"  {which:9s} {mode:6s} 末尾写 方向 {a.stats().get('方向'):>4}"
                  f" 叶容量 {max_leaf(a):>4}"
                  f"｜随行写 方向 {b.stats().get('方向'):>4} 叶容量 {max_leaf(b):>4}"
                  f"｜**结构**{'相同' if shape(a) == shape(b) else '**不同 ←**'}")
        print()

    print("  ⚠️ 判据是**叶容量**（分辨率），不是方向数 —— §I4 允许插件定扇出之后，")
    print("     方向数那一列**本来就会变**（`test_emergence` ③ 末段）。")
    print("     比的是**同一个建法下**「末尾写 vs 随行写」—— 批建与维护的指纹本来")
    print("     就不同（`_path`/`_expanded`/`_tried` 是历史相关的），那样比没有意义。")
    print()
    print(f"  ★ **前提读数**：随行写覆写**真的**往成员集里加过新成员 **{_InlineWrite.hits}** 次。")
    if _InlineWrite.hits == 0:
        print("     ⇒ **0 次** ⇒ 这个对照**根本没生效**，上面「没差别」不算数。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
