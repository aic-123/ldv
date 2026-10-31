#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""确定性判分器（禁 LLM）—— E1/E2 共用。

SCORER_VERSION = 4  # v2 去重；v3 递归 records；v4：任意键下 ≥2 个 id 形字符串的列表都算一对
#   （B-0 r1 试点 v3 后仍剩 2/15 全 0：模型把配对放在 `related_ids` 键里 —— 键名不可猜。）
#   （B-0 r1 试点：模型自造 {"problems":[{"items":[{"records":[...]}]}]}，
#    id 藏在 records 里 —— v2 抽不出 ⇒ 全判 0。金标准没测自造嵌套，试点补上了这一课。）。⚠️ 改动本文件 ⇒ 版本号 +1、重算 sha256、**全量重放**已有 run
（DESIGN_V2 §5-T4）；报告里新旧版本并列，旧 run 不删。

判定（与历史实验同口径）：
- 命中 = 一个矛盾对的两个 id 都对（集合匹配）。
- recall = 命中/k；precision = 命中/报告数；空报告在 k>0 时 = 0 分。
- 陷阱（同标题同年份的重复）被报告 ⇒ 记入 precision 分母且不算命中。
- 格式宽容：从任意合理输出里抽 pairs（三种常见形状），抽不出 ⇒ format_ok=False，
  该 run **计入分母**、单独统计（DESIGN_V2 §5-T2）。
