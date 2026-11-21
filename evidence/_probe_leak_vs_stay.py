"""实测：`sequence` 维护路径上，「漏的项」与「滞留的项」**是不是同一批**？

    cd outputs
    LDV_CORPUS=openalex-small PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python _probe_leak_vs_stay.py

## 为什么要问这个

`§1` 硬要求表**第三行**（健全性 `members(d) ⊆ 覆盖(d)`）与**第四行**
（覆盖不漏 `覆盖(父) ⊆ ∪覆盖(子)`）在文档里被反复说成「**两条独立机制**」。
但两者可能是**同一批项**的两种说法：

    漏的项   `覆盖(父) − ∪覆盖(子)` 里的项
    滞留的项 每个子方向都**证明**不收它 ⇒ 停在父这一层

「每个子方向都证明不收它」= `命中(k, {x}) = 否`，而「否」按定义是
「x **不在** k 的覆盖里」⇒ 滞留项天然就是漏项的一部分。

若两者**相等**，那么：
    · 出路 (4)（项留在父方向）**不是**在修另一条机制，它是在**消化**第四行的漏；
    · 第四行与「滞留 == 0」是**同一个条件**；
    · 加一个 catch-all（出路 (1)）会让滞留**归零** ⇒ 出路 (4) 变成空转。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "repo"))

from ldv.checks._fixtures import (  # noqa: E402
    build_sequence,
    coverage_of,
    load,
    make_builder,
)
from ldv.checks._fixtures import build_incremental  # noqa: E402


def main() -> None:
    loaded = load()
    if loaded is None:
        print("语料不在")
        return
    nodes, edges, _ = loaded
    ids = sorted(nodes)
    cover = coverage_of("sequence", nodes, edges)

    print(f"语料：{len(nodes)} 项 / {sum(len(v) for v in edges.values())} 边\n")

    for tag, k in (
        ("一次建完", build_sequence(nodes, edges)[0]),
        ("维护 30（先建 6）", build_incremental(make_builder("sequence", nodes, edges),
                                                nodes, ids[:6], ids[6:])),
    ):
        print(f"【{tag}】")
        all_leaked: set[str] = set()
        all_stayed: set[str] = set()
        for d in k.all_directions():
            kids = k.children_of(d)
            if not kids:
                continue
            leaked = cover(d.payload) - set().union(*(cover(x.payload) for x in kids))
            stayed = set(k.stayed_of(d))
            all_leaked |= leaked
            all_stayed |= stayed
            if leaked or stayed:
                print(f"  {d.did:<5} 漏 {len(leaked):>3}  滞留 {len(stayed):>3}  "
                      f"| 漏−滞 {len(leaked - stayed):>3}  滞−漏 {len(stayed - leaked):>3}")
        print(f"  ── 合计：漏项 {len(all_leaked)}  滞留项 {len(all_stayed)}  "
              f"| 漏−滞 {len(all_leaked - all_stayed)}  滞−漏 {len(all_stayed - all_leaked)}")
        print(f"     两者{'**相等**' if all_leaked == all_stayed else '**不等**'}"
              f"（滞留 {len(all_stayed)} 项）\n")

    print("=" * 74)
    print("读法：")
    print("  · 若「漏−滞 = 0」⇒ **每一个漏项都被滞留下了** ⇒ 出路 (4) 正是在消化第四行的漏。")
    print("  · 「滞−漏 > 0」不奇怪：滞留项可能同时被别的子方向覆盖（`否` 是插件说的，")
    print("    而漏是按 payload 现算的）—— 两者口径不同，差在这。")

    # --- 兜底子方向（出路 (1)）会不会接住这些项？ ---------------------------
    # 推断是「会」：兜底的 payload = 父的前缀本身 ⇒ 覆盖(兜底) = 覆盖(父)。
    # 但「推断」不算数 —— 直接拿插件问一次（`命中(d, {x})` 是不是「否」）。
    print("\n【兜底子方向（出路 (1)，payload = 父的前缀）会不会接住滞留项？】")
    from ldv.core.direction import ORIGIN_SPLIT, Direction  # noqa: E402
    from ldv.core.kernel import Query  # noqa: E402
    from ldv.core.interfaces import call_hit  # noqa: E402
    from ldv.core.tri import Tri  # noqa: E402

    k = build_incremental(make_builder("sequence", nodes, edges),
                          nodes, ids[:6], ids[6:])
    plug = k.plugin
    yes_all = 0
    no_all = 0
    for d in k.all_directions():
        stayed = sorted(k.stayed_of(d))
        if not stayed:
            continue
        fallback = Direction(did=f"{d.did}~fallback", rank=d.rank + 1,
                             payload=d.payload,          # ← **父的前缀本身**
                             witness=(d.did,), origin=ORIGIN_SPLIT, parent=d.did)
        verdicts = [call_hit(plug, fallback, Query(ideal=frozenset({x}))) for x in stayed]
        yes_all += sum(1 for v in verdicts if v is Tri.YES)
        no_all += sum(1 for v in verdicts if v is Tri.NO)
        print(f"  {d.did:<5} 滞留 {len(stayed):>3} 项 ⇒ 兜底对它们的 `命中`："
              f"是 {verdicts.count(Tri.YES)}  否 {verdicts.count(Tri.NO)}  "
              f"未展开 {verdicts.count(Tri.UNEXPANDED)}")
    print(f"  ── 合计：是 {yes_all}  否 {no_all}")
    print("  ⇒ 若「否 = 0」⇒ 兜底方向**接住了每一个滞留项** ⇒ 滞留会**归零**，")
    print("     而「滞留 == 0」正是 `B17` 的前提 ⇒ 出路 (4) 与它的三件记账**全部变成空转**。")
    print("     ⚠️ 但代价没消失：兜底的覆盖 = 父的覆盖 ⇒ 下降进它**一点精度都不涨**")
    print("       （`细化量 = 0`）。两边的**假阳代价一样**，差的只是**记账形状**。")

    # --- 滞留项在**查询结果**里收得到吗？ -----------------------------------
    # `kernel.query` 只在**叶**上收成员（`if kids: … else: hits |= members`）。
    # 滞留项停在**内部**节点上 ⇒ 它可能**不进** `hit_items`。
    print("\n【滞留项进不进 `hit_items`？（`query` 只在**叶**上收成员）】")
    from ldv.core.kernel import Query as _Q  # noqa: E402
    missed: list[str] = []
    found: list[str] = []
    for d in k.all_directions():
        for x in sorted(k.stayed_of(d)):
            r = k.query(_Q(ideal=frozenset({x})))
            (found if x in r.hit_items else missed).append(f"{d.did}:{x}")
    print(f"  滞留项共 {len(found) + len(missed)} 个：进 `hit_items` 的 {len(found)}，"
          f"**没进**的 {len(missed)}")
    if missed:
        print(f"      例：{missed[:4]}")
        print("  ⇒ ⚠️ `hit_items` 是**按名字看像答案、实际不全**的一个字段 ——")
        print("     它只在叶上收成员，而滞留项停在内部节点 ⇒ **静默地少**。")
        print("     设计口径是「**输出是方向**」⇒ 靠父的 `命中` 说「是」即可检索；")
        print("     但 `hit_items` 这个**名字**会让人把它当成项集合。")
    else:
        print("  ⇒ 全部收得到（`hit_items` 与「输出是方向」两条口径一致）。")


if __name__ == "__main__":
    main()
