"""在真语料上跑一遍多层折叠（`§M0`–`§M6`），看基线是不是绿的。

用法（**cwd = repo**）：
    python ../outputs/_probe_multilevel.py
    LDV_CORPUS=openalex-small python ../outputs/_probe_multilevel.py

⚠️ **必须从 `repo/` 跑**（工作区根下有一份 2026-10-05 的陈旧 `ldv/` 副本）。
"""
import os
import sys

sys.path.insert(0, ".")

from ldv.checks._fixtures import load, coverage_of
from ldv.checks.abstraction import VIEW_SPEC_PATH, load_spec_file, spec_for
from ldv.checks._framework import Report
from ldv.checks.coverage import corpus_fingerprint
from ldv.checks.multilevel import (MULTILEVEL_CODES, render_levels, run_multilevel)
from ldv.run_checks import batch_kernel

loaded = load()
if loaded is None:
    print("语料找不到 ⇒ 跳过（不是通过）")
    raise SystemExit(0)
nodes, edges, _ = loaded

doc = load_spec_file()
which = str(doc.get("方向") or "keyset")
spec, why = spec_for(doc, which, corpus_fingerprint(nodes, edges))
print(f"方向 {which}｜{why}")
if spec is None:
    print("⇒ 这一层本趟**判不了**（跳过 ≠ 通过）：声明是别的语料/方向上的。")
    raise SystemExit(0)

kernel, plugin, _q, _mk = batch_kernel(which, nodes, edges)
cover = coverage_of(which, nodes, edges)
rep = Report(plugin="(多层)", expects=MULTILEVEL_CODES)
levels = run_multilevel(spec, kernel, cover, plugin, rep)

print(render_levels(levels))
print()
print(rep.render())
print(f"退出码 {1 if rep.red else 0}")
