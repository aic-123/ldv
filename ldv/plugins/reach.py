"""方向 B —— **锚点可达**（C0 §2）。

    方向 payload = 一个**锚点集合** `S`（项 id 的集合）
    项 x 属于方向 d  ⟺ 存在路径 `x →* s`（`s ∈ S`），含 `x ∈ S` 自身
    合并 = **并**（锚点变多 ⇒ 方向变粗）
    代价 = **距离**（x 到最近锚点走几步）
    命中 = 可达性闭包，**需要遍历整张图**

---

## 与方向 A 的关键差异（这是 C0 那张表的落地）

| | A 键集包含 | B 锚点可达 |
|---|---|---|
| 命中要看什么 | 节点**自己**的字段 | 节点在图里的**位置** |
| 合并 | 交（收缩） | **并**（扩张） |
| 代价 | 计数 | **距离** |
| 「未知」从哪来 | — | **拒绝遍历**（性能）／分辨率不够 |

**世界在哪里**：`§I1` 的签名是 `命中(方向, 查询)` —— **没有世界参数**。
所以 B 只能把**图闭包进插件实例**。这不是绕路，是接口的必然结果：
GiST 的键是自包含的，本设计的方向允许不自包含 ⇒ 插件必须自带世界。

---

## 一条必须守住的性质：`归属 ⊆ 可达`

`命中` 说「否」的健全性依赖 `members(d) ⊆ reachable(payload(d))`：

    若 `reachable(S) ∩ q.ideal = ∅`，则 `members(d) ∩ q.ideal = ∅` ⇒ 「否」成立

而这个包含关系由**两条**保证，缺一不可：

1. `劈开` 给每一组的 payload = 该组各项**签名之并**（签名 = 能到达的汇点集合）
2. `代价` = 到最近锚点的距离 ⇒ 距离 0 的项归自己那组

⇒ 组内每一项到自己的 payload 距离都是 0 ⇒ 必在 `reachable` 里。
**若这两条不一致，`B1`（假阴）就会红。**
"""

from __future__ import annotations

from collections import deque
from typing import Any, Iterable, Sequence

from ..core.direction import Direction
from ..core.interfaces import UsageSignal
from ..core.tri import Tri

EMPTY: frozenset[str] = frozenset()


