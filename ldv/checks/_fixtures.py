"""夹具 —— 把语料、三个插件、内核、查询集装起来。

**查询的 `ideal` 是外生声明的 ground truth**，由语料算出来，不是插件产出的。
`B1`（假阴）拿它当答案比 —— 拿被测对象自己产出的东西当答案就什么都验不出来。

**随机源是定死的**（`random.Random(seed)`）—— 同数据同历史 ⇒ 同结构，
这是 §8.2 要的可复现性，也让「红了」这件事本身可复现。
"""

from __future__ import annotations

import os
import random
from pathlib import Path
from typing import Any

from ..core.kernel import Kernel, Query
from ..corpus.loader import Node, load_edges, load_nodes

#: 语料目录的候选位置 —— **仓库内的那份排在最前**。
#:
#: ⚠️ 这里曾经写死一条开发机绝对路径（`C:/Users/.../rl-scaffold/nodes`）。
#: 那对**任何 clone 这个仓库的人**都不存在，而症状不是报错、是**检查报「跳过」**——
#: 一个「语料不在」的仓库和一个「检查全过」的仓库，在汇总上长得不一样（跳过要显式报出），
#: 但都很容易被读成「没问题」。所以：
#:
#:     `LDV_CORPUS` 环境变量 → `ldv/corpus/nodes/`（仓库内，首选）→ 仓库根下的 `corpus/nodes/`
#:
#: 找不到就**报跳过**，绝不报通过（`run_checks` 与 `run_tests` 都这样）。
_HERE = Path(__file__).resolve().parent          # …/ldv/checks


def corpus_candidates() -> tuple[Path, ...]:
    env = os.environ.get("LDV_CORPUS")
    out: list[Path] = []
    if env:
        out.append(Path(env))
    out.append(_HERE.parent / "corpus" / "nodes")            # ldv/corpus/nodes
    out.append(_HERE.parent.parent / "corpus" / "nodes")     # <仓库根>/corpus/nodes
    return tuple(out)


SEED = 20261005


def find_corpus() -> Path | None:
    for cand in corpus_candidates():
        if cand.is_dir() and any(cand.glob("*.md")):
            return cand
    return None


def load() -> tuple[dict[str, Node], dict[str, frozenset[str]], frozenset[str]] | None:
    root = find_corpus()
    if root is None:
        return None
    nodes = load_nodes(root)
    edges, dangling = load_edges(nodes)
    return nodes, edges, dangling


def items(nodes: dict[str, Node]) -> dict[str, dict[str, Any]]:
    return {nid: n.as_item() for nid, n in nodes.items()}


# --- 查询集 -----------------------------------------------------------------

def keyset_queries(nodes: dict[str, Node]) -> list[Query]:
    """按「键」造查询，`ideal` 由语料算出。"""
    vocab = sorted({k for n in nodes.values() for k in n.keys})
    picks = [k for k in vocab if "=" in k] or vocab[:4]
    out: list[Query] = []
    for k in picks:
        out.append(Query(ideal=_ideal(nodes, {k}, set()), require=frozenset({k}),
                         label=f"有 {k}"))
    for i, k in enumerate(picks):
        other = picks[(i + 1) % len(picks)]
        out.append(Query(ideal=_ideal(nodes, {k, other}, set()),
                         require=frozenset({k, other}), label=f"同时有 {k}+{other}"))
    for k in picks:
        out.append(Query(ideal=_ideal(nodes, set(), {k}), forbid=frozenset({k}),
                         label=f"没有 {k}"))
    # 一个**必然为空**的查询：两个互斥的枚举取值同时要求
    out.append(Query(ideal=frozenset(), require=frozenset({"type=概念", "type=案例"}),
                     label="互斥要求（应为空）"))
    return out


def _ideal(nodes: dict[str, Node], require: set[str], forbid: set[str]) -> frozenset[str]:
    return frozenset(
        nid for nid, n in nodes.items()
        if require <= n.keys and not (n.keys & forbid)
    )


def reach_queries(nodes: dict[str, Node], edges: dict[str, frozenset[str]]) -> list[Query]:
    """按「项集合」造查询，`ideal` 是**外生挑的**项集合。"""
    rng = random.Random(SEED)
    ids = sorted(nodes)
    out: list[Query] = []
    for size in (1, 3, 6, 12):
        for t in range(3):
            ideal = frozenset(rng.sample(ids, min(size, len(ids))))
            out.append(Query(ideal=ideal, label=f"随机 {len(ideal)} 项 #{t}"))
    out.append(Query(ideal=frozenset(ids), label="全部项"))
    out.append(Query(ideal=frozenset(), label="空集"))
    return out


