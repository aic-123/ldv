# -*- coding: utf-8 -*-
"""三组实现：A 全塞窗口 / B 检索 top-k / C ldv。**同一个问题、同一套判分。**

共同的问题措辞（**不给 k**，给了就是泄题）：

    下面这份材料里可能存在若干处**自相矛盾**：同一篇论文的年份被写成了两个不同的值。
    请找出**全部**矛盾，输出 JSON：{"pairs": [{"a": "S0123", "b": "S0456"}], "count": N}

判分（三组同一套）：

    recall     = 命中真值对 / k
    precision  = 命中 / 报告数（报告 0 条且 k=0 ⇒ 1.0；k>0 且报告 0 条 ⇒ 0.0）
    localization = 报告的对**两个 id 都对**才算命中（只报一个 id 记「未定位」，单列）
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter

import ds

SYSTEM = "你是严谨的文档审校员。只输出 JSON，不要解释、不要 Markdown 代码块。"

INSTRUCTION = (
    "下面这份材料的每一行都以〔Sxxxx〕开头（这是行 id）。材料里可能存在若干处**自相矛盾**："
    "同一篇论文的年份被写成了两个不同的值。\n"
    "请找出**全部**矛盾，并且**只输出**这样的 JSON：\n"
    '{"pairs": [{"a": "S0123", "b": "S0456"}], "count": 2}\n'
    "注意：同一篇论文被重复陈述、但年份**相同**，不算矛盾。行 id 必须逐字照抄。"
)


def _parse(text: str) -> tuple[list[tuple[str, str]], int | None]:
    """从模型输出里抠 pairs。**宽容**：JSON 优先，失败就正则抓 S 号配对。"""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?|```$", "", t).strip()
    try:
        obj = json.loads(t)
        pairs = []
        for it in (obj.get("pairs") or []):
            a, b = str(it.get("a") or ""), str(it.get("b") or "")
            if a and b:
                pairs.append((a, b))
        return pairs, obj.get("count")
    except Exception:                             # noqa: BLE001 - 模型可能不听话
        ids = re.findall(r"S\d{4}", t)
        return list(zip(ids[0::2], ids[1::2])), None


def score(reported: list[tuple[str, str]], truth: list[dict]) -> dict:
    """同一套判分。`truth` 每条含 `a`/`b`。"""
    gold = {frozenset((t["a"], t["b"])) for t in truth}
    ok = 0
    seen = set()
    for a, b in reported:
        key = frozenset((a, b))
        if key in gold and key not in seen:
            ok += 1
            seen.add(key)
    k = len(truth)
    rep = len(reported)
    return {
        "k": k, "reported": rep, "hit": ok,
        "recall": (ok / k) if k else (1.0 if rep == 0 else 0.0),
        "precision": (ok / rep) if rep else (1.0 if k == 0 else 0.0),
    }


# ── A：全塞窗口 ────────────────────────────────────────────────────────
def group_A(text: str, *, max_tokens: int = 4096, thinking: bool = False) -> dict:
    msg = [{"role": "system", "content": SYSTEM},
           {"role": "user", "content": INSTRUCTION + "\n\n=== 材料开始 ===\n"
            + text + "\n=== 材料结束 ==="}]
    r = ds.chat(msg, max_tokens=max_tokens, json_mode=True, thinking=thinking)
    pairs, count = _parse(r["text"])
    return {"pairs": pairs, "count": count, "usage": r["usage"], "ms": r["ms"],
            "finish": r["finish"], "text": r["text"][:500]}


# ── B：检索 top-k（字符二元组 BM25 + 单轮）──────────────────────────────
def _terms(s: str) -> list[str]:
    s = re.sub(r"〔S\d{4}〕", "", s)
    s = re.sub(r"\s+", "", s)
    return [s[i:i + 2] for i in range(len(s) - 1)]


class BM25:
    def __init__(self, docs: list[dict], k1: float = 1.5, b: float = 0.75):
        self.docs = docs
        self.k1, self.b = k1, b
        self.tf = [Counter(_terms(d["text"])) for d in docs]
        self.len = [sum(c.values()) for c in self.tf]
        self.avg = sum(self.len) / max(1, len(docs))
        df = Counter()
        for c in self.tf:
            df.update(c.keys())
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def top(self, query: str, k: int = 8) -> list[dict]:
        q = _terms(query)
        scored = []
        for i, c in enumerate(self.tf):
            s = 0.0
            for t in q:
                f = c.get(t, 0)
                if not f:
                    continue
                s += self.idf.get(t, 0.0) * f * (self.k1 + 1) / (
                    f + self.k1 * (1 - self.b + self.b * self.len[i] / max(1e-9, self.avg)))
            scored.append((s, i))
        scored.sort(reverse=True)
        return [self.docs[i] for s, i in scored[:k] if s > 0] or self.docs[:k]


def group_B(chunks: list[dict], *, topk: int = 8, max_tokens: int = 4096) -> dict:
    bm = BM25(chunks)
    got = bm.top("同一篇论文 年份 前后不一致 自相矛盾 两个不同的年份", topk)
    ctx = "\n".join(d["text"] for d in got)
    msg = [{"role": "system", "content": SYSTEM},
           {"role": "user", "content": INSTRUCTION
            + f"\n\n（说明：以下是检索到的 {len(got)} 个片段，材料可能不完整）"
            + "\n\n=== 片段开始 ===\n" + ctx + "\n=== 片段结束 ==="}]
    r = ds.chat(msg, max_tokens=max_tokens, json_mode=True)
    pairs, count = _parse(r["text"])
    return {"pairs": pairs, "count": count, "usage": r["usage"], "ms": r["ms"],
            "chunks": len(got), "finish": r["finish"], "text": r["text"][:500]}
