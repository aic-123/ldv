#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验二：干扰鲁棒性曲线（预注册见 PREREG2.md，先于任何 API 调用）。

    python exp/exp2.py --ratios 0,30,60,90 --seeds 1,2 --groups ABC

相关材料固定 250 条论文登记；噪声 = 讨论班日程（同形状、含年份陷阱）。
问题：列出全部论文登记里年份 ≤ T 的 id（约 30 条，ground truth 稳定）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import corpus   # noqa: E402
import ds       # noqa: E402

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"

N_RELEVANT = 250
N_ANSWER = 30          # 目标答案条数（由阈值 T 实现）

PAPER = "〔{sid}〕论文登记：《{title}》发表于 {year} 年，载于 {venue}。"
NOISE = "〔B{sid}〕讨论班日程：研读《{title}》，{year} 年秋季安排，地点 {venue}。"

FINAL_Q = ("以上材料里有「论文登记」和「讨论班日程」两种记录。"
           "只数**论文登记**：列出其中年份 ≤ {t} 年的全部论文 id（〔P…〕方括号里的编号）。"
           "只输出 JSON：{{\"ids\": [\"…\"]}}，不要解释。")


def build(papers: list[dict], *, ratio: int, seed: int) -> tuple[str, dict, int]:
    """返回 (材料文本, ground truth, 阈值 T)。相关 250 条固定，噪声按比例。"""
    rnd = __import__("random").Random(seed)
    pool = [p for p in papers if p["year"] <= 2021]
    pool = sorted(pool, key=lambda x: x["id"])
    relevant = pool[:N_RELEVANT]
    years = sorted(p["year"] for p in relevant)
    t = years[max(0, N_ANSWER - 1)]                    # 阈值 ⇒ 恰好 ~30 条答案
    truth_ids = sorted(p["id"] for p in relevant if p["year"] <= t)
    n_noise = round(N_RELEVANT * ratio / max(1, 100 - ratio)) if ratio < 100 else 0
    noise_pool = pool[N_RELEVANT:]
    lines = [PAPER.format(sid=p["id"], title=p["title"][:56], year=p["year"],
                          venue=p["venue"][:28]) for p in relevant]
    for i in range(n_noise):
        q = noise_pool[(i * 7 + seed) % len(noise_pool)]
        ny = 1995 + ((i * 13 + seed) % 27)             # 噪声年份含 ≤T 的（陷阱）
        lines.append(NOISE.format(sid=f"{i:07d}", title=q["title"][:56],
                                  year=ny, venue=q["venue"][:20]))
    rnd.shuffle(lines)
    text = "\n".join(lines)
    return text, {"ids": truth_ids, "threshold": t}, t


def score_f1(got: set[str], want: set[str]) -> dict:
    tp = len(got & want)
    prec = tp / len(got) if got else 0.0
    rec = tp / len(want) if want else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return {"precision": round(prec, 3), "recall": round(rec, 3), "f1": round(f1, 3)}


def _chat(msgs, *, max_tokens: int) -> dict:
    time.sleep(2.0)
    return ds.chat(msgs, max_tokens=max_tokens, temperature=0, tries=6)


def group_A(text: str, want: set[str], t: int, *, max_tokens: int = 16000) -> dict:
    r = _chat([{"role": "user", "content": text + "\n\n" + FINAL_Q.format(t=t)}],
              max_tokens=max_tokens)
    # id 形如 W1915329329 / B0000122（字母 + ≥6 位数字）；噪声 id 也匹配 ——
    # 模型把讨论班当论文收进来会掉精度，这正是陷阱的判定方式
    ids = set(re.findall(r"\b[A-Z]\d{6,}\b", r["text"]))
    s = score_f1(ids, want)
    return {**s, "n_got": len(ids), "finish": r["finish"], "ms": r["ms"],
            "usage": r["usage"], "input_chars": len(text)}


def group_B(text: str, want: set[str], t: int) -> dict:
    """字面检索 top-8 块 → 模型作答（与实验一同法）。"""
    size = 1200
    chunks = [text[i:i + size] for i in range(0, len(text), size)]

    def bg(s):
        s = re.sub(r"\s", "", s)
        return {s[i:i + 2] for i in range(len(s) - 1)}
    qb = bg(f"论文登记 年份 {t} 年及以前")
    scored = sorted(range(len(chunks)), key=lambda i: -len(qb & bg(chunks[i])))
    ctx = "\n".join(chunks[i] for i in scored[:8])
    r = _chat([{"role": "user", "content": ctx + "\n\n" + FINAL_Q.format(t=t)}],
              max_tokens=4000)
    ids = set(re.findall(r"\b[A-Z]\d{6,}\b", r["text"]))
    return {**score_f1(ids, want), "n_got": len(ids), "finish": r["finish"]}


