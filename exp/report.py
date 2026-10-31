# -*- coding: utf-8 -*-
"""把 `exp/runs/*.jsonl` 汇总成表（含方差与成本）。判分逻辑与 `groups.score` 同一套。

    python exp/report.py            # 打最新一批
    python exp/report.py --all      # 全部批次
"""
from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

#: 单价（元 / 百万 token，2026-10-29 实测：空闲时段 deepseek-flash）
IN_MISS, IN_HIT, OUT = 1.0, 0.02, 4.0


def cost(usage: dict) -> float:
    miss = usage.get("prompt_cache_miss_tokens", usage.get("prompt_tokens", 0))
    hit = usage.get("prompt_cache_hit_tokens", 0)
    out = usage.get("completion_tokens", 0)
    return (miss * IN_MISS + hit * IN_HIT + out * OUT) / 1_000_000


def load(pattern: str = "runs/*.jsonl") -> list[dict]:
    rows = []
    for p in sorted(glob.glob(str(HERE / pattern))):
        for ln in open(p, encoding="utf-8"):
            if ln.strip():
                rows.append(json.loads(ln))
    return rows


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--file", default="")
    a = ap.parse_args(argv[1:])
    if a.file:
        rows = [json.loads(ln) for ln in open(a.file, encoding="utf-8") if ln.strip()]
    else:
        pat = "runs/*.jsonl" if a.all else sorted(glob.glob(str(HERE / "runs/*.jsonl")))[-1].split("runs")[-1].lstrip("\\/")
        rows = load(pat if isinstance(pat, str) else "runs/*.jsonl")

    ns = sorted({r["n"] for r in rows})
    print(f"批次 {len(rows)} 条记录｜n={ns}")
    total = 0.0
    for n in ns:
        chars = max(r["stats"]["chars"] for r in rows if r["n"] == n)
        print(f"\n### n={n}（材料 ≈{chars} 字）")
        print("| k | A recall（min~max） | A prec | A 误报数 | B recall | C recall | A tokens | A 秒 |")
        print("|---|---|---|---|---|---|---|---|")
        for k in sorted({r["k"] for r in rows if r["n"] == n}):
            sub = [r for r in rows if r["n"] == n and r["k"] == k]
            def vals(g, key):
                return [r[g][key] for r in sub if g in r]
            def fmt(xs):
                if not xs:
                    return "—"
                m = sum(xs) / len(xs)
                return f"{m:.2f}（{min(xs):.2f}~{max(xs):.2f}）"
            ar = vals("A", "recall")
            ap_ = vals("A", "precision")
            fp = [r["A"]["reported"] - r["A"]["hit"] for r in sub if "A" in r]
            toks = [r["A"]["usage"].get("prompt_tokens") for r in sub if "A" in r]
            secs = [r["A"]["ms"] / 1000 for r in sub if "A" in r]
            print(f"| {k} | {fmt(ar)} | {fmt(ap_)} | "
                  f"{sum(fp)/len(fp):.0f} | {fmt(vals('B','recall'))} | "
                  f"{fmt(vals('C','recall'))} | "
                  f"{int(sum(toks)/len(toks)) if toks else 0} | "
                  f"{sum(secs)/len(secs):.0f} |")
            for r in sub:
                for g in ("A", "B", "C"):
                    if g in r and "usage" in r[g]:
                        total += cost(r[g]["usage"])
    print(f"\n本批 API 花费：**¥{total:.2f}**（按 ¥1/M 未命中 · ¥0.02/M 命中 · ¥4/M 输出）")
    print("⚠️ 同一份材料重复跑会命中**前缀缓存**（实测 96.8k/96.9k 命中）⇒ 冷启动口径约贵 50 倍；"
          "\n   报成本时要标「冷」还是「热」，否则会低估一个数量级。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv))
