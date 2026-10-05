"""四条流程的**可运行实现** —— 工作流程 §1–§4。

    流程 A 运行      `run_query()`       收到查询 → 展示候选 → 记录使用
    流程 B 维护      `insert_items()`    插入新项 → 报告四情形 → 断言局部性
    流程 C 构建验收   → `run_checks.py`（已单独实现）
    流程 D 自优化    `run_optimize()`    收集记录 → 更新内生量 → 断言外生未动

**流程之间只通过三个东西耦合**：方向结构、见证、使用记录（工作流程 §0）。
所以这里每个函数只读它该读的那三样，彼此没有共享状态。

---

## 为什么这一步不是「把已有函数包一层」

流程 A / B / D 原来**只存在于工作流程文档的表格里**。把它们写成能跑的东西时，
每一处「照文档实现」的地方都压出了一个真问题：

    A-1  §8.1 说「只有内核知道它**展示**了什么」——
         但内核原来只知道它**遍历**过什么。两者在多层查询下不同。
         ⇒ 内核补了 `show()`，「展示」变成**显式的一步**。

    A-2  流程 A 原来没有入口 —— 一次「查询 + 展示 + 记账」要调用方自己拼。
         ⇒ `run_query()` 把它固定成 R0–R5 的五步，并保证
           「没给反馈」**不产记录**（没看见 ≠ 看见了不好）。

    B-1  §M0–§M5 是**维护**（插入新项）——
         但 `insert()` 原来只接受「`build()` 时已声明的项」。
         ⇒ 补了 `insert(id, item)`。

    B-2  ★ **`insert()` 从来没让结构长大过。** `expand()` 把「分不开」也写成
         永久标记，于是一个方向一旦被判成叶就永远是叶 —— 树只在**第一批**项上
         长过层。实测（36 项语料，方向 A）：

             一次建完 36         39 个方向   最大叶容量 10
             先建  6，维护 30   **7 个方向** 最大叶容量 **31**   ← 退化成平表

         ⇒ 叶不是终态：成员集变了就**重试**。§M2 情形④（涌现）原来不可达。
           改后同一格：47 个方向，最大叶容量 10。

    B-3  `InsertReport.render()` 把「没长出新层」印成「结构未动」——
         而同一份报告里 `changed` 有 5 项（整条锥的归属都 +1 了）。报告自相矛盾。
         ⇒ 两件事分开印。另外「§K2 判分不开」原来被印成「涌现 2」——
           那两个方向一步都没走 —— 也拆出来单独印（见 B-4）。

    B-4  ★ **§K2 的字面是「不建这一层」，而实现是「先建出来再标记失效」。**
         两者产出不同的结构：字面读法下，被判「分不开」的那一层**根本不存在**。
         旧实现留下**一对空壳子方向**，于是「空权威层比例」这个度量里混进了
         「试过几次」的痕迹，不再是纯粹的「形状好不好用」。
         ⇒ `expand()` 改成：先用**探针**算归属（不建方向、不写账本），
           两侧都非空才真的建；一侧为空则只追加一条 `unsplittable`（挂在**父**上）。
         ⇒ 代价没消失，只是**换了地方**：那一层不建 ⇒ 那些项只能留在这个叶里
           ⇒ **叶容量变大、分辨率下降**。度量也跟着搬（`§K2 判空` + 叶容量）。

    D-1  §F0–§F4 的实现早就在 `core/selfopt.py` 里了 ——
         这一步只是把它按流程串起来，并**第一次在真实使用记录上跑**。

    D-2  `FlowD.内生量确实变了` **退化恒真**：`optimize()` 无条件把
         `penalty_scale` 设成记录条数，所以 0 条记录时 `1.0 → 0.0` 也算「变了」。
         那是「记录数」在动，不是学到了东西。
         ⇒ 加 `学到东西`（至少一个方向拿到 `expand_priority`），
           并让它进退出码 —— **空转不许与通过长得一样**。

⇒ 六个「文档写了、代码没接上」+ 一个「断言退化恒真」。这就是要真跑一遍的理由。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .core import selfopt
from .core.direction import (
    EVENT_BORN,
    EVENT_UNSPLITTABLE,
    EVENT_WITNESS_UPDATED,
)
from .core.kernel import _did_order

# ═══ 流程 A · 运行（工作流程 §1） ═════════════════════════════════════════════


@dataclass(frozen=True)
class FlowA:
    """流程 A 的产物。"""

    query: Any
    result: Any                       # QueryResult（R3 的三去向）
    shown: tuple[str, ...]            # ★ 展示顺序 —— **就是曝光位次**
    records: tuple[Any, ...]          # 本次产生的使用记录（没反馈的方向不产记录）

    @property
    def 没给反馈(self) -> int:
        return len(self.shown) - len(self.records)

    @property
    def 空转(self) -> bool:
        """一次候选都没摆出来 ⇒ 这一步没有产出（**不许与通过长得一样**）。"""
        return not self.shown

    def render(self) -> str:
        r = self.result
        return (
            f"查询「{self.query.label or '—'}」→ {r.render()}\n"
            f"  展示 {len(self.shown)} 个候选"
            f"（位次 1..{len(self.shown)}）｜产生记录 {len(self.records)} 条"
            f"｜没给反馈 {self.没给反馈} 个（**不产记录**：没看见 ≠ 看见了不好）"
        )


def synthetic_feedback(kernel: Any, query: Any, result: Any) -> dict[str, float]:
    """**合成**反馈 —— 真实系统里这一格来自使用者。

    为什么可以合成：`ideal` 本身就是**外生声明的 ground truth**（§I2 的纪律），
    所以「这个方向收下的项里有没有该命中的」是**算得出来**的。

    判据：方向成员与 `ideal` 有交 ⇒ `+1`（有用）；否则 `−1`（白摆）。

    ⚠️ 两者都**不是**「没看见」。「没看见」用 `None` ⇒ §I6 返回 `None` ⇒ **不产记录**
       （§K6 / `B6`：无信号 ≠ 负信号）。

    ⚠️ 这只是**把 A → D 串起来**用的，不代表真实使用分布。
       真实分布下倾向加权才真正起作用（见 `C7-流程跑通.md` §4）。
    """
    fb: dict[str, float] = {}
    for did in result.yes:
        d = kernel.direction(did)
        fb[did] = 1.0 if (kernel.members_of(d) & query.ideal) else -1.0
    return fb


def run_query(kernel: Any, query: Any, *,
              feedback: dict[str, float] | None = None,
              auto_feedback: bool = True) -> FlowA:
    """流程 A。R0–R4 由内核做；**R5 的关键是「展示」与「遍历」分开**。

    R5 原来没有入口 —— 内核在遍历时顺手记了「最后一层前沿」当展示。
    这里把它拆成显式两步：

        R4  内核遍历，得到候选（说「是」的方向）
        R5a `kernel.show(候选)` —— **展示**：内核记下摆了哪些、什么次序
        R5b 逐条 `record_usage` —— 倾向权重由内核按**展示位次**填

    `feedback` 给的是「这次交互有没有用」；**缺席 = 没看见 = 不产记录**。
    """
    result = kernel.query(query)                      # R0–R4
    shown = kernel.show(result.yes)                   # R5a ★ 展示
    fb = (dict(feedback) if feedback is not None
          else (synthetic_feedback(kernel, query, result) if auto_feedback else {}))
    records = []
    for did in shown:                                 # R5b
        rec = kernel.record_usage(kernel.direction(did), {"outcome": fb.get(did)})
        if rec is not None:
            records.append(rec)
    return FlowA(query=query, result=result, shown=shown, records=tuple(records))


# ═══ 流程 B · 维护（工作流程 §2） ═════════════════════════════════════════════


@dataclass(frozen=True)
class InsertReport:
    """一次插入的变动报告 —— §M2 的四种情形各占一格。

    ⚠️ 这里只有**三**格（情形①②④），不是四格。

    §M2 情形③「标记失效（不删）」在内核里**当前不可达** —— 它需要一条**删除/收缩
    流程**（把某个方向作废但保留账本记录），而那条流程还没建。所以这里**不放这个槽**：
    一个永远为空的字段会让读报告的人以为「情形③ 查过了」。

    这不是遗漏，是**刻意留空** —— 与 `core/kernel.py` 里「`EVENT_INVALIDATED`
    故意不 import」是同一件事的两面。删掉这个槽的同时，`render()` 里那句
    「标记失效 …（不删）」也一并删掉，免得读者以为它曾经亮过。
    """

    item: str
    cone: tuple[str, ...]                       # M1 支撑锥 = 插入路径
    born: tuple[str, ...] = ()                  # 情形④ 涌现 —— **真的长出来了**
    unsplittable: tuple[str, ...] = ()          # §K2 判「这一层不建」（**判定**，不是失效）
    witness_updated: tuple[str, ...] = ()       # 情形② 见证更新
    changed: tuple[str, ...] = ()               # 存在或归属发生变化的全部方向
    out_of_cone: tuple[str, ...] = ()           # M3：落在 Cone(x) 外的变动 ⇒ 必须为空
    events: int = 0                             # ★ 本次追加的账本事件**总条数**
    #: ⚠️ `events` 和上面几个字段**不是一回事**：那些是按 did 归类后的**去重视图**。
    #: 账本核对必须用 `events` —— 用去重视图去比会差出「重叠数」那么多条。

    def render(self) -> str:
        bits = [f"插 {self.item}｜锥 {len(self.cone)} 层"]
        if self.born:
            bits.append(f"涌现 {len(self.born)}：{list(self.born[:3])}")
        if self.witness_updated:
            bits.append(f"见证更新 {len(self.witness_updated)}：{list(self.witness_updated[:3])}")
        if self.unsplittable:
            # ⚠️ 这不是「涌现」，也**不是「失效」** —— §K2 判「这一层不建」时
            #    那一层**根本没建出来**，所以没有东西可失效。它是一条**判定**。
            bits.append(f"§K2 判空（这一层不建）{len(self.unsplittable)}"
                        f"：{list(self.unsplittable[:2])}")
        if not self.born and not self.witness_updated:
            # ⚠️ 「没长出新层」**不等于**「结构未动」。
            #
            # 原来这里印的是「结构未动」—— 而同一份报告里 `changed` 有 5 项
            # （整条锥的归属都 +1 了）。**报告自己打自己**。
            bits.append(f"没长出新层（归属沿锥更新 {len(self.changed)} 个方向）")
        if self.out_of_cone:
            bits.append(f"‼ 锥外变动 {list(self.out_of_cone[:3])}（M3 违规）")
        return "｜".join(bits)


def insert_items(kernel: Any, items: Iterable[tuple[str, Any]]) -> list[InsertReport]:
    """流程 B。M0–M5，**逐项插入、每次前后比对**。

    入参是 `(item_id, item)` 对；`item` 非空 ⇒ 这是**新项**（不在 `build()` 的全集里）。

    M3 的「变动 ⊆ `Cone(x)`」如果只查**终态**就什么都没查 —— 终态本来就自洽。
    所以这里逐项比对，与 `B8` 是同一个形状（那条检查吃过「只查终态」的亏）。
    """
    reports: list[InsertReport] = []
    for iid, item in items:
        before_dirs = {d.did for d in kernel.all_directions()}
        before_mem = {d.did: kernel.members_of(d) for d in kernel.all_directions()}
        before_events = len(kernel.ledger)

        path = kernel.insert(iid, item)                     # M1 / M2

        new_events = list(kernel.ledger)[before_events:]
        changed: set[str] = set()
        for d in kernel.all_directions():
            if d.did not in before_dirs:
                changed.add(d.did)                          # 新出现的
            elif kernel.members_of(d) != before_mem.get(d.did):
                changed.add(d.did)                          # 归属变了
        cone = set(path)
        # 允许：在路径上；或**父在路径上**（展开是沿路径发生的）
        out = sorted(
            did for did in changed
            if did not in cone
            and (kernel.direction(did).parent is None
                 or kernel.direction(did).parent not in cone)
        )
        # ★ 「建出来」和「真的长出来了」是两件事；「判空」是第三件。
        #   §K2 判「不建」时**一个方向都不建**，所以 did 不重叠 —— 但按种类分开
        #   归类仍然必要（一个 did 可能先判空、后来长出来）。
        reports.append(InsertReport(
            item=iid,
            cone=tuple(path),
            born=tuple(sorted({e.did for e in new_events if e.kind == EVENT_BORN},
                              key=_did_order)),
            unsplittable=tuple(sorted({e.did for e in new_events
                                       if e.kind == EVENT_UNSPLITTABLE}, key=_did_order)),
            witness_updated=tuple(e.did for e in new_events
                                  if e.kind == EVENT_WITNESS_UPDATED),
            changed=tuple(sorted(changed)),
            out_of_cone=tuple(out),
            events=len(new_events),
        ))
    return reports


# ═══ 流程 D · 自优化（工作流程 §4） ═══════════════════════════════════════════


@dataclass(frozen=True)
class FlowD:
    """流程 D 的产物。"""

    before: tuple = ()            # 内生量指纹（改之前）
    after: tuple = ()             # 内生量指纹（改之后）
    info: dict = field(default_factory=dict)
    params: Any = None            # 更新后的参数表

    @property
    def 内生量确实变了(self) -> bool:
        return self.before != self.after

    @property
    def 学到东西(self) -> bool:
        """**有证据的**变化 —— 至少一个方向拿到了 `expand_priority`。

        ⚠️ 判「这一步有没有产出」**不能**用 `内生量确实变了`：它**退化恒真**。

        `optimize()` 无条件把 `penalty_scale` 设成记录条数，所以 0 条记录时
        `1.0 → 0.0` 也算「变了」。那是「记录数」这个数在动，
        **不是学到了任何东西** —— 空转与通过长得一模一样。

        实测：`python -m ldv.cli d`（不先跑 A ⇒ 没有记录）
             旧输出「内生量：2 项 → 2 项（**变了**）」＋退出码 0。
        """
        return self.info.get("更新到的方向数", 0) > 0

    def render(self) -> str:
        i = self.info
        if not self.学到东西:
            verdict = "⚠ **空转**：没有带权记录 ⇒ 内生量只动了常数项，没学到东西"
        elif self.内生量确实变了:
            verdict = "内生量确实变了（**有证据**）"
        else:
            verdict = "内生量没变 —— 该查为什么"
        return (
            f"带权记录 {i.get('带权记录', '?')} 条｜被拦下（缺倾向）{i.get('被拦下（缺倾向）', '?')} 条"
            f"｜更新到 {i.get('更新到的方向数', '?')} 个方向\n"
            f"  内生量：{len(self.before)} 项 → {len(self.after)} 项（{verdict}）\n"
            f"  外生项：{i.get('外生项指纹')}（**逐项未变**，F3 已断言）"
        )


def run_optimize(kernel: Any, params: selfopt.Params) -> FlowD:
    """流程 D。F0–F4 —— 实现全在 `core/selfopt.py`，这里按流程把它串起来。

    `optimize()` 自带 F3（外生项未动）与 F4（无全局评分）两条断言，红了就抛。
    所以「跑完了」本身就是这两条护栏成立的证据。
    """
    before = params.fingerprint_endogenous()
    after, info = selfopt.optimize(kernel, params)          # F0–F4
    return FlowD(before=before, after=after.fingerprint_endogenous(),
                 info=info, params=after)


# ═══ 装配：把 A → B → D 串成一条链 ═══════════════════════════════════════════


def make_params(direction_name: str, outermost_intent: str) -> selfopt.Params:
    """外生项**由人声明**（§8.2）—— 这里把两个必填的外生量写死成人给的值。"""
    return selfopt.Params(
        exogenous={"direction_functions": [direction_name],
                   "outermost_intent": outermost_intent},
        endogenous={"expand_priority": {}, "penalty_scale": 1.0},
    )


def render_chain(a: FlowA | None, b: Sequence[InsertReport] | None,
                 d: FlowD | None) -> str:
    """把三个流程的输出拼成一份可读追踪。"""
    lines: list[str] = []
    if a is not None:
        lines.append("── 流程 A · 运行 ──")
        lines.append("  " + a.render().replace("\n", "\n  "))
    if b is not None:
        lines.append("── 流程 B · 维护 ──")
        grew = [r for r in b if r.born]
        touched = sum(len(r.changed) for r in b)
        # ⚠️ 这里原来写 `// 2` —— 因为旧实现一次判空**建一对**方向，报告里记两个 did。
        #    字面 §K2 之后判空**不建方向**，事件是**一条**（挂在父上），所以不再除 2。
        judged = sum(len(r.unsplittable) for r in b)
        lines.append(f"  插入 {len(b)} 项：长出新层 {len(grew)} 项；"
                     f"归属更新累计 {touched} 处（**都在各自的锥上**）"
                     + (f"；§K2 判「这一层不建」{judged} 次" if judged else ""))
        for r in b[:6]:
            lines.append("  · " + r.render())
        if len(b) > 6:
            lines.append(f"  · …（还有 {len(b) - 6} 项）")
        viol = [r for r in b if r.out_of_cone]
        lines.append(f"  M3 断言：锥外变动 {len(viol)} 处"
                     + ("（**必须为 0**）" if not viol else " ‼ 违规"))
    if d is not None:
        lines.append("── 流程 D · 自优化 ──")
        lines.append("  " + d.render().replace("\n", "\n  "))
    return "\n".join(lines)
