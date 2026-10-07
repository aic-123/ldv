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
from typing import Any, Iterable

from ..core.kernel import Kernel, Query
from ..corpus.loader import CorpusError, Node, load_edges, load_nodes

#: 语料目录的候选位置 —— **仓库内的那份排在最前**。
#:
#: ⚠️ 语料路径**绝不能**写死成开发机绝对路径（形如 `C:/Users/<某人>/.../nodes`）：
#: 那对**任何 clone 这个仓库的人**都不存在，而症状不是报错、是**检查报「跳过」**——
#: 一个「语料不在」的仓库和一个「检查全过」的仓库，在汇总上长得不一样（跳过要显式报出），
#: 但都很容易被读成「没问题」。所以：
#:
#:     `LDV_CORPUS` 环境变量（**路径或名字**）→ `ldv/corpus/nodes/`（仓库内，首选）
#:                                            → 仓库根下的 `corpus/nodes/`
#:
#: 找不到就**报跳过**，绝不报通过（`run_checks` 与 `run_tests` 都这样；
#: `.github/workflows/ci.yml` 另有一条判据专门守「语料真的来自仓库内」）。
#:
#: ⚠️ **但「显式指定却找不到」是另一回事** —— 那是报错，不是跳过。见 `find_corpus`。
_HERE = Path(__file__).resolve().parent          # …/ldv/checks


def external_corpus_candidates(name: str) -> tuple[Path, ...]:
    """按**名字**找外部语料：`_data/<名字>/nodes`（仓库旁，再上一级）。

    外部数据**不进仓库**（怎么取、丢了什么，见 `ldv/tools/intake_openalex.py`
    的映射表与 DROP 表）。所以 `LDV_CORPUS=openalex-citations` 这种**名字**写法
    必须能被解析 —— 否则「把料接进来跑一遍」只能靠绝对路径，而绝对路径
    正是上面那条警告要消灭的东西。
    """
    root = _HERE.parent.parent                   # <仓库根>
    return (root / "_data" / name / "nodes",
            root.parent / "_data" / name / "nodes")   # 仓库旁 → 上一级


def corpus_candidates() -> tuple[Path, ...]:
    """**完整候选链**（从具体到兜底）—— 给报错信息与测试看的。

    ⚠️ `find_corpus` **不**直接拿它当查找池：显式指定时只认前两项。
    写成一条链会让「显式指定但找不到」**悄悄走到兜底**上，而那条路是错的。
    """
    env = os.environ.get("LDV_CORPUS")
    out: list[Path] = []
    if env:
        out.append(Path(env))
        out.extend(external_corpus_candidates(env))   # ★ 也允许只给**名字**
    out.append(_HERE.parent / "corpus" / "nodes")            # ldv/corpus/nodes
    out.append(_HERE.parent.parent / "corpus" / "nodes")     # <仓库根>/corpus/nodes
    return tuple(out)


SEED = 20261005


def find_corpus() -> Path | None:
    """找语料目录。**显式指定却找不到 ⇒ 报错，不回落到默认语料。**

    ⚠️ 这一条不是洁癖：回落的后果是「语料**指错了**」与「语料**指对了**」
    **长得一模一样** —— `LDV_CORPUS=openalex-citation`（少个 s）跑的是仓库里那 36 项，
    而所有检查照样报绿、退出码 0。**空转与通过长得一模一样**，
    这一次出现在**指路**上。

    ⇒ 两条路**互斥**：设了 `LDV_CORPUS` 就只在显式候选里找，找不到报错；
       没设才走兜底链，兜底链全落空才报「跳过」。
    """
    env = os.environ.get("LDV_CORPUS")
    pool = ((Path(env),) + external_corpus_candidates(env)) if env else (
        _HERE.parent / "corpus" / "nodes",
        _HERE.parent.parent / "corpus" / "nodes",
    )
    for cand in pool:
        if cand.is_dir() and any(cand.glob("*.md")):
            return cand
    if env:
        raise CorpusError(
            f"LDV_CORPUS={env!r} 找不到语料 —— 试过：{[str(c) for c in pool]}。"
            " 显式指定的语料找不到时**不回落到默认语料**"
            "（回落会让「指错了」和「指对了」长得一模一样）。")
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


