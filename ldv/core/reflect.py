# -*- coding: utf-8 -*-
"""反思层 —— **内生量的报告 + 只调展开顺序**（2026-10-17）。

## 它在边界内的哪一侧

    §5 的表：  层内哪个方向值得展开   → 内生，**可自我优化**
               什么算一个方向 / 外生项 → **不许碰**

本模块**只**做前者：读结构的**内生量**（成员数 / 子方向数 / 是否待展开），
给出一份**机器可读**的报告，并据此调整**展开顺序**。

    ✗ 不改方向函数、不改判据、不改任何外生项
    ✗ 不引入标量分（`docs/评估标准.md` 的「不作分」红线）
    ✗ 不做「健康度打分」—— 那会让系统实质上充当裁判

## ★ 前提是一条实测

「只调展开顺序」安全，前提是**顺序不影响最终结构**。2026-10-17 实测：

    同一份语料，按正序 / 反序跑完全部查询
    ⇒ 方向数 19 / 19，结构签名（did / parent / 子数 / 成员数）**逐字相同**

⇒ 所以「读报告后结构不变」可以写成判据（`test_predicates` ㉒）。

## 排序规则（外生写死，可复现）

    待展开的方向按「成员数降序，并列按方向 id 升序」
    —— 只依赖结构，不依赖访问历史 ⇒ 同结构两次给出同一顺序
"""
from __future__ import annotations

from typing import Any

#: 「值得先展开」的排序规则 —— **外生写死**（不按访问频率，那会依赖历史）
#: 成员数降序（大的先展开，收益大）；并列按 did 升序（确定性）
SORT_KEY = "成员数降序·did 升序"


# ── 显著性分档（2026-10-22）──────────────────────────────────────────────────
#
# ★★ **返回档位，不返回分数。** 一旦有分，它就会被拿去跨方向比较/排序 ——
#    那是裁判，撞 E-1「不作分」。三档是**有序等级**，不是 0/1/2 的数值。
#
# ★★ 判据是**后果**（不处理会不会错），不是影响面：
#    影响面只用来定**档内顺序**。「分档管什么时候做，排序管先做哪个」。
SIGNIFICANCE = {
    "必须": "不处理 ⇒ **结论可能错**（结构自相矛盾：边打架 / 子覆盖越出父 / 孤立方向）",
    "可延后": "不处理 ⇒ **结论对但不全**（切不开 / 待展开 —— 结构一致，只是不够细）",
    "可忽略": "不处理 ⇒ **没有变化**（切了白切 m≤1 / 已展开）",
}

#: 哪些矛盾类型属于「结论可能错」—— 这三类都是结构自相矛盾
MUST_TYPES = ("边打架", "子覆盖越出父", "孤立方向")


def significance(item: dict) -> str:
    """**显著性函数** —— 一条待办 ⇒ 档位（`必须` / `可延后` / `可忽略`）。

    输入两种形状：
        矛盾：  `{"类型": "边打架" | "子覆盖越出父" | "孤立方向" | "切不开", ...}`
        待展开：`{"待展开": True, "成员数": m, "子方向数": c, ...}`

    ★★ 只看**后果**，不看大小：一个 3 项的方向如果结构矛盾，仍然「必须」；
       一个 5000 项的叶如果只是「切不开」，仍然「可延后」。
       把大小混进档位，就是把分档做成排序（那是另一件事，见 `graded` 的档内排序）。

    ⚠️ 「可延后」**不等于不重要** —— 它只是「不处理不会让结论错」。
       切不开往往要人加维度（`§D1`），所以它照样会被提给人，只是不挡路。
    """
    t = item.get("类型")
    if t in MUST_TYPES:
        return "必须"
    if t == "切不开":
        return "可延后"
    # 「展开失败」= 试过展开、但分不出子方向 ⇒ 表达力不足（要人加维度）。
    # ⚠️ 它和「待展开」都满足 `m > 1 且 c == 0`，区别只在 `is_expanded`：
    #    不看这个标志就会把「试过分不开」当成「还没试」—— 实测 ④ 第一版就栽在这。
    if item.get("来源") == "展开失败":
        return "可延后"
    if item.get("待展开"):
        return "可延后"
    return "可忽略"


