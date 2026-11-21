"""每一层的**账**：这一层答得出多少项、折掉了多少（`§A5` / `§M4`）。

用法（**cwd = repo**）：
    python ../outputs/_probe_level_ledger.py
    LDV_CORPUS=openalex-small python ../outputs/_probe_level_ledger.py

⚠️ **必须从 `repo/` 跑**（工作区根下有一份 2026-10-05 的陈旧 `ldv/` 副本）。

「对整层结构做解释」在设计里**不是**靠多折一层 —— 是靠每层的
`ans`（这一层答得出的原始项）与 `lost`（折这一层丢掉的项，`§A5` 的账）。
所以「折不动」该看的是：**账是不是空的**。
"""
import os
import sys

sys.path.insert(0, ".")

from ldv.checks._fixtures import load
from ldv.checks._framework import Report
from ldv.checks.abstraction import spec_for
from ldv.checks.multilevel import run_multilevel
from ldv.run_checks import batch_kernel, coverage_of, load_spec_file, corpus_fingerprint

loaded = load()
if loaded is None:
    print("语料找不到 ⇒ 跳过")
    raise SystemExit(0)
nodes, edges, _ = loaded
print(f"语料 {os.environ.get('LDV_CORPUS', '(默认)')}：{len(nodes)} 项 / {len(edges)} 边\n")

doc = load_spec_file()
if not doc:
    print("`view_spec.json` 不在 ⇒ 跳过（外生项未声明）")
    raise SystemExit(0)
which = str(doc.get("方向") or "")
corpus = corpus_fingerprint(nodes, edges)
spec, why = spec_for(doc, which, corpus)
if spec is None:
    print(f"外生声明用不上：{why}")
    raise SystemExit(0)

kernel, plugin, _q, _mk = batch_kernel(which, nodes, edges)
cover = coverage_of(which, nodes, edges)
rep = Report(plugin="(probe)")
levels = run_multilevel(spec, kernel, cover, plugin, rep)

print(f"方向 `{which}`；{why}\n")
print(f"{'层':>3s} {'|U|':>6s} {'|Q|':>6s} {'收缩比':>8s}"
      f" {'答得出':>8s} {'丢':>6s} {'累计账':>8s} {'停因':>12s}")
for i, lv in enumerate(levels):
    print(f"{i:3d} {len(lv.spec.universe):6d} {len(lv.q):6d} {lv.shrink:8.3f}"
          f" {len(lv.ans):8d} {len(lv.lost):6d} {len(lv.ledger):8d}"
          f" {lv.stopped or '—':>12s}")
print()
print("⇒ 「折不动」意味着 `lost` 为空 —— 折叠**没有丢掉任何解释力**，")
print("   也就没有「多一层」能换来的东西。**解释力不在层数上，在账上。**")
