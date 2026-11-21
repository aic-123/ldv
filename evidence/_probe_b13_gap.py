"""实测：`B13` 对 **DAG**（一个方向两个父）**是否放行**？

    cd outputs
    PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python _probe_b13_gap.py

这条是 `§10.2 D` 那个裁定的**关键证据**：如果 `B13` 对 DAG 也是绿的，
那么「本设计是树」这条前提**就没有任何检查守着** ——
而 `§10.2 D` 的 node-copying 路线**正是押在它上面**。

做法：正常建一棵 keyset 树，然后**手工**把某个子方向再挂到**第二个父**下面。
`_children` 是唯一写 `父→子` 那条边的地方，所以这是**最贴近判据**的注入。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "repo"))

from ldv.checks._fixtures import build_keyset, load  # noqa: E402
from ldv.checks._framework import Report  # noqa: E402
from ldv.checks.structure import b13_rank_well_founded  # noqa: E402
from ldv.core.tri import Tri  # noqa: E402


def sem_in_degree(k) -> dict[str, int]:
    deg = {d.did: 0 for d in k.all_directions()}
    for p in k.all_directions():
        for c in k.children_of(p):
            deg[c.did] = deg.get(c.did, 0) + 1
    return deg


def show(tag: str, k) -> None:
    deg = sem_in_degree(k)
    mx = max(deg.values()) if deg else 0
    multi = [d for d, n in deg.items() if n >= 2]
    rep = Report(plugin="keyset", expects=("B13",))
    b13_rank_well_founded(k, rep)
    a = rep.assertions[0]
    print(f"  {tag:<28} 语义入度 max {mx}  多父方向 {len(multi)} 个"
          f"  ⇒  B13 = {a.result}")
    if multi:
        print(f"      多父的：{multi[:4]}")


def main() -> None:
    loaded = load()
    if loaded is None:
        print("语料不在")
        return
    nodes, _edges, _ = loaded

    print("【一、基线：正常建出来的树】")
    k, _ = build_keyset(nodes)
    show("基线（树）", k)

    print("\n【二、注入：把某个子方向挂到**第二个父**下（造一个 DAG）")
    # 找两个**不同**的父，各有一个子方向；把 P2 的第一个子改挂到 P1 名下。
    parents = [d for d in k.all_directions() if k.children_of(d)]
    if len(parents) < 2:
        print("  这棵树上不到两个有子的父 —— 语料太小，换个语料")
        return
    p1, p2 = parents[0], parents[1]
    victim = k.children_of(p2)[0]
    print(f"  P1 = {p1.did}（原有子 {[c.did for c in k.children_of(p1)]}）")
    print(f"  P2 = {p2.did}（原有子 {[c.did for c in k.children_of(p2)]}）")
    print(f"  把 {victim.did}（原本只挂在 {p2.did} 下）**再挂到 {p1.did} 下**")
    k._children[p1.did] = tuple(k._children[p1.did]) + (victim.did,)  # noqa: SLF001
    show("注入后（DAG）", k)

    print()
    print("=" * 74)
    deg = sem_in_degree(k)
    mx = max(deg.values())
    rep = Report(plugin="keyset", expects=("B13",))
    b13_rank_well_founded(k, rep)
    verdict = rep.assertions[0].result
    print(f"结论：语义入度已经被改成 {mx}（不再是 1），而 `B13` 报 **{verdict}**。")
    if verdict == Tri.YES:
        print("⇒ **前提没被守**：`§10.2 D` 押在「入度 = 1」上，而 `B13` 对 DAG 放行。")
        print("   这正是「空转与通过长得一模一样」—— 前提成立与否，判据上长得一样。")
    else:
        print("⇒ `B13` 已经守住了入度 —— 本脚本的前提（它放行 DAG）不成立，结论要改。")


if __name__ == "__main__":
    main()
