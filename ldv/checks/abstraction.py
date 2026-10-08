"""流程 E · 抽象层 —— `§A1` / `§A2` / `§A3`（`docs/分层方向视图-抽象层.md` §9）。

    python -m ldv.run_checks            # 判据在 `(视图)` 那一组里

算法在 `core/views.py`（纯的）。本模块只做两件事：**把结构装成视图**、
**把三条判据变成会红的断言**。

---

## 三条判据各守什么

    §A1 健全性   `具体化(v) ⊇ ∪{v 覆盖的原始项}`      —— 视图**不许丢东西**
    §A2 稳定     `B₁ ⊆ E⁻¹(B₂)` 或 `B₁ ∩ E⁻¹(B₂) = φ` —— 「稳定」是对 `E` 说的
    §A3 最粗     不存在更粗的稳定划分                  —— `Q` 就是那个**唯一**的解

⚠️ **`§A3` 在谓词上包含 `§A2`**（不稳定的 `Q` 不可能是那个唯一解 ⇒ `§A3` 也红）。
   两条都留着，理由与各自的覆盖面写在 `a3_coarsest` 的 docstring 里 ——
   **不是**「多一层保险」，是「`§A2` 无条件判、`§A3` 会因为枚举上限跳过」。
   设计稿 §9 那张表**没有**这一条（它只写了「存在更粗的稳定划分 ⇒ 红」），
   是**补出来的**：照字面实现会让一个不稳定的 `Q` 报绿。

`§A1` 是 **§K8 那条不对称契约**（假阴禁止 / 假阳计量）的视图侧对应物 ——
Cousot & Cousot 1977 §6 的 `Co ⊑ C_A` **必须**、`C_A ⊑ Co` **不必须**，
逐字就是「视图不许丢东西、但可以更粗」。见设计稿 §2。

---

## ★ 视图的派生 payload 用**已有的** `§I2 合并` —— 不新增接口方法

`§A1` 里的「具体化」不是「把成员的覆盖并起来」（那样它就**只是 `B16` 换个写法**，
恒同真、不提供独立信息）。它是：

    具体化(v)  =  覆盖( 合并({ d : d ∈ v }) )        ← 视图**自己**答得到的原始项
    v 覆盖的原始项 =  ∪{ members(d) : d ∈ v }        ← 下层**声明**它收着的项

    §A1:  具体化(v)  ⊇  ∪{ members(d) }             「多了合法，少了即违规」

「合并」正是 `§I2` 那一个方法（`插件.合并(ds)`），三个插件都实现过，
而且它的契约本来就写着**必须覆盖所有输入**（`plugins/keyset.py::merge` 的
docstring 里那条证明）。⇒ 这一步**不新增接口方法**，也不用重验 `§I1`–`§I7`。

⚠️ **所以 `§A1` 与 `B16` 不是同一个东西，虽然它们长得很像**：

    `B16`   逐**方向**：`members(d) ⊆ 覆盖(d.payload)`
    `§A1`   逐**视图**：`∪members(d) ⊆ 覆盖(合并(整块的 payload))`

   `B16` 全绿**推不出** `§A1` 绿：合并是**另一个函数**（`keyset` 上是 `req` 取交、
   `forb` 取交），它可能把覆盖**收窄**到装不下某个成员。实测：见
   `run_tests.test_view_a1_not_implied_by_b16`（构造一个「合并会丢项」的插件，
   `B16` 绿而 `§A1` 红）。

---

## 外生项从哪来：**没声明就跳过，不许猜**

`P` 与 `E` 必须外生（设计稿 §3 两条边界 / `§K9` / `B14`）。所以：

    人把它们写在 `ldv/checks/view_spec.json` 里（带**语料指纹**）
    没写 / 指纹对不上 / 写的是别的方向 ⇒ 三条判据**全部报「跳过」**并印原因

⚠️ **「跳过」不是「通过」**。这正是设计稿 §10 停止条件第 1 条：
   「初始划分 `P` 或关系 `E` 没声明 —— 它们必须外生。猜一个就是替人做 `§K9` 的决定。」

⚠️ 而**声明了却坏了**（JSON 写错、`P` 不是划分、方向名不在内核里）是**另一回事**：
   那是**报错**，不是跳过。两条路的区别与 `_fixtures.find_corpus` 里
   「显式指定却找不到 ⇒ 报错」完全同源。

---

## 已知答案的对照组：**合成图上先跑三态，再上真语料**

设计稿 §9 的 ⚠️ 逐字：`§A1`–`§A3` 都必须先在**已知答案的合成图上**跑一遍
（三态：过 / 红 / 跳过各一例），再上真语料。

⇒ `known_answer_controls()` 就是那一批，`run_tests.test_view_known_answers` 跑它。
   理由是本项目的老教训：**「空转与通过长得一模一样」**，
   而合成图上的已知答案是对照组里**最便宜**的那一档。

---

## 本趟**没有**做的三条（写在明处，不留给读者猜）

    `§A4` 类别不许说错（distributive / algebraic / holistic）  —— 见设计稿 §5
    `§A5` 账逐条可指认                                        —— 见设计稿 §6
    `§A6` 落盘-读回                                           —— 见设计稿 §9

它们各自要的前置件还没有：`§A4` 要「读数的类别声明」这个外生字段，
`§A5` 要先把「视图答不出的那些」与内核账本上的 `stayed` / `out_of_scope` 对齐，
`§A6` 要 `core/persist.py` 的视图侧对应物。
**没做就是没做** —— 这三条现在**不在** `VIEW_CODES` 里，
所以「套件全绿」这句话**不覆盖它们**（`test_injections` 的注册表自检守这件事）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Sequence

from ..core.tri import Tri
from ..core.views import (
    ViewSpec,
    coarser_stable_exists,
    coarsest_stable_refinement,
    partition_of,
    refines,
    stable,
)
from ._framework import Report

#: 视图侧判据的编号 —— 与 `run_checks.PLUGIN_CODES` / `KERNEL_CODES` **并列**，
#: 不是它们的一部分（视图层与插件无关，见模块开头）。
#: ⚠️ **只列**已经实现了的。没实现的不许写进来 —— 写进来就等于声称
#:    「套件全绿」覆盖了它，而它根本没跑（注册表自检会红）。
VIEW_CODES = ("A1", "A2", "A3")

#: 外生声明放**代码旁边**，与 `cover_leak_baseline.json` 同一个理由：
#: 它要跟着代码走、进版本库，换了语料或换了方向就该一起改。
VIEW_SPEC_PATH = Path(__file__).resolve().parent / "view_spec.json"


# --- 视图对象 -----------------------------------------------------------------

@dataclass(frozen=True)
class View:
    """一张视图 —— `Q` 的一块 + 它**答得到的原始项**。

        `block`           这块覆盖哪些**方向**（`Q` 的一块）
        `payload`         派生 payload = `插件.合并({那些方向})`（§I2，**不新增方法**）
        `concretization`  **具体化** = `覆盖(payload)` —— 这张视图答得到的原始项
        `covered`         它**声明**收着的原始项 = `∪ members(d)`
        `category`        §5 的三类之一（`§A4` 用；**本趟只留字段不判**）
        `ledger`          §6 的账（`§A5` 用；**本趟只留字段不判**）

    ⚠️ `concretization` 与 `covered` **必须分开存**，不许用一个算另一个：
       `§A1` 判的就是这两者之间的包含关系。若 `concretization` 是
       `covered` 现场算出来的，那条判据**恒真** —— 它就不再是判据了。
    """

    vid: str
    block: frozenset[str]
    payload: Any = None
    concretization: frozenset[str] = frozenset()
    covered: frozenset[str] = frozenset()
    category: str = ""
    ledger: tuple[str, ...] = field(default_factory=tuple)

    @property
    def n_dirs(self) -> int:
        return len(self.block)


@dataclass(frozen=True)
class ViewSet:
    """一组视图 + 它由来的那个 spec（判据要拿 spec 复算稳定性）。"""

    spec: ViewSpec
    q: tuple[frozenset[str], ...]
    views: tuple[View, ...]

    def render(self) -> str:
        return (f"{len(self.views)} 张视图（{len(self.spec.universe)} 个方向，"
                f"{len(self.spec.partition)} 个初始块，{len(self.spec.relation)} 条边）")


# --- 装配 ---------------------------------------------------------------------

def build_views(kernel: Any, spec: ViewSpec, cover: Callable[[Any], frozenset[str]],
                plugin: Any) -> ViewSet:
    """把内核里的结构按 `spec` 装成视图集合。

    `Q = 最粗稳定细化(spec)`（**唯一**，见 `core/views.py`），每块一张视图。

    ⚠️ **`合并` 可能抛异常**（payload 形状由插件产出）。抛了就**不让它静默**：
       记成一张 `concretization` 算不出来的视图，`§A1` 会把它报成违规。
       悄悄跳过会让「合并坏了」与「这块没有成员」长得一模一样。
    """
    q = coarsest_stable_refinement(spec)
    views: list[View] = []
    for i, block in enumerate(q):
        dirs = [kernel.direction(d) for d in sorted(block)]
        covered: set[str] = set()
        for d in dirs:
            covered |= set(kernel.members_of(d))
        try:
            payload = plugin.merge(dirs)
            conc = frozenset(cover(payload))
            bad = ""
        except Exception as exc:  # noqa: BLE001 - payload 由插件产出
            payload, conc = None, frozenset()
            bad = f"合并/覆盖算不出来：{type(exc).__name__}: {exc}"
        v = View(vid=f"V{i}", block=block, payload=payload,
                 concretization=conc, covered=frozenset(covered))
        views.append(v if not bad else replace(v, category=bad))
    return ViewSet(spec=spec, q=q, views=tuple(views))


# --- 外生 spec 的加载 ----------------------------------------------------------

def load_spec_file() -> dict[str, Any]:
    if not VIEW_SPEC_PATH.is_file():
        return {}
    return json.loads(VIEW_SPEC_PATH.read_text(encoding="utf-8"))


def spec_for(doc: dict[str, Any], which: str, corpus: dict[str, int]) -> tuple[ViewSpec | None, str]:
    """把外生声明解成一个 `ViewSpec`。返回 `(spec, 说明)`；`spec is None` ⇒ 报**跳过**。

    三种「跳过」各有各的话，因为它们要人做的事**不一样**：

        文件不在                 ⇒ 「还没人声明」
        指纹对不上               ⇒ 「声明是**别的语料**上写的」
        `方向` 不是这一条        ⇒ 「声明是**别的方向**的」

    ⚠️ **声明了却坏了 ⇒ 抛异常**（不是跳过）。`P` 不是划分、`universe` 里有
       内核不认识的方向 id、JSON 语法错 —— 这些都是**写错了**，
       与「还没写」是两件事。混成一条「跳过」会让写错的声明**看起来像没写**，
       于是没人去修它。
    """
    if not doc:
        return None, (f"外生项**未声明**（`{VIEW_SPEC_PATH.name}` 不在）—— "
                      f"`P` / `E` 必须外生（设计稿 §3 / §K9），"
                      f"猜一个就是替人做决定（§10 停止条件 1）")
    frozen = doc.get("语料") or {}
    if frozen != corpus:
        return None, (f"外生声明是在**另一份语料**上写的（{frozen} vs 现在 {corpus}）"
                      f"⇒ 本语料上**判不了**（跳过 ≠ 通过）")
    if doc.get("方向") != which:
        return None, (f"外生声明写的是**另一条方向**（{doc.get('方向')!r} vs {which!r}）"
                      f"⇒ 本方向**判不了**（跳过 ≠ 通过）")
    universe = tuple(doc.get("universe") or ())
    partition = tuple(frozenset(b) for b in (doc.get("partition") or ()))
    relation = frozenset(tuple(e) for e in (doc.get("relation") or ()))
    # ⚠️ `ViewSpec.__post_init__` 会抛 —— **故意不让它被吞**。见上面那段。
    spec = ViewSpec(universe=universe, partition=partition, relation=relation)
    return spec, f"外生声明已读到（{VIEW_SPEC_PATH.name}，语料 {frozen}）"


# --- §A1 ----------------------------------------------------------------------

def a1_soundness(vs: ViewSet, rep: Report) -> None:
    """**具体化不许比它声明收着的少** —— `§K8` 的视图侧，`§2` 那条不对称。

        具体化(v)  ⊇  ∪{ members(d) : d ∈ v }

    `⊇` **不是** `=`：多了合法（视图可以更粗，那是**假阳**，计量不进退出码），
    少了即违规（那是**假阴**，§K8 唯一禁止的那一侧）。

    ## 为什么这条必须能红（它的注入是什么）

    在**规范构造**（`build_views` 用 `插件.合并` 派生 payload）下它是绿的，
    而绿的**理由**可能是「合并真的覆盖得住」，也可能是「这条判据根本没在判」
    —— **两者长得一模一样**。所以注入直接**掐掉一项**：
    把某张视图的 `concretization` 删掉一个成员，看它红不红
    （`test_injections.inj_a1`）。**红的形状就是这条判据的全部内容。**

    ⚠️ 视图集合为空 ⇒ **跳过**，不是过（设计稿 §9）。
    """
    if not vs.views:
        rep.add("A1", "视图健全性：具体化 ⊇ ∪成员", Tri.UNEXPANDED,
                "视图集合为空 —— 判不了，不是通过")
        return
    bad: list[str] = []
    checked = 0
    for v in vs.views:
        checked += len(v.covered)
        miss = sorted(v.covered - v.concretization)
        if miss:
            why = v.category or ""
            bad.append(f"{v.vid}（{v.n_dirs} 个方向）的具体化少了 {len(miss)} 项"
                       f"{miss[:3]}" + (f"；{why}" if why else ""))
    rep.add("A1", "视图健全性：具体化(v) ⊇ ∪{v 覆盖的原始项}（§K8 的视图侧）",
            Tri.NO if bad else Tri.YES,
            (f"{len(bad)} 张视图丢了东西：{bad[:2]}") if bad
            else f"{len(vs.views)} 张视图、{checked} 个成员项全部落在各自的具体化里"
                 f"（多出来的部分算假阳，只计量不进退出码）")


# --- §A2 ----------------------------------------------------------------------

def a2_stable(spec: ViewSpec, q: Sequence[frozenset[str]], rep: Report) -> None:
    """**稳定** —— 逐字按 Paige–Tarjan 的定义（设计稿 §3）：

        对每对块 `B₁, B₂`：`B₁ ⊆ E⁻¹(B₂)` 或 `B₁ ∩ E⁻¹(B₂) = φ`

    ⇒ 「存在一块**跨**在 `E⁻¹(B₂)` 内外」就是红。反例里印出**是哪两个元素**
      （一个走得到、一个走不到），因为「`B₁` 不稳定」这句话**指不出**该改哪里。

    ⚠️ **`E` 为空 ⇒ 跳过。** 空关系下**任何**划分都稳定 —— 这条判据在那时
       **没有内容**。判据的适用范围由「它在这条方向上有没有内容」决定，
       不由偏好决定（`B17`(b) 在 `reach` 上降级是同一条）。
    """
    if not spec.relation:
        rep.add("A2", "视图稳定：B₁ ⊆ E⁻¹(B₂) 或 B₁ ∩ E⁻¹(B₂) = φ", Tri.UNEXPANDED,
                "外生关系 `E` 为空 ⇒ 任何划分都稳定 ⇒ 这条判据**没有内容**（跳过 ≠ 通过）")
        return
    ok, bad = stable(spec, q)
    rep.add("A2", "视图稳定：对每对块，要么整块走得到、要么整块走不到",
            Tri.YES if ok else Tri.NO,
            f"{len(q)} 块全部稳定（{len(spec.relation)} 条外生边）" if ok
            else f"{len(bad)} 对块不稳定：{bad[:2]}")


# --- §A3 ----------------------------------------------------------------------

def a3_coarsest(spec: ViewSpec, q: Sequence[frozenset[str]], rep: Report) -> None:
    """**最粗** —— 「不存在更粗的稳定划分」。

    oracle 是 `core/views.coarser_stable_exists`：**暴力枚举** `Q` 的粗化
    （只在同一个 `P` 块内部合并），逐个验稳定性。它与
    `coarsest_stable_refinement` **不共用一行代码** —— 用「再算一遍比一比」
    当 oracle 会与被判对象**共享盲点**（两边同时错、判据永远绿，
    `false-green` 形状 3）。

    ⚠️ **撞上枚举上限 ⇒ 跳过**，不是过。`Bell(k)` 长得快，而「搜不完」
       与「搜完了没有」在只看布尔值时**长得一模一样** —— 所以
       `coarser_stable_exists` 返回的是三态（`None` = 没搜完）。

    ⚠️ **`Q` 不是 `P` 的细化**也在这里报 —— 设计稿 §3 边界 ① 说视图只会
       比初始划分**更细**。少了这一条，一个「把两个初始块并起来」的 `Q`
       照样可以稳定，于是 `§A2` 绿而它根本不是 `P` 的细化。

    ⚠️ **`Q` 自己不稳定 ⇒ 也红。** 这一条是**补出来的**，设计稿 §9 那张表
       没写：`§A3` 的声称是「`Q` 就是那个**唯一**的解」，而不稳定的划分
       **不可能**是那个解 ⇒ 声称是**假的**，该红。
       （早先的写法只查「有没有更粗的稳定划分」，于是喂一个不稳定的 `Q`
         会**报绿** —— 实测：`Q = P` 时唯一的粗化是恒等，被跳过，结论
         「没有更粗的稳定划分」⇒ 过。**一个不稳的划分被报成「已是最粗」**，
         这正是本项目最忌的那种假绿。）

    ## ★ 与 `§A2` 的**重叠**，写在这里不藏着

    按谓词，`§A3` **包含** `§A2`（不稳定 ⇒ 红）。两条都留着，理由**不是**「多一层保险」：

        `§A2`  **无条件**判稳定（只要 `E` 非空）—— 它永远不会因为别的原因跳过
        `§A3`  会因为**枚举上限**跳过（`Bell(k)` 长得快）⇒ 它可能**判不了**

    ⇒ 「`§A2` 绿」这句话的覆盖面**严格宽于**「`§A3` 绿」。
      而两条的**反例**也不同：`§A2` 指得出**哪一对块、哪两个元素**；
      `§A3` 分开报三种红（不是 `P` 的细化 / 未到不动点 / `P` 选粗了），
      最后一种对应**停止条件 3**（`P` 是外生的，只能由人改）。
    """
    if not spec.relation:
        rep.add("A3", "视图最粗：不存在更粗的稳定划分", Tri.UNEXPANDED,
                "外生关系 `E` 为空 ⇒ 稳定是空条件 ⇒ 这条判据**没有内容**（跳过 ≠ 通过）")
        return
    if not refines(spec, q):
        rep.add("A3", "视图最粗：不存在更粗的稳定划分", Tri.NO,
                f"`Q` **不是** `P` 的细化：有块跨出了初始块"
                f"（{len(q)} 块 / {len(spec.partition)} 个初始块）—— 设计稿 §3 边界 ①")
        return
    ok, bad = stable(spec, q)
    if not ok:
        rep.add("A3", "视图最粗：不存在更粗的稳定划分", Tri.NO,
                f"`Q` 自己就**不稳定**（{len(bad)} 对块）⇒ 它不可能是那个唯一解；"
                f"反例见 `§A2`：{bad[:1]}")
        return
    has, why, seen = coarser_stable_exists(spec, q)
    if has is None:
        rep.add("A3", "视图最粗：不存在更粗的稳定划分", Tri.UNEXPANDED,
                f"{why} —— 判不了，不是通过")
        return
    rep.add("A3", "视图最粗：不存在更粗的稳定划分（`Q` 就是那个**唯一**的解）",
            Tri.NO if has else Tri.YES,
            f"存在更粗的稳定划分：{why} —— `P` 本身选粗了（停止条件 3：`P` 外生，"
            f"只能由人改）" if has
            else f"{len(q)} 块已是最粗（枚举了 {seen} 个粗化，没有一个稳定）")


# --- 度量（不进退出码） --------------------------------------------------------

def view_profile(vs: ViewSet) -> dict[str, Any]:
    """读数。**进输出不进退出码**（设计稿 §6 的 `§A6` 那一行说得很清楚：
    账的条数、视图的块数是**度量**）。

    报三个数，缺一个都看不出来：

        `方向数 / 视图数`    压下去多少 —— 「什么都没压」在这里看得见
        `最大块`             最大的一张视图装了几个方向 —— **代价**信号
        `具体化/成员`        假阳有多少 —— 视图答得出的比它声明收着的多多少

    ⚠️ **「视图数 == 方向数」不是红，是读数**（设计稿 §4 E3）。
       那说明这一层**没有可合并的东西** —— 是一个**结论**（该层的视图 = 该层本身），
       不是失败。把它做成红就会出现一条**常驻的红**，
       而「一条常驻的红等于没人再看红」。
    """
    if not vs.views:
        return {"方向数": len(vs.spec.universe), "视图数": 0, "最大块": 0,
                "具体化": 0, "成员": 0, "没压下去": False}
    sizes = [v.n_dirs for v in vs.views]
    conc = sum(len(v.concretization) for v in vs.views)
    cov = sum(len(v.covered) for v in vs.views)
    return {
        "方向数": len(vs.spec.universe),
        "视图数": len(vs.views),
        "最大块": max(sizes),
        "具体化": conc,
        "成员": cov,
        "没压下去": len(vs.views) == len(vs.spec.universe),
    }


def render_views(vs: ViewSet | None, prof: dict[str, Any], which: str = "") -> str:
    head = f"视图（{which}）" if which else "视图"
    if vs is None or not vs.views:
        return f"{head}：没有视图（未展开，**不是**通过）"
    return (f"{head}：{prof['方向数']} 个方向 ⇒ {prof['视图数']} 张视图"
            f"（最大一块 {prof['最大块']} 个方向）"
            f"｜具体化 {prof['具体化']} 项 vs 声明收着 {prof['成员']} 项"
            + ("｜⚠️ **一个方向都没压下去**（该层的视图 = 该层本身，"
               "这是**结论**不是失败 —— §4 E3）" if prof["没压下去"] else "")
            + "（度量，不进退出码）")


# --- 已知答案的对照组（合成图，三态各一例） ------------------------------------

def known_answer_specs() -> list[dict[str, Any]]:
    """合成图上的**已知答案** —— 设计稿 §9 要求的「先跑三态，再上真语料」。

    每一条都写清**答案是什么**（`want`），因为「跑一遍看看」不构成对照：

        过      `P = {{a,b},{c,d}}`、`E = {(a,c)}` ⇒ `Q = {{a},{b},{c,d}}`
                —— 手推得到，而且 `stable` 与 `coarsest` 都成立
        红·不粗  同一个 spec，喂**离散** `Q` ⇒ 稳定但**不是最粗** ⇒ `§A3` 红
        红·不稳  同一个 spec，喂 `P` 自己 ⇒ `{a,b}` 跨在 `E⁻¹({c,d})` 内外 ⇒ `§A2` 红
        跳过    `E = ∅` ⇒ 两条判据都**没有内容** ⇒ 跳过（不是过）

    ⚠️ 「离散划分**总是**稳定」是个小定理：单元素块 `{x}` 对任何 `E⁻¹(B)`，
       要么整个在里面（`x` 在里面）、要么整个在外面。⇒ 它是「稳定但通常不是最粗」
       的**最便宜**的那一档，`§A3` 的注入就用它。
    """
    U = ("a", "b", "c", "d")
    p2 = (frozenset({"a", "b"}), frozenset({"c", "d"}))
    e = frozenset({("a", "c")})
    disc = tuple(frozenset({x}) for x in U)
    return [
        {"label": "过·真最粗稳定细化",
         "spec": ViewSpec(universe=U, partition=p2, relation=e),
         "q": coarsest_stable_refinement(ViewSpec(universe=U, partition=p2, relation=e)),
         "want": {"A2": Tri.YES, "A3": Tri.YES}},
        {"label": "红·不是最粗（离散划分：稳定但更细）",
         "spec": ViewSpec(universe=U, partition=p2, relation=e),
         "q": disc,
         "want": {"A2": Tri.YES, "A3": Tri.NO}},
        {"label": "红·不稳定（喂 `P` 自己）",
         "spec": ViewSpec(universe=U, partition=p2, relation=e),
         "q": partition_of(p2),
         "want": {"A2": Tri.NO, "A3": Tri.NO}},
        {"label": "跳过·`E` 为空（两条判据都没有内容）",
         "spec": ViewSpec(universe=U, partition=p2, relation=frozenset()),
         "q": partition_of(p2),
         "want": {"A2": Tri.UNEXPANDED, "A3": Tri.UNEXPANDED}},
    ]


def _result(rep: Report, code: str) -> Tri:
    for a in rep.assertions:
        if a.code == code:
            return a.result
    return Tri.UNEXPANDED


def known_answer_controls() -> list[str]:
    """跑合成对照组，返回**失败清单**（空 = 全对）。给 `run_tests` 用。"""
    fails: list[str] = []
    for case in known_answer_specs():
        rep = Report(plugin="(合成)")
        a2_stable(case["spec"], case["q"], rep)
        a3_coarsest(case["spec"], case["q"], rep)
        for code, want in case["want"].items():
            got = _result(rep, code)
            if got is not want:
                fails.append(f"{case['label']}：`{code}` 期望 {want}，实测 {got}")
    return fails


def a1_known_answer(dropped: bool) -> Tri:
    """`§A1` 的合成对照 —— 手工造两张视图，`dropped=True` 时掐掉一项。

    ⚠️ 这里**不碰内核**：`§A1` 判的是「具体化 ⊇ 声明收着的」，
       与内核无关，所以对照也不该依赖内核（依赖了就不是最便宜的那一档）。
    """
    spec = ViewSpec(universe=("d1", "d2"), partition=(frozenset({"d1", "d2"}),),
                    relation=frozenset({("d1", "d2")}))
    v = View(vid="V0", block=frozenset({"d1", "d2"}),
             concretization=frozenset({"i1", "i2", "i3"}),
             covered=frozenset({"i1", "i2"}))
    if dropped:
        v = replace(v, concretization=frozenset({"i1"}))
    rep = Report(plugin="(合成)")
    a1_soundness(ViewSet(spec=spec, q=(frozenset({"d1", "d2"}),), views=(v,)), rep)
    return _result(rep, "A1")