def graded(kernel: Any, cap: int | None = None) -> dict[str, list[dict]]:
    """把「待办」按显著性分档 —— **档内**按成员数降序（影响面定序）。

    ⚠️ 档内排序沿用 `SORT_KEY`（成员数降序 · did 升序）：
       分档管「什么时候做」，排序管「先做哪个」—— 两件事，不混。
    ⚠️ 三个桶**都是清单**（`可忽略` 也列出来），由调用方决定要不要显示 ——
       在报告里只报它的**数量**，免得清单被噪声撑满。
    """
    rows: list[dict] = []
    # ① 结构矛盾（三类）+ 切不开（给了 cap 才查）
    for row in (contradictions(kernel, cap).get("矛盾") or []):
        d = kernel.direction(row["方向"]) if row.get("方向") else None
        m = len(kernel.members_of(d)) if d is not None else 0
        rows.append({**row, "来源": "矛盾", "成员数": m})
    # ② 待展开（还有东西可分但没分）/ 展开失败（试过分不开）
    for d in kernel.all_directions():
        m = len(kernel.members_of(d))
        c = len(kernel.children_of(d))
        if m > 1 and c == 0:
            if kernel.is_expanded(d):
                # ⚠️ `is_expanded` 为真但没子方向 ⇒ **表达力不足**，不是「还没试」
                rows.append({"来源": "展开失败", "方向": d.did, "成员数": m,
                             "说明": "试过展开，但分不开 —— 需要新维度（§D1 人的动作）"})
            else:
                rows.append({"来源": "待展开", "方向": d.did, "成员数": m,
                             "子方向数": c, "待展开": True,
                             "说明": "还有东西可分但没分"})
    buckets: dict[str, list[dict]] = {"必须": [], "可延后": [], "可忽略": []}
    for it in rows:
        buckets[significance(it)].append(it)
    for k in buckets:
        buckets[k].sort(key=lambda x: (-(x.get("成员数") or 0), x.get("方向") or ""))
    return buckets


