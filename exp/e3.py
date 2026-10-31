#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E3 臂 B：CSV 直给对照（PREREG_E3）。

三臂中的 A（R-L）/ C（R-0）沿用 E1 数据，本脚本只跑 B 臂：
    窗口 = SHORT_GUIDE + CSV 全文 + 材料，单轮裸调用（无工具），
    其余参数与 E1 run_R 完全一致（max_tokens=16000、temperature=0、tries=6）。

    python exp/e3.py --runs 1 --tasks 3        # 试点
    python exp/e3.py --runs 1,2,3              # 全量（deepseek-flash）

判分 exp/score.py v4；落盘 exp/runs/E3-<ts>.jsonl，字段与 E1 对齐。
B 臂窗口额外含 CSV（~7k token），落盘 csv_tokens 供成本核算。
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
import corpus        # noqa: E402
import llm           # noqa: E402
import score         # noqa: E402
from e1 import (N_TASKS, SHORT_GUIDE, TASK_BARE, build_task, est_tokens,
                load_pool)  # noqa: E402

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"
CSV_PATH = HERE / "e3_rule_table.csv"  # B 臂主结果
RULES_CSV = HERE / "e3_rule_table_rules.csv"  # B′ 诊断臂（PREREG_E3 V2）


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="1,2,3")
    ap.add_argument("--tasks", type=int, default=N_TASKS)
    ap.add_argument("--model", default="deepseek-flash")
    ap.add_argument("--csv", default="b", choices=["b", "bp"], help="b=全节点（B 臂） bp=规则-only（B′ 诊断臂）")
    a = ap.parse_args()
    llm.set_model(a.model)

    csv_path = CSV_PATH if a.csv == "b" else RULES_CSV
    csv_text = csv_path.read_text(encoding="utf-8")
    csv_tokens = est_tokens([{"content": csv_text}])

    POOL = load_pool()
    print(f"唯一标题池：{len(POOL)} 篇｜模型 {a.model}｜csv_tokens={csv_tokens}", flush=True)

    tag = f"E3-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    RUNS.mkdir(exist_ok=True)
    out_path = RUNS / f"{tag}.jsonl"
    n_done = 0

    with out_path.open("a", encoding="utf-8") as fh:
        for run in [int(x) for x in a.runs.split(",") if x.strip()]:
            for task_idx in range(1, a.tasks + 1):
                t0 = time.perf_counter()
                mat = build_task(POOL, task_idx, run)
                user = (f"{SHORT_GUIDE}\n\n以下是规则与登记表（CSV）：\n{csv_text}\n\n"
                        f"{mat['text']}\n\n{TASK_BARE}\n\n请以 JSON 报告你发现的问题。")
                win_tokens = est_tokens([{"content": user}])
                r = llm.chat([{"role": "user", "content": user}],
                             max_tokens=16000, temperature=0, tries=6)
                raw = r.get("text") or ""
                pairs, format_ok = score.extract_pairs(raw)
                sc = score.score(pairs, mat["truth"], mat["traps"])
                u = r.get("usage") or {}
                rec = {"exp": "E3", "arm": ("B-csv" if a.csv == "b" else "Bp-rules"),
                       "group": ("B-csv" if a.csv == "b" else "Bp-rules"),
                       "task_idx": task_idx, "run": run, "model": a.model,
                       "n_chars": mat["chars"], "task_tokens": mat["task_tokens"],
                       "csv_tokens": csv_tokens, "win_tokens": win_tokens,
                       "truth_k": len(mat["truth"]), "n_traps": len(mat["traps"]),
                       "SCORER_VERSION": score.SCORER_VERSION,
                       "ts": datetime.now().isoformat(timespec="seconds"),
                       "raw_output": raw, "finish": r.get("finish"),
                       "format_ok": format_ok, "latency_s": round(time.perf_counter() - t0, 1),
                       "tokens": {"in": u.get("in", 0), "out": u.get("out", 0),
                                  "cached": u.get("cached", 0)},
                       "guide": "csv", "reported": len(pairs), **sc}
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
                n_done += 1
                print(f"B r{run} t{task_idx:02d}: F1={rec['f1']:.3f} P={rec['precision']:.3f} "
                      f"R={rec['recall']:.3f}｜报 {rec['reported']} 中 {sc.get('hit', '?')}"
                      f"｜陷阱 {sc.get('false_on_trap', 0)}｜fin={r.get('finish')}")
    print(f"\n完成 {n_done} 条 → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
