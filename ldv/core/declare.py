# -*- coding: utf-8 -*-
"""值谓词声明 —— 让「字段上的取值/比较」成为可用谓词，且每一步可审计（2026-11-02）。

## 它补的是哪一格

`问 → 键表达式 → 这一带` 这条链上，最窄的一环是**键从哪来**：

    `type=读书`、`我=重要的`      ← `ENUM_FIELDS` 硬编码，只有 7 个字段能产出
    `作者`、`日期`、`金额`        ← 字段存在性，只能问「有没有」
    `金额>=3000`、`状态=待修`     ← **不存在** —— 这才是人真正会问的那类

这份语料的 `金额` / `状态` / `类别` / `耗时` / `天数` 全都在 front matter 里，
但因为没有谓词，它们对查询**不可见**。这不是「没数据」，是「没词汇」。

本模块把「字段上的比较」变成一等谓词：`字段 算子 值`，算子只有六个
`= != >= <= > <`。声明后它作为键进划分，与 `type=读书` 完全同级。

## 三条硬规矩

1. **只加键，不改材料。** 与 `derived/` 同理：删掉 `predicates.json` 的
   `字段谓词` 段，结构立刻回到原状。材料文件一个字节都不动。
2. **0 命中拒收。** 声明一个在这份材料上一条都不匹配的谓词 ⇒ 它进划分后是
   一个空维度，而「空维度」与「拼错了」在界面上长得一模一样。⇒ 当场拒绝，
   并把证据（有该字段几条、取值样例、是不是数值）递回去。
3. **只追加，不覆盖。** 同名条目已存在（人或 agent 写的）⇒ 原样保留，
   返回 `already=True`。agent 不能改写人写的东西。

## 它和 `request.py` 的分工

    `request.py`  发现「缺**维度**」（B19 连续展开没变细）⇒ 请人加**字段**
    本模块        发现「缺**谓词**」（问了 `金额>=3000`，词汇表里没有这条）
                  ⇒ 当场给提案，批准后**立刻**可用

两者都只到「提案」为止。区别是：缺字段只能人回去改材料，缺谓词可以在
本进程内闭环 —— 所以这一格允许 agent 填，条件是留下 `来源 / 依据 / at`。
"""
from __future__ import annotations

import difflib
import json
from pathlib import Path

from . import clock
from typing import Any, Iterable, Sequence

#: 算子。**顺序有意义**：`>=` 必须先于 `>` 匹配，否则 `金额>=3000` 会被拆成 `金额>` + `=3000`
OPS: tuple[str, ...] = (">=", "<=", "!=", "=", ">", "<")

#: `predicates.json` 里本模块独占的段名（其余段一律原样保留）
SECTION = "字段谓词"


def parse(key: str) -> tuple[str, str, str] | None:
    """`金额>=3000` → `("金额", ">=", "3000")`；不是这种形状 ⇒ `None`。

    ⚠️ 只认**单算子**。`1000<金额<3000` 这种区间**不拆**（拆了就要有布尔组合，
       那是另一件事，且会撞「方向必须二分」那条）。要区间就声明两条。
    """
    if not isinstance(key, str) or not key.strip():
        return None
    for op in OPS:
        idx = key.find(op)
        if idx <= 0 or idx + len(op) >= len(key):
            continue
        field, val = key[:idx].strip(), key[idx + len(op):].strip()
        if not field or not val:
            continue
        # 字段名或**取值**里不该再带算子（`1<金额<9` / `金额>=100>50` ⇒ 拒，不猜）
        if any(o in field for o in OPS) or any(o in val for o in OPS):
            return None
        return field, op, val
    return None


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _cell(node: Any, field: str) -> Any:
    f = getattr(node, "fields", {}) or {}
    return f.get(field, None)


def matches(node: Any, field: str, op: str, raw: str) -> bool | None:
    """`True` 命中 / `False` 不命中 / `None` 这一条**不可比较**（缺字段或非数值）。

    `None` 与 `False` 必须分开：前者是「这条没有这个字段」，后者是「有，但不满足」。
    合并会让人以为「整份材料都不满足」，而真相是「大部分条目根本没这个字段」。
    """
    v = _cell(node, field)
    if v is None:
        return None
    if op in ("=", "!="):
        same = str(v) == str(raw)
        return same if op == "=" else (not same)
    a, b = _num(v), _num(raw)
    if a is None or b is None:
        return None
    return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]


