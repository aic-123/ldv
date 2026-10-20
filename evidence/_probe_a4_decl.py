"""量一遍 `§A4` 该在 `view_spec.json` 里声明哪些读数 —— 逐条实测，不靠记。

跑法（必须在 repo 里、以模块方式）：

    cd repo
    PYTHONIOENCODING=utf-8 PYTHONUTF8=1 \
      C:/Users/19253/.workbuddy-ai/binaries/python/versions/3.13.12/python.exe \
      ../outputs/_probe_a4_decl.py
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "repo"
sys.path.insert(0, str(ROOT))

from ldv.checks._fixtures import coverage_of, load  # noqa: E402
from ldv.checks.abstraction import (  # noqa: E402
    READINGS, SUMMARIES,
    build_views, judge_reading, load_spec_file, reading_ctx, spec_for,
    subtree_of,
)
from ldv.core.views import view_parts  # noqa: E402

loaded = load()
nodes, edges, _ = loaded
from ldv.run_checks import batch_kernel, corpus_fingerprint  # noqa: E402

doc = load_spec_file()
spec, why = spec_for(doc, str(doc.get("方向")), corpus_fingerprint(nodes, edges))
print("spec:", why)
kernel, plugin, _, _ = batch_kernel(str(doc["方向"]), nodes, edges)
cover = coverage_of(str(doc["方向"]), nodes, edges)
vs = build_views(kernel, spec, cover, plugin)

sub = lambda d: subtree_of(kernel, d)
parts = view_parts(vs.q, sub)
ctx = reading_ctx(kernel, spec)

print(f"\n块数 {len(vs.q)}；有下层的块 {sum(1 for p in parts.values() if len(p) >= 2)}")
for b, ps in sorted(parts.items(), key=lambda kv: sorted(kv[0])):
    print(f"  {sorted(b)}  → {len(ps)} 个部件")

print("\n=== 候选声明逐条实测 ===")
CAND = [
    {"名": "计数", "类别": "distributive", "G": "取和"},
    {"名": "计数", "类别": "distributive", "G": "取最大"},   # 期望红
    {"名": "和", "类别": "distributive", "G": "取和"},
    {"名": "最大", "类别": "distributive", "G": "取最大"},
    {"名": "最大", "类别": "distributive", "G": "取和"},     # 期望红
    {"名": "平均", "类别": "algebraic", "摘要": "计数与和", "H": "和除计数"},
    {"名": "平均", "类别": "distributive", "G": "取平均"},   # 期望红
    {"名": "中位数", "类别": "distributive", "G": "取平均"},  # 期望红
]
for d in CAND:
    ok, why2 = judge_reading(d["名"], d["类别"], d, parts, ctx)
    print(f"  {'过' if ok else '红'}  {d}  —— {why2}")

print("\n=== holistic 见证搜索 ===")


def search(read_name, summary_name, pool):
    f, _ = READINGS[read_name]
    sf = SUMMARIES[summary_name]
    seen = {}
    for S in pool:
        key = sf(S, ctx)
        seen.setdefault(key, []).append(S)
    hits = []
    for key, group in seen.items():
        for A, B in itertools.combinations(group, 2):
            fa, fb = f(A, ctx), f(B, ctx)
            if abs(float(fa) - float(fb)) > 1e-9:
                hits.append((A, B, key, fa, fb))
                break
        if hits:
            break
    return hits


# 候选池：各块、各部件、各 reach，以及随机子集
pool = set()
for b in vs.q:
    pool.add(frozenset(b))
for ps in parts.values():
    for p in ps:
        pool.add(frozenset(p))
for k in range(1, 5):
    for combo in itertools.combinations(sorted(spec.universe), k):
        pool.add(frozenset(combo))
pool = sorted(pool, key=lambda s: (len(s), sorted(s)))
print(f"候选池 {len(pool)} 个集合")

for rn, sn in [("中位数", "计数与和"), ("覆盖计数", "计数与和"), ("最大", "计数与和")]:
    hits = search(rn, sn, pool)
    if hits:
        A, B, key, fa, fb = hits[0]
        print(f"  {rn}: 见证 {sorted(A)} vs {sorted(B)}｜摘要 {key} 相同｜读数 {fa} vs {fb}")
    else:
        print(f"  {rn}: 池里没找到见证")

# 关键的那一对（记忆里是 D1/D11 vs D1/D21，这里现算确认）
for A, B in [(frozenset({"D1", "D11"}), frozenset({"D1", "D21"})),
             (frozenset({"D1", "D11"}), frozenset({"D1", "D22"}))]:
    f, _ = READINGS["覆盖计数"]
    sf = SUMMARIES["计数与和"]
    print(f"  {sorted(A)} vs {sorted(B)}: 摘要 {sf(A, ctx)} / {sf(B, ctx)}"
          f"｜覆盖计数 {f(A, ctx)} / {f(B, ctx)}")