def contradictions(kernel: Any, cap: int | None = None) -> dict[str, Any]:
    """**结构层**的矛盾清单（2026-10-17，用户拍板「就在结构层」）。

    只聚合**结构上能直接验**的三类，**不碰语义**（语义矛盾要理解内容 ⇒ 撞红线）：

        边打架       方向 `d` 声明了 `parent=p`，但 `d` 不在 `p` 的子列表里
        （`B13` 良基树检查的同一件事）
        子覆盖越出父 子方向的成员并集 ⊄ 父方向成员（`§I4` 覆盖嵌套的同一件事）
        孤立方向     没有父、也不是根（`parent` 为空但 `did != 根`）

    ⚠️ 本函数**只报不改**，且**不新增判据** —— 它把已有检查的读数汇总成一份清单。
       `checks/` 里的判据仍然各管各的（`B13` / `§I4` / …），这里不替代它们。

    ⚠️ 清单为空时也要**明说本趟查了什么**（否则「没查」与「查了没发现」长得一样）。

    ## ★★ 修订方式：**加新谓词 / 请求新方向**（2026-10-19 用户裁定）

    这三类矛盾都指向同一件事：**当前谓词与方向切不开这批项** ——

        边打架       d 声明了父 p，但不在 p 的子列表里 ⇒ 归属与声明对不上
        子覆盖越出父 子方向的成员并集 ⊄ 父方向成员 ⇒ 有一条路把项送到了错的层
        孤立方向     既没父也不是根 ⇒ 它没有「从哪来」

    ⇒ 修订动作**不是**「系统自己补子列表 / 裁成员」（那会把 bug 掩盖成绿），
      而是 **`contradiction_requests()` 产出一条请求**：要一个能把这些项
      归到正确位置的新谓词，或一个新方向。

    ⇒ 请求进 `directions.json` 的 `请求` 段，与 `B19` 那条**同一个通道**：
      人拍板 ⇒ 写 `已批准` ⇒ `request.apply_approved` 真的用上（闭环）。

    ⚠️ 仍然**不自动改结构**：加谓词/加方向是**人的决定**，系统只产证据 + 执行决定。

    ## ★ 第四类：**切不开**（2026-10-19，`cap` 给了才查）

    前三类是**结构不变量** —— 反向对照（`outputs/_probe_contra_reverse.py`）证明
    它们**能红**，但**正确内核永远不会红**：`_children` 由子方向自己声明的 parent
    反推、`union ⊆ parent` 由 payload 收窄保证。⇒ 9 份语料上恒为 0。

    而「矛盾 ⇒ 加新谓词 / 请求新方向」真正对应的现象是**叶切不开**：

        叶容量 > cap   ⇒ 这些项**材料里有差异，但当前谓词与方向切不开它们**

    ⇒ 它是**表达力矛盾**，与前三类（**结构矛盾**）性质不同，但**修订动作相同**
      （加新维度 / 新谓词）⇒ 归进同一份清单、同一个请求通道。

    ⚠️ 阈值逻辑**只有一处实现**（`request.leaf_cap_requests`），这里只是换了个包装，
       不另造一套「什么叫叶」。
    """
    rows: list[dict[str, Any]] = []
    dirs = kernel.all_directions()
    checked = {"边打架": 0, "子覆盖越出父": 0, "孤立方向": 0}

    for d in dirs:
        # ① 边打架
        if d.parent:
            checked["边打架"] += 1
            try:
                p = kernel.direction(d.parent)
                kids = [c.did for c in kernel.children_of(p)]
                if d.did not in kids:
                    rows.append({"类型": "边打架", "方向": d.did, "父": d.parent,
                                 "说明": "声明了父，但不在父的子列表里"})
            except Exception:
                rows.append({"类型": "边打架", "方向": d.did, "父": d.parent,
                             "说明": "声明的父不存在"})
        # ② 子覆盖越出父
        kids = kernel.children_of(d)
        if kids:
            checked["子覆盖越出父"] += 1
            union = set()
            for k in kids:
                union |= set(kernel.members_of(k))
            if not union <= set(kernel.members_of(d)):
                rows.append({"类型": "子覆盖越出父", "方向": d.did,
                             "越出": len(union - set(kernel.members_of(d))),
                             "说明": "子方向的成员并集不在父方向成员里"})
        # ③ 孤立方向（既无父、也不是根 —— 根是 `D0`）
        if not d.parent and d.did != "D0":
            checked["孤立方向"] += 1
            rows.append({"类型": "孤立方向", "方向": d.did,
                         "说明": "没有父，也不是根 D0"})

    n_struct = len(rows)

    # ④ **切不开**（表达力矛盾）—— 只在调用方给了 `cap` 时查
    cap_rows: list[dict[str, Any]] = []
    if cap is not None:
        from .request import leaf_cap_requests       # 局部导入：避免 core 内部循环
        lr = leaf_cap_requests(kernel, cap)
        for r in lr.get("请求", []):
            cap_rows.append({
                "类型": "切不开",
                "方向": r["方向"],
                "项数": r["涉及的项数"],
                "阈值": int(cap),
                "说明": (f"这个叶有 {r['涉及的项数']} 个项 > 阈值 {cap} —— "
                         f"每次展开都分开了，但分完还是这么大"),
            })
        checked["切不开"] = lr["本趟查了"].get("查过的叶", 0)

    return {
        "矛盾数": n_struct + len(cap_rows),
        "矛盾": rows + cap_rows,
        # ★ 两类分开数：**结构矛盾恒 0 是正常的**，别让「矛盾 0」读成「没问题」
        "分组": {"结构矛盾": n_struct, "切不开": len(cap_rows)},
        # ★ 查了什么（「没查」与「查了没发现」必须分得开）
        "检查项": (["边打架", "子覆盖越出父", "孤立方向"]
                   + (["切不开"] if cap is not None else [])),
        "未查": ([] if cap is not None else ["切不开（调用方没给 cap）"]),
        "覆盖读数": {"方向数": len(dirs), **checked},
    }