def evidence(nodes: dict[str, Any], key: str, *, top: int = 5) -> dict[str, Any]:
    """把「这个谓词在材料里长什么样」查清楚 —— 提案与拒收共用这一份。"""
    p = parse(key)
    if p is None:
        return {"可声明": False, "理由": "不是「字段 算子 值」的形状",
                "提示": f"算子只有 {' '.join(OPS)}；区间请拆成两条"}
    field, op, raw = p
    have, hit, skipped = 0, 0, 0
    dist: dict[str, int] = {}
    for n in nodes.values():
        v = _cell(n, field)
        if v is None:
            continue
        have += 1
        dist[str(v)] = dist.get(str(v), 0) + 1
        m = matches(n, field, op, raw)
        if m is None:
            skipped += 1
        elif m:
            hit += 1
    out: dict[str, Any] = {
        "键": key, "字段": field, "算子": op, "值": raw,
        "有该字段的条目": have, "总条目": len(nodes),
        "命中": hit, "不可比较（缺字段或非数值）": skipped,
        "取值样例": sorted(dist.items(), key=lambda kv: -kv[1])[:top],
    }
    if have == 0:
        fields = sorted({k for n in nodes.values() for k in (getattr(n, "fields", {}) or {})})
        out["可声明"] = False
        out["理由"] = f"语料里没有字段 `{field}` —— 这是**缺维度**，不是缺谓词"
        out["相近字段"] = difflib.get_close_matches(field, fields, n=3, cutoff=0.5)
        out["该找谁"] = "request.py 那条路：请人往 front matter 加字段"
    else:
        out["可声明"] = hit > 0
        if hit == 0:
            out["理由"] = (f"字段有 {have} 条，但没有一条满足 `{op} {raw}` —— "
                           f"声明它只会得到一个空维度")
        elif skipped:
            out["理由"] = f"可用：{hit} 条命中，{skipped} 条因缺字段/非数值被跳过"
    return out


def load(corpus_dir: Path) -> list[dict[str, Any]]:
    """读 `predicates.json` 的 `字段谓词` 段。没有/坏了 ⇒ 空表（不猜）。"""
    path = Path(corpus_dir) / "predicates.json"
    if not path.is_file():
        return []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for d in doc.get(SECTION, []) or []:
        if not isinstance(d, dict):
            continue
        key = str(d.get("名") or "")
        if parse(key) is None:
            continue          # 手写坏了就跳过，不静默改成别的意思
        out.append(d)
    return out


def add(corpus_dir: Path, key: str, *, why: str = "", by: str = "agent",
        nodes: dict[str, Any] | None = None, allow_empty: bool = False,
        at: str | None = None) -> dict[str, Any]:
    """追加一条声明。**只追加**：同名已存在 ⇒ 原样保留，返回 `already`。

    `nodes` 给了就当场算命中数；0 命中且 `allow_empty=False` ⇒ 拒收（规矩 2）。
    """
    p = parse(key)
    if p is None:
        return {"ok": False, "reason": "形状不对", "evidence": evidence(nodes or {}, key)}
    field, op, raw = p
    corpus_dir = Path(corpus_dir)
    path = corpus_dir / "predicates.json"
    doc: dict[str, Any] = {}
    if path.is_file():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            doc = {}
    cur = [d for d in (doc.get(SECTION) or []) if isinstance(d, dict)]
    for d in cur:
        if str(d.get("名")) == key:
            return {"ok": True, "already": True, "记录": d,
                    "说明": "同名谓词已存在，**未覆盖**（只追加）"}
    if nodes is not None:
        ev = evidence(nodes, key)
        if not ev.get("可声明") and not allow_empty:
            return {"ok": False, "reason": ev.get("理由"), "evidence": ev}
    rec = {"名": key, "字段": field, "算子": op, "值": raw,
           "来源": by, "依据": why,
           "at": at or clock.now().isoformat(timespec="seconds")}
    cur.append(rec)
    doc.setdefault("_note", [
        "`字段谓词`：在**字段取值**上声明的一等谓词（`字段 算子 值`）。",
        "算子只有 = != >= <= > <；区间请拆两条。",
        "本段由 `ldv/core/declare.py` 追加，**只追加不覆盖**；删掉某条即回到声明前的结构。",
    ])
    doc[SECTION] = cur
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "记录": rec}


def apply(nodes: dict[str, Any], preds: Sequence[dict[str, Any]],
          ledger: dict[str, Any] | None = None) -> int:
    """把声明过的谓词作为键**就地**加进 `Node.keys`，返回加键条数。"""
    added = 0
    for d in preds:
        key = str(d.get("名") or "")
        p = parse(key)
        if p is None:
            if ledger is not None:
                ledger.setdefault("未生效", []).append({"谓词": key, "理由": "形状不对"})
            continue
        field, op, raw = p
        n = 0
        for node in nodes.values():
            if matches(node, field, op, raw) is True:
                node.keys = frozenset(set(node.keys) | {key})
                n += 1
        added += n
        if ledger is not None:
            ledger.setdefault("逐条", []).append({"谓词": key, "加键": n})
    return added
