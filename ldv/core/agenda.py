# -*- coding: utf-8 -*-
"""议程层：**什么时候该主动处理哪条待办**（2026-10-27）。

反思层（`reflect.py`）回答的是「结构哪里不平」—— 它产**三档清单**，
而且刻意**只看后果、不看时间**（`significance()` 是纯函数，同一份结构永远同档）。

这一层回答另一个问题：**现在轮到谁**。

    ── 为什么需要它 ──────────────────────────────────────────────
    `significance()` 每次都从零算 ⇒ 一个矛盾出现 1 次和出现 100 次**档位一样**、
    **先后也一样**。也就是说「反复出现的问题更该处理」这件事，系统里
    **没有任何地方记着它** —— 不是没实现，是没地方记（`Event` 连时间戳都没有）。

    ── 两个量，两个方向，别混 ────────────────────────────────────
    **激活 B_i（ACT-R base-level）**：`B_i = ln(Σ_j age_j^-d)`，d=0.5。
        出现得多、最近出现过 ⇒ B 高。**这是「热」**，随时间是**下降**的。
    **阈值 τ(k)**：`τ(k) = τ0 - step·k`，k = 连续空闲检查轮次。
        **这是「挑剔度」**，随空闲是**下降**的（越来越不挑）。
        触发条件：`B_i > τ(k)`。

    ⚠️ 这两个方向**相反**，抄 ACT-R 时最容易抄反：
        ACT-R 原意是「记忆自己变冷，阈值不动」；
        这里要的是「门槛越来越松」——所以 τ 必须**单独**随 k 降，
        **不能**指望「让 B 随时间升」（那是反 ACT-R）。

    ── 为什么这不撞 E-1（不作分）────────────────────────────────
    1. **档位不由这里定。** 三档仍由 `significance()` 按**后果**判，
       本层只**读**档位、从不改。
    2. **激活只在同档内比较。** 跨档用**有序等级**（必须 → 可延后 → 可忽略），
       不用数值比大小。所以「一个小问题反复 100 次盖过一个大问题」不会发生 ——
       它最多在「可延后」档里排第一，永远排不到「必须」前面。
    3. **它只决定「什么时候做」，不决定「做不做」。** τ 单调下降 ⇒
       任何存在的待办**最终**都会跨过门槛（见下「老化兜底」）。

    ── 饥饿兜底 ─────────────────────────────────────────────────
    τ 随 k 单调下降 ⇒ 只要空闲轮次够多，最低的 B 也会触发。
    **但人类需求一直插进来 ⇒ k 不涨 ⇒ 还是饿死。**
    ⇒ 另有一条**与 k 无关**的兜底：一条待办**存在超过 `STALE_DAYS` 天** ⇒
      无条件报出来（借的是 SCX-AURA 那种「bounded starvation avoidance」：
      每档有硬延迟预算，超预算无条件抢调度）。
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta

from . import clock
from pathlib import Path
from typing import Any, Iterable, Sequence

#: ACT-R 的衰减参数。0.5 是 ACT-R 的默认值，不在这里调 ——
#: 调它等于改「记忆多快变冷」，那是另一件事（要单独标定，不能顺手改）。
ACTIVATION_D = 0.5

#: 阈值起点。单位是**天**（见 `_age_days`）：
#: B=0 大致对应「1 天前出现过 1 次」；B=1.6 对应「最近 1 天内出现 5 次」。
TAU0 = 0.5

#: 每多一次空闲检查，阈值降多少。
STEP = 0.3

#: 老化兜底：待办存在超过这么多天 ⇒ 无条件报（不管 k）。
STALE_DAYS = 7

#: 「agent 说不知道」之后的冷却天数 —— **不是永久跳过**（那是漏）。
COOLDOWN_DAYS = 1

#: 时间单位下限（1 分钟）：防止 `age → 0` 时 `age^-0.5 → ∞`。
_MIN_AGE_DAYS = 1.0 / 1440.0

#: 档位的**有序**等级 —— 只用来排先后，**不当分数用**。
TIER_ORDER = {"必须": 0, "可延后": 1, "可忽略": 2}


#: 「闲了一轮」的时间尺度（**暂定值**，同 `usage` 缓冲那 50 批的性质）：
#: 宿主一整天没来问，`k` 也会自己涨到上限 —— 这就是「内部时钟」要解决的那件事。
IDLE_PERIOD_MIN = 30

#: `k` 的上限。`τ(k)=0.5-0.3k`，k=6 已经是 -1.3（几乎什么都不挑）；
#: 再涨只会让「全都要」—— 那不是主动，是噪声。上限把它钉住。
IDLE_CAP = 6


def _age_days(t: datetime, now: datetime) -> float:
    """天为单位的年龄（下限 1 分钟：防 `age^-0.5 → ∞`）。"""
    return max(clock.age_days(t, now), _MIN_AGE_DAYS)


def activation(times: Sequence[datetime], now: datetime,
               d: float = ACTIVATION_D) -> float:
    """ACT-R 的 base-level activation：`B = ln(Σ_j age_j^-d)`。

    没有历史 ⇒ `-inf`（**没出现过的东西没有激活**，不是「中等」）。
    ⚠️ 返回 `-inf` 而不是 0：0 会落在阈值中间，等于凭空给了它一次机会。
    """
    if not times:
        return float("-inf")
    # ⚠️ 累加变量**不能叫 `total`** —— `B12` 的名字正则会扫到**局部变量**
    #    （`(^|_)(score|objective|utility|global|importance|total)(_|$)`）。
    #    `total` 在这里是「时间的倒数幂之和」，是计数不是评分，
    #    但 `B12` 的纪律是「扫名字、宁可误报」⇒ **改名字，不放松判据**。
    acc = sum(_age_days(t, now) ** (-d) for t in times)
    return math.log(acc)


def threshold(k: int, tau0: float = TAU0, step: float = STEP) -> float:
    """空闲第 k 轮的门槛：`τ(k) = τ0 - step·k`。**单调不增**（k 越大越松）。"""
    return tau0 - step * max(0, int(k))


def todo_key(row: dict) -> str:
    """一条待办的**稳定标识** —— 跨轮次认得出「还是那一条」。

    ⚠️ 不能用 `成员数` 之类的量做 key：成员数会变（结构在长），
       那同一条矛盾会被当成两条，激活永远攒不起来。
    """
    src = str(row.get("来源") or "")
    ident = row.get("类型") or row.get("方向") or "?"
    return f"{src}|{ident}"


# ── 账本：`<corpus>/agenda.jsonl`（只追加）──────────────────────────────


def agenda_path(nodes_dir: Path) -> Path:
    return nodes_dir.parent / "agenda.jsonl"


def load_history(nodes_dir: Path) -> dict[str, list[datetime]]:
    """读「每条待办什么时候被看到过」。

    ⚠️ 只认 `kind=seen` 的行。`kind=unknown`（agent 说不知道）不产生激活 ——
       它只设冷却（见 `cooldown_until`）。
    """
    p = agenda_path(nodes_dir)
    out: dict[str, list[datetime]] = {}
    if not p.is_file():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("kind") != "seen":
            continue
        try:
            ts = datetime.fromisoformat(str(rec.get("ts")))
        except ValueError:
            continue
        for key in rec.get("keys") or []:
            out.setdefault(str(key), []).append(ts)
    return out


def cooldown_until(nodes_dir: Path) -> dict[str, datetime]:
    """被 agent 判过「不知道」的待办 ⇒ 冷却到什么时候。"""
    p = agenda_path(nodes_dir)
    out: dict[str, datetime] = {}
    if not p.is_file():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("kind") != "unknown":
            continue
        try:
            ts = datetime.fromisoformat(str(rec.get("ts")))
        except ValueError:
            continue
        for key in rec.get("keys") or []:
            out[str(key)] = ts + timedelta(days=COOLDOWN_DAYS)
    return out


def append_record(nodes_dir: Path, *, kind: str, keys: Iterable[str],
                  k: int | None = None, now: datetime | None = None) -> Path:
    """追加一行账本。`kind` 只有两种：`seen`（本轮看到了）/ `unknown`（判过不知道）。"""
    if kind not in ("seen", "unknown"):
        raise ValueError(f"未知的议程记录种类 {kind!r}")
    now = now or clock.now()
    rec: dict[str, Any] = {"ts": now.isoformat(timespec="seconds"), "kind": kind,
                           "keys": sorted({str(x) for x in keys})}
    if k is not None:
        rec["k"] = int(k)
    p = agenda_path(nodes_dir)
    with p.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return p


# ── 议程：把「待办」变成「现在轮到谁」─────────────────────────────────


def agenda(kernel: Any, *, k: int = 0,
           history: dict[str, list[datetime]] | None = None,
           cooldown: dict[str, datetime] | None = None,
           now: datetime | None = None,
           type_of: Any = None, cap: int | None = None) -> dict[str, Any]:
    """按「激活 vs 阈值」把待办分成**到点** / **未到** / **老化兜底**。

    ★ 档位**原样透传**（来自 `significance()`），本函数不重判、不调序改档。
    ★ 排序键 = `(档位序, -激活)` —— 跨档用**有序等级**，同档才比激活。
    """
    from .reflect import graded
    from .request import LEAF_CAP

    cap = LEAF_CAP if cap is None else cap
    now = now or clock.now()
    hist = history if history is not None else {}
    cool = cooldown if cooldown is not None else {}
    tau = threshold(k)

    g = graded(kernel, cap, type_of=type_of)
    rows: list[dict[str, Any]] = []
    for tier in ("必须", "可延后", "可忽略"):
        for row in g[tier]:
            key = todo_key(row)
            seen = hist.get(key) or []
            # ★★ **首次见到 ⇒ 按「刚刚发现」算**（2026-10-27，真内核上试出来的）。
            #
            #   ACT-R 里没痕迹的记忆 `B = -inf`，永远过不了门槛 —— 那是对的：
            #   **没被想起过的记忆本来就不该自己冒出来**。
            #
            #   但这里的对象不是记忆痕迹，是**结构里客观存在的缺口**：
            #   它一直在那儿，只是之前没人看过。「第一次看到」= **刚发现**，
            #   应该是激活**最高**的时刻，不是最低。
            #
            #   ⚠️ 第一版照抄 ACT-R 给了 `-inf` ⇒ 真内核上实测：
            #      `k=0/3/6` 三档全是「到点 0」，主动性**永远启动不了第一次**
            #      （判据当时是手工造行测的，正好绕过了这个坑 —— 教训：
            #       判据必须有一条跑**真内核**）。
            first_time = not seen
            b = activation(seen or [now], now)
            first = min(seen) if seen else now
            stale = bool(first is not None
                         and (now - first) >= timedelta(days=STALE_DAYS))
            until = cool.get(key)
            cooling = bool(until is not None and now < until)
            rows.append({
                "键": key,
                "档位": tier,
                "激活": (None if b == float("-inf") else round(b, 3)),
                "首次": first_time,
                "出现次数": len(seen),
                "最近一次": (max(seen).isoformat(timespec="seconds")
                             if seen else None),
                "首次出现": first.isoformat(timespec="seconds"),
                "老化": stale,
                "冷却中": cooling,
                "待办": row,
            })

    # 触发判定：**老化**无条件触发；其余要求「激活过门槛」且不在冷却
    due = [r for r in rows
           if not r["冷却中"]
           and (r["老化"]
                or (r["激活"] is not None and r["激活"] > tau))]
    pending = [r for r in rows if r not in due]

    due.sort(key=lambda r: (TIER_ORDER.get(r["档位"], 9),
                            -(r["激活"] if r["激活"] is not None else -99.0)))
    pending.sort(key=lambda r: (TIER_ORDER.get(r["档位"], 9),
                                -(r["激活"] if r["激活"] is not None else -99.0)))

    return {
        "空闲轮次": int(k),
        "阈值": round(tau, 3),
        "公式": ("激活 B=ln(Σ age^-0.5)（age 单位：天）；"
                 f"阈值 τ(k)={TAU0} - {STEP}·k；触发条件 B > τ"),
        "到点": due,
        "未到": pending,
        "说明": ("**到点 ≠ 必须做** —— 它只说「按当前门槛，轮到它了」。"
                 "档位仍由后果定（必须/可延后/可忽略），本层不改档。"),
        "下一轮": (int(k) + 1 if not due else None),
        "老化兜底": [r["键"] for r in due if r["老化"]],
    }


# ── 内部时钟：把「闲了多少」从宿主手里接过来（2026-10-29）─────────────
#
# 在有时钟之前，`k` 只能由宿主数：它每说一次「我闲了」，k +1。
# 问题是**宿主不说话，k 就不动** —— 一个三天没人来问的库，门槛还停在 k=0，
# 待办一条都不会自己浮上来。「主动性」于是依赖外部有没有人搭理它。
#
# 现在有一层 `clock`（`ldv/core/clock.py`），于是 k 可以**由时间推**：
#
#     k = clamp( (now - 最近一次「看到待办」的时间) // IDLE_PERIOD_MIN , 0, IDLE_CAP )
#
# ⚠️ **它仍然只决定「什么时候报」，不决定「做不做」。** 档位、答案集合、
#    判定逻辑一个字没改 —— 变的只是 k 从哪来。
# ⚠️ 没有账本 / 账本里一条 `seen` 都没有 ⇒ `k=0`。**没有基线就不许假装闲过**
#    （否则第一次运行就会把所有待办一次性倒出来）。


def last_seen_at(nodes_dir: Path) -> datetime | None:
    """账本里**最后一次「看到待办」**的时刻；没有 ⇒ `None`（不猜）。"""
    p = agenda_path(nodes_dir)
    if not p.is_file():
        return None
    last: datetime | None = None
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("kind") != "seen":
            continue
        try:
            ts = datetime.fromisoformat(str(rec.get("ts")))
        except ValueError:
            continue
        if last is None or ts > last:
            last = ts
    return last


def idle_rounds(nodes_dir: Path, *, period_minutes: float = IDLE_PERIOD_MIN,
                cap: int = IDLE_CAP, now: datetime | None = None) -> int:
    """**由时间推**的空闲轮次：距上次「看到待办」过了多少个 `period_minutes`。

    `cap` 是硬上限：再久也不无限降门槛（见 `IDLE_CAP` 的注释）。
    没有基线 ⇒ 0。
    """
    now = now or clock.now()
    base = last_seen_at(nodes_dir)
    if base is None:
        return 0
    minutes = clock.age_days(base, now) * 1440.0
    if minutes <= 0:                      # 时钟回拨：不许把 k 推成负的，也不假装闲过
        return 0
    return max(0, min(int(cap), int(minutes // max(1e-9, period_minutes))))


def tick(kernel: Any, nodes_dir: Path, *,
         period_minutes: float = IDLE_PERIOD_MIN, cap: int = IDLE_CAP,
         now: datetime | None = None, record: bool = True) -> dict[str, Any]:
    """**自己走一格**：由时间算 k → 算议程 → （可选）记下这一轮摆出了什么。

    `record=True` 记的是「**摆出来了**」（`kind=seen`），不是「做了」——
    与 MCP 侧 `drill` / `next` 的记账口径一致：曝光 ≠ 有用。
    """
    now = now or clock.now()
    k = idle_rounds(nodes_dir, period_minutes=period_minutes, cap=cap, now=now)
    out = agenda(kernel, k=k, history=load_history(nodes_dir),
                 cooldown=cooldown_until(nodes_dir), now=now)
    base = last_seen_at(nodes_dir)
    out["时钟"] = {
        "k 来源": "内部时钟（时间推）",
        "周期（分钟）": period_minutes,
        "上限": cap,
        "上次看到待办": (base.isoformat(timespec="seconds") if base else None),
        "闲置（分钟）": (round(clock.age_days(base, now) * 1440.0, 1)
                         if base else None),
    }
    if record and out["到点"]:
        append_record(nodes_dir, kind="seen",
                      keys=[r["键"] for r in out["到点"]], k=k, now=now)
    return out
