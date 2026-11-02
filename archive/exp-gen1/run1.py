# -*- coding: utf-8 -*-
"""实验一：矛盾密度递增。跑矩阵、落 JSONL、打印曲线。

    python exp/run1.py --n 600 --ks 0,5,20 --runs 1 --groups AB

⚠️ 结果**每次都落到 `exp/runs/`**（含 prompt 字数、tokens、耗时、原始输出），
   单次结果不许只留在屏幕里 —— 方差要靠它算。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import corpus
import groups


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=600, help="事实条数（≈材料规模）")
    ap.add_argument("--ks", default="0,5,20", help="矛盾密度（逗号分隔）")
    ap.add_argument("--traps", type=int, default=10)
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--topk", type=int, default=8)
    ap.add_argument("--thinking", action="store_true",
                    help="A 组开思考模式（对照组：预注册说它是唯一可能推翻结论的变量）")
    ap.add_argument("--max-tokens", type=int, default=16000,
                    help="A 的输出预算（4096 会在 k≥20 时被截断 —— 实测 finish=length）")
    ap.add_argument("--groups", default="ABC")
    ap.add_argument("--root", default="", help="C 组建租户的根目录（默认临时目录）")
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv[1:])

    papers = corpus.load_papers(a.n)
    if len(papers) < a.n:
        print(f"⚠ 只取到 {len(papers)} 篇（要 {a.n}）")
    out_path = Path(a.out) if a.out else HERE / "runs" / (
        time.strftime("run1-%Y%m%d-%H%M%S") + ".jsonl")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"落盘：{out_path}")

    root = Path(a.root) if a.root else Path(tempfile.mkdtemp(prefix="exp1-"))
    rows = []
    for k in [int(x) for x in a.ks.split(",") if x.strip()]:
        mat = corpus.make(papers, k, traps=a.traps, seed=a.seed)
        st = mat["stats"]
        ch = corpus.chunks(mat["text"])
        print(f"\n── k={k}｜{st['n_facts']} 句 / {st['chars']} 字 / {len(ch)} 块"
              f"｜间距 {st['min_gap']}~{st['max_gap']} 句")
        for run in range(a.runs):
            rec = {"n": a.n, "k": k, "run": run, "stats": st, "seed": a.seed}
            if "A" in a.groups:
                t0 = time.perf_counter()
                r = groups.group_A(mat["text"], max_tokens=a.max_tokens, thinking=a.thinking)
                s = groups.score(r["pairs"], mat["truth"])
                rec["A"] = {**s, "usage": r["usage"], "ms": r["ms"],
                            "thinking": a.thinking,
                            "wall": round(time.perf_counter() - t0, 1),
                            "finish": r["finish"], "raw": r["text"]}
                print(f"  A run{run}: recall {s['recall']:.2f} prec {s['precision']:.2f}"
                      f"｜报 {s['reported']} 中 {s['hit']}｜"
                      f"prompt {r['usage'].get('prompt_tokens')} tok｜{r['ms']/1000:.0f}s")
            if "B" in a.groups:
                r = groups.group_B(ch, topk=a.topk)
                s = groups.score(r["pairs"], mat["truth"])
                rec["B"] = {**s, "usage": r["usage"], "ms": r["ms"],
                            "chunks": r["chunks"], "finish": r["finish"], "raw": r["text"]}
                print(f"  B run{run}: recall {s['recall']:.2f} prec {s['precision']:.2f}"
                      f"｜报 {s['reported']} 中 {s['hit']}｜块 {r['chunks']}｜"
                      f"prompt {r['usage'].get('prompt_tokens')} tok")
            if "C" in a.groups:
                import ldv_group
                r = ldv_group.group_C(mat, root)
                s = groups.score(r["pairs"], mat["truth"])
                rec["C"] = {**s, "queries": r["queries"], "q_p50_ms": r["q_p50_ms"],
                            "q_total_s": r["q_total_s"], "build_s": r["build_s"],
                            "load_s": r["load_s"], "tokens": 0}
                print(f"  C run{run}: recall {s['recall']:.2f} prec {s['precision']:.2f}"
                      f"｜报 {s['reported']} 中 {s['hit']}｜{r['queries']} 次查询"
                      f"（{r['q_total_s']}s）｜建 {r['build_s']}s 载 {r['load_s']}s｜0 token")
            with out_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            rows.append(rec)

    # 曲线
    print("\n密度曲线（recall / precision）")
    print("  k   A.recall  A.prec   B.recall  B.prec  C.recall  C.prec   A_prompt_tok")
    for k in sorted({r["k"] for r in rows}):
        sub = [r for r in rows if r["k"] == k]
        def avg(g, key):
            xs = [r[g][key] for r in sub if g in r]
            return sum(xs) / len(xs) if xs else float("nan")
        tok = [r["A"]["usage"].get("prompt_tokens") for r in sub if "A" in r]
        print(f"  {k:<3} {avg('A','recall'):8.2f} {avg('A','precision'):8.2f} "
              f"{avg('B','recall'):9.2f} {avg('B','precision'):7.2f} "
              f"{avg('C','recall'):9.2f} {avg('C','precision'):7.2f} "
              f"{int(sum(tok)/len(tok)) if tok else 0:10}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
