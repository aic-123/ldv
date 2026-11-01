#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S5 诊断（不改判据）：A 的每个方向成员集，在 AB 中是否仍有 ≥90% 被某方向包含。

目的：区分「加料推翻旧结构」与「谓词字面漂移（forbid 集合随材料扩张）」。
输出诊断读数，不产生 PASS/FAIL，不参与判定。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
import entry as E  # noqa: E402

root = Path(tempfile.mkdtemp(prefix="m1-s5diag-")) / "_data" / "oa"
shutil.copytree(REPO / "_data" / "openalex-citations", root)
nodes_dir = root / "nodes"
files = sorted(nodes_dir.glob("*.md"))
hold = root.parent / "hold_b"
hold.mkdir(parents=True, exist_ok=True)
for p in files[3000:]:
    shutil.move(str(p), str(hold / p.name))

aA = E.App(root=str(nodes_dir))
kA = aA.kernels["keyset"]
memA = {d.did: frozenset(kA.members_of(d)) for d in kA.all_directions()}
predA = {}
for d in kA.all_directions():
    req, forb = d.payload
    predA[d.did] = (tuple(sorted(req)), tuple(sorted(forb)))

for p in sorted(hold.glob("*.md")):
    shutil.copy2(p, nodes_dir / p.name)
aAB = E.App(root=str(nodes_dir))
kAB = aAB.kernels["keyset"]
memAB = {d.did: frozenset(kAB.members_of(d)) for d in kAB.all_directions()}

rows = []
for did, m in memA.items():
    if not m:
        continue
    # 最小超集：在所有包含 m_A 的 AB 方向里取 size 最小者（排除根/过大超集）
    supers = [(did2, m2) for did2, m2 in memAB.items() if m <= m2]
    if supers:
        best_did, best_m2 = min(supers, key=lambda kv: len(kv[1]))
        rows.append({"did_A": did, "size_A": len(m), "kind": "superset",
                     "best_did_AB": best_did, "size_AB": len(best_m2),
                     "growth": round(len(best_m2) / len(m), 3)})
    else:
        # 没有超集：报最小缺失
        miss_best, best_did = len(m), None
        for did2, m2 in memAB.items():
            miss = len(m - m2)
            if miss < miss_best:
                miss_best, best_did = miss, did2
        rows.append({"did_A": did, "size_A": len(m), "kind": "no_superset",
                     "best_did_AB": best_did, "min_missing": miss_best,
                     "missing_ratio": round(miss_best / len(m), 3)})

sup = [r for r in rows if r["kind"] == "superset"]
bounded = [r for r in sup if r["growth"] <= 1.4]
print(json.dumps({
    "n_dirs_A": len(rows),
    "有超集(只增不减成立)": len(sup),
    "ratio_superset": round(len(sup) / len(rows), 4),
    "超集且增长<=1.4x": len(bounded),
    "ratio_bounded": round(len(bounded) / len(rows), 4),
    "增长分布": sorted({r["growth"] for r in sup})[:8],
    "no_superset": [r for r in rows if r["kind"] == "no_superset"][:3],
    "note": "诊断项，不参与 PASS/FAIL；最小超集口径，排除根方向的平凡解",
}, ensure_ascii=False, indent=1))