class ReachPlugin:
    """§I1–§I7 的七个方法。"""

    name = "reach"

    def __init__(self, edges: dict[str, Iterable[str]],
                 traverse_budget: int = 10_000) -> None:
        self.edges: dict[str, frozenset[str]] = {k: frozenset(v) for k, v in edges.items()}
        self._rev: dict[str, set[str]] = {k: set() for k in self.edges}
        for src, dsts in self.edges.items():
            for dst in dsts:
                self._rev.setdefault(dst, set()).add(src)
        self.traverse_budget = traverse_budget

    # --- 图 ---------------------------------------------------------------

    def reachable(self, anchors: Iterable[str]) -> frozenset[str]:
        """能走到任一锚点的项（含锚点自身）—— **反向** BFS。"""
        seen = set(anchors)
        q = deque(seen)
        while q:
            cur = q.popleft()
            for prev in self._rev.get(cur, ()):
                if prev not in seen:
                    seen.add(prev)
                    q.append(prev)
        return frozenset(seen)

    def distance(self, item_id: str, anchors: frozenset[str]) -> float:
        """到最近锚点的步数。走不到返回 `|V|+1`（有限值 —— `§I3` 要求可比）。"""
        if item_id in anchors:
            return 0.0
        seen = {item_id}
        q = deque([(item_id, 0)])
        while q:
            cur, dist = q.popleft()
            for nxt in self.edges.get(cur, ()):
                if nxt in anchors:
                    return float(dist + 1)
                if nxt not in seen:
                    seen.add(nxt)
                    q.append((nxt, dist + 1))
        return float(len(self.edges) + 1)

    def signature(self, item_id: str) -> frozenset[str]:
        """该项能到达的**汇点**（无出边的项）之集合。走不到汇点 ⇒ 退化成自己。"""
        sinks = {n for n in self.edges if not self.edges[n]}
        if item_id in sinks:
            return frozenset({item_id})
        seen = {item_id}
        q = deque([item_id])
        found: set[str] = set()
        while q:
            cur = q.popleft()
            if cur in sinks:
                found.add(cur)
                continue
            for nxt in self.edges.get(cur, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    q.append(nxt)
        return frozenset(found) or frozenset({item_id})

    # --- §I2 合并 ---------------------------------------------------------

    def merge(self, ds: Sequence[Direction]) -> frozenset[str]:
        """取并。**必须覆盖**（§I2）。

        覆盖的证明：`reachable(⋃S_i) ⊇ reachable(S_i)`（反向 BFS 从更大的起点集出发
        只会看到更多）⇒ 原属 d_i 的项仍可达新锚点 ⇒ 覆盖成立。
        """
        out: set[str] = set()
        for d in ds:
            out |= set(d.payload)
        return frozenset(out)

    # --- §I3 代价 ---------------------------------------------------------

    def penalty(self, d: Direction, item: Any) -> float:
        """**距离** —— A 的代价是「计数」，这里没有计数的位置。

        §I3 只要求「同一层内两两可比」。距离满足：全序、有限、同一量纲。
        **内核不参与折算** —— 所以「走几步算贵」由这里说了算。
        """
        return self.distance(str(item["id"]), frozenset(d.payload))

    # --- §I4 劈开 ---------------------------------------------------------

    def split(self, parent: Direction, items: Sequence[Any]) -> tuple[frozenset[str], frozenset[str]] | None:
        """按**图上的位置**二分：先按能到达的汇点，再按到汇点的距离，最后按 id。

        payload = **该组各项自己**（每项都是自己的锚点）。

        **为什么这里也是两个**：这个方向的 payload 是**锚点集合**，
        而「按图上的位置排序后取中点」的语义就是二分 —— k 叉在这里没有对应的语义
        （切三段并不比切两段更细）。二分是**自然形状**。
        （反向判据：`§I4` 改成 k 叉之后，B 的数**一个都没动**。）

        ## ⚠️ 父方向在这里**帮不上忙**（实测，不是推的）

        `覆盖(子) ⊆ 覆盖(父)` 要求 `reachable(子.payload) ⊆ reachable(父.payload)`，
        即子 payload 得是父 payload 的子集。但

            子 payload 必须**覆盖得住自己的成员**（`B16` / `B1` 都靠它）
            而维护之后，成员是**后来才长进来的** —— 父的 payload 在劈开时就冻结了
            ⇒ 新成员的 id **不在**父 payload 里 ⇒ 子 payload 不得不**超出**父 payload

        ⇒ 想强行取交（`left ∩ parent.payload`）会**丢掉新成员** ⇒ 假阴（`B1` 红）。
          这是「**成员可以晚于 payload 到达**」的后果，不是这里少写了一句。
          ⇒ 所以 B 只接受这个参数，**不用**它 —— 覆盖嵌套在维护路径上本来就保不住。

        ⚠️ 为什么锚点取「本组各项」而不是「本组能到达的汇点」：
           后者在只有 2 个汇点的图上**只能切一层** —— 切完两边签名相同，
           下层再也分不开（实测：7 个方向、只展开 1 个）。
           取本组各项当锚点，则组内每一项到本组距离恒为 0，
           方向可以一路细到单项，**而「可达」的语义一点没丢** ——
           方向仍然表示「能到达这组项的东西」，只是锚点更细。

        ⇒ 这条改动的代价是**精度**（`reachable` 比 `members` 大得多，
          假阳变多），收益是**层能真的分下去**。
          按 §K8，这个交换是允许的 —— 假阳只赔性能，假阴才赔正确性。

        **分不开**时返回 `None`（**声明**），不返回两个相同 payload。
        """
        del parent                      # 见上：维护之后父 payload 已经不够用了
        ids = sorted(str(it["id"]) for it in items)
        if len(ids) < 2:
            return None
        sinks = frozenset(n for n in self.edges if not self.edges[n])
        ordered = sorted(
            ids,
            key=lambda i: (sorted(self.signature(i)),
                           self.distance(i, sinks) if sinks else 0.0, i),
        )
        mid = len(ordered) // 2
        left, right = ordered[:mid], ordered[mid:]
        if not left or not right:
            return None
        return (frozenset(left), frozenset(right))

    # --- §I1 命中 ---------------------------------------------------------

    def hit(self, d: Direction, query: Any) -> Tri:
        """**偏函数** —— 有两种情况会返回「未展开」。

        1. **分辨率不够**：`d.rank < q.min_rank` ⇒ 方向太粗，回答不了那个分辨率
        2. **拒绝遍历**：锚点集超过预算 ⇒ 宁可说「不知道」，也不说「没有」

        ⚠️ 两条都往「未展开」偏，**不往「否」偏** —— 「否」是唯一会漏结果的分支（§K8）。
        """
        min_rank = int(getattr(query, "min_rank", 1))
        if d.rank < min_rank:
            return Tri.UNEXPANDED
        anchors = frozenset(d.payload)
        if len(anchors) > self.traverse_budget:
            return Tri.UNEXPANDED
        ideal = frozenset(getattr(query, "ideal", frozenset()))
        reach = self.reachable(anchors)
        return Tri.YES if (reach & ideal) else Tri.NO

    # --- §I5 编码 / 解码 --------------------------------------------------

    def encode(self, d: Direction) -> bytes:
        return ("A:" + ",".join(sorted(d.payload))).encode("utf-8")

    def decode(self, blob: bytes) -> frozenset[str]:
        text = blob.decode("utf-8")
        return frozenset(x for x in text[2:].split(",") if x)

    # --- §I6 信号 ---------------------------------------------------------

    def signal(self, d: Direction, interaction: Any) -> UsageSignal | None:
        outcome = interaction.get("outcome")
        if outcome is None:
            return None
        return UsageSignal(did=d.did, outcome=float(outcome),
                           note=str(interaction.get("note", "")))

    # --- §I7 记录 ---------------------------------------------------------

    def record(self, d: Direction) -> dict:
        return {"kind": self.name, "did": d.did, "rank": d.rank,
                "anchors": sorted(d.payload), "anchors_n": len(d.payload)}
