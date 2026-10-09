"""检索器层（流程 T）—— 接受需求 `r`，用抽象层交出的**认识**决定「**先看哪一带**」。

    python -m ldv.run_checks            # 判据在 `(检索)` 那一组里（`ldv/checks/retrieval.py`）

**只做六件事**（`基线§4`）—— 一条都不多：

    T0 分派     `r` 的每栏 → `Query`（**结构映射**，不解释语义）  分栏是**结构**，不是语义
    T1 认识     盘上 `spec` 的指纹 ⇄ 当前结构的 `spec` 的指纹     `csr` 唯一 ⇒ **`spec` 即版本**
    T2 排序     `插件.merge` + `插件.hit` 把块分成「先看 / 后看」   两个都是**已有方法**，不新增
    T3 跑 A     **调** `flow.run_query`，不自立终止依据        本层**不自立**终止依据
    T4 组装     候选 + **结果层面**的解释                        遍历顺序**不许**进输出
    T5 展示/记录 走流程 A 的 `R5a` / `R5b`                       —— 由 `run_query` 一并完成

★ **中心定理**（`基线§3`）：**候选集 ≡ 不用视图时的候选集；视图只改变「跑了多少块才得到它」。**

## ★★ 本层当前是「预备」的（2026-10-09 实测）

    `T0` / `T1` / `T4` / `T5` 有实效（分派 / 认识是否当前 / 解释 / 展示记录）；
    **`T2` / `T3` 是为将来预留的** —— `T2` 算出的顺序**无处可达**：

        `kernel.query` / `flow.run_query` 只收 `q`，从 `root` 遍历整棵树
        而视图块**横跨树**（不是子树）⇒ 「只扫先看的块」**做不到**
        （不能传参，也不能复制流程 A 的循环 —— `流程§0` 禁）

    ⇒ 三个探针（`outputs/_probe_retriever_inert.py`）：顺序**反转** / 换成**空元组** /
      换成**离散划分** ⇒ 输出**逐项相同**。⇒ **收益 = 0**，读数**如实印 0**（`render_reading`）。

⚠️ **一枚已删的东西**（免得被读成「守卫被拿掉了」）：
   `run_in_order` 里原来有一条 `assert set(scanned) == set(order)` —— 它**恒真**
   （`scanned` 就是由 `order` 构造的）⇒ 那是**恒真式冒充守卫**，本仓库最忌的形状。
   循环体现在是**空的**，没有任何可断言的东西 ⇒ 断言已删，理由写在那里。

⇒ 要让它从「预备」变「生效」，只有一条路（另一条被 `§3.2` 关着）：
  **给流程 A 一个「从给定方向集出发」的入口** —— 而那是**要人拍**的接口决定。
  见 `基线§13 13.0`。

## 本层不许做的事

    ✗ 用**块**的判断代替**方向**的判断（终止）
      —— 按块终止的正确性依赖 `§A1` 绿，而**一条判据不该依赖另一条判据绿**（`基线§3.1` 信任链）
    ✗ 复制流程 A 的遍历循环
      —— `流程§0`：**直接调，不复制**。症状**不是红，是检查报绿**（本仓库栽过）
    ✗ 把「先看 / 后看」摆给用户看
      —— `基线§3.2` / `E-2`「排序即推荐」

## 一处**已作废**的默认值（原文保留，免得被当成还有效）

`CONSTRAINTS.md §C1` 当初给的默认值是「**扫完全部块，不早停**」，理由是「顺序只影响代价」。
**那个默认值本身是错的**（2026-10-09 实测）：

    它说「先看的块先跑完 ⇒ 更早能报告『有候选』」—— 而实现里**没有增量报告**
    （一次调用拿全套结果）⇒ **代价也没有变**。
    真因是：**顺序在当前接口下无处可达**（见上面那一段）⇒ 「不早停」是**症状**，不是病根。

⇒ 现在按 `基线§4 T3 ①/②`：**不自立终止依据**（①，仍然成立）+
  **如实写下顺序无处可达**（②）。⇒ 那一行的 `基线§4 步骤 T3`「有『是』⇒ 停」
  **不算 bug** —— 它说的是「由流程 A 自己的 `R3` 决定」，与本层不自立终止依据**一致**。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping

from .cli import DIRECTIONS as BAR_NAMES
from .core.kernel import Kernel, Query
from .core.tri import Tri
from .core.views import ViewSpec, coarsest_stable_refinement, partition_of
from . import flow

__all__ = [
    "BAR_NAMES",
    "DispatchError",
    "as_bar",
    "Reason",
    "Recognition",
    "Retrieval",
    "current_recognition",
    "dispatch",
    "explain",
    "order_blocks",
    "render_reading",
    "render_reason",
    "retrieve",
    "run_in_order",
    "spec_fingerprint",
]


class DispatchError(RuntimeError):
    """`r` 里有栏、而没有对应插件 —— **分派不了**（`基线§8` 停止条件 1）。

    ⚠️ 这是**抛**，不是三态：猜一个「这段处境该交给谁」就是替人做决定。
    """


# ═══ T0 分派 ═══════════════════════════════════════════════════════════════════
#
# `基线§2` 唯一的实质内容：**本层只分派，不解释语义**。
# ⇒ 分派只能是**结构映射**：栏 → `Query`。字段名**照抄** `Query`，一个字不新发明。
#
# ⚠️ 为什么不是「每插件一个不透明 blob + 新方法 `parse(栏) → q`」：
#    `§I1`–`§I7` 七个方法里**没有**这一个。加它就是 `§T4` 停止条件 ①（新增内核方法）。

#: 栏里**允许**出现的字段 —— 就是 `Query` 的字段。多一个就抛（静默丢掉一个约束
#: 会让「这个约束被考虑了」与「它被扔了」长得一模一样）。
_BAR_FIELDS = ("ideal", "require", "forbid", "min_rank", "label")


def dispatch(need: Mapping[str, Mapping[str, object]],
             known: Iterable[str] = BAR_NAMES) -> dict[str, Query]:
    """`步骤 T0` —— 把 `r` 的每一栏交给对应插件，取回一组 `q`。

    `need` 的外层键 = **插件名**（= `插件.name`）；值 = 那一栏。

    ⚠️ 栏**存在但为空**（`§C5` 缺口 4）⇒ 得到一个默认 `Query`，**交给插件自己判**
       （`§I1` 三态本来就允许空查询）。**不是**跳过、**不是**红。
    """
    known_set = set(known)
    out: dict[str, Query] = {}
    for bar, spec in need.items():
        if bar not in known_set:
            raise DispatchError(
                f"`r` 里有栏 {bar!r} 没有对应插件（已知 {sorted(known_set)}）"
                f" —— 分派不了，**不许猜**（基线§8 停止条件 1）")
        out[bar] = _to_query(bar, spec)
    return out


def as_bar(query: Query) -> dict[str, object]:
    """`Query` → 一栏。`_to_query` 的**逆**，与它**一处定义**（两处用：`run_checks` 与对照）。

    ⚠️ 只产出 `_BAR_FIELDS` 那几个键 —— 多一个 `_to_query` 就会抛（那是**故意**的）。
    """
    return {"ideal": query.ideal, "require": query.require, "forbid": query.forbid,
            "min_rank": query.min_rank, "label": query.label}


def _to_query(bar: str, spec: Mapping[str, object]) -> Query:
    """栏 → `Query`。**结构映射**：只认 `_BAR_FIELDS`，多一个字段就抛。"""
    extra = sorted(set(spec) - set(_BAR_FIELDS))
    if extra:
        raise DispatchError(
            f"栏 {bar!r} 里有本层不认识的字段 {extra} —— 本层**不解释语义**（基线§2），"
            f"静默丢掉一个约束与「它被考虑了」长得一模一样")
    return Query(
        ideal=frozenset(spec.get("ideal") or ()),
        require=frozenset(spec.get("require") or ()),
        forbid=frozenset(spec.get("forbid") or ()),
        min_rank=int(spec.get("min_rank", 1)),
        label=str(spec.get("label", "")),
    )


# ═══ T1 取当前认识 ════════════════════════════════════════════════════════════

def spec_fingerprint(spec: ViewSpec) -> str:
    """**认识的版本** = `spec` 的指纹（`基线§6`）。

    `Q = csr(spec)` 而 `csr` **唯一** ⇒ `spec` 一变 `Q` 必然跟着变、`spec` 不变 `Q` 必然不变
    ⇒ **`spec` 的指纹就是「认识版本」**。不需要新加一个量。

    ⚠️ 落盘件另有一个 `FORMAT`（`"ldv-views/1"`）—— 那是**格式**版本，与认识版本**不是一回事**。
    """
    blob = json.dumps(spec.as_dict(), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Recognition:
    """`步骤 T1` 的产物 —— **当前**那份认识。

        `spec`          当前结构的 `spec`（**唯一**的版本载体）
        `q`             实际拿来给块排序的那个划分
        `source`        它是**怎么来的**：`盘上` / `重算`
        `fingerprint`   **实际用到的那份认识**的 `spec` 指纹
        `on_disk`       盘上到底有没有一份认识 —— 决定 `T2` 判据**判还是跳**
    """

    spec: ViewSpec
    q: tuple[frozenset[str], ...]
    source: str
    fingerprint: str
    on_disk: bool


def current_recognition(spec: ViewSpec, recognition: Any = None) -> Recognition:
    """`步骤 T1` —— 取**当前**认识（`基线§6`）。

        `recognition is None`     盘上没有任何认识 ⇒ 现算 `csr(spec)`
        指纹相符                   ⇒ **用盘上那份**（它是当前的）
        指纹不符                   ⇒ **不许用**（重算 `csr(spec)`）

    `recognition` 是 `core/view_persist.from_dict` 读回来的那份 `ViewSet`
    （**读盘不在这里做** —— 本模块只管认不认识它是不是当前的）。

    ⚠️ **不比对的话，「陈旧的认识」与「当前的认识」在只看输出时长得一模一样** ——
       正是本项目最忌的形状（`基线§6`）。
    """
    if recognition is None:
        return Recognition(spec=spec, q=coarsest_stable_refinement(spec),
                           source="重算（盘上没有任何认识）",
                           fingerprint=spec_fingerprint(spec), on_disk=False)
    on_disk_fp = spec_fingerprint(recognition.spec)
    if on_disk_fp != spec_fingerprint(spec):
        return Recognition(spec=spec, q=coarsest_stable_refinement(spec),
                           source=f"重算（盘上陈旧：{on_disk_fp} ≠ 当前）",
                           fingerprint=spec_fingerprint(spec), on_disk=True)
    return Recognition(spec=spec, q=partition_of(recognition.q),
                       source="盘上（指纹相符）",
                       fingerprint=spec_fingerprint(spec), on_disk=True)


# ═══ T2 排序 ══════════════════════════════════════════════════════════════════

def _carrier(kernel: Kernel, block: frozenset[str], plugin: Any) -> Any:
    """把「一块」交给 `§I1 命中` 时的**载体方向**。

    `§I1 hit(d, q)` 的第一参数是 `Direction`（三个插件的 `hit` 都读 `d.rank` / `d.payload`），
    而 `§I2 合并` 产出的是 **payload**，不是方向 ⇒ 需要一个载体。

    ⚠️ `rank` 取块内**最小**秩：`§K6`「不知道不许当没有」⇒ 秩取小 ⇒ 更容易`未展开`
       ⇒ 更容易排「**先看**」= **保守的那一侧**。
    ⚠️ 这**只影响排序**，**永远不影响终止**（`基线§3` 定理 + `§C1`）。
    """
    ids = sorted(block, key=_did_order)
    dirs = [kernel.direction(d) for d in ids]
    return replace(dirs[0], payload=plugin.merge(dirs),
                   rank=min(d.rank for d in dirs))


def order_blocks(kernel: Kernel, plugin: Any, q: tuple[frozenset[str], ...],
                 query: Query) -> tuple[tuple[frozenset[str], ...], tuple[frozenset[str], ...]]:
    """`步骤 T2` —— 把块排成「**先看** / **后看**」（`基线§4` T2）。

        是   ⇒ 先看
        未展开 ⇒ 先看      ← `§K6`：不知道**不许**当没有
        否   ⇒ 后看

    ⚠️ **`否` 只是「排在后面」，不是「不跑」** —— 见 `§3.1`：按块终止要依赖 `§A1` 绿，
       本层**不依赖**它。

    ⇒ 返回 `(先看, 后看)`。块**内部**的顺序 = `Q` 自己的规范化顺序
      （`views.partition_of`：按最小元素排，`§C5` 缺口 2）—— **不是**任何度量顺序。
    """
    first: list[frozenset[str]] = []
    late: list[frozenset[str]] = []
    for block in q:
        if plugin.hit(_carrier(kernel, block, plugin), query) is Tri.NO:
            late.append(block)
        else:
            first.append(block)
    return tuple(first), tuple(late)


# ═══ T3 按顺序跑流程 A ════════════════════════════════════════════════════════

def run_in_order(kernel: Kernel, query: Query,
                 order: tuple[frozenset[str], ...]) -> tuple[Any, tuple[frozenset[str], ...]]:
    """`步骤 T3` —— 按 T2 的顺序**逐块**把该块的方向交给**同一条路**，跑流程 A。

    ★ **「同一条路」就是 `flow.run_query`** —— 流程 A 的入口。`流程§0`：**直接调，不复制**。
      复制它的遍历循环是**违约**，而症状**不是红，是检查报绿**（本仓库栽过那一次）。

    ⚠️ **`flow.run_query` 不接受「只跑哪些块」这个参数。** 加这个参数 = 改流程/内核接口
       = `§T4` 停止条件 ①。⇒ 所以：

    ## ★★ 这个循环**现在是个空转循环**（2026-10-09 实测，如实写在这里）

        `order` 里的块被逐一看过，但**没有任何东西因为「看过了」而改变** ——
        `kernel.query` / `flow.run_query` 只收 `q`，从 `root` 遍历**整棵树**
        （而视图块**横跨树**，不是子树）⇒ 「只扫先看的块」**做不到**（不能传参、不能复制）。

        ⇒ **`T2` 的顺序目前不影响任何东西**（`基线§4 T3 ②` 有三个探针：
          顺序反转 / 换成空元组 / 换成离散划分 ⇒ 输出**逐项相同**）。

    ## ⚠️ 原来这里有一条断言，**已删**（2026-10-09）

        原来是：`assert set(scanned) == set(order)` —— 它**恒真**
        （`scanned` 就是由 `order` 构造出来的）。**恒真式冒充守卫是本仓库最忌的形状**：
        它读起来像「有人守着『不许自立终止依据』」，实际什么都没守。

        ⇒ 删掉，不换成另一条断言 —— 循环体是空的，**没有任何可断言的东西**。
          「本层不自立终止依据」这句话的真实位置是**文档**（`基线§4 T3 ①`），
          而不是一条恒真的断言。

    ⚠️ **本层不自立终止依据**（`基线§4 T3 ①`）：能决定「停」的只有流程 A 自己的 `R3`。
    """
    scanned = tuple(order)                      # ⚠️ 恒等记录 —— 见上面「空转循环」
    return flow.run_query(kernel, query), scanned


# ═══ T4 组装 ══════════════════════════════════════════════════════════════════

#: 一条理由 = **(一个候选方向, 一个项)**，且那个项**在该方向的覆盖里**。
#:
#: ⚠️ 为什么不是一句自然语言：`T3` 判据要**机器可判**（`§C5` 缺口 3）。
#:    一句散文判不了 ⇒ 那条判据只能恒绿。
Reason = tuple[str, str]


def render_reason(reason: Reason) -> str:
    """理由的**打印形态** —— 判定与打印**同源**（照 `multilevel.TITLES` 的「一处定义、两处用」）。"""
    return f"{reason[0]} 覆盖 {reason[1]}"


def explain(candidates: Iterable[str], kernel: Kernel) -> tuple[Reason, ...]:
    """`步骤 T4` —— **结果层面**的解释：每条理由指得到一个**实际结果**。

    ⚠️ 它说的**不是**「内核先看了哪一带」（调度层面）—— 遍历顺序**不许**进输出
       （`基线§3.2`）。顺序按方向 id **字符串序**排，那是**规范化**顺序，不是遍历顺序。
    """
    out: list[Reason] = []
    for did in sorted(candidates):
        items = sorted(kernel.members_of(kernel.direction(did)))
        if items:
            out.append((did, items[0]))
    return tuple(out)


# ═══ 组装：一次检索 ═══════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Retrieval:
    """一次检索的**结果** —— `T1`–`T4` 的产物，也是三条判据读的对象。"""

    query: Query
    candidates: frozenset[str]
    first: tuple[frozenset[str], ...]      # 「先看」的块（**内部调度**，不进输出）
    late: tuple[frozenset[str], ...]       # 「后看」的块
    scanned: tuple[frozenset[str], ...]    # T3 实际跑过的块（`§C1` ① 那条断言守的东西）
    reasons: tuple[Reason, ...]            # T4 的解释
    recognition: Recognition

    def reading(self) -> dict[str, int]:
        """**现算**的读数（`§T0` 第 5 条：会漂的数不许写死）。"""
        n_dirs = sum(len(b) for b in self.first) + sum(len(b) for b in self.late)
        return {
            "方向数": n_dirs,
            "块数": len(self.first) + len(self.late),
            "先看的方向数": sum(len(b) for b in self.first),
            "先看的块数": len(self.first),
            "后看的块数": len(self.late),
            "跑过的块数": len(self.scanned),
        }


def render_reading(r: Retrieval) -> str:
    """**读数** —— 进输出，**不进退出码**（`设计§2.2`）。

    ⚠️ **本层当前的收益是 0**（`基线§13 13.0`）：`T2` 的顺序**无处可达**
       （`基线§4 T3 ②`）⇒ 「先看多少块」**不改变任何东西**（三个探针：逐项相同）。
    ⚠️ 而「跑过 N/N 块」是**构造性恒真**，**不是读数** —— 顺序无处可达 ⇒ 跑的就是全部。

    ⇒ 所以下面印的每一个数都**只是描述**（认识有多粗 / 顺序长什么样），
      **没有一个可以读成收益**。这一点必须印出来 ——
      否则「收益 = 0」与「有收益」在只看数字时**长得一模一样**。
    """
    d = r.reading()
    return (f"认识 |Q| = {d['块数']} 块 / {d['方向数']} 个方向；"
            f"「先看」{d['先看的块数']} 块（{d['先看的方向数']} 个方向）、"
            f"「后看」{d['后看的块数']} 块"
            f"　⚠️ **收益 = 0**（顺序无处可达，基线§4 T3 ②）—— "
            f"先看多少块不改变任何东西；「跑过 {d['跑过的块数']}/{d['块数']} 块」"
            f"是构造性恒真，不是读数")


def retrieve(kernel: Kernel, plugin: Any, need: Mapping[str, Mapping[str, object]], *,
             spec: ViewSpec, recognition: Any = None,
             known: Iterable[str] = BAR_NAMES) -> Retrieval:
    """`步骤 T0`–`步骤 T4` 走一遍。`T5`（展示 + 记录）由 `flow.run_query` 一并完成。

        `need`         `r` —— 按插件分栏的约束集合（外层键 = 插件名）
        `spec`         **当前**结构的外生声明（`U` / `P` / `E`）
        `recognition`  盘上那份认识（`view_persist.from_dict` 读回来的）；`None` = 没有

    本次调用只跑**一个内核**（一个插件）⇒ T2/T3 用的 `q = r[plugin.name]`；
    其余栏**只做分派校验**（这正是 `步骤 T0` 的产物「一组 `q`」）。
    """
    dispatched = dispatch(need, known)
    if plugin.name not in dispatched:
        raise DispatchError(
            f"`r` 里**没有** {plugin.name!r} 这一栏 —— 这个内核没有需求可跑"
            f"（已知栏 {sorted(dispatched)}）")
    query = dispatched[plugin.name]

    rec = current_recognition(spec, recognition)
    first, late = order_blocks(kernel, plugin, rec.q, query)
    flow_a, scanned = run_in_order(kernel, query, first + late)
    candidates = frozenset(flow_a.result.yes)
    return Retrieval(query=query, candidates=candidates, first=first, late=late,
                     scanned=scanned, reasons=explain(candidates, kernel), recognition=rec)


def _did_order(did: str) -> tuple[int, str]:
    """方向 id 的**规范化**顺序：`D12` 排在 `D3` 后面。

    ⚠️ 这只是「同一个集合只有一种写法」（§8.2 可复现性）—— **不是**任何度量顺序，
       更**不是**遍历顺序（`基线§3.2`）。
    """
    head = did.lstrip("D")
    return (int(head), did) if head.isdigit() else (1 << 30, did)
