"""覆盖类判据 —— `B16` / `B18` / `B19`，与它们的三条度量。

    B16  过滤器健全   §1 硬要求表**第三行**   `members(d) ⊆ 覆盖(d)`
    B18  覆盖不漏     §1 表**第四行**（⬜ 2026-10-07 **已降级**为「度量 + baseline 守卫」）
                       `覆盖(父) ⊆ ∪覆盖(子)`
                       ⚠️ 它与「`滞留 == 0`」是**同一个条件**（实测漏项就是滞留项）
    B19  进步量守卫   §10.2 §6                「反复声称能分」却连续 N 次没让覆盖变细

三条共用**同一个外生 oracle**（`_fixtures.coverage_of`）—— 不调插件、不调内核。

---

## 为什么这一族必须外生

按 `§I3 代价` 判归属（`代价 = 0`）是个**陷阱**：那是**把被检查对象的运算重放一遍**。
McConnell / Mehlhorn / Näher / Schweitzer 2011 §5.5 把这种写法当成**两个被排除的极端**之一：

> We could take w as the record of the computation of P on x … **However, it does not
> satisfy the simplicity requirement, since a proof of the witness property is tantamount
> to proving the correctness of P.**

而且这条判据对 `reach` **在原理上就过强**，不只是实现问题：`代价 = 0` 只对**锚点本身**
成立，而 `覆盖 = reachable(payload)` 是「能走到锚点的**所有**项」。GiST 1995 明说
`Consistent` **允许不精确**（"an accurate test for satisfiability is not required here"）
⇒ 拿一个**精确性**代理去判一个**只要求不许假阴**的量，是判据选错了。

⇒ 所以这一族的判据一律改成**独立重算**：`_fixtures.coverage_of` 按方向的定义算覆盖，
与 `equiv_classes`（`B17` 的 oracle）并列放。**不新增接口方法**，也不用重验 §I1–§I7。

---

## 三条的分工（不要合并）

    度量 · 健全性        一次建完 / 维护两条路径的 `members ⊄ 覆盖` 计数
    度量 · 覆盖不漏      两条路径的 `覆盖(父) − ∪覆盖(子)` 计数
    度量 · 进步量        每次展开的 `|覆盖(父)| − max|覆盖(子)|`，与最长「无进步」连续段

    B16                  批建路径上 `members ⊆ 覆盖` —— **判据**
    B18                  两条路径上 `覆盖不漏` 的 **baseline 守卫** —— **判据**
    B19                  连续 N 次「无进步」展开 —— **判据**

⚠️ 三条判据的**基线**不同，这一点必须说清：

    B16  批建路径基线**绿**（三个方向都 0）⇒ 可以直接是判据
         维护路径基线**红**（reach 128/328、sequence 36/176）⇒ 只能是度量
         ⇒ 所以 `B16` **只跑批建路径**，维护路径由度量报出（见 `soundness_profile`）
    B18  批建路径基线**绿**（0 漏）⇒ 判据
         维护路径基线**红**（sequence 16 漏 / 2 对）⇒ baseline 冻结 + 对**新增**红
    B19  基线绿，且**必须能红**（注入验证里用一个「子 payload ⊇ 父 payload」的插件证明）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ._framework import Report
from ..core.tri import Tri

#: 进步量守卫的阈值 —— 照 SP-GiST 的前例（`longValuesOK`）：
#: "the SP-GiST core will raise an error if the leaf datum does not become any
#: smaller within **ten** cycles of choose method calls."
PROGRESS_N = 10

#: baseline 文件放**代码旁边**，不是 `outputs/` —— 它要跟着代码走、进版本库，
#: 换了语料或改了插件就该一起改。ESLint 把 `eslint-suppressions.json` 也这么放。
BASELINE_PATH = Path(__file__).resolve().parent / "cover_leak_baseline.json"


# --- 度量：健全性（两条路径） -------------------------------------------------

def soundness_profile(kernel: Any, cover: Callable[[Any], frozenset[str]]) -> dict[str, Any]:
    """`members(d) ⊆ 覆盖(d)` —— §1 硬要求表**第三行**，按方向统计。

    覆盖由**外生 oracle** 算（`_fixtures.coverage_of`），不调插件。

    ## 为什么它必须单独量，而且必须**两条路径都量**

    `B16` 查的就是这一条，但它只在**一次建完**的内核上跑。而这一条在两条路径上
    **不是同一件事**：

        一次建完     每个方向的成员集在**第一次展开时就是完整的**
                     ⇒ 子方向的 payload 从这批成员算出来，一定盖得住它们 ⇒ 天然成立
        维护路径     成员是**后来才长起来的**，而 payload 在劈开时就**冻结**了
                     ⇒ 子方向收不住「它没见过的项」 ⇒ 破了也没人查

    ⇒ **一次建完全绿、维护路径全红** —— 这就是「空转与通过长得一模一样」在
      **流程**上的形态：检查存在、是绿的，但它从没跑在出问题的那条路上。

    ## 读数（36 项语料，先建 6 维护 30）

        方向        `代价 = 0` 判（旧）      **外生覆盖**判（本模块）
        ─────────────────────────────────────────────────────────
        keyset            0 / 232                  0 / 232
        reach          **246 / 328**           **128 / 328**（最差方向 = 根 D0）
        sequence           36 / 176                 36 / 176

    ⇒ `reach` 那一行少了 118 —— 全部是**旧判据自己造出来的**：
      `代价 = 0` 只对锚点成立，于是「能走到锚点的项」被记成「越界」。
      **这不是修好了 118 个缺陷，是换掉了那把尺子。**

    ⚠️ 这是**硬要求**（§K8 禁假阴的结构来源），但它以**度量**形式报出而不是进退出码，
       理由只有一条：B / C 的修法要先落地（§10.2），而一条基线就红的判据
       过不了注入验证（注入验证要求基线绿）。
    """
    bad: dict[str, tuple[int, int]] = {}
    members = 0
    outside = 0
    for d in kernel.all_directions():
        mem = kernel.members_of(d)
        if not mem:
            continue
        members += len(mem)
        try:
            cov = cover(d.payload)
        except Exception:  # noqa: BLE001 - payload 由插件产出，形状可能不合
            bad[d.did] = (len(mem), len(mem))
            outside += len(mem)
            continue
        out = len([i for i in mem if i not in cov])
        if out:
            bad[d.did] = (out, len(mem))
            outside += out
    return {
        "方向数": len(kernel.all_directions()),
        "成员数": members,
        "越界成员数": outside,
        "越界方向": bad,
        "最大越界方向": max(bad, key=lambda k: bad[k][0]) if bad else "—",
    }


def render_soundness(batch: dict[str, Any], inc: dict[str, Any],
                     which: str = "") -> str:
    """把两条路径的健全性读数渲染成**一行**。"""
    head = f"健全性（{which}）" if which else "健全性"
    b, i = batch["越界成员数"], inc["越界成员数"]
    if not b and not i:
        return (f"{head}：一次建完 {b}/{batch['成员数']}、维护 {i}/{inc['成员数']} "
                f"个成员落在覆盖外（两条路径都成立）")
    return (f"★ {head}**被破坏**（§1 硬要求表第三行）："
            f"一次建完 {b}/{batch['成员数']}、**维护 {i}/{inc['成员数']}** 个成员落在覆盖外"
            f"（最差方向 {inc['最大越界方向']}）"
            f"—— §K8 的结构来源，不是无害趋势；修法见设计文档 §10.2")


# --- B16 --------------------------------------------------------------------

def b16_members_covered(kernel: Any, cover: Callable[[Any], frozenset[str]],
                        rep: Report, path: str = "批建") -> None:
    """**成员必须落在自己方向的覆盖里** —— §K8 的结构版本，**与查询集无关**。

        members(d)  ⊆  覆盖(d)

    为什么这条要紧：`命中` 说「否」是在**证明**「这个方向里没有命中」，
    而它手里的依据只有**方向自己**（payload）。若某个成员根本不在方向的覆盖里，
    那个证明就**管不到它** —— 它可能命中，而 `命中` 已经说了「否」。
    ⇒ 这就是假阴的**结构来源**。

    ## 与 `B1` 的分工（实测过，不是推理）

        B1   行为式：拿**查询集**的 ground truth 比 —— 只查得到被查询覆盖到的方向
        B16  结构式：拿**覆盖**比 —— 不看查询集，一次全查

    实测：同一个「插入时选**代价最大**的子方向」的坏内核（健全性破坏 102 个成员），

        B1  在 40 条查询下判「否」  ← 查到了
        B1  在  3 条查询下判「是」  ← **绿了**
        B16                        ← 无论查询集怎么变，都是「否」

    ⇒ 查询集一换，`B1` 就可能瞎；`B16` 不会。**两个都留**：
      `B1` 管「插件**说**得准不准」，`B16` 管「内核**分**得对不对」。

    ## ★ **两条路径各判一次**，`path` 参数就是用来把这件事印出来的

    这条判据**必须在两条路径上各跑一次** —— 因为它们不是同一件事：

        一次建完     每个方向的成员集在**第一次展开时就是完整的**
                     ⇒ 子方向的 payload 从这批成员算出来，一定盖得住它们 ⇒ 天然成立
        维护路径     成员是**后来才长起来的**，而 payload 在劈开时就**冻结**了
                     ⇒ 子方向收不住「它没见过的项」

    ⚠️ **判据落在哪条路径上，由那条路径的基线决定**：
       基线绿 ⇒ 可以当判据，并单独做注入验证；
       基线红 ⇒ 只能当**度量**，或走 baseline 守卫。
       两条路现在都绿 ⇒ 两条路都跑，**每一行单独判、单独注入验证**。
       把 `path` 印进标题，是因为「只跑一条路」与「两条路都过」在汇总里长得一模一样 ——
       **范围一旦放宽，也必须在输出里看得见**。
    """
    bad: list[str] = []
    checked = 0
    for d in kernel.all_directions():
        mem = kernel.members_of(d)
        if not mem:
            continue
        checked += len(mem)
        try:
            cov = cover(d.payload)
        except Exception as exc:  # noqa: BLE001 - payload 由插件产出
            bad.append(f"{d.did} 的覆盖算不出来：{type(exc).__name__}: {exc}")
            continue
        outside = sorted(i for i in mem if i not in cov)
        if outside:
            bad.append(f"{d.did} 有 {len(outside)}/{len(mem)} 个成员落在覆盖外"
                       f"（{outside[:3]}）")
    if checked == 0:
        rep.add("B16", f"过滤器健全（{path}路径）", Tri.UNEXPANDED, "没有任何方向有成员")
        return
    rep.add("B16", f"过滤器健全（{path}路径）：成员 ⊆ 覆盖（外生覆盖，与查询集无关）",
            Tri.NO if bad else Tri.YES,
            f"{len(bad)} 个方向有成员越界：{bad[:2]}" if bad
            else f"{checked} 个成员全部落在自己方向的覆盖里（覆盖由外生 oracle 算）")


# --- 度量：覆盖不漏（两条路径） -----------------------------------------------

def cover_leak_profile(kernel: Any,
                       cover: Callable[[Any], frozenset[str]]) -> dict[str, Any]:
    """`覆盖(父) ⊆ ∪覆盖(子)` —— §1 表**第四行**，按**父子对**统计。

    ⚠️ 该行 **2026-10-07 已降级**为「有代价的度量 + `B18` baseline 守卫」：
    实测**漏项与滞留项是同一批**（16 == 16，双向差 0）⇒ 它与「`滞留 == 0`」是
    **同一个条件**，而后者已被出路 (4) 用「代价在划分」换掉。见 §10.2 B 末。

    返回值里两个数都要有，别只报一个：

        `漏项数`   一共漏了几项 —— **代价**信号
        `漏的对数` 有几对父子漏了 —— **定位**信号（哪一层先破）

    实测（36 项语料）：

        方向        一次建完          维护路径（先建 6 维护 30）
        ────────────────────────────────────────────────────────
        keyset      0 漏 / 12 对       0 漏 / 12 对
        reach       0 漏 / 35 对       0 漏 / 35 对
        sequence    0 漏 /  5 对       **16 漏 / 2 对**（D0 漏 8、D1 漏 8）

    ⇒ 顺带推翻了一句话：`reach` 的覆盖**一次都没漏**，而它的健全性破了 128/328
      ⇒ 健全性越界**不是**「覆盖不漏」造成的，是另一条机制（根的 payload 是**外生**
      锚点集，与「后来添的项」无关）。**根因是两条，不是一条。**
    """
    pairs: list[dict[str, Any]] = []
    leak_items = 0
    for d in kernel.all_directions():
        kids = kernel.children_of(d)
        if not kids:
            continue
        try:
            cp = cover(d.payload)
            union: set[str] = set()
            for k in kids:
                union |= cover(k.payload)
        except Exception:  # noqa: BLE001 - payload 由插件产出
            continue
        leaked = sorted(cp - union)
        if leaked:
            leak_items += len(leaked)
            pairs.append({"父": d.did, "子": [k.did for k in kids],
                          "漏项数": len(leaked), "例": leaked[:3]})
    return {
        "已展开方向数": sum(1 for d in kernel.all_directions() if kernel.children_of(d)),
        "漏项数": leak_items,
        "漏的对数": len(pairs),
        "明细": pairs,
    }


def render_cover_leak(batch: dict[str, Any], inc: dict[str, Any],
                      which: str = "") -> str:
    """一行渲染 —— 两条路径分开报，**别合并成一个数**。"""
    head = f"覆盖不漏（{which}）" if which else "覆盖不漏"
    b, i = batch["漏项数"], inc["漏项数"]
    if not b and not i:
        return (f"{head}：一次建完 {b} 漏 / {batch['已展开方向数']} 对、"
                f"维护 {i} 漏 / {inc['已展开方向数']} 对（两条路径都成立）")
    d = inc["明细"][0] if inc["明细"] else (batch["明细"][0] if batch["明细"] else {})
    where = (f"（例：{d.get('父')} 漏 {d.get('漏项数')} 项）" if d else "")
    return (f"★ {head}**被破坏**（§1 表第四行，⬜ 2026-10-07 已降级）："
            f"一次建完 {b} 漏 / {batch['已展开方向数']} 对、"
            f"**维护 {i} 漏 / {inc['漏的对数']} 对**{where}"
            f"—— ⚠️ 它与「`滞留 == 0`」是**同一个条件**（实测漏项就是滞留项），"
            f"不是与健全性并列的另一条机制")


# --- B18：覆盖不漏的 baseline 守卫 -------------------------------------------

def _key(which: str, path: str) -> str:
    return f"{which}|{path}"


def load_baseline() -> dict[str, Any]:
    if not BASELINE_PATH.is_file():
        return {}
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def corpus_fingerprint(nodes: Any, edges: Any) -> dict[str, int]:
    """语料指纹 —— 用来判定「这份 baseline 是不是**本语料**上冻的」。

    ⚠️ 为什么非有不可：baseline 是**语料相关**的读数（`sequence` 维护路径 16 漏
       是在那 36 项上量的）。换一份语料跑，`183 > 16` 会被报成
       **「新增覆盖不漏」** —— 而真相是**「baseline 是别的语料的」**。
       两者在输出里长得一模一样，正是本仓库一直在防的形状。

    ⇒ 只记**两个数**（项数 / 边数），不记内容：够用，而且换语料一定会变。
    """
    return {"项数": len(nodes), "边数": sum(len(v) for v in edges.values())}


def write_baseline(entries: dict[str, Any], corpus: dict[str, int] | None = None) -> None:
    body = {
        "_note": ("覆盖不漏（§1 表第四行，⬜ 2026-10-07 已降级为「度量 + baseline 守卫」）"
                  "的**冻结基线**。"
                  "由 `python -m ldv.run_checks --write-cover-leak-baseline` 生成。"
                  "新增违规会红；条目不再发生也会红（ESLint `--prune-suppressions` 那条纪律）。"
                  "`语料` 是**本基线是在哪份语料上冻的** —— 换了语料这条守卫报「跳过」，"
                  "不报「红」：**「baseline 是别的语料的」与「新增违规」必须分得开**。"),
        "语料": corpus or {},
        "基线": entries,
    }
    BASELINE_PATH.write_text(
        json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")


def b18_cover_leak_baseline(obs: dict[str, dict[str, Any]], rep: Report,
                            corpus: dict[str, int],
                            baseline: dict[str, Any] | None = None) -> None:
    """**新增违规会红，条目不再发生也会红** —— 两头都守。

    ## 为什么不是「一条写死的判据」

    §1 表第四行**原为硬要求，2026-10-07 已降级**（它与「`滞留 == 0`」是同一个条件，
    见 §10.2 B 末），而维护路径上它**基线就红**（sequence 16 漏 / 2 对）。
    一条基线就红的判据**过不了注入验证**，只能先做成度量 —— 而纯度量**永远不红**，
    于是「声称是硬的、实际没人管」这个毛病原样保留。

    ESLint 的 Bulk Suppressions 给了第 (4) 条：

    > **Existing violations must be resolved before enabling the rule** …
    > To address this, ESLint provides a way to **suppress existing violations** …
    > **While the rule will be enforced for new code, the existing violations will not
    > be reported.**

    ⇒ **baseline 严格强于纯度量**：它**保留了「能红」**。而且 ESLint 配了一条
      **只减不增**的纪律（"an error is reported about **unused suppressions**"）——
      本模块照抄，因为否则 baseline 会退化成**永久豁免**。

    ## 三条规则

        观测 > baseline   → **红**（新增违规）
        baseline > 0 且 观测 == 0 → **红**（条目不再发生 ⇒ 去收紧 baseline）
        其余              → 绿；观测 < baseline 时**另报**「可以收紧」

    ## ★ 第 0 条：baseline 必须是**本语料**上冻的（否则**跳过**，不红不绿）

    没有这一条的话，「换了一份语料」会被报成「新增覆盖不漏」——
    实测：`openalex-small`（281 项）上 `sequence|maintenance` 漏 **183**，
    而 baseline 是在 36 项上冻的 **16** ⇒ 报「新增覆盖不漏：183 漏（baseline 16）」。
    **那句话是假的**：没有任何东西变差，只是 baseline 换了参照物。

    ⇒ 语料指纹对不上 ⇒ 报 **未展开**（跳过），并说清是**换了语料**。
      跳过 ≠ 通过：它明确地说「这条守卫在本语料上判不了」，而不是「没问题」。

    ⚠️ **`corpus` 是必填参数**（不是 `= None` 的默认值）。理由与第 0 条同源：
       防御**不能因为调用方少传一个参数而静默消失** —— 那正是本仓库一直在防的形状。
       漏传会在调用点直接 `TypeError`，而不是变成一条「永远绿」的守卫。

    ⚠️ 而且 **baseline 里没记指纹**（`语料` 为空）也走**跳过**，理由同第 0 条：
       判不了 ≠ 没问题。少了这一条，一个旧格式的 baseline 文件
       会让整条守卫**无声地失去防御**（`frozen == {}` 恒不等于 `corpus` 之外无差别）。
    """
    doc = baseline if baseline is not None else load_baseline()
    base = doc.get("基线", {})
    frozen = doc.get("语料") or {}
    if frozen != corpus:
        why = (f"baseline 是在**另一份语料**上冻的（{frozen} vs 现在 {corpus}）"
               if frozen else
               f"baseline 里**没记语料指纹**（`语料` = {frozen!r}）"
               f"⇒ 无法判定它是不是本语料的")
        rep.add("B18", "覆盖不漏（baseline 守卫）", Tri.UNEXPANDED,
                f"{why} ⇒ 这条守卫在本语料上**判不了**。要判就先 "
                f"`python -m ldv.run_checks --write-cover-leak-baseline` 重冻。"
                f"**跳过 ≠ 通过**：它没说本语料不漏")
        return
    bad: list[str] = []
    tight: list[str] = []
    checked = 0
    for key in sorted(obs):
        o = obs[key]
        b = base.get(key, {"漏项数": 0, "漏的对数": 0})
        checked += 1
        if o["漏项数"] > b["漏项数"]:
            bad.append(f"{key} 新增覆盖不漏：{o['漏项数']} 漏（baseline {b['漏项数']}）"
                       f"—— {o['明细'][:1]}")
        if b["漏项数"] > 0 and o["漏项数"] == 0:
            bad.append(f"{key} 的 baseline 条目**不再发生**（baseline {b['漏项数']} 漏）"
                       f"⇒ 该收紧 baseline，不要留着当永久豁免")
        if 0 < o["漏项数"] < b["漏项数"]:
            tight.append(f"{key} {b['漏项数']}→{o['漏项数']}")
    if checked == 0:
        rep.add("B18", "覆盖不漏", Tri.UNEXPANDED, "没有可比的父子对")
        return
    detail = ("；".join(bad) if bad
              else f"{checked} 个「方向|路径」都没有新增覆盖不漏"
                   + (f"；可收紧：{tight}" if tight else ""))
    rep.add("B18", "覆盖不漏：覆盖(父) ⊆ ∪覆盖(子)（baseline 守卫：新增红、条目失效红）",
            Tri.NO if bad else Tri.YES, detail)


# --- 度量 + B19：进步量守卫 ---------------------------------------------------

def progress_profile(kernel: Any,
                     cover: Callable[[Any], frozenset[str]]) -> dict[str, Any]:
    """每次展开的**细化量**，以及最长的「无进步」连续段。

        `细化量(d) = |覆盖(d)| − max_k |覆盖(k)|`

    「进步」为什么是**覆盖的细化度**而不是叶容量：叶容量是**后果**、会滞后；
    覆盖的细化度才是**插件声称的东西** —— 守卫要抓的正是「**声称**却分不出东西」。
    （与 SP-GiST 逐字同形：原文算的是 **leaf datum 的大小**，
      "the leaf datum does not become any **smaller** within ten cycles"。）

    ⚠️ **`§K2` 的「两侧非空」保证的是成员变细，不保证覆盖变细。**
       一条 `payload(子) ⊇ payload(父)` 的派生规则（§10.2 出路 (3)）会让
       **每一次展开**的细化量都 ≤ 0 ⇒ 本守卫立刻红。
       ⇒ 这就是 (3) 的代价的**计量形态**：它拿覆盖的变细换了成员的正确。
    """
    rows: list[dict[str, Any]] = []
    flag: dict[str, bool] = {}
    for d in kernel.all_directions():
        kids = kernel.children_of(d)
        if not kids:
            continue
        try:
            cp = len(cover(d.payload))
            best = max(len(cover(k.payload)) for k in kids)
        except Exception:  # noqa: BLE001 - payload 由插件产出
            continue
        refine = cp - best
        flag[d.did] = refine <= 0
        rows.append({"方向": d.did, "秩": d.rank, "覆盖": cp,
                     "最大子覆盖": best, "细化量": refine})

    longest = 0

    def walk(d: Any, run: int) -> None:
        nonlocal longest
        r = run + 1 if flag.get(d.did) else 0
        longest = max(longest, r)
        for k in kernel.children_of(d):
            walk(k, r)

    walk(kernel.root, 0)
    return {
        "已展开方向数": len(rows),
        "无进步展开": sum(1 for r in rows if r["细化量"] <= 0),
        "最长无进步连续段": longest,
        "N": PROGRESS_N,
        "最小细化量": min((r["细化量"] for r in rows), default=0),
        "无进步的": [r["方向"] for r in rows if r["细化量"] <= 0][:4],
    }


def render_progress(prof: dict[str, Any], which: str = "") -> str:
    head = f"进步量（{which}）" if which else "进步量"
    if prof["已展开方向数"] == 0:
        return f"{head}：没有已展开的方向（未展开，**不是**通过）"
    return (f"{head}：{prof['已展开方向数']} 次展开，其中**无进步** "
            f"{prof['无进步展开']} 次；最长连续段 {prof['最长无进步连续段']}"
            f"（阈值 N={prof['N']}）｜最小细化量 {prof['最小细化量']}"
            f"（度量，不进退出码）")


def b19_progress_guard(prof: dict[str, Any], rep: Report) -> None:
    """**反复声称能分、却连续 N 次没让覆盖变细 ⇒ 红。**

    与 `§K2 判空` 是**两条不同的检查**，不能合并：

        §K2 判空    判「**这次**分不开」—— 单次性质
        B19         判「**反复声称**却分不出东西」—— **跨次数**性质

    N = 10 照 SP-GiST 前例。⚠️ 在 36 项语料上树的深度只有 7，
    所以这条在真语料上**不会红** —— 它的「能红」由**注入验证**证明
    （注入一个 `payload(子) ⊇ payload(父)` 的插件 ⇒ 每次展开都无进步 ⇒ 红）。
    **「真语料上不红」必须与「判据是空转」分开**：前者是性质成立，后者是没有性质。
    """
    if prof["已展开方向数"] == 0:
        rep.add("B19", "进步量守卫", Tri.UNEXPANDED, "没有任何方向被展开过")
        return
    over = prof["最长无进步连续段"] >= prof["N"]
    rep.add("B19", f"进步量守卫：连续 {prof['N']} 次展开都必须让覆盖变细",
            Tri.NO if over else Tri.YES,
            (f"连续 {prof['最长无进步连续段']} 次展开没有让覆盖变细"
             f"（≥ N={prof['N']}）：{prof['无进步的']}") if over
            else (f"{prof['已展开方向数']} 次展开中无进步 {prof['无进步展开']} 次，"
                  f"最长连续段 {prof['最长无进步连续段']} < N={prof['N']}"))
