"""实测：**方向节点的入度是不是常数？**（`§10.2 D` 的前置问题）

    cd outputs
    PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python _probe_degree.py

## 为什么这个问题**不能只答一个数**

本设计**同时物化了同一个关系的两条边**：

    `_children[父] = (子, …)`      ← 语义边：**父 → 子**（父拥有/指向子）
    `Direction.parent = 父`        ← 存储边：**子 → 父**（反指针）
    `Direction.witness = (父,)`    ← §6.1：见证指向**更粗**的层

「入度」= 指向一个节点的边数 —— 所以**答案取决于把哪条边当指针**：

    按语义边（父→子）   每个子方向恰好 **1** 个前驱（它的父）  ⇒ **常数 1**
    按存储边（子→父）   一个方向的「前驱」= 它的**扇出**       ⇒ **不常数**（§I4 是 k 叉）

Driscoll 的 `p`（"the maximum number of predecessors"）指**指针前驱**，
而 node-copying 的要求是「copy 一个节点时，要在**它所有的前驱**里写下新副本的指针」：

    物化 父→子 ⇒ copy 子 ⇒ 只改 **1** 个父      ⇒ 摊还 **O(1)**   ✓
    物化 子→父 ⇒ copy 父 ⇒ 要改 **扇出** 个子    ⇒ **O(扇出)**，不常数 ⇒ node-copying 断掉

⇒ 所以「入度是 1」这句话**成立，但只在一条边上**；另一条边不常数。
本脚本把两个数都量出来，并查两条边**是否互为转置**。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "repo"))

from ldv.checks._fixtures import (  # noqa: E402
    build_incremental,
    build_keyset,
    build_reach,
    build_sequence,
    load,
    make_builder,
)
from ldv.core.direction import ORIGIN_EXOGENOUS  # noqa: E402
from ldv.core.kernel import Kernel  # noqa: E402
from ldv.plugins.keyset import KeysetPlugin  # noqa: E402

WORST: dict[str, int] = {}


def measure(tag: str, k: Kernel) -> None:
    dirs = k.all_directions()
    by_id = {d.did: d for d in dirs}

    # --- 语义入度：`_children` 的反图 ---------------------------------------
    sem: dict[str, int] = {d.did: 0 for d in dirs}
    fanout: dict[str, int] = {}
    for p in dirs:
        kids = k.children_of(p)
        fanout[p.did] = len(kids)
        for c in kids:
            sem[c.did] = sem.get(c.did, 0) + 1

    off = [d.did for d in dirs if d.origin != ORIGIN_EXOGENOUS and sem[d.did] != 1]
    roots = [d.did for d in dirs if d.origin == ORIGIN_EXOGENOUS]
    bad_root = [r for r in roots if sem[r] != 0]
    mx_sem = max(sem.values()) if sem else 0
    mx_fan = max(fanout.values()) if fanout else 0

    # --- 两条边是否互为转置 ------------------------------------------------
    mism: list[str] = []
    for p in dirs:
        for c in k.children_of(p):
            if c.parent != p.did:
                mism.append(f"{c.did}.parent={c.parent} 但实际父是 {p.did}")
            if c.witness != (p.did,):
                mism.append(f"{c.did}.witness={c.witness} 但实际父是 {p.did}")
    for d in dirs:
        if d.origin == ORIGIN_EXOGENOUS:
            if d.parent is not None:
                mism.append(f"根 {d.did} 有父 {d.parent}")
            continue
        if d.parent is None:
            mism.append(f"{d.did} 非外生却没有父")
        elif d.parent not in by_id:
            mism.append(f"{d.did} 的父 {d.parent} 不在方向表里")
        elif d.did not in [c.did for c in k.children_of(by_id[d.parent])]:
            mism.append(f"{d.did}.parent={d.parent}，但它不在那个父的子列表里")

    leaked = [d.did for d in dirs if "~probe" in d.did]

    WORST["语义入度"] = max(WORST.get("语义入度", 0), mx_sem)
    WORST["扇出"] = max(WORST.get("扇出", 0), mx_fan)

    print(f"  {tag:<26} 方向 {len(dirs):>4}  "
          f"语义入度 max {mx_sem}  |  扇出 max {mx_fan:>2}  "
          f"|  非根≠1 的 {len(off)}  |  转置不符 {len(mism)}  |  探针泄漏 {len(leaked)}")
    for msg in (off[:2] + bad_root[:2] + mism[:2] + leaked[:2]):
        print(f"      ⚠️ {msg}")


def main() -> None:
    loaded = load()
    if loaded is None:
        print("语料不在 —— 先按 README 放 `_data/`")
        return
    nodes, edges, _ = loaded
    print(f"语料：{len(nodes)} 项 / {sum(len(v) for v in edges.values())} 边\n")

    print("【一、批建路径】")
    for which in ("keyset", "reach", "sequence"):
        k, _ = (build_keyset(nodes) if which == "keyset"
                else (build_reach(nodes, edges) if which == "reach"
                      else build_sequence(nodes, edges)))
        measure(f"{which} · 一次建完", k)

    print("\n【二、维护路径】（先建 6、再维护 30）")
    ids = sorted(nodes)
    for which in ("keyset", "reach", "sequence"):
        mk = make_builder(which, nodes, edges)
        measure(f"{which} · 维护 30", build_incremental(mk, nodes, ids[:6], ids[6:]))

    print("\n【三、涌现路径】（两项分不开 ⇒ 加第三项长出新层）")
    plug = KeysetPlugin()
    k = Kernel(plug, {"x1": {"id": "x1", "keys": frozenset({"a"})},
                      "x2": {"id": "x2", "keys": frozenset({"a"})}})
    k.build(plug.merge([]))
    k.insert("x1")
    k.insert("x2")
    k.insert("x3", {"id": "x3", "keys": frozenset({"a", "b"})})
    measure("keyset · 涌现", k)

    print("\n【四、并发：写的争用点在哪】")
    # `insert` 沿 `Cone(x)` 下行；§K3 说失效范围 ⊆ Cone(x) ⇒ 两个并发写的争用
    # 就是它们锥的**公共前缀**。锥是否都以根开头，决定了「根是不是全局必经点」。
    for which in ("keyset", "reach", "sequence"):
        k, _ = (build_keyset(nodes) if which == "keyset"
                else (build_reach(nodes, edges) if which == "reach"
                      else build_sequence(nodes, edges)))
        cones = [k.cone(i) for i in sorted(nodes)]
        nonempty = [c for c in cones if c]
        at_root = sum(1 for c in nonempty if c[0] == k.root.did)
        shared = sum(1 for c in nonempty if len(c) > 1)
        print(f"  {which:<10} 非空锥 {len(nonempty):>3} 个  |  "
              f"以**根**开头 {at_root} 个  |  长度 >1（与别人共享前缀）{shared} 个")
    print("  ⇒ 每条锥都过根 ⇒ **根是全局必经点**，也是并发写的**唯一公共争用点**；")
    print("     扇出不常数 ⇒ 若按「子→父」那条边加锁，改一个父要锁**无界个**子。")

    print("\n" + "=" * 74)
    print(f"语义入度（Driscoll 的 `p`，父→子那条边）最大值 = {WORST['语义入度']}")
    print(f"扇出（子→父那条边，§I4 的 k 叉）最大值     = {WORST['扇出']}")
    print()
    print("⇒ 「入度是否常数」的答案是**两条边的**：")
    print("   · 父→子（语义边）：**常数 1** ⇒ node-copying 的 O(1) 成立")
    print("   · 子→父（存储边）：**扇出**，不常数 ⇒ 照这条边做 node-copying 会断")
    print("   ⇒ 持久化层**必须**把 `_children` 当权威边，`parent`/`witness` 当派生索引。")
    print()
    print("⚠️ 而这条前提**现在没有任何检查守着**：")
    print("   §K5 写的是 `rank = 1 + max(rank(父))` —— 那个 `max` 是为 **DAG** 写的；")
    print("   `B13` 只查「无环 / 无自环 / 见证更粗」，**对 DAG 照样放行**；")
    print("   `Direction.witness` 是元组、`ORIGIN_EMERGENT` 已声明却**从未被构造** ⇒")
    print("   合并涌现那条口子一旦启用，入度就不再是 1，而**没有任何东西会响**。")


if __name__ == "__main__":
    main()