def contradiction_requests(kernel: Any, contra: dict[str, Any] | None = None
                           ) -> dict[str, Any]:
    """把矛盾清单**转成方向请求**（2026-10-19，用户裁定：修订方式是加谓词/加方向）。

    每一类矛盾都指向「**当前谓词与方向切不开这批项**」，所以请求的内容是同一个：

        需要一个能把这批项**归到正确位置**的新谓词（或一个新方向）

    ⚠️ **只产证据，不改结构** —— 与 `request.direction_requests` 同一个原则：
       系统只提问、不回答；人拍板后由 `request.apply_approved` 执行。

    ⚠️ 清单为空时**明说查了什么**（否则「没查」与「查了没发现」长得一样）。

    返回结构与 `request.direction_requests` 对齐（`请求` / `本趟查了` / `说明`），
    这样 `request.write_requests` 可以直接把它并进 `directions.json`。
    """
    c = contra if contra is not None else contradictions(kernel)
    rows = c.get("矛盾", []) or []
    reqs: list[dict[str, Any]] = []
    for r in rows:
        kind = str(r.get("类型", ""))
        did = str(r.get("方向", ""))
        # 涉及的项：尽量窄 —— 只取**真正有关**的那批，不是整个语料
        involved: list[str] = []
        try:
            d = kernel.direction(did)
            involved = sorted(kernel.members_of(d))
        except Exception:
            involved = []
        if kind == "子覆盖越出父":
            # 越出的那部分才是问题所在（父的成员之外的那些）
            try:
                p = kernel.direction(did)
                union: set[str] = set()
                for k in kernel.children_of(p):
                    union |= set(kernel.members_of(k))
                involved = sorted(union - set(kernel.members_of(p)))
            except Exception:
                pass
        # 两类矛盾的**证据不同**，请求措辞也必须不同（否则读的人分不清该改什么）：
        #   结构矛盾  项被送到了错的位置 ⇒ 要「归到正确位置」
        #   切不开    项挤在一个叶里、切不动 ⇒ 要「切开这批项」
        if kind == "切不开":
            want = ("需要一个能**切开这批项**的新维度（一个 front matter 字段，"
                    "或一个新谓词）—— 每次展开都分开了，但分完还是挤在一起")
        else:
            want = ("需要一个能把这批项**归到正确位置**的新谓词（或一个新方向）"
                    "—— 当前谓词与方向切不开它们")
        reqs.append({
            "信号": f"矛盾：{kind}",
            "方向": did,
            "父": r.get("父"),
            "涉及的项": involved,
            "涉及的项数": len(involved),
            "当前状态": str(r.get("说明", "")),
            "请求": want,
            "判据编号": "矛盾",
        })
    return {
        "请求": reqs,
        "本趟查了": c.get("覆盖读数", {}),
        "说明": (f"矛盾 {len(reqs)} 条 ⇒ {len(reqs)} 条请求"
                 f"（结构 {c.get('分组', {}).get('结构矛盾', 0)} / "
                 f"切不开 {c.get('分组', {}).get('切不开', 0)}）—— "
                 if reqs else
                 f"矛盾 0 条（查了 {c.get('检查项')}；"
                 f"未查 {c.get('未查') or '无'}）—— ")
                + "人拍板后写进 `directions.json` 的 `已批准` / `已拒绝`",
    }