# --- 内核装配 ---------------------------------------------------------------

def build_keyset(nodes: dict[str, Node]) -> tuple[Kernel, Any]:
    from ..plugins.keyset import KeysetPlugin

    plug = KeysetPlugin()
    k = Kernel(plug, items(nodes))
    k.build(plug.merge([]))              # 根 = (∅, ∅)：最外层意图不约束键
    for nid in sorted(nodes):
        k.insert(nid)
    return k, plug


def build_reach(nodes: dict[str, Node], edges: dict[str, frozenset[str]],
                traverse_budget: int = 10_000) -> tuple[Kernel, Any]:
    from ..plugins.reach import ReachPlugin

    plug = ReachPlugin(edges, traverse_budget=traverse_budget)
    k = Kernel(plug, items(nodes))
    # 根 = 全部项当锚点：最外层意图声明为「覆盖一切」
    k.build(frozenset(nodes))
    for nid in sorted(nodes):
        k.insert(nid)
    return k, plug


def probe_queries(nodes: dict[str, Node], edges: dict[str, frozenset[str]]) -> list[Query]:
    """各插件共用的**探针查询集** —— 用于行为式覆盖判定（§I2 / §I5）。"""
    return keyset_queries(nodes) + reach_queries(nodes, edges)


# --- 只建根的内核（B8 用） ---------------------------------------------------

def make_keyset_root(nodes: dict[str, Node]) -> Any:
    """每次调用返回一个**全新**的、只建了根的内核 —— B8 逐项插入要用。"""
    def _make() -> Kernel:
        from ..plugins.keyset import KeysetPlugin

        plug = KeysetPlugin()
        k = Kernel(plug, items(nodes))
        k.build(plug.merge([]))
        return k

    return _make


def make_reach_root(nodes: dict[str, Node], edges: dict[str, frozenset[str]],
                    traverse_budget: int = 10_000) -> Any:
    def _make() -> Kernel:
        from ..plugins.reach import ReachPlugin

        plug = ReachPlugin(edges, traverse_budget=traverse_budget)
        k = Kernel(plug, items(nodes))
        k.build(frozenset(nodes))
        return k

    return _make


# --- 方向 C 的序列 -----------------------------------------------------------

def sequences(nodes: dict[str, Node],
              edges: dict[str, frozenset[str]], cap: int = 6) -> dict[str, tuple[str, ...]]:
    """`seq(x)` = 从 x 出发的**确定性链**的 `type` 值序列。

    每步取 **id 最小**的未访问后继，走到无后继或已访问为止（最多 `cap` 步）。
    「确定性」是必须的：只要有一个环节依赖遍历顺序，方向就不可复现（§8.2）。
    """
    out: dict[str, tuple[str, ...]] = {}
    for nid in sorted(nodes):
        toks: list[str] = []
        seen = {nid}
        cur = nid
        for _ in range(cap):
            nxt = sorted(v for v in edges.get(cur, ()) if v not in seen)
            if not nxt:
                break
            cur = nxt[0]
            seen.add(cur)
            toks.append(str(nodes[cur].fields.get("type")))
        out[nid] = tuple(toks)
    return out


def build_sequence(nodes: dict[str, Node], edges: dict[str, frozenset[str]]) -> tuple[Kernel, Any]:
    from ..plugins.sequence import SequencePlugin

    plug = SequencePlugin(sequences(nodes, edges))
    k = Kernel(plug, items(nodes))
    k.build(frozenset({()}))              # 根 = 空前缀：覆盖一切
    for nid in sorted(nodes):
        k.insert(nid)
    return k, plug


def make_sequence_root(nodes: dict[str, Node], edges: dict[str, frozenset[str]]) -> Any:
    def _make() -> Kernel:
        from ..plugins.sequence import SequencePlugin

        plug = SequencePlugin(sequences(nodes, edges))
        k = Kernel(plug, items(nodes))
        k.build(frozenset({()}))
        return k

    return _make


# --- 「先建 k、再维护 N−k」的构造器 ------------------------------------------
#
# 与上面 `make_*_root` 的区别：那个只建**根**（0 项），这个建的是
# 「**当时只认识这 k 项**」的内核 —— 后来那些项走 `insert(id, item)` 后添。
# 这正是流程 B 的维护路径，也是「增量 ≡ 全量」要比的那一侧。

