# -*- coding: utf-8 -*-
"""自问层：把「待办 + 注意力账本 + 处境触碰」合成**自问句**（2026-11-03 设计稿）。

## 为什么要有这个模块

`ROLES.md` §2 里 **B 自问**（没人问）的接口早已押定（`reflect.report()` /
`contradictions()` / `graded()` / `agenda`），世界试验的显性问协议（§2.6）也把
三样原料读出来了（热方向 / 处境触碰 / 擦边命中）——但输出一直是**待办清单**，
不是「我想做什么」。显性问协议原文写着：

> 解读权留给人（**或将来的自问层**）。

**本模块就是那个将来**：把四样原料组合成**自问句**。它不产生任何新信息——
全部原料来自既有结构，它只做**组合与成句**。

## 三种自问句（每句强制四段，V15 契约复用）

| 类型 | 原料 | 请求 |
|---|---|---|
| **矛盾** | `reflect.contradictions()` 的清单（必须档） | 人裁决（项级标记，判断权在调用方） |
| **待展开** | `graded()` 可延后档（待展开 / 展开失败 / 越界）+ 议程激活排序 | 人声明维度——**它不发明维度**（§K9），只指出「这里细不下去」 |
| **无词** | 处境触碰：本段处境逐个对 `all_keys`，**不在词表的逐字列出** | 人决定要不要 `declare` |

每句四段：`类型 / 陈述 / 证据 / 请求`。**证据逐字可解析**——没有证据的句子
**不许生成**，返回空清单并说明空因。空是设计的诚实（同「曝光不算热」）。

★ **展示次序（v0.1.58 提权）：无词 → 矛盾 → 待展开。** 有缺词时无词问
  必占 cap 内第一个坑位——它是唯一能喊「我不会」的句子，不许被待展开
  挤掉（考卷实证：旧排序下 40 条待展开 + cap=5，无词问永远轮不到）。

## 两条通道（2026-11-03 裁定 + V2 增补）

- **tick 内嵌**：`agenda()` 的返回经 `attach()` 加上 `自问句` 字段
  （I5 式可移除性：删掉字段其余逐字相同）
- **出箱（outbox）**：`emit()` 在落盘 / teach / drill 的收尾段被调用，
  把自问句写进 `<语料根>/outbox.jsonl`（只追加）——**不自起进程，搭已有
  操作的顺风车**；发射条件沿用议程数学（`B > τ(k)`，`k` 由距上次出箱的
  真实时间推）。**自反馈禁令**：出箱**永不写** `agenda.jsonl`（不自问句喂自问句）。

## 红线

不发明维度（§K9）· 不改候选集（§X2）· 无守护进程（只在被调用的那一刻成句）·
不打分（排序只用档位 + 激活两个既有量，禁词不碰）· 冷却沿用议程既有机制。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from . import clock
from . import agenda as AG
from . import reflect as RF

#: 出箱每次最多几句（音量天然有界，2026-11-03 设计稿 §V2）
OUTBOX_CAP = 3
#: 同一句被驳回后的冷却天数（沿用议程常量）
COOLDOWN_DAYS = AG.COOLDOWN_DAYS


def _field(key: str) -> str:
    """`金额>=3000` ⇒ `金额`（与 `chain._field` 同口径）。"""
    for op in (">=", "<=", "!=", "=", ">", "<"):
        if op in key:
            return key.split(op, 1)[0]
    return key


def _q(kind: str, key: str, statement: str, evidence: list[dict[str, Any]],
       ask: str, *, tier: str = "必须", activation: float | None = None,
       extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """组一句自问句。**证据为空 ⇒ 返回 None**（无证据的句子不许生成）。"""
    if not evidence:
        return None
    q = {"类型": kind, "键": key, "档位": tier, "陈述": statement,
         "证据": evidence, "请求": ask}
    if activation is not None:
        q["激活"] = round(activation, 3)
    if extra:
        q.update(extra)
    return q


# ── 三种自问句的 builders ────────────────────────────────────────────


def _known_keys(kernel: Any, known_keys: set[str] | None) -> set[str]:
    """词表：调用方给的优先；否则从方向 payload 并集推导（无 App 也能用）。"""
    if known_keys is not None:
        return set(known_keys)
    out: set[str] = set()
    for d in kernel.all_directions():
        m, n = d.payload
        out |= {str(x) for x in set(m) | set(n)}
    return out

def _contradiction_qs(kernel: Any, now: datetime) -> list[dict[str, Any]]:
    """矛盾问（必须档）：`reflect.contradictions()` 清单逐条成句。"""
    out: list[dict[str, Any]] = []
    contra = RF.contradictions(kernel)
    for row in contra.get("矛盾", []):
        kind = str(row.get("类型", "矛盾"))
        did = str(row.get("方向", "?"))
        key = f"矛盾:{kind}:{did}"
        q = _q(
            "矛盾", key,
            f"结构里有一处「{kind}」：{row.get('说明', '')}——用哪个？由你裁决。",
            [{"did": did, "类型": kind, "原文": row.get("说明", ""),
              "可复算": "重跑 reflect.contradictions() 应含同一条"}],
            "裁决", tier="必须")
        if q:
            out.append(q)
    return out


def _expand_qs(agenda_out: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    """待展开问（可延后档）：议程「到点」里来源为 待展开/展开失败/越界 的行。"""
    out: list[dict[str, Any]] = []
    for row in agenda_out.get("到点", []):
        todo = row.get("待办") or {}
        src = str(todo.get("来源", ""))
        if src not in ("待展开", "展开失败", "越界"):
            continue
        did = str(todo.get("方向", "?"))
        m = todo.get("成员数")
        key = str(row.get("键") or f"待展开:{src}:{did}")
        ask = ("声明维度" if src in ("待展开", "展开失败")
               else "确认这些项的归属")
        why = {"待展开": "还有东西可分但一直没分",
               "展开失败": "上次尝试展开没有变细",
               "越界": "有项在根方向的覆盖之外"}.get(src, src)
        q = _q(
            "待展开", key,
            f"「{did}」这一支撑不起更细的划分（{why}，{m} 项）——"
            "要不要一个新维度，由你定；我不发明维度。",
            [{"did": did, "成员数": m, "来源": src,
              "可复算": "重跑 reflect.graded() 同档应含同一条"}],
            ask, tier="可延后",
            activation=row.get("激活"),
            extra={"出现次数": row.get("出现次数"), "老化": row.get("老化")})
        if q:
            out.append(q)
    return out


def _unmapped_qs(known: set[str], context_keys: Iterable[str],
                 now: datetime, nodes: Any = None) -> list[dict[str, Any]]:
    """缺事问（v0.1.65）：处境词**都认识**，但没有一件"事"收它们 ⇒ 反问。

    "事" = 带 `行为` 字段的节点（教的时候写好的：人物/行为/应照）。
    词认识而事没有 —— 沉默是假的诚实，反问才是：要不要教成一件事，由你定。
    """
    if not nodes or not context_keys:
        return []
    toks = [str(x).strip() for x in context_keys
            if str(x).strip() and str(x).strip() in (known or set())]
    if len(toks) < 2:
        return []
    bare = [t[5:] if t.startswith("name=") else t for t in toks]
    uncovered: list[str] = []
    for b in bare:
        if not b:
            continue
        covered = False
        for n in nodes.values():
            f = getattr(n, "fields", {}) or {}
            xw = str(f.get("行为") or "")
            if xw and (b in xw or b == str(f.get("name") or "")):
                covered = True
                break
        if not covered:
            uncovered.append(b)
    if not uncovered:
        return []                  # 每个词都有事收着 ⇒ 不问
    q = _q(
        "缺事", "缺事:" + "|".join(uncovered),
        f"这些词我都认识，但还没被教成一件事（人物/行为/应照）——"
        "要不要教，由你定；我不代定。",
        [{"原文": t, "可复算": "词在 all_keys 里；没有任何带「行为」字段的节点收它"}
         for t in uncovered],
        "教成一件事", tier="可延后")
    return [q]


def _wordless_qs(known: set[str], context_keys: Iterable[str],
                 now: datetime) -> list[dict[str, Any]]:
    """无词问（处境触碰）：本段处境里、词表外的东西，逐字列出。"""
    known = known or set()
    missing = [str(x).strip() for x in context_keys
               if str(x).strip() and str(x).strip() not in known]
    if not missing:
        return []
    q = _q(
        "无词", "无词:" + "|".join(missing),
        f"这段处境里有 {len(missing)} 个东西我没有词描述——"
        "要不要声明成谓词/维度，由你定；我不代填。",
        [{"原文": tok, "可复算": "不在 all_keys 里"} for tok in missing],
        "声明维度", tier="可延后")
    return [q] if q else []


def report(kernel: Any, *, nodes_dir: Any = None,
           context_keys: Iterable[str] = (), now: datetime | None = None,
           cap: int = 5, k: int = 0, known_keys: set[str] | None = None,
           history: dict[str, list[datetime]] | None = None,
           cooldown: dict[str, datetime] | None = None,
           nodes: Any = None) -> dict[str, Any]:
    """自问句清单。**只读**——不改树、不改账本、不改任何返回。

    `nodes_dir` 用于读议程冷却（`agenda.jsonl`）；不给就跳过冷却过滤
    （调用方自己知道有没有基线）。`context_keys` 是本段处境里出现的东西
    （处境触碰的输入；不传 ⇒ 跳过无词问）。
    """
    now = now or clock.now()
    cool = cooldown if cooldown is not None else (
        AG.cooldown_until(nodes_dir) if nodes_dir is not None else {})
    hist = history if history is not None else (
        AG.load_history(nodes_dir) if nodes_dir is not None else {})

    ag = AG.agenda(kernel, k=k, history=hist, cooldown=cool, now=now)
    qs: list[dict[str, Any]] = []
    qs += [q for q in _contradiction_qs(kernel, now) if q]
    qs += [q for q in _expand_qs(ag, now) if q]
    known = _known_keys(kernel, known_keys)
    qs += [q for q in _wordless_qs(known, context_keys, now) if q]
    if not any(q["类型"] == "无词" for q in qs):
        qs += [q for q in _unmapped_qs(known, context_keys, now, nodes) if q]

    # 冷却过滤（D-3）：被驳回过的句子不再出——但**报数**，不静默
    cooling = [q for q in qs if cool.get(q["键"]) is not None
               and now < cool[q["键"]]]
    qs = [q for q in qs if q not in cooling]
    # ── 排序（v0.1.58 提权）：无词问排第一 ─────────────────────────────
    #   考卷实证（2026-11-03）：旧排序（矛盾0/待展开1/无词2）让无词问垫底，
    #   40 条待展开 + cap=5 ⇒ 它永远轮不到展示——处境里全是它不认识的东西时，
    #   它照常回不相关的待展开，「我没有词」这个唯一能喊「我不会」的信号
    #   被自己人堵死。提权后：有缺词 ⇒ 无词问必占 cap 内第一个坑位。
    #   无处境时无词问不存在，排序对其余类型次序无影响（I5 式可移除）。
    qs.sort(key=lambda q: ({"无词": 0, "缺事": 1, "矛盾": 2, "待展开": 3}.get(q["类型"], 9),
                           -(q.get("激活") if q.get("激活") is not None else -99.0)))
    n_shown = min(len(qs), cap)
    dropped = len(qs) - n_shown
    empty_because = None
    if not qs:
        empty_because = ("无待办" if not ag.get("到点") and not context_keys
                         else "有待办但全部在冷却" if cooling
                         else "无证据（证据空的句子不生成）")
    return {
        "问句": qs[:n_shown],
        "空因": empty_because,
        "冷却中": len(cooling),
        "超出上限未展示": dropped,
        "长说明": ("自问句的全部原料来自既有结构（反思待办 / 议程激活 / 处境对词表），"
                   "本层只做组合与成句——不发明维度（§K9）、不改候选集（§X2）、不打分。"),
    }


def render(questions: Iterable[dict[str, Any]]) -> str:
    """一行一句的人话。**模板只允许引用证据字段**——不引入清单外概念（§S1）。"""
    lines: list[str] = []
    for i, q in enumerate(questions, 1):
        ev = "、".join(str(e.get("did") or e.get("原文")) for e in q.get("证据", []))
        lines.append(f"{i}. [{q['类型']}] {q['陈述']} ｜ 证据：{ev} ｜ 请求：{q['请求']}")
    return "\n".join(lines)


def verify(q: dict[str, Any], kernel: Any,
           known_keys: set[str] | None = None) -> bool:
    """**D-1 证据强制**（机械）：自问句的证据能解析回真实结构。

    反向对照的靶子：把「证据」换成编造引文 ⇒ 本函数必须红。
    """
    kind = q.get("类型")
    if kind == "矛盾":
        contra = RF.contradictions(kernel)
        pairs = {(str(r.get("类型")), str(r.get("方向")))
                 for r in contra.get("矛盾", [])}
        return all((str(e.get("类型")), str(e.get("did"))) in pairs
                   for e in q.get("证据", [])) and bool(q.get("证据"))
    if kind == "待展开":
        rows = {str(r.get("方向")) for tier in ("必须", "可延后")
                for r in (RF.graded(kernel)[tier])}
        return all(str(e.get("did")) in rows for e in q.get("证据", [])) \
            and bool(q.get("证据"))
    if kind == "无词":
        known = _known_keys(kernel, known_keys)
        ev = [str(e.get("原文")) for e in q.get("证据", [])]
        return bool(ev) and all(t not in known for t in ev)
    return False


# ── 出箱（outbox）：搭已有操作的顺风车，不自起进程 ───────────────────


def _outbox_path(nodes_dir: Any) -> Any:
    return Path(nodes_dir).parent / "outbox.jsonl"


def emit(kernel: Any, *, nodes_dir: Any, context_keys: Iterable[str] = (),
         now: datetime | None = None, cap: int = OUTBOX_CAP,
         period_minutes: float = AG.IDLE_PERIOD_MIN,
         idle_cap: int = AG.IDLE_CAP,
         known_keys: set[str] | None = None) -> dict[str, Any]:
    """出箱：把自问句写进 `<语料根>/outbox.jsonl`（只追加）。

    **发射点 = 既有操作的收尾段**（drill / teach 之后由调用方触发）——
    不是新调用，是副作用写。**自反馈禁令**：本函数**永不写** `agenda.jsonl`
    （出箱不喂自问句）；冷却沿用 `agenda.jsonl` 的 `unknown` 记录。

    **只在「使用态」语料上发生**：语料根自带 `usage/config.json`（半衰期
    外生参数落盘的那份）才算使用态——演示语料/测试语料不出箱。
    """
    import os
    now = now or clock.now()
    nodes_dir = Path(nodes_dir)
    outp = _outbox_path(nodes_dir)
    usage_cfg = nodes_dir.parent / "usage" / "config.json"
    if not usage_cfg.is_file():
        return {"出箱": 0, "跳过": "非使用态语料（无 usage/config.json）"}
    # k_out：距上次出箱的真实时间推（无 outbox ⇒ k=0，**没有基线就不许假装闲过**）
    seen_tokens: set[str] = set()                  # 出箱历史里的缺词（无词问去重）
    last = None
    if outp.is_file():
        for line in reversed(outp.read_text(encoding="utf-8").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                rec0 = json.loads(line)
            except ValueError:
                continue
            if last is None and rec0.get("ts"):
                try:
                    last = datetime.fromisoformat(str(rec0["ts"]))
                except ValueError:
                    last = None
            for e in rec0.get("证据", []):
                if isinstance(e, dict) and e.get("原文"):
                    seen_tokens.add(str(e["原文"]))
    k_out = 0
    if last is not None:
        k_out = int(min(idle_cap, (now - last).total_seconds()
                        // (period_minutes * 60)))
    tau = AG.threshold(k_out)
    r = report(kernel, nodes_dir=nodes_dir, context_keys=context_keys,
               now=now, cap=10 ** 6, k=k_out, known_keys=known_keys)
    kept: list[dict[str, Any]] = []
    for q in r["问句"]:
        if len(kept) >= cap:
            break
        if q["类型"] == "矛盾" or q["档位"] == "必须":
            kept.append(q)                       # 必须档恒出
        elif q["类型"] == "无词":
            toks = [str(e.get("原文")) for e in q.get("证据", [])]
            if any(t not in seen_tokens for t in toks):
                kept.append(q)                   # 无词问：**新缺词**才出（自带去重）
        elif q["档位"] == "可延后" and (q.get("激活") is not None
                                        and q["激活"] > tau):
            kept.append(q)                       # 可延后要过出箱门槛
        # 可忽略档、未过门槛的可延后：不出箱
    if not kept:
        return {"出箱": 0, "句": [], "k_out": k_out, "阈值": round(tau, 3),
                "跳过": "没有到门槛的自问句"}
    with outp.open("a", encoding="utf-8", newline="\n") as f:
        for q in kept:
            rec = {"ts": now.isoformat(timespec="seconds"), "来源": "出箱", **q}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    os.utime(outp)                                # mtime = 本次操作窗口（D-8 可验）
    return {"出箱": len(kept), "句": kept, "k_out": k_out,
            "阈值": round(tau, 3), "路径": str(outp)}


def attach(agenda_out: dict[str, Any], kernel: Any, *, nodes_dir: Any = None,
           context_keys: Iterable[str] = (), now: datetime | None = None,
           cap: int = 5, known_keys: set[str] | None = None) -> dict[str, Any]:
    """**tick 内嵌**（2026-11-03 裁定）：给议程返回加一个 `自问句` 字段。

    I5 式可移除性：删掉这个字段，议程的其余返回**逐字相同**。
    """
    r = report(kernel, nodes_dir=nodes_dir, context_keys=context_keys,
               now=now, cap=cap, known_keys=known_keys)
    out = dict(agenda_out)
    out["自问句"] = r["问句"]
    out["自问句_空因"] = r["空因"]
    return out
