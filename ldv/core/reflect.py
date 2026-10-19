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


def contradictions(kernel: Any) -> dict[str, Any]:
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

    return {
        "矛盾数": len(rows),
        "矛盾": rows,
        # ★ 查了什么（「没查」与「查了没发现」必须分得开）
        "检查项": ["边打架", "子覆盖越出父", "孤立方向"],
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
        reqs.append({
            "信号": f"矛盾：{kind}",
            "方向": did,
            "父": r.get("父"),
            "涉及的项": involved,
            "涉及的项数": len(involved),
            "当前状态": str(r.get("说明", "")),
            "请求": ("需要一个能把这批项**归到正确位置**的新谓词（或一个新方向）"
                     "—— 当前谓词与方向切不开它们"),
            "判据编号": "矛盾",
        })
    return {
        "请求": reqs,
        "本趟查了": c.get("覆盖读数", {}),
        "说明": (f"矛盾 {len(reqs)} 条 ⇒ {len(reqs)} 条请求 —— "
                 if reqs else
                 f"矛盾 0 条（查了 {c.get('检查项')}）—— ")
                + "人拍板后写进 `directions.json` 的 `已批准` / `已拒绝`",
    }


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