def _graded_report(kernel: Any) -> dict[str, Any]:
    """报告里的「分级」段 —— 三档清单 + 档内顺序。

    ⚠️ `可忽略` **只报数量不列清单**：它是最多的那档（每个已展开的方向都在里面），
       列出来会把真正的待办淹掉。要全量看用 `graded()`。
    """
    from .request import LEAF_CAP          # 局部导入：避免 core 内部循环
    g = graded(kernel, LEAF_CAP)
    return {
        "档位说明": SIGNIFICANCE,
        "必须": g["必须"],
        "可延后": g["可延后"],
        "可忽略数": len(g["可忽略"]),
        "档内顺序": SORT_KEY,
    }


def plan(kernel: Any, *, rounds: int = 4, per_round: int = 3,
         cap: int | None = None, tried: Any = None) -> dict[str, Any]:
    """**建树计划**：按显著性逐层展开，报「下一步该展开哪些」。

    ★ 「动机」在**建树阶段**的落地 —— 树还没建完时，系统能说「先展开这几支」，
      而不是只能等建完了再报「没待办」。

    ★★ 为什么需要它：完整树上「待展开」恒为 0（`build_keyset` 会 `insert` 到底），
       所以「待展开」这一档在**建完之后**永远为空 —— 那不是 bug，是「树建完就没待办了」。
       它只在**建树过程中**有意义，而建树过程需要一个「只建了根」的内核作起点
       （`_fixtures.make_keyset_root`）。

    ⚠️ 它**只调顺序 + 展开**，不改方向函数 / 判据 / 外生项（与 `apply` 同一纪律）。
    ⚠️ **惰性内核是可以被查询的**（实测 2026-10-22）：`kernel.query` 对每个说「是」
       的方向**无条件 `expand`** ⇒ 半成品树查询 = **边查边建**，答案与完整树**逐字相同**
       （内置 36 项：查询前 1 个方向 → 查询后 25 个，命中 36 vs 36、三态计数相同）。
       ⇒ `plan` 不服务查询**不是**因为「会漏」，而是因为**查询自己就会展开** ——
         让 `plan` 去服务查询是重复劳动。
    ⚠️ `per_round` 是**外生定死**的（不按材料自适应 —— 那会让行为依赖历史）。
    """
    from .request import LEAF_CAP
    cap = LEAF_CAP if cap is None else cap

    # ⚠️ **必须记住「试过的」**：`expand` 判「分不开」时**不写** `_expanded`
    #    （叶不是终态，成员集变了要能重试）⇒ `is_expanded` 恒 False ⇒
    #    不加这个集合，同一个分不开的方向会被**每轮重复选中**、每次都是空操作。
    #    实测：不加时第 3–6 轮反复展开 D5，而「剩余待展开」还从 6 涨到 8。
    tried_set: set[str] = set(tried or ())

    def _todo() -> list[dict]:
        return [x for x in graded(kernel, cap)["可延后"]
                if x.get("来源") == "待展开" and x["方向"] not in tried_set]

    log: list[dict[str, Any]] = []
    # 本次调用里「试过、但分不开」的方向 —— ⚠️ 不要用「试过数 − 展开数」算：
    # 多次续建时 tried_set 含历史、展开数只是本次 ⇒ 会得出 17 这种假数（实测）。
    failed: set[str] = set()
    # ⚠️ 变量名不能叫 `total` —— `B12` 的名字正则是
    #    `(^|_)(score|objective|utility|global|importance|total)(_|$)`，它会扫到**局部变量**。
    #    `total` 是计数不是评分，但 B12 的纪律是「扫名字、宁可误报」⇒ 改名字，不放松判据。
    n_expanded = 0
    for i in range(rounds):
        todo = _todo()
        if not todo:
            log.append({"第几轮": i + 1, "展开": [], "剩余待展开": 0})
            break
        done: list[str] = []
        for x in todo[:per_round]:
            did = x["方向"]
            tried_set.add(did)
            try:
                d = kernel.direction(did)
                if kernel.is_expanded(d):
                    continue                 # 已展开的不算「试过」，也不算失败
                kernel.expand(d)
                # 只有**真切出子方向**才算展开成功（分不开的进「试过」但不进 done）
                if kernel.children_of(d):
                    done.append(did)
                else:
                    failed.add(did)
            except Exception:                    # 单方向失败不拖垮整趟
                continue
        n_expanded += len(done)
        log.append({"第几轮": i + 1, "展开": done, "剩余待展开": len(_todo())})
        if not done:                             # 一轮一个都没切开 ⇒ 停，别空转
            break
    return {"轮次": log, "总展开": n_expanded, "剩余待展开": len(_todo()),
            # ⚠️ 「剩余 0」有两种含义，必须分开报：全建完了 / 剩下的都试过且分不开
            "试过分不开": len(failed),
            "分不开方向": sorted(failed),
            # 续建要把它存进快照 —— 否则每次重试那几支，剩余**不收敛**
            "试过方向": sorted(tried_set),
            "档内顺序": SORT_KEY, "每轮上限": per_round}


