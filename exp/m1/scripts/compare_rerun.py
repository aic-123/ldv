#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阶段二复验对照：`exp/m1_run1`（首次）vs `exp/m1`（重跑）。

只读两份落盘读数，不重跑任何实验。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

M1 = Path(__file__).resolve().parents[1]
OLD = M1.parent / "m1_run1"
NEW = M1


def text_checks(p: Path, prefix: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(rf"\[({prefix}[^\]]*)\]\s+(PASS|FAIL|RED)", line.strip())
        if m:
            out[m.group(1)] = "FAIL" if m.group(2) == "RED" else m.group(2)
    return out


def json_num(p: Path, keys: list[str]) -> dict[str, object]:
    if not p.exists():
        return {}
    d = json.loads(p.read_text(encoding="utf-8"))
    return {k: d.get(k) for k in keys if k in d}


def main() -> int:
    rows: list[tuple[str, str, object, object]] = []

    for tag, name, pref in (("S", "raw/struct_probe.txt", "S"),
                            ("V14", "raw/v14_accept.txt", "A"),
                            ("V14i", "raw/v14_accept.txt", "I")):
        o, n = text_checks(OLD / name, pref), text_checks(NEW / name, pref)
        for k in list(o) + [x for x in n if x not in o]:
            rows.append((tag, k, o.get(k, "—"), n.get(k, "—")))

    p_keys = ["P1_F1_real_review", "P2_F1_fake_article",
              "P3_F2_real_review", "P4_F2_fake_article"]
    o = json_num(OLD / "v_facts" / "result_rejudge.json", p_keys)
    n = json_num(NEW / "v_facts" / "result_rejudge.json", p_keys)
    for k in p_keys:
        rows.append(("V11", k, o.get(k, "—"), n.get(k, "—")))

    q_keys = ["Q1_G1_review", "Q2_G2_article", "Q3_G3_review(应<=2)",
              "Q4_G4_tiny(应>=3)", "G3_article(默认偏好)"]
    o = json_num(OLD / "v_decide" / "result.json", q_keys)
    n = json_num(NEW / "v_decide" / "result.json", q_keys)
    for k in q_keys:
        rows.append(("V12", k, o.get(k, "—"), n.get(k, "—")))

    print(f"{'组':5s} {'判据':32s} {'首次':>6s} {'重跑':>6s}  一致")
    print("-" * 66)
    same = 0
    for tag, k, o, n in rows:
        ok = o == n
        same += ok
        print(f"{tag:5s} {k:32s} {str(o):>6s} {str(n):>6s}  {'✓' if ok else '✗ 变化'}")
    print("-" * 66)
    print(f"共 {len(rows)} 项，一致 {same}，变化 {len(rows) - same}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