"""
from __future__ import annotations

import hashlib
import json
import re

SCORER_VERSION = 4  # v2 去重；v3 递归 records；v4：任意键下 ≥2 个 id 形字符串的列表都算一对
#   （B-0 r1 试点 v3 后仍剩 2/15 全 0：模型把配对放在 `related_ids` 键里 —— 键名不可猜。）
#   （B-0 r1 试点：模型自造 {"problems":[{"items":[{"records":[...]}]}]}，
#    id 藏在 records 里 —— v2 抽不出 ⇒ 全判 0。金标准没测自造嵌套，试点补上了这一课。）


def sha256_of(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


# ── 宽容抽取 ─────────────────────────────────────────────────────────
_ID = r"[A-Za-z]*\d{4,}"


def extract_pairs(text: str) -> tuple[list[dict], bool]:
    """从模型输出里抽矛盾对。返回 (pairs, format_ok)。

    兼容三种形状：
      1. {"pairs": [{"ids": ["a","b"], "year_a": 1, "year_b": 2, ...}]}   ← 规范 schema
      2. [{"ids": [...]}, ...] 或 [["a","b"], ...]                        ← 裸数组
      3. 行式：`S0001 vs S0002：2015 / 2016` 或 `S0001-S0002`             ← 短引导下的自由文本
    抽不出任何结构且文本里连两个 id 都凑不齐 ⇒ format_ok=False。
    """
    text = text.strip()
    if not text:
        return [], False
    # ① 规范 JSON —— **递归走整棵树**：不猜容器键名（模型自造 problems/issues/
    #    items/records 嵌套，v2 只认固定键名 ⇒ 全漏）。收集一切"成对 id"。
    try:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            obj = json.loads(m.group(0))
            collected: list = []
            _walk(obj, collected)
            if collected:
                return collected, True
    except Exception:
        pass
    # ② 裸 JSON 数组
    try:
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if m:
            arr = json.loads(m.group(0))
            if isinstance(arr, list) and arr:
                return _norm(arr), True
    except Exception:
        pass
    # ③ 行式自由文本：一行里出现两个 id 即算一对
    pairs = []
    for line in text.splitlines():
        ids = re.findall(_ID, line)
        if len(ids) >= 2:
            ys = re.findall(r"(20\d\d)", line)
            pairs.append({"ids": ids[:2],
                          "year_a": int(ys[0]) if ys else None,
                          "year_b": int(ys[1]) if len(ys) > 1 else None})
    if pairs:
        return pairs, True
    return [], False


def _ids_of(rec) -> list[str]:
    """从一条 record 里抽 id：dict 取 id/ids 键；字符串取首个 id 形 token。"""
    if isinstance(rec, dict):
        v = rec.get("id") or rec.get("ids") or rec.get("sid")
        if isinstance(v, list):
            return [str(x) for x in v]
        if v is not None:
            m = re.findall(_ID, str(v))
            return m[:1] if m else []
        return []
    if isinstance(rec, str):
        return re.findall(_ID, rec)[:1]
    return []


def _walk(node, out: list, depth: int = 0) -> None:
    """递归收集一切「成对 id」：records 数组（每条一个 id，≥2 条 ⇒ 一对）、
    ids 键、id1/id2 相邻对。深度限 8，防循环引用。"""
    if depth > 8:
        return
    if isinstance(node, dict):
        recs = node.get("records")
        if isinstance(recs, list) and len(recs) >= 2:
            ids = []
            for rec in recs:
                ids.extend(_ids_of(rec)[:1])
            if len(ids) >= 2:
                out.append({"ids": ids[:2], "year_a": None, "year_b": None,
                            "title": node.get("title")})
        # ★ v4：任意键下的列表，只要 ≥2 个元素是 id 形 ⇒ 一对
        #   （键名不可猜：ids / related_ids / pair / involved / … 都出现过）
        for v in node.values():
            if isinstance(v, list) and len(v) >= 2:
                idlike = [str(x) for x in v
                          if isinstance(x, str) and re.fullmatch(_ID, x.strip())]
                if len(idlike) >= 2:
                    out.append({"ids": idlike[:2], "year_a": None,
                                "year_b": None, "title": node.get("title")})
        for v in node.values():
            _walk(v, out, depth + 1)
    elif isinstance(node, list):
        for v in node:
            _walk(v, out, depth + 1)


def _norm(items: list) -> list[dict]:
    out = []
    for it in items:
        if isinstance(it, dict):
            ids = it.get("ids") or it.get("pair") or it.get("id") or []
            if isinstance(ids, str):
                ids = re.findall(_ID, ids)
            out.append({"ids": [str(x) for x in (ids or [])][:2],
                        "year_a": it.get("year_a"), "year_b": it.get("year_b"),
                        "title": it.get("title")})
        elif isinstance(it, (list, tuple)) and len(it) >= 2:
            out.append({"ids": [str(it[0]), str(it[1])],
                        "year_a": it[2] if len(it) > 2 else None,
                        "year_b": it[3] if len(it) > 3 else None, "title": None})
        elif isinstance(it, str):
            ids = re.findall(_ID, it)
            if len(ids) >= 2:
                out.append({"ids": ids[:2], "year_a": None, "year_b": None,
                            "title": None})
    return out


# ── 判分 ─────────────────────────────────────────────────────────────
def score(pairs: list[dict], truth: list[dict], traps: list[dict]) -> dict:
    """truth/traps 的每条 = {"a": sid, "b": sid}。返回 recall/precision/f1 与明细。"""
    def key(d) -> frozenset:
        return frozenset(str(x) for x in d.get("ids") or [])

    want = {frozenset((t["a"], t["b"])) for t in truth}
    trap = {frozenset((t["a"], t["b"])) for t in traps}
    # ★ v2：**去重**。模型把同一对报两遍 = 1 份报告，不是 2 次命中
    #   （v1 的金标准没测重复报告，B-0 r1 试点跑出 R=1.2/2.0 才暴露）。
    got = {key(p) for p in pairs}
    hit = len(got & want)
    false_on_trap = len(got & trap)
    reported = len(got)
    recall = hit / len(want) if want else (1.0 if reported == 0 else 0.0)
    precision = hit / reported if reported else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"recall": round(recall, 4), "precision": round(precision, 4),
            "f1": round(f1, 4), "hit": hit, "k": len(want), "reported": reported,
            "false_on_trap": false_on_trap,
            "missed": sorted(tuple(sorted(w)) for w in want - set(got))[:8]}


def score_belief(ans: dict, *, now: int, orig: int, fix_date: str,
                 fix_reason: str) -> dict:
    """E2 三问 + 陷阱 Q4。ans = {"now","original","changed":{"when","why"},"q4"}。

    Q4 判序：先判"是否指出作废"（q4_refused），**指出作废 ⇒ 不落陷阱**，
    只有"直接给旧值且无任何作废表述"才算命中陷阱。
    """
    def yr(v):
        m = re.search(r"(20\d\d)", str(v))
        return int(m.group(1)) if m else None
    now_g, orig_g = yr(ans.get("now")), yr(ans.get("original"))
    ch = ans.get("changed") or {}
    refused = any(w in str(ans.get("q4") or "")
                  for w in ("作废", "已更正", "勘误", "不再有效", "改为",
                            f"是 {now}", f"是{now}", str(now)))
    return {"q1_now": now_g == now,
            "q2_original": orig_g == orig,
            "q3_when": fix_date in str(ch.get("when") or ""),
            "q3_why": fix_reason in str(ch.get("why") or ""),
            # 陷阱 Q4：直接答旧值 = 落陷阱；**指出作废/更正 = 不算**
            # （金标准自检抓到的：只判 yr==orig 会把"2007，但已作废"误判成落陷阱）
            "q4_refused": refused,
            "q4_trap": (yr(ans.get("q4")) == orig and not refused
                        ) if ans.get("q4") is not None else None}


def compression_summary(rows: list[dict]) -> str:
    """确定性压缩：把已结束任务的助手输出换成定长结论行（禁 LLM，DESIGN_V2 §1）。"""
    lines = []
    for r in rows:
        s = r.get("score") or {}
        lines.append(f"[任务 {r.get('task_idx')}] 报告 {s.get('reported', '?')} 处矛盾，"
                     f"命中 {s.get('hit', '?')}/{s.get('k', '?')}。")
    return "此前任务的结论（过程已压缩）：\n" + "\n".join(lines)