def report(kernel: Any) -> dict[str, Any]:
    """生成反思报告（**全是内生量**，每个数都能从结构重算）。

    字段：
    - `方向数` / `待展开数`：结构规模
    - `展开序`：建议先展开的方向 id（**外生规则**排序）
    - `逐方向`：每个方向的成员数 / 子方向数 / 是否待展开
    - `排序规则`：写死的那条规则名（可审计）

    ⚠️ 报告里**没有**「分数」「健康度」「优先级分」这类字段 —— 有意为之。
    """
    rows: list[dict[str, Any]] = []
    for d in kernel.all_directions():
        m = len(kernel.members_of(d))
        c = len(kernel.children_of(d))
        rows.append({"did": d.did, "成员数": m, "子方向数": c,
                     # 「待展开」= 还有东西可分但没分 —— **结构性**判据
                     "待展开": m > 1 and c == 0})
    rows.sort(key=lambda r: (-r["成员数"], r["did"]))     # 外生排序规则
    return {
        "方向数": len(rows),
        "待展开数": sum(1 for r in rows if r["待展开"]),
        "展开序": [r["did"] for r in rows if r["待展开"]],
        "逐方向": rows,
        "排序规则": SORT_KEY,
        # ★ **结构层**矛盾清单（2026-10-17）—— 只聚合已有检查，不新增判据
        "矛盾": contradictions(kernel),
        # ★ **显著性分档**（2026-10-22）—— 三档由**后果**定，档内按成员数定序。
        #   ⚠️ 它不是分数：`必须` / `可延后` / `可忽略` 是**有序等级**（E-1「不作分」）。
        "分级": _graded_report(kernel),
    }


def expand_order(kernel: Any) -> list[str]:
    """系统消费的接口：建议的展开顺序（方向 id 列表）。"""
    return report(kernel)["展开序"]


def apply(kernel: Any, limit: int = 8) -> dict[str, Any]:
    """按报告顺序**预展开**（`limit` 个），返回执行摘要。

    ⚠️ 这是「系统使用报告」的唯一落点 —— 它只调**顺序**，不改结构：
       最终方向树与不预展开时**逐字相同**（前提见模块 docstring 的实测）。
    ⚠️ `limit` 是**外生定死**的（不按材料自适应 —— 那会让行为依赖历史）。
    """
    order = expand_order(kernel)[:limit]
    done: list[str] = []
    for did in order:
        try:
            d = kernel.direction(did)
            if not kernel.is_expanded(d):
                kernel.expand(d)
                done.append(did)
        except Exception:                      # 单方向失败不拖垮整趟
            continue
    return {"预展开": done, "请求数": len(order)}
