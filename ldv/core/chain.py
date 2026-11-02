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
