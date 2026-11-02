"""把「一支答案」还原成一条**链路**（根 → 该支），并标出哪几步是问题要的。

## 为什么要有这个模块

`query()` 返回的是一排**并列的支**。那是「有哪些答案」，不是「怎么想到的」。
而方向树里每一支都带 `parent`（`Direction.parent`，`kernel.py:74`）——
从根走下来的那条路**本来就在结构里**，只是一直没被读出来。

于是「LDV 给出的是一条思考链路」这句话，此前没有接口支撑：
调用方拿到的永远是平铺的支，只能自己去猜先后。这个模块把那条路读出来。

★★ **它只读。** 不展开、不插入、不排序、不改任何方向。
   `chain_of()` 跑前跑后，树的签名（方向数 / 边数 / 成员总数）逐字相同。

## 链路里两种步，必须分开数

树的切分键是「出现率最接近 1/2 的键」（`plugins/keyset.py:88`，信息量最大），
**不是**「和当前问题最相关的键」。后果是：答「我标过星的那 13 条」时，
链路的第一刀是「不含日期」——因为 120 条里 30 条有日期，最均衡。

那一刀对检索是必要的（它就是路由），对**思考**是噪声：没有人会从
「先把有日期的 90 条排除掉」开始想「我标星的东西」。

⇒ 每一步判一个 `相干`：

    相干 = 这一步新加的那个谓词，其**字段**出现在本次需求里

        requirement = {我=重要的}          ⇒ 字段 {我}
        r7 新加 `我`         ⇒ 相干
        r2 新加 `日期`(取反) ⇒ 过路

⚠️ 相干 ≠ 该步可以省掉。省掉它检索就走不到。相干说的是**该不该讲给人听**：
   讲链路时过路步可以折成「其余 N 条」，相干步必须留在台面上。

## 三个读数

    `长`      链路总步数（含根）
    `相干步`  其中字段落在需求里的步数
    `过路步`  其余（= 树自己的历史，与本次问题无关）

人描述自己的思考时，`过路步` 通常是 0 —— 他本来就没走过那些岔路。
"""

from __future__ import annotations

from typing import Any, Iterable


def _field(key: str) -> str:
    """`金额>=3000` ⇒ `金额`；`我=重要的` ⇒ `我`；`日期` ⇒ `日期`。"""
    for op in (">=", "<=", "!=", "=", ">", "<"):
        if op in key:
            return key.split(op, 1)[0]
    return key


def _delta(parent: Any, cur: Any) -> tuple[list[str], list[str]]:
    """这一步比父多了哪些约束（`must` 加进来的 / `mustnot` 加进来的）。"""
    pm, pn = parent.payload
    cm, cn = cur.payload
    return sorted(set(cm) - set(pm)), sorted(set(cn) - set(pn))


def chain_of(kernel: Any, did: str, *, require: Iterable[str] = (),
             forbid: Iterable[str] = ()) -> dict[str, Any]:
    """根 → `did` 的链路。**只读**。

    `require` / `forbid` 只用来判「相干」，不参与路由 —— 传空也能用，
    那时全部步都算过路（调用方自己知道没有需求可比）。
    """
    fields = {_field(k) for k in [*require, *forbid]}
    d = kernel.direction(did)
    steps: list[dict[str, Any]] = []
    cur = d
    while cur is not None:
        steps.append({
            "rank": cur.rank,
            "did": cur.did,
            "成员数": len(kernel.members_of(cur)),
            "子方向数": len(kernel.children_of(cur)),
        })
        cur = kernel.direction(cur.parent) if cur.parent else None
    steps.reverse()
    for i, s in enumerate(steps):
        cur = kernel.direction(s["did"])
        if i == 0:
            s["新增必须"] = []
            s["新增排除"] = []
            s["切"] = None
            s["动作"] = "起点"
            s["相干"] = False
            continue
        m, n = _delta(kernel.direction(steps[i - 1]["did"]), cur)
        # 一步只切一个键 ⇒ 两半各加一条；维护路径上可能两侧都加，取两侧并集
        cut = (m + n)
        s["新增必须"] = m
        s["新增排除"] = n
        s["切"] = cut[0] if cut else None
        s["动作"] = ("收" if m else "排除") if not (m and n) else "分"
        s["相干"] = bool(cut) and _field(cut[0]) in fields
    n_rel = sum(1 for s in steps if s["相干"])
    return {
        "did": did,
        "长": len(steps),
        "相干步": n_rel,
        "过路步": len(steps) - n_rel - 1,     # 减掉根那一层（它没切任何东西）
        "需求字段": sorted(fields),
        "路": steps,
    }


def chains(kernel: Any, dids: Iterable[str], *, require: Iterable[str] = (),
           forbid: Iterable[str] = ()) -> list[dict[str, Any]]:
    """一次给多支算链路。**按 `长` 升序**（短链先讲 —— 短链更接近人的说法）。"""
    out = [chain_of(kernel, d, require=require, forbid=forbid) for d in dids]
    out.sort(key=lambda c: (c["长"], c["did"]))
    return out


def summary(cs: list[dict[str, Any]]) -> dict[str, Any]:
    """一支也没有时也要能报（`n=0` 是合法结果，不是错误）。"""
    if not cs:
        return {"支数": 0, "长": {"最小": 0, "最大": 0}, "相干步": 0, "过路步": 0}
    return {
        "支数": len(cs),
        "长": {"最小": min(c["长"] for c in cs), "最大": max(c["长"] for c in cs)},
        "相干步": sum(c["相干步"] for c in cs),
        "过路步": sum(c["过路步"] for c in cs),
    }