def _forward_closure(edges: dict[str, frozenset[str]],
                     universe: Iterable[str] | None = None) -> dict[str, frozenset[str]]:
    """每个项**从它出发**能走到的项（含它自己）—— **正向** BFS。

    与插件的 `reachable()`（从锚点出发的**反向** BFS）方向相反、代码不共用 ——
    所以不是「同一条路的两个说法」。
    n = 36 时 O(n·(n+m)) 完全够用；**不引 Tarjan**，因为「简单」本身就是
    checker 的硬要求（McConnell 等 2011 的 Simplicity，p.22/§5.1）。
    """
    starts = list(universe) if universe is not None else list(edges)
    out: dict[str, frozenset[str]] = {}
    for start in starts:
        seen = {start}
        stack = [start]
        while stack:
            cur = stack.pop()
            for nxt in edges.get(cur, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        out[start] = frozenset(seen)
    return out


def _reverse_closure(edges: dict[str, frozenset[str]],
                     universe: Iterable[str] | None = None) -> dict[str, frozenset[str]]:
    """每个项**反向**闭包：能走到它的项（含它自己）。

    ⚠️ 这是**同一段 `_forward_closure` 喂反向图**，不是另写一份遍历。
       两份手写的遍历会**一起**错（`false-green` 形状 3「共享盲点」）；
       这里要的是「同一段已验证的代码 + 不同的输入」。

    为什么需要它：`reach` 的覆盖是「能走到锚点的**所有**项」，按定义就是
    `∪_{a ∈ payload} rev[a]`。原来的实现按正向闭包算
    （`fwd[x] & anchors`，逐项扫），**代价形状完全不同** —— 见 `coverage_of`。
    """
    starts = list(universe) if universe is not None else list(edges)
    rev: dict[str, set[str]] = {n: set() for n in starts}
    for src, dsts in edges.items():
        for dst in dsts:
            rev.setdefault(dst, set()).add(src)
    return _forward_closure({k: frozenset(v) for k, v in rev.items()}, universe=starts)


def _mutually_reachable(edges: dict[str, frozenset[str]]) -> dict[str, frozenset[str]]:
    """每个项的**强连通分量**（= 与它互相可达的项，含它自己）。"""
    fwd = _forward_closure(edges)
    return {n: frozenset(m for m in fwd[n] if n in fwd[m]) for n in fwd}


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


# --- 外生**覆盖**定义 —— 覆盖类判据的公共 oracle -----------------------------
#
#     覆盖(d) = 「按该方向的**语义**，d 说『是』的那些项」
#
# 这份定义**必须外生算**（不调插件、不调内核），理由与 `equiv_classes` 完全相同：
#
#     拿插件的 `§I3 代价` 判「插件覆盖住了没有」 ⇒ 检查器与被检查对象**共用同一段代码**
#     ⇒ `false-green` 形状 3「共享盲点」：两边永远同时通过
#
# McConnell 等 2011 §5.5 把这条排除得更明确：拿「被检查对象的运算记录」当见证
# **不算 certifying** —— 「证明这条见证性质等于证明 P 正确」。
#
#     方向       覆盖的定义                                        与 `代价 = 0` 的关系
#     ─────────────────────────────────────────────────────────────────────────────
#     keyset     `require ⊆ keys(x)` 且 `keys(x) ∩ forbid = ∅`      恰好等价
#     sequence   某个前缀是 `seq(x)` 的前缀                          恰好等价
#     reach      `x` 能走到某个锚点（`forward(x) ∩ payload ≠ ∅`）    **大得多** ——
#                `代价 = 0` 只对**锚点本身**成立，覆盖却是「能走到锚点的所有项」
#
# ⚠️ `reach` 那一行是本段存在的理由：拿 `代价 = 0` 判覆盖**在原理上就过强**，
#    不只是实现问题。GiST 1995 明说 `Consistent` **允许不精确** ——
#    "an accurate test for satisfiability is not required here" ——
#    所以「用精确性代理判一个只要求不许假阴的量」是判据选错了，不是插件写错了。
#
# --- 代价的形状：为什么 `reach` 要按**反向**闭包算 ---------------------------
#
# 这一族是**唯一**随语料超线性增长的地方（`README` 的「已知的未做」）。形状是：
#
#     覆盖族在 `run_checks.run_one` 里分**七八块**读同一个 oracle
#     ⇒ 同一个 payload 被**反复**要（实测 281 项：调用 4552 次、不同 payload 680 个）
#
#     而 `reach` 的单次调用原来按**正向**闭包逐项扫：
#         `{x : fwd[x] ∩ anchors ≠ ∅}`   代价 O(Σ_x min(|fwd[x]|, |anchors|))
#     覆盖的定义却允许按**锚点**逐个并：
#         `∪_{a ∈ payload} rev[a]`       代价 O(Σ_{a ∈ payload} |rev[a]|)
#
# ⇒ 两件事**分开量、分开记**：换单次调用的算法（`_reverse_closure`）与
#   换「同一 payload 算几遍」（`Cover` 的去重）。混在一起就分不清收益来自哪一边。
#
# ⚠️ 这**不是**把超线性修掉了。McConnell 等 2011 §6 第 2 条说得很清楚 ——
#    "**Ideally**, the running time of a checker is linear in the size of its input"
#    ——「Ideally」是**理想**，不是硬要求；Mehlhorn 2010 §3 更直接地给了反例
#    （3-连通性：线性算法有，但**没有一个是 certifying 的**，最快的 certifying
#    是 O(n²)，且"remains a challenge"）。⇒ 这里是**常数因子与单次形状**的改进，
#    渐近问题记在文档里，**不声称解决了**。

#: 方向 C 的「序列到此为止」哨兵 —— **与 `plugins/sequence.py` 的 `END` 同值**。
#: oracle 自己实现前缀关系、不 import 插件的代码；这里只复制这条**约定**。
SEQ_END = "\x00end"


def _is_prefix(p: tuple[str, ...], s: tuple[str, ...]) -> bool:
    return len(p) <= len(s) and s[:len(p)] == p


_EMPTY: frozenset[str] = frozenset()


class Cover:
    """`cover(payload) -> frozenset[str]`，**按 payload 的规范形去重**，并把命中数报出来。

    ## 为什么去重是允许的 —— 前提只有一条：**纯**

    Acar / Blelloch / Harper 2002（POPL，§2 末「Side Effects」）：

    > Also, **the memoization of the kind done by lazy languages will not affect the
    > correctness of change-propagation, because the value remains the same whether it
    > has been calculated or not.**

    ⇒ 「值算没算都一样」是**理由**，不是同义反复：它把「去重是否改变答案」
      归约成「`cover` 是不是纯函数」。这里的 `cover` 闭包住 `nodes` / `edges` / `rev`
      （构造时固定），调用不写任何东西、不读外部状态 ⇒ **纯**。

    ⚠️ 同一节还给了**反面**，所以「纯」不是顺手一提：

    > The problem is that **function caching and modifiables interact in subtle ways —
    > function caching requires purely functional code**, but our framework involves
    > side-effects in its implementation.

    ⇒ 这条前提**必须被检查**，不能靠读代码断言。判据在
      `run_tests.test_cover_oracle_transparency`：去重前后**逐项相同**；
      注入一个「键取错」的缓存就红。

    ## 为什么键是 payload 的**规范形**，不能是 `did`

    `did = f"D{内核计数器}"`（`core/kernel.py::_new`）—— **每个内核各自从 0 开始**。
    批建内核的 `D3` 与维护内核的 `D3` 是**两个不同的方向**。

    ⇒ 按 `did` 去重会把两份覆盖**串台** ⇒ 判据拿错的覆盖去比 ⇒ **假绿**。
      所以键取 `coverage_of` **本来就要做的那一步规范化**（`frozenset(req)` 等），
      不引入任何新假设 —— 也正因为这样，**跨内核**共享是定义上成立的，不是巧合。

    ## 读数的形状（两个数，缺一个都看不出来）

        `调用` / `命中` / `不同 payload`   ⇒ 去重**有没有真的发生**
        `缓存元素数`                        ⇒ 代价落在**内存**上多少

    ⚠️ **只报倍数不够**：一个「从来没命中」的缓存与一个「全命中」的缓存在
       判据的结论上**长得一模一样** —— 那正是本仓库一直在防的形状。
    """

    def __init__(self, which: str, fn: Any, canon: Any, dedup: bool = True) -> None:
        self.which = which
        self._fn = fn                 # 收**规范形**，不再自己解构 payload
        self._canon = canon
        self._dedup = dedup
        self._memo: dict[Any, frozenset[str]] = {}
        self.calls = 0
        self.hits = 0

    def __call__(self, payload: Any) -> frozenset[str]:
        self.calls += 1
        key = self._canon(payload)        # ← 只消费 payload **一次**
        if self._dedup:
            got = self._memo.get(key)
            if got is not None:
                self.hits += 1
                return got
        val = self._fn(key)
        if self._dedup:
            self._memo[key] = val
        return val

    @property
    def stats(self) -> dict[str, int]:
        return {"调用": self.calls, "命中": self.hits,
                "不同 payload": self.calls - self.hits,
                "缓存元素数": sum(len(v) for v in self._memo.values())}


def coverage_of(which: str, nodes: dict[str, Node],
                edges: dict[str, frozenset[str]], dedup: bool = True) -> Cover:
    """返回 `cover(payload) -> frozenset[str]`（只落在语料内的那部分）。

    与 `equiv_classes` **并列**放在夹具里 —— 两者是同一类东西：按方向的定义重算，
    不调被测对象。`B16` / 健全性 / 覆盖不漏 / 进步量守卫 四条共用它。

    ## `dedup=False` 是给**单变量对照**用的，不是给生产用的

    它让同一个 oracle 的**两种算法**能并排跑：`dedup=True` 与 `dedup=False`
    在**同一批 payload** 上必须**逐项相同**。这正是 Acar 那条前提的判据形态。

    ⚠️ **两条路的 `raw` 是同一个函数** —— 所以这条对照验的是**去重**，不是 `raw`。
       `raw` 本身由「与参考实现逐项相同」那条对照验（`run_tests` 里另有一条，
       参考实现是 `_forward_closure` 版本，即改动前的那一个）。
    """
    if which == "keyset":
        keys = {i: nodes[i].keys for i in nodes}

        def raw_keyset(key: Any) -> frozenset[str]:
            req, forb = key
            return frozenset(i for i in nodes
                             if req <= keys[i] and not (keys[i] & forb))

        return Cover(which, raw_keyset,
                     lambda p: (frozenset(p[0]), frozenset(p[1])), dedup)

    if which == "sequence":
        seqs = {i: tuple(v) + (SEQ_END,) for i, v in sequences(nodes, edges).items()}

        def raw_sequence(key: Any) -> frozenset[str]:
            return frozenset(
                i for i in nodes
                if any(_is_prefix(p, seqs[i]) for p in key)
            )

        return Cover(which, raw_sequence,
                     lambda p: tuple(tuple(x) for x in p), dedup)

    if which == "reach":
        # ★ 反向闭包，不是正向。覆盖的定义就是「能走到锚点的**所有**项」
        #   = `∪_{a ∈ payload} rev[a]` —— 按锚点逐个并，而不是逐项扫一遍。
        rev = _reverse_closure(edges, universe=nodes)

        def raw_reach(key: Any) -> frozenset[str]:
            out: set[str] = set()
            for a in key:
                out |= rev.get(a, _EMPTY)
            return frozenset(out)

        return Cover(which, raw_reach, lambda p: frozenset(p), dedup)

    raise ValueError(which)

