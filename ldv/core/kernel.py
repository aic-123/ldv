"""内核 —— 设计文档 §4.1。

内核负责（§4.1 逐条）：

    层的存在与秩 · 细化关系的形式 · 见证与支撑锥 · 按需展开 ·
    失效只分叉不覆盖 · 倾向权重的记账位 · 遍历循环 · 三态语义 ·
    §K8 那条契约（假阴禁止 / 假阳计量）

**内核不认识任何方向。** 它只认 `§I1`–`§I7` 七个方法（`core/interfaces.py`）。

---

## 一条必须先说清的约定：**秩越大越细**

    rank 1   = 根（最粗）—— **外生**，人声明的最外层意图
    rank r+1 = 由 rank r 展开出来的**更细**方向
    叶       = 细到不能再细（装不下两项）

于是：

    §K1 严格派生      层 n+1 的见证 ⊆ 层 ≤n     ← 子方向的见证是**父**（更粗）
    §I2 定义细化      `细 ⊑ 粗` ⟺ `合并({细}) = 粗`
    §K3 局部失效      插入 x 的失效范围 ⊆ `Cone(x)`

**`Cone(x)` 在内核里就是「插入 x 时走过的那条路径」** ——
`代价`（§I3）逐层选子方向，选出来的链就是 x 的支撑锥。
这不是实现取巧：GiST 的插入路径本来就同时是「x 属于哪些键」的答案，
所以「失效 ⊆ 支撑锥」是**结构上成立**的，§K3 因此可以真的被检查（`B8`）。

---

## 一条刻意不给内核的能力

内核**没有**「这一项属于哪个方向」的谓词。归属由**内核按 `代价` 分配**得到，
不是问插件问出来的。

理由：`§I1 命中` 允许假阳（§K8），所以 `命中(d, x) = 是` **不能**证明 x ∈ d；
拿它当归属谓词，§I4 要求的「不重不漏」就**无法检查**（重叠永远看不出来）。
把归属交给内核，划分就成了内核的**构造性质**，`B4` 才检查得动。

⇒ **划分是内核的责任，不是插件的责任。** 插件只负责「提出两个候选方向」。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .direction import (
    EVENT_BORN,
    # ⚠️ `EVENT_INVALIDATED` **故意没在这里用**：§M2 情形③（「不再被强制」）
    #    需要「删除」流程才可达，本轮只做了插入。事件种类保留在账本里
    #    （`§K4` 的区分靠它），等删除流程落地时再接上。
    EVENT_OUT_OF_SCOPE,
    EVENT_STAYED,
    EVENT_UNSPLITTABLE,
    EVENT_USAGE,
    EVENT_WITNESS_UPDATED,
    ORIGIN_EXOGENOUS,
    ORIGIN_SPLIT,
    Direction,
    Ledger,
)
from .interfaces import (
    PluginContractError,
    UsageSignal,
    WeightedRecord,
    call_hit,
    call_penalty,
    call_split,
    validate_plugin,
)
from .tri import Tri, fold

#: 倾向权重的下界。`1/位置` 会随位置线性衰减，夹一个下界是为了**限住权重上界**
#: （权重 = 结果 / 倾向 ≤ 1/FLOOR）。不夹的话，一个排在第 500 位的方向
#: 会拿到 500 倍的权重，方差大到把自优化掀翻。
PROPENSITY_FLOOR = 0.05


@dataclass(frozen=True)
class Query:
    """一次查询。

    `ideal` 是**外生声明的 ground truth** —— 「这次查询应该命中哪些项」。
    它**不是**插件算出来的，所以 `B1`（假阴）才有得比。
    dce 的做法一致：合成语料带 ground truth，不拿被测对象自己产出的东西当答案。
    """

    ideal: frozenset[str]
    require: frozenset[str] = frozenset()   # 要求出现的键（方向 A 用）
    forbid: frozenset[str] = frozenset()    # 要求不出现的键
    min_rank: int = 1                       # 要求的分辨率：秩 < 它的方向只能答「未展开」
    label: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.ideal, frozenset):
            raise TypeError("ideal 必须是 frozenset —— 顺序不该影响查询语义")


@dataclass
class QueryResult:
    """`R3` 的产物。**三态分开记** —— 不许把「未展开」并进「否」（§K6 / B10）。"""

    status: Tri
    hit_items: frozenset[str] = frozenset()
    yes: tuple[str, ...] = ()           # 说「是」的方向
    no: tuple[str, ...] = ()            # 说「否」的方向
    unexpanded: tuple[str, ...] = ()    # 说「未展开」的方向
    layers_visited: int = 0
    trace: tuple[tuple[str, str], ...] = ()

    @property
    def counts(self) -> dict[str, int]:
        return {"是": len(self.yes), "否": len(self.no), "未展开": len(self.unexpanded)}

    def render(self) -> str:
        """**输出形态** —— B10 / B6 检查的就是这里。

        三种去向必须长得不一样：
            「确定没有」  vs  「未知，需展开」  vs  「命中」
        合并其中任意两个都是 §K6 违规。
        """
        c = self.counts
        if self.status is Tri.YES:
            head = f"命中 {len(self.hit_items)} 项"
        elif self.status is Tri.NO:
            head = "确定没有"
        else:
            head = "未知，需展开"
        return (
            f"{head}｜是={c['是']} 否={c['否']} 未展开={c['未展开']}"
            f"｜层={self.layers_visited}"
        )


class Kernel:
    """内核。构造时**只**接受一个插件和一批项 —— 没有别的参数。

    「没有别的参数」是 §4.3 的落地：**没有全局目标函数**。
    若这里多出一个 `weights=` 或 `objective=`，§B12 就该红。
    """

    def __init__(self, plugin: Any, items: dict[str, Any]) -> None:
        missing = validate_plugin(plugin)
        if missing:
            raise PluginContractError(
                f"插件缺少方法 {missing} —— §I1–§I7 七个方法一个都不能少"
            )
        self.plugin = plugin
        self.items: dict[str, Any] = dict(items)
        self.ledger = Ledger()
        self._dirs: dict[str, Direction] = {}
        self._children: dict[str, tuple[str, ...]] = {}
        self._members: dict[str, set[str]] = {}
        #: **成功**展开过的方向（= 真的有子层）。叶**不**进这里 ——
        #: 见 `expand`：把叶也写进来就等于宣布「叶是终态」，那是原来那处缺陷的形状。
        self._expanded: set[str] = set()
        #: 展开**失败**过的方向 → **它是对哪一批成员判的**。
        #: 「分不开」只对那一次的成员集成立 —— 成员集变了就重试（见 `expand`）。
        self._tried: dict[str, frozenset[str]] = {}
        self._path: dict[str, tuple[str, ...]] = {}
        self._counter = 0
        self._root: Direction | None = None
        # 记账位（§8.1）—— 内核知道它**展示过**什么，所以倾向由内核填
        #: **展示**顺序 —— 由 `show()` 显式设。倾向权重的位次从这里来。
        self._shown: tuple[str, ...] = ()
        #: 遍历经过的**最后一层前沿** —— 纯内省用，**不是**展示。
        self._last_frontier: tuple[str, ...] = ()
        self.usage: list[WeightedRecord] = []

    # --- 建 ---------------------------------------------------------------

    def build(self, root_payload: Any) -> Direction:
        """建根。根是**外生**的 —— 它的 payload 由调用方（人）给出。"""
        self._root = self._new(rank=1, payload=root_payload, witness=(),
                               origin=ORIGIN_EXOGENOUS, parent=None)
        self._members[self._root.did] = set(self.items)
        return self._root

    @property
    def root(self) -> Direction:
        if self._root is None:
            raise RuntimeError("还没 build()")
        return self._root

    def _new(self, *, rank: int, payload: Any, witness: tuple[str, ...],
             origin: str, parent: str | None) -> Direction:
        did = f"D{self._counter}"
        self._counter += 1
        d = Direction(did=did, rank=rank, payload=payload, witness=witness,
                      origin=origin, parent=parent)
        self._dirs[did] = d
        self.ledger.append(EVENT_BORN, did, rank=rank, origin=origin, parent=parent)
        return d

    # --- 读 ---------------------------------------------------------------

    def direction(self, did: str) -> Direction:
        return self._dirs[did]

    def all_directions(self) -> list[Direction]:
        return [self._dirs[k] for k in sorted(self._dirs, key=_did_order)]

    def children_of(self, d: Direction) -> tuple[Direction, ...]:
        return tuple(self._dirs[k] for k in self._children.get(d.did, ()))

    def members_of(self, d: Direction) -> frozenset[str]:
        return frozenset(self._members.get(d.did, ()))

    @property
    def placed(self) -> frozenset[str]:
        """**真的放进了结构**的项 —— 与 `items` 不是一回事。

        `§10.2` 出路 (1) 之后，内核会**认识**一些它**没放进结构**的项
        （落在根覆盖之外的那些，见 `_outside_root`）。两者必须分得开 ——
        合并成一个数就等于把「知道」与「收下了」当成一件事。
        """
        out: set[str] = set()
        for mem in self._members.values():
            out |= mem
        return frozenset(out)

    def is_expanded(self, d: Direction) -> bool:
        return d.did in self._expanded

    def cone(self, item_id: str) -> tuple[str, ...]:
        """§3 的**支撑锥** —— 插入路径。

        「见证含 x 的方向」在内核里等于「x 在插入时走过的那些方向」。
        """
        return tuple(self._path.get(item_id, ()))

    # --- 证明「不收」的方向（§10.2 出路 (1) / (4)） -------------------------

    def _refuses(self, d: Direction, item_id: str) -> bool:
        """d 是否**证明**不收这个项 —— 用 `§I1 命中` 的**单元素查询**问。

        ⚠️ **只有「否」才是否证**：

            否          这是**证明** ⇒ 项按定义不在这个方向的覆盖里
            是          允许是假阳（§K8）⇒ 不作数
            未展开      「不知道」不许被当成「不在」⇒ 不作数

        方向与 §K8 完全一致：**否**是证明，**是**是允许的假阳。

        ## 为什么用 `命中` 而不是新加一个「覆盖谓词」

        `命中` 已经**就是**那个谓词 —— `ideal = {x}` 的单元素查询问的就是
        「这个方向里有没有 x」。所以这条规则**不新增接口方法** ⇒ 不用重验 §I1–§I7。
        （清单里这一项原来是全表唯一可能要重验的；这样落地就不用。）

        ⚠️ **它偏保守**：`是` 与 `未展开` 都放行，所以只有「有证明」才拦。
           这条的不对称与 §K8 同向 —— 拦错了会**少放**，放错了才是**假阴**。

        ⚠️ **它对某些插件是空转的**：`keyset.hit` 对单元素 `ideal` 恒答「是」
           （它的「否」只在 `req`/`forb` 与查询冲突时下，而单元素查询两者都空）
           ⇒ 对 A 这条永远不拦。**空转本身不是缺陷**（A 的健全性本来就 0/328），
           但要**说清**：拦得住拦不住取决于插件敢不敢对单元素查询下「否」。
        """
        try:
            return call_hit(self.plugin, d,
                            Query(ideal=frozenset({item_id}))) is Tri.NO
        except Exception:  # noqa: BLE001 - 插件是外部代码
            return False        # 插件炸了**不许**当成「不在」—— 那是往假阴偏

    def stayed_of(self, d: Direction) -> frozenset[str]:
        """**留在这一层**的项 —— 每个子方向都证明不收它（§10.2 出路 (4)）。

        直接从账本读，不另存一份 —— 单一真相源（§K4 只追加）。
        """
        return frozenset(e.detail.get("item", "")
                         for e in self.ledger
                         if e.kind == EVENT_STAYED and e.did == d.did)

    def _outside_root(self, item_id: str) -> bool:
        """根是否**证明**这个项落在它的覆盖之外 —— 见 `_refuses`。

        ## 它为什么挂在**根**上

        Bε-tree 靠一条不变量把「待处理的项不许丢」钉住：**待处理的项必须落在
        查询路径上**。查询是从根沿 `劈开` 往下走的 ⇒ **根**在**每一条**查询路径上，
        所以这是唯一能保证「记了账就一定会被看见」的位置。

        ## 与出路 (4) 的关系：这是「无处可停」的那一支

        下降时每一步只进入「没有证明不收它」的子方向；**所有**子方向都证明不收 ⇒
        项**留在父方向**（出路 (4)）。根没有父 ⇒ 无处可停 ⇒ 只能**不放进结构**，
        并记一条 `out_of_scope`。⇒ 两条是**同一条规则**的两个分支，
        区别只在「有没有地方可以停」。
        """
        if self._root is None:
            return False
        return self._refuses(self._root, item_id)

    # --- 展开（按需，§M2） -------------------------------------------------

    def expand(self, d: Direction) -> tuple[Direction, ...]:
        """按需展开一个方向。**不预先物化**（§3 名词：展开的定义）。

        返回值 `()` 表示 **d 是叶** —— 分不开，或分开后有一半是多余的（§K2）。

        四情形（§M2）：
            ① 分不开            → 叶（**不建这一层**，见下）
            ② 见证被更新        → 追加 `witness_updated`（**事实**变化，不是判断）
            ④ 长出新的非平凡结构 → 追加 `born`（**涌现**）

        ⚠️ **情形③「标记失效（不删）」在这里不实现** —— 它需要一条**删除/收缩**
        流程（把某个方向作废但保留账本记录），而那条流程还没建。`EVENT_INVALIDATED`
        因此**故意不 import**（`from .direction import …` 里没有它）。
        留一个永不触发的事件种类，等于给读代码的人一盏永远不亮的灯。

        ---

        ## ★ 叶**不是终态** —— 成员集变了就重试（这才是情形④能发生的地方）

        原来的实现把「分不开」也写进 `_expanded`，于是一个方向一旦被判成叶就
        **永远是叶**。后果很具体：树只在**第一批**项上长过层，之后插进来的项
        全都堆进已有叶里 —— **维护永远不会加深结构**。

            实测（同一份 36 项语料，只是建法不同；keyset 方向）。

            ⚠️ 当时**两个缺陷叠在一起**，读表要分开看：
                (i)  `insert()` 从不重试 ⇒ 结构退化（方向数掉、叶容量爆）
                (ii) 判空时物化**两个空壳** ⇒ 方向数里混进 2 × 判空次数

                建法              改前 方向数   其中空壳   改前 最大叶容量
                ────────────────────────────────────────────────────
                一次建完 36        39         14        10
                先建 30，维护  6   35         12        10
                先建 24，维护 12   31         12        10
                先建 12，维护 24   15          8        16
                先建  6，维护 30  **7**        **4**   **31**

            最后一行不是「维护很便宜」，是**检索结构退化成了平表**：
            36 项挤在 3 个真方向里，其中一个叶装 31 项（另外 4 个方向是空壳）。

        **改后**（同一份语料、五种建法，三个方向都量了）：

            方向       建法            方向数   §K2 判空   最大叶容量
            ───────────────────────────────────────────────────────
            keyset    一次建完 36       25       7         10
            keyset    先建  6 维护 30   25       4         10
            reach     一次建完 36       71       0          1
            reach     先建  6 维护 30   71       0          1
            sequence  一次建完 36       21       8         10
            sequence  先建  6 维护 30   21       7         10

        三个后果，都要单独说：

        * **尺度与分辨率不再随建法变化**（方向数、最大叶容量逐项相同）——
          维护不再把结构退化成平表。这是修复要的效果。
          `run_tests.test_emergence` ③ 把这条钉住了，而且**跨三个方向**查，
          免得只是 keyset 的巧合。
        * **空壳消失**（39 → 25 = 25 个真方向 + 0 个空壳）。可验的后果是
          `members(d) ≠ ∅` 恒成立 —— 那条断言在旧实现下会红。
        * ⚠️ **但树不同构**（`_shape()` 实测不相等）。这是**当前实现
          （路径局部增量）的性质**，不是「增量」的固有代价 ——
          Godin 1995 的增量概念格既增量又顺序无关，Naor–Teague 走阈值重建。
          修复要的是「不退化」，不是「维护必须重建成批建的样子」；
          但「不同构」这件事**要在文档里写成选择**，不能写成定理（见 `C8` N4）。
        * `§K2 判空` 是**唯一**还会随建法变的数 —— 因为它是「判过几次」的记账，
          本来就与历史有关（§K2 的时间维度）。它是**度量**，不是判据。

        ---

        ## ★ §K2 的字面是「不建这一层」—— 那就真的**不建**

        原来是「先建出一对子方向，再把它们标成失效」。那不是「不建」，
        是「建了再作废」。两者的区别不是风格：

            建了再作废   方向数里多出 2 个空壳 ⇒ 「空权威层比例」这个
                         **形状信号**被污染成「试了几次」
            不建         形状信号干净了，但**代价没有消失** ——
                         它搬到「叶容量」上：这一层不建，这些项只能留在
                         这个叶里 ⇒ 叶容量变大 ⇒ **分辨率下降**

        ⇒ 判空时只追加一条 `unsplittable`（挂在**父方向**上，因为判定的主体是它，
          而且**没有子方向可以挂**），不建任何方向。
        ⇒ 度量也跟着搬：`stats()` 同时报「§K2 判空」与「叶容量」，
          它们**是同一件事的两种记账**（设计文档 §7.1）。

        ---

        ## 与 `B15` 不冲突（这是改这里之前必须确认的事）

        `B15` 要的是「同一批项、一次建完、换插入顺序 ⇒ 树同构」。它成立靠的是：
        `build()` 把**当时的全集**装进根，所以每个方向第一次被展开时，
        它的成员集**已经是完整的** —— 展开一次就定形，重试逻辑永不触发。

        维护路径正相反：成员集是**后来才长起来的**，重试才会触发。
        两者说的不是一回事：`B15` 守**批建**，维护的历史相关性是**固有**的。

        ---

        ## ★ §I4 的返回是「None | 一组方向」，**不是固定两个**

        三件事同时成立，而且**互相独立**：

        **(1) 分支数由插件定（k 叉）。** 二分是 B-tree 遗产，不是所有方向的最自然形状：
            有序方向（序列前缀）的自然形状是 **trie —— 一个节点按「下一个符号」分出 k 个**。
            被迫二分 ⇒ 中间层造出「两个符号的并」这种**不是前缀**的 payload
            ⇒ 覆盖变大（假阳）且要多花一层。内核现在照插件给的个数建子方向，
            不写死 2。

        **(2) 「分不开」是插件**显式声明**的（返回 `None`），不是内核猜出来的。**
            原来编码成「返回两个相同 payload」，内核从「分配后一侧为空」**反推**
            「插件分不开」。两种东西于是长得一样：

                插件**判**分不开        接口的正常用法 —— 插件的判断
                插件给的候选**切不动**   候选退化 —— 插件与内核之间的摩擦

            ⇒ 现在分开记（账本 `unsplittable` 的 `by` 字段），`stats()` 分两个数报。
              分开记才有信息量：一个**永远返回空组**的插件与一个**诚实声明分不开**的
              插件，在「反推」的读法下产出完全相同的账本 —— 那就是
              「空转与通过长得一模一样」在接口层的形态。

        **(3) 少给（<2）是违约，不是「分不开」。** 由 `call_split` 判。
            内核**不替插件猜**：要么说 `None`，要么给 ≥2 个。

        ⚠️ **判空的两条路径都不建方向**（§K2 的字面），代价仍然搬到**叶容量**上。
            `None` 路径与「切不动」路径的**代价完全一样**，区别只在**归因**。
        """
        if d.did in self._children:
            return self.children_of(d)

        items = sorted(self._members.get(d.did, ()))
        if len(items) < 2:
            # 情形①：单项，分不开。**不记 `_tried`** ——
            # 它只是「还没到能判的时候」，等长到 2 项再试。
            return ()

        if self._tried.get(d.did) == frozenset(items):
            return ()                       # 这批成员试过了，结论不变

        group = call_split(self.plugin, d, [self.items[i] for i in items])

        if group is None:
            # 路径一：**插件声明**分不开 —— 接口的正常用法，是插件的判断。
            self.ledger.append(EVENT_UNSPLITTABLE, d.did,
                               reason="§K2：插件声明这一层分不开", by="plugin",
                               members=len(items))
            self._tried[d.did] = frozenset(items)
            return ()

        # 先用**探针**算归属：不建方向、不写账本 ⇒ 判成「分不开」时不留残骸。
        # 平手判给**靠前**那个候选 —— 与二分时「`c1.did` 数值上小于 `c2.did`」等价。
        # ⚠️ 探针的 did 必须**互不相同**：插件若按 `did` 定序，同串会让它看到平手。
        probes = [_probe(d, p, j) for j, p in enumerate(group)]
        buckets: list[list[str]] = [[] for _ in probes]
        for iid in items:
            costs = [call_penalty(self.plugin, pr, self.items[iid]) for pr in probes]
            buckets[min(range(len(costs)), key=lambda j: (costs[j], j))].append(iid)

        # §K2 非平凡：**每一侧**都得有东西，否则这一层不建 —— 就照字面来。
        if any(not g for g in buckets):
            # 路径二：候选**切不动**。归因与路径一不同（这是插件与内核之间的摩擦），
            # 但**代价相同**（都不建这一层）。
            self.ledger.append(EVENT_UNSPLITTABLE, d.did,
                               reason="§K2：这一层不建（劈开后一侧为空）", by="kernel",
                               sizes=tuple(len(g) for g in buckets), members=len(items))
            self._tried[d.did] = frozenset(items)
            return ()

        kids = tuple(self._new(rank=d.rank + 1, payload=p, witness=(d.did,),
                               origin=ORIGIN_SPLIT, parent=d.did)
                     for p in group)
        for kid, g in zip(kids, buckets):
            self._members[kid.did] = set(g)
        self._children[d.did] = tuple(k.did for k in kids)
        self._expanded.add(d.did)
        self._tried.pop(d.did, None)        # 长出来了 ⇒ 上一次的失败不再成立
        self.ledger.append(EVENT_WITNESS_UPDATED, d.did,
                           witness=tuple(k.did for k in kids),
                           sizes=tuple(len(g) for g in buckets))
        return kids

    # --- 插入（§M0–§M5） --------------------------------------------------

    def insert(self, item_id: str, item: Any = None) -> tuple[str, ...]:
        """插入一个项，返回它走过的路径（= `Cone(x)`）。

        §K3：**失效范围 ⊆ `Cone(x)`** —— 这里「失效范围」就是本函数触碰过的方向集合，
        而它按构造等于路径。`B8` 会把这条再独立查一遍。

        ## `item` 非空 ⇒ 这是一个**新项**（流程 B 的维护路径）

        `build()` 会把**当时的全集**装进根，所以「后添的项」必须能在此刻才纳入 ——
        否则它从第一层就参与分组，那不是维护，是重建。

        ⚠️ **后插的项不参与它之前已经发生过的分组。** 它只走已有的树，走到叶就落在那里。

        这与 `B15`（换插入顺序 ⇒ 树同构）**不冲突**，因为两者说的不是一回事：

            B15    同一批项、**一次建完**，只是插入顺序不同 ⇒ 结构必须同构
            维护   先建一批、**后来再添** ⇒ 历史本来就不同，结构不同是对的

        ⇒ `B15` 守的是**批建**的可复现性。维护的历史相关性是**当前实现的
          性质**，**不是**「增量」的固有代价（`C8` N3 有反例）。

        ## ★ 落在**根覆盖之外**的项：记一条事件，**不塞进结构**

        见 `_outside_root`。要点：只有 `命中(根, {x}) = 否` 才算数 ——
        那是**证明**，不是猜测。这样「最外层意图之外」从一个**静默的错位**
        变成一条**显式、可数**的账（`stats()['根覆盖之外']`）。

        ⚠️ 代价是**范围**，不是正确性：那些项在这条方向上检不出来，
           但它们**在账上**。这正是这一条的全部价值 ——
           把一个静默的状态换成一条显式的记录。
        """
        if item is not None:
            self.items[item_id] = item
        if item_id not in self.items:
            raise KeyError(item_id)
        if self._outside_root(item_id):
            self.ledger.append(EVENT_OUT_OF_SCOPE, self.root.did, item=item_id,
                               reason="§I1：根对单元素查询判「否」⇒ 按证明落在根覆盖之外")
            self._path[item_id] = ()
            return ()
        path: list[str] = []
        d = self.root
        while True:
            path.append(d.did)
            kids = self.expand(d)
            if not kids:
                # 叶：**先把项落下来，再看落下来之后分不分得开**（§M2 情形④）。
                #
                # ⚠️ 这一步不能省。只在「进入一个方向时」试展开的话，
                #    刚落地的那一项永远轮不到被考虑 —— 它的叶带着**落它之前**
                #    的结论。落完再试一次，才闭合。
                self._members.setdefault(d.did, set()).add(item_id)
                kids = self.expand(d)
                if not kids:
                    break
                # 落下去之后长出了新层 ⇒ 这一项也得往下走。
                # 每层子方向的成员是父的**真子集**（另一侧非空），所以一定终止。
            # ★ 只进入「没有**证明**不收它」的子方向（§10.2 出路 (4)）。
            #   见 `_refuses`：`是` 与 `未展开` 都放行，只有「否」是证明。
            ok = [k for k in kids if not self._refuses(k, item_id)]
            if not ok:
                # 每个子方向都证明不收它 ⇒ **停在这一层**，项留在父方向。
                #
                # 为什么不拒绝（出路 (3) 的读法）：拒绝会让项从**成员集**里消失，
                # 而 `B1` 的 ground truth 正是「成员 ∩ 查询」——
                # 于是「健全性变绿」会**部分来自数据变少**。留在父方向则：
                # 项仍可检索（父的 `命中` 说「是」）、`B1` 的 ground truth 不动，
                # 代价只落在**划分**上（`B4` 要收窄成「漏的恰好是账上那些」）。
                # 外部印证：X-tree 的 supernode ——
                #   "created during insertion **only if there is no other possibility**"
                self.ledger.append(EVENT_STAYED, d.did, item=item_id,
                                   reason="§10.2 出路 (4)：每个子方向都证明不收它 ⇒ 留在这一层",
                                   kids=tuple(k.did for k in kids))
                break
            nxt = min(ok, key=lambda k: (call_penalty(self.plugin, k, self.items[item_id]), k.did))
            d = nxt
        for did in path:
            self._members.setdefault(did, set()).add(item_id)
        self._path[item_id] = tuple(path)
        return tuple(path)

    # --- 遍历循环（§R0–§R5） ----------------------------------------------

    def query(self, q: Query) -> QueryResult:
        """§R0–§R5。

        `R2` 的**三分支不许合并** —— 所以这里把三态分别收进三个元组，
        最后按 `R3` 的三去向给一个 `Tri`。**没有**任何一步把「未展开」当「否」。
        """
        yes: list[str] = []
        no: list[str] = []
        unexp: list[str] = []
        trace: list[tuple[str, str]] = []
        hits: set[str] = set()
        layer = 0
        frontier: list[Direction] = [self.root]

        while frontier:
            layer += 1
            self._last_frontier = tuple(d.did for d in frontier)   # 内省用，**不是**展示
            nxt: list[Direction] = []
            for d in frontier:
                v = call_hit(self.plugin, d, q)                 # §R1
                trace.append((d.did, str(v)))
                if v is Tri.YES:                                # §R2 分支一
                    yes.append(d.did)
                    kids = self.expand(d)
                    if kids:
                        nxt.extend(kids)
                    else:
                        hits |= {i for i in self._members.get(d.did, ()) if i in q.ideal}
                elif v is Tri.NO:                               # §R2 分支二
                    no.append(d.did)
                else:                                           # §R2 分支三 —— 独立第三值
                    unexp.append(d.did)
            frontier = nxt

        # §R3 三去向
        if yes and not unexp:
            status = Tri.YES
        elif yes:
            status = fold(Tri.YES, Tri.UNEXPANDED)              # 有是有，但不全 —— 不许说「确定」
        elif unexp:
            status = Tri.UNEXPANDED
        else:
            status = Tri.NO
        if hits:
            status = Tri.YES if not unexp else Tri.UNEXPANDED

        return QueryResult(status=status, hit_items=frozenset(hits),
                           yes=tuple(yes), no=tuple(no), unexpanded=tuple(unexp),
                           layers_visited=layer, trace=tuple(trace))

    # --- 展示（§8.1 的记账前提） -------------------------------------------

    def show(self, ds: Iterable[Any]) -> tuple[str, ...]:
        """§8.1 的「展示」—— **内核记录它把哪些方向、按什么次序摆给了使用者**。

        ⚠️ **这不是遍历的副作用。遍历经过的方向 ≠ 摆给使用者看的方向。**

            遍历   会走很多层、很多「未展开」、很多「否」—— 那是内核自己的事
            展示   只摆**候选**（说「是」的那些），而且**次序就是曝光位次**

        原来的实现把「展示」设成**最后一层的前沿**，于是多层查询之后它停在最深那层：
        给一个第 1 层的方向记使用，位次会算成「不在展示里 ⇒ 当作最靠后」，
        倾向被压到下界。**曝光模型的前提（位次有意义）在那时是假的。**

        ⇒ 所以「展示」必须是**显式的一步**，由流程 A 在拿到候选之后调用。
          （`C7-流程跑通.md` 记了这条是怎么暴露出来的。）

        入参可以是方向对象，也可以是 id —— 遍历结果里两种都有。
        """
        self._shown = tuple(d.did if hasattr(d, "did") else str(d) for d in ds)
        return self._shown

    @property
    def shown(self) -> tuple[str, ...]:
        """本轮展示过的方向 id（按曝光位次）。"""
        return self._shown

    # --- 使用记录（§I6 / §8.1） -------------------------------------------

    def record_usage(self, d: Direction, interaction: Any) -> WeightedRecord | None:
        """调 §I6，然后**由内核填倾向权重**（§8.1）。

        分工：插件只判定「这次交互算不算信号」；倾向由内核填，
        因为**只有内核知道它展示了什么、展示在第几位**。

        ## 曝光模型：按**位置**，不按个数

            展示在第 k 位  ⇒  propensity = 1 / k      （并夹在 PROPENSITY_FLOOR 以上）

        为什么不能按「展示了几个」：那等于假设**每个位置的曝光概率相同**。
        真实检索里第一位和第十位的曝光概率差一个量级 ——
        按个数算，倾向加权会把偏差**原样带进**自优化，
        而自优化的目的恰恰是修掉这个偏差。

        位置来自**展示顺序**（`show()` 设的），所以插件**无法伪造**它 ——
        这也是 §8.1 把填倾向这件事留给内核的原因。

        ## 没有展示上下文时

        若这一轮**没调用过 `show()`**（比如单元测试直接记一条），
        位次无从谈起。这里**不抛异常**（那会把只想测 §I6 的调用者一起打死），
        而是按「只展示过它自己」算，并在 `extra` 里**标出来** ——
        **不知道就要写「不知道」，不能默默当成知道**（§K6 的同一条纪律）。
        """
        sig: UsageSignal | None = self.plugin.signal(d, interaction)
        if sig is None:
            return None                      # 「没有观测到使用」≠「使用了但为负」
        if self._shown:
            shown = self._shown
            no_context = False
        else:
            shown = (d.did,)
            no_context = True
        try:
            position = shown.index(d.did) + 1        # 1-based，**曝光位次**
        except ValueError:
            position = len(shown) + 1                # 不在本次展示里 ⇒ 当作最靠后
        propensity = max(1.0 / position, PROPENSITY_FLOOR)
        rec = WeightedRecord(did=d.did, outcome=float(sig.outcome),
                             propensity=propensity, note=sig.note,
                             extra={"shown": list(shown), "shown_n": len(shown),
                                    "position": position,
                                    "无展示上下文": no_context})
        self.usage.append(rec)
        self.ledger.append(EVENT_USAGE, d.did,
                           outcome=rec.outcome, propensity=rec.propensity,
                           position=position)
        return rec

    # --- 自省 -------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """自省。**代价与形状分开报，而且要知道代价搬到哪儿去了。**

        ## §K2 判「这一层不建」之后，代价**不会消失，只会换地方**

            旧实现（建了再作废）  代价记在「空权威层」上 —— 方向数里多出空壳
            字面实现（不建）      代价记在「**叶容量**」上 —— 这一层不建，
                                  这些项就只能留在这个叶里 ⇒ 叶装得多 ⇒
                                  **分辨率下降**（查询要多扫几项）

        ⇒ 所以两个数一起报，**它们是同一件事的两种记账**：

            `§K2 判空`    有多少**方向**被判「不建」 —— 形状与接口贴合度的信号
            `最大/平均叶容量`  那些项最后堆到哪儿去了 —— **代价**信号

        实测（36 项语料，一次建完）：reach 判空 0 / 最大叶容量 1 / 平均 1.00；
        keyset 判空 7 / 10 / 2.77；sequence 判空 8 / 10 / 3.27。
        形状越自然，两个数都越小。

        ⚠️ `§K2 判空` 数的是**方向**（`len(self._tried)`），不是**判定事件数**。
        成员集每变一次就重判一次（§K2 的时间维度），所以账本里的 `unsplittable`
        事件**可能多于**这个数。两者都别当判据用 —— 它们是**度量**。

        ## §I4 之后多一个数：判空的**归因**

        `§I2 判空` 有两条路径，代价**完全一样**（都不建这一层），但归因不同：

            声明分不开   插件返回 `None` —— 接口的**正常**用法，是插件的判断
            切不动       插件给了候选，但分配后一侧为空 —— 插件与内核之间的摩擦

        分开报的理由：一个**永远返回空组**的插件，与一个**诚实声明分不开**的插件，
        在只报总数的读法下**产出完全相同的数** —— 那就是
        「空转与通过长得一模一样」。分开报之后，「切不动」恒为 0 才说明
        **插件确实在用它自己的判断说话**，而不是内核在替它兜底。

        ## 「方向」和「有子层」要分开报

        `方向 = 有子层 + 叶` 恒成立，所以这两个数可以互相验。
        ⚠️ 这个键原来叫「已展开」，但它把**叶**也算进去了 —— 名不副实。

        ## 「认识」与「纳入」也要分开报（§10.2 出路 (1)）

        `认识 = len(items)`、`纳入 = len(placed)`。两者之差就是**根覆盖之外**的项数 ——
        那些项内核**认识**、但**没有收进结构**。代价落在**范围**上（在这条方向上
        检不出来），所以它必须是个**能被看见的数**：不然「根覆盖之外」就退化成
        一个没人知道的静默状态 —— 那正是这一条要消灭的东西。

        ⚠️ 三者要一起看：`认识 = 纳入 + 根覆盖之外`。任一条不成立就是记账错了。

        ## 再一个数：**滞留**（§10.2 出路 (4)）

        `滞留` = 「每个子方向都**证明**不收它 ⇒ 留在父方向」的项数。
        它与 `根覆盖之外` 是**两条不同的代价**，必须分开报：

            根覆盖之外   项**不在**任何方向的覆盖里（根下「否」）⇒ **范围**损失
            滞留         项**在**父的覆盖里，但**没有子方向**表达得出来 ⇒ **划分**代价
                         （项仍可检索 —— 父的 `命中` 说「是」）

        合并成一个数就把「声称盖不住」与「这一层表达不出来」当成一件事。
        ⇒ 滞留的代价落在**划分**上：`B4` 要收窄成
          「不重，且**漏的恰好是账上那些**」（见 `checks/contract.py`）。

        ## 字面 §K2 还有一个**可验**的后果：不存在空壳方向

        判空**不建** ⇒ 每个方向都是被真的分配过成员的 ⇒ `members(d) ≠ ∅` 恒成立。
        旧实现每判空一次物化**两个**空壳，于是 `方向数 = 活方向数 + 2 × 判空次数`。
        ⇒ 这两条是一组「改前会红」的判据（`run_tests.test_emergence` ② 里查了它们）：
        `每个方向的成员集非空`，以及 `方向 = 有子层 + 叶`（不许多出那两个）。

        ## ★ 分辨率要看「**项停在哪儿**」，不能只看叶（这是 `叶容量` 的一个洞）

        `叶容量` 的定义是「叶里最多几项」—— 它默认**每一项都走到了叶**。
        §10.2 出路 (4) 之后这个前提**不成立**：项可以停在**内部**节点上。

            实测（`sequence`，先建 2 维护 34）：
                叶容量      **2**     ← 看起来分辨率很高
                最大停留数  **34**    ← 真相：34 项堆在**根**上，根是内部节点
            同一份结构，`叶容量` 报 2 而真实扫描量是 34 —— **一个数骗人，一个数不骗**。

        ⇒ 所以补一个 `最大停留数`：

            `停(d) = |members(d) \\ ∪ members(子)|`     ← 停在 d 上、没再往下走的项
            `最大停留数 = max_d 停(d)`

        **它是 `叶容量` 的推广**：项都走到叶时，叶的 `停` 就是它的成员数，
        内部节点的 `停` 是 0 ⇒ 两者**相等**（批建路径上实测相同）。

        ⚠️ **不能用 `Cone(x)` 的末位来算** —— 锥是**插入时**记下的，
           而某个方向**后来**才劈开时，已在它里面的项会被重新分配，
           锥却不会跟着延长 ⇒ 算出来会把「早就走到叶的项」当成「停在内部节点」。
           ⇒ 必须**从成员集现算**（`停(d)` 的定义）。
        ⇒ 两条**恒等式**（可验，见 `run_tests`）：
           `Σ_d 停(d) == 纳入`、`Σ_{d 非叶} 停(d) == 滞留（账上）`。
        """
        dirs = list(self._dirs.values())
        leaves = [d for d in dirs if not self._children.get(d.did)]
        caps = [len(self._members.get(d.did, ())) for d in leaves]
        # ★ 停留数：从**成员集现算**，不用 `Cone(x)`（见上）。
        stop: dict[str, int] = {}
        for d in dirs:
            mine = set(self._members.get(d.did, ()))
            kids = self._children.get(d.did, ())
            if not kids:
                stop[d.did] = len(mine)
            else:
                union: set[str] = set()
                for k in kids:
                    union |= self._members.get(k, set())
                stop[d.did] = len(mine - union)
        fans = [len(k) for k in self._children.values()]
        unsplit = [e for e in self.ledger if e.kind == EVENT_UNSPLITTABLE]
        oos = [e for e in self.ledger if e.kind == EVENT_OUT_OF_SCOPE]
        stayed = [e for e in self.ledger if e.kind == EVENT_STAYED]
        return {
            "方向": len(self._dirs),
            "有子层": len(self._children),
            "叶": len(leaves),
            "§K2 判空": len(self._tried),
            "判空·声明": sum(1 for e in unsplit if e.detail.get("by") == "plugin"),
            "判空·切不动": sum(1 for e in unsplit if e.detail.get("by") == "kernel"),
            # ★ 「认识」与「收下了」分开报（§10.2 出路 (1)）。
            #   代价落在**范围**上，所以必须是个能被看见的数 ——
            #   不然「根覆盖之外」就退化成一个没人知道的静默状态。
            "认识": len(self.items),
            "纳入": len(self.placed),
            "根覆盖之外": len(oos),
            # ★ 「范围损失」与「划分代价」也分开报（§10.2 出路 (4)）。
            "滞留": len(stayed),
            "最大扇出": max(fans) if fans else 0,
            "最大叶容量": max(caps) if caps else 0,
            "平均叶容量": round(sum(caps) / len(caps), 2) if caps else 0.0,
            # ★ 分辨率的**推广** —— 项停在内部节点时，`叶容量` 看不见（见上）。
            "最大停留数": max(stop.values()) if stop else 0,
            "停在叶上": sum(stop[d.did] for d in leaves),
            "停在内部": sum(stop[d.did] for d in dirs if self._children.get(d.did)),
            "事件": len(self.ledger),
            "使用记录": len(self.usage),
        }


def _did_order(did: str) -> tuple[int, str]:
    """`D12` 要排在 `D3` 后面 —— 按数值排，不按字典序。"""
    if did.startswith("D") and did[1:].isdigit():
        return (int(did[1:]), "")
    return (1 << 30, did)


def _probe(parent: Direction, payload: Any, slot: int = 0) -> Direction:
    """造一个**探针方向** —— 只用来算 `§I3 代价`，不进 `_dirs`、不写账本。

    为什么需要它：判「这一层分不分得开」必须先把项按代价分配一遍，
    而分配需要候选方向。若直接用真方向，判成「分不开」时就会留下残骸，
    反复重试还会不断累积，把 §K2 的比例信号冲淡。

    `did` 取一个**不会与真方向撞车**的串（真方向都是 `D<数字>`），
    并且**每个候选一个**（`slot`）—— 插件若按 `did` 定序，同串会让它看到平手。
    """
    return Direction(did=f"{parent.did}~probe{slot}", rank=parent.rank + 1, payload=payload,
                     witness=(parent.did,), origin=ORIGIN_SPLIT, parent=parent.did)