def folded_of(kernel: Any, did: str, *, require: Iterable[str] = (),
              forbid: Iterable[str] = (), n_answer: int | None = None,
              max_siblings: int = 4) -> dict[str, Any]:
    """折叠视图（v0.1.55）：把「同字段的连续二元切分」合并成一个**字段步**。

    ## 为什么要有这个第二视图

    树是二叉的 ⇒ 一个字段有 N 个值就要切 N−1 次，链上表现为连续多步
    （实测 C5：`type◆ → type◆ → type◆ → type◆ → type◆` 五步其实是**一个**决策）。
    于是「链长 5–10 vs 人 1–2」的差不在想得深——**字段决策数与人一致（实测 2）**——
    而在**讲述单位**比人小（二元步 vs 字段决策）。

    依据（全部先于本实现）：
    - `exp/m2/PREREG_V16.md` 机械验收 M1–M4 全过（8/8 需求，C5 8→4 步）；
    - `exp/m2/v16_blind/` 盲判 F1/F2 双 24/24（折叠无理解劣势）；
    - `exp/m2/v17/` 出声思维实测：人字段决策均值 1.86、第一刀恒为基本层级；
    - Green & Petre 1996：同字段连切 = **repetition viscosity**，折叠 = design maneuver。

    ★★ **只读**。不展开、不插入、不排序、不改任何方向 —— 输入是
       `chain_of()` 的同一条路，输出只是**换了个讲述粒度**。

    ## 四个读数（`CHAIN_CHANGE_PLAN.md` §1.1–1.5）

        筛选步数    相干字段步的个数（「分几步」数这个，与人的字段决策同单位）
        检索深度    第一个「成员数 == n_answer」的折叠步位置（答案在这里就齐了）
        到叶深度    折叠步总数（归档口径）
        讲述顺序    相干字段按成员数降序（**人先看大类**——Rosch 基本层级；
                    树的顺序不变，这只是呈现顺序）
    兄弟分支：答案支的同父兄弟（被谓词排除的近邻），带成员数——
    把「排除」变成**可见的**（人裁决「全身算不算练背」时 LDV 是沉默的；
    列出来 ≠ 改候选集，`n_branches` 不动，§X2 合规）。
    """
    c = chain_of(kernel, did, require=require, forbid=forbid)
    fields = set(c["需求字段"])
    body = c["路"][1:]
    steps: list[dict[str, Any]] = []
    i = 0
    while i < len(body):
        s = body[i]
        cut = s.get("切")
        f = str(cut).split("=")[0] if cut else None
        if f is None:                       # 子方向细分：树自己的历史
            steps.append({"字段": None, "动作": "子类细分", "成员数": s["成员数"],
                          "did": s["did"], "刀数": 1, "过路": True, "相干": False})
            i += 1
            continue
        j, cuts, last = i, 0, s
        while j < len(body):
            cut2 = body[j].get("切")
            f2 = str(cut2).split("=")[0] if cut2 else None
            if f2 == f:
                cuts += 1
                last = body[j]
                j += 1
            else:
                break
        steps.append({"字段": f, "动作": f"按 {f} 筛选", "刀数": cuts,
                      "成员数": last["成员数"], "did": last["did"],
                      "过路": f not in fields, "相干": f in fields})
        i = j
    n_filter = sum(1 for x in steps if x["相干"])
    # 1.2 两个深度
    depth_answer = depth_leaf = len(steps)
    if n_answer is not None:
        for idx, x in enumerate(steps, 1):
            if x["成员数"] == n_answer:
                depth_answer = idx
                break
    # 1.5 讲述顺序：相干字段按成员数降序（稳定），`步` 的顺序不动
    tell = [x["字段"] for x in sorted((x for x in steps if x["相干"]),
                                      key=lambda x: (-x["成员数"], str(x["字段"])))]
    # 1.3 兄弟分支可见：答案支的同父兄弟（排除自己）
    leaf = kernel.direction(did)
    sibs: list[dict[str, Any]] = []
    if leaf.parent:
        par = kernel.direction(leaf.parent)
        pm, pn = par.payload
        for sb in kernel.children_of(par):
            if sb.did == did:
                continue
            sm, sn = sb.payload
            add = sorted(set(sm) - set(pm)) or sorted(set(sn) - set(pn))
            sibs.append({"切": add[0] if add else sb.did,
                         "成员数": len(kernel.members_of(sb))})
    sibs.sort(key=lambda x: -x["成员数"])
    siblings: list[dict[str, Any]] = sibs[:max_siblings]
    if len(sibs) > max_siblings:
        siblings.append({"另有": len(sibs) - max_siblings})
    return {
        "did": did,
        "折叠步数": len(steps),
        "筛选步数": n_filter,
        "检索深度": depth_answer,
        "到叶深度": depth_leaf,
        "讲述顺序": tell,
        "兄弟分支": siblings,
        "步": steps,
        "长_原链": c["长"],
    }


def folded(kernel: Any, dids: Iterable[str], *, require: Iterable[str] = (),
           forbid: Iterable[str] = (), n_answer: int | None = None,
           max_siblings: int = 4) -> list[dict[str, Any]]:
    """一次给多支算折叠视图（顺序跟随入参，不再另排）。"""
    return [folded_of(kernel, d, require=require, forbid=forbid,
                      n_answer=n_answer, max_siblings=max_siblings)
            for d in dids]