def make_builder(which: str, nodes: dict[str, Node],
                 edges: dict[str, frozenset[str]]) -> Any:
    """返回 `build(subset_ids) -> (kernel, plugin)`。

    `subset_ids` 是**构造时内核就认识**的项；其余项由调用方 `insert` 后添。

    ⚠️ **根 payload 必须跟着 subset 走**，不能偷偷用全集：
       `reach` 的根是「全部项当锚点」，增量时它只该是「**当时已知的**锚点」。
       用全集就等于作弊 —— 那一侧就不是增量了。
    """
    all_items = items(nodes)

    def build(subset_ids: list[str]) -> tuple[Kernel, Any]:
        from ..plugins.keyset import KeysetPlugin
        from ..plugins.reach import ReachPlugin
        from ..plugins.sequence import SequencePlugin

        sub = sorted(set(subset_ids))
        known = {i: all_items[i] for i in sub}
        if which == "keyset":
            plug = KeysetPlugin()
            k = Kernel(plug, known)
            k.build(plug.merge([]))
        elif which == "reach":
            plug = ReachPlugin(edges)
            k = Kernel(plug, known)
            k.build(frozenset(sub))          # ← 当时**已知**的锚点，不是全集
        elif which == "sequence":
            plug = SequencePlugin(sequences(nodes, edges))
            k = Kernel(plug, known)
            k.build(frozenset({()}))
        else:
            raise ValueError(which)
        for i in sub:
            k.insert(i)
        return k, plug

    return build


def build_incremental(build: Any, nodes: dict[str, Node],
                      init_ids: list[str], later_ids: list[str]) -> Kernel:
    """先建 `init_ids`，再把 `later_ids` 逐个 `insert(id, item)` 后添。

    ⇒ 结束时**项集与全集相同**。所以任何结构差异都是**结构性**的，
      不是「项少了」造成的 —— 这一点由 `divergence.py` 显式断言。
    """
    all_items = items(nodes)
    k, _ = build(init_ids)
    for i in later_ids:
        k.insert(i, all_items[i])
    return k


# --- 「不可分等价类」—— `B17` 的**外生** oracle ------------------------------
#
# 两个项在该方向的**语义下不可分** ⟺ 对**任何** payload 它们都给同一答案。
# 这个关系**必须外生算**（不调插件），否则就是 `false-green` 形状 3「共享盲点」：
# 拿插件的谓词去算「插件能不能分开」，两边永远同时通过。
#
#     keyset    键集相同           —— 方向谓词只看 `keys(x)`
#     sequence  序列相同           —— 方向谓词只看 `seq(x)` 的前缀
#     reach     **互相可达**        —— 互相可达 ⇒ 对任何锚点集同进同出；
#                                    反之（z 走不到 y）取 S={y} 就分开
#
# ⚠️ **reach 的等价关系不是「汇点签名相同」。** 实测签名只有 3 类，
#    而内核把它们分成了 36 个叶 —— 签名相同**不**蕴涵不可分。
#    这一点由 `equivalence.py` 的读数显式报出（它是「payload 比签名更有表达力」的证据）。


def _mutually_reachable(edges: dict[str, frozenset[str]]) -> dict[str, frozenset[str]]:
    """每个项的**强连通分量**（= 与它互相可达的项，含它自己）。

    用**正向 BFS** 算，与插件的 `reachable()`（从锚点出发的**反向** BFS）
    方向相反、代码不共用 —— 所以不是「同一条路的两个说法」。
    n = 36 时 O(n·(n+m)) 完全够用；**不引 Tarjan**，因为「简单」本身就是
    checker 的硬要求（McConnell 等 2011 的 Simplicity，p.22/§5.1）。
    """

    def forward(start: str) -> set[str]:
        seen = {start}
        stack = [start]
        while stack:
            cur = stack.pop()
            for nxt in edges.get(cur, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return seen

    fwd = {n: forward(n) for n in edges}
    return {n: frozenset(m for m in fwd[n] if n in fwd[m]) for n in edges}


def equiv_classes(which: str, nodes: dict[str, Node],
                  edges: dict[str, frozenset[str]]) -> dict[str, frozenset[str]]:
    """把每个项映到它的**不可分等价类**（一个含它自己的集合）。

    同一个集合 ⟺ 两个项在该方向语义下不可分。**不调插件。**
    """
    if which == "keyset":
        return {i: frozenset(j for j in nodes if nodes[j].keys == nodes[i].keys)
                for i in nodes}
    if which == "sequence":
        seqs = sequences(nodes, edges)
        return {i: frozenset(j for j in nodes if seqs[j] == seqs[i]) for i in nodes}
    if which == "reach":
        return _mutually_reachable(edges)
    raise ValueError(which)