# ── C 组：结构查询 + 脚本侧过滤（机制作答，0 token）──────────────────
NODE = """---
id: "{sid}"
type: "{kind}"
title: "{sid}"
evidence_status: "原始记录"
relations: []
---

{sid} 的登记原文：{text}
"""


def group_C(papers: list[dict], root: Path, *, seed: int, ratio: int,
            want: set[str], t: int) -> dict:
    import entry
    text, _truth, _t = build(papers, ratio=ratio, seed=seed)
    tenant = f"exp2-s{seed}-r{ratio}"
    t0 = time.perf_counter()
    nd = root / tenant / "nodes"
    nd.mkdir(parents=True, exist_ok=True)
    dd = root / tenant / "derived"
    dd.mkdir(parents=True, exist_ok=True)
    recs, kinds, years = [], {}, {}
    for line in text.splitlines():
        m = re.match(r"〔([A-Za-z]\w+)〕(论文登记|讨论班日程)：", line)
        if not m:
            continue
        sid, kind = m.group(1), "论文" if m.group(2) == "论文登记" else "讨论班"
        ym = re.search(r"(\d{4}) 年", line)
        recs.append((sid, kind, ym.group(1) if ym else "?", line))
    for sid, kind, _y, line in recs:
        (nd / f"{sid}.md").write_text(NODE.format(sid=sid, kind=kind, text=line),
                                      encoding="utf-8")
        kinds[sid] = kind
    (dd / "year.json").write_text(json.dumps(
        {"_meta": {"field": "year",            # ⚠️ 缺 field 装载器会**静默跳过**（实测）
                   "what": "实验二：年份升成键", "why": "聚合问题要按键过滤，不靠模型扫全库",
                   "derived_from": "记录行", "generated_by": "exp/exp2.py"},
         "values": {sid: y for sid, _k, y, _l in recs}}, ensure_ascii=False),
        encoding="utf-8")
    app = entry.App(root=nd)
    out = app.query({"plugin": "keyset", "require": ["type=论文"], "forbid": []})
    ids = set(out.get("answer_ids") or [])
    got = {i for i in ids
           if (next((k.split("=", 1)[1] for k in (app.keys_of.get(i) or ())
                     if k.startswith("year=")), "?")).isdigit()
           and int(next(k.split("=", 1)[1] for k in (app.keys_of.get(i) or ())
                        if k.startswith("year="))) <= t}
    return {**score_f1(got, want), "n_nodes": len(recs), "n_paper": len(ids),
            "n_got": len(got), "wall_s": round(time.perf_counter() - t0, 1),
            "tokens": 0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ratios", default="0,30,60,90")
    ap.add_argument("--seeds", default="1,2")
    ap.add_argument("--groups", default="ABC")
    ap.add_argument("--papers", type=int, default=4037)
    a = ap.parse_args()

    papers = corpus.load_papers(a.papers)
    RUNS.mkdir(exist_ok=True)
    tag = datetime.now().strftime("run2-%Y%m%d-%H%M%S")
    out_path = RUNS / f"{tag}.jsonl"
    root = HERE.parent / "exp2_tenants"
    root.mkdir(exist_ok=True)

    for ratio in [int(x) for x in a.ratios.split(",") if x.strip()]:
        for seed in [int(x) for x in a.seeds.split(",") if x.strip()]:
            text, truth, t = build(papers, ratio=ratio, seed=seed)
            want = set(truth["ids"])
            rec = {"exp": 2, "ratio": ratio, "seed": seed, "at": tag,
                   "n_chars": len(text), "n_answer": len(want), "threshold": t}
            print(f"== ratio={ratio}% seed={seed}｜材料 {len(text)} 字｜"
                  f"答案 {len(want)} 条（≤{t} 年）==")
            if "A" in a.groups:
                r = group_A(text, want, t)
                rec["A"] = r
                print(f"  A: F1={r['f1']} P={r['precision']} R={r['recall']}"
                      f"｜报 {r['n_got']}｜finish={r['finish']}｜{r['ms']/1000:.0f}s")
            if "B" in a.groups:
                r = group_B(text, want, t)
                rec["B"] = r
                print(f"  B: F1={r['f1']}｜报 {r['n_got']}｜finish={r['finish']}")
            if "C" in a.groups:
                r = group_C(papers, root, seed=seed, ratio=ratio,
                            want=want, t=t)
                rec["C"] = r
                print(f"  C: F1={r['f1']}｜{r['n_nodes']} 节点｜{r['wall_s']}s｜0 token")
            with out_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"记录 → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
