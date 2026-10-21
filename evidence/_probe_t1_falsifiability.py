"""探针 2：`T1` 在**正确插件**上能不能红？—— 这决定 `§C1` 的默认值是不是把这一层做成了空壳。

如果正确插件上「后看的块里有真命中」不可能成立，那么：
    跳过「后看」的块 ⇒ 一个候选都不丢 ⇒ **早停是安全的**
⇒ 而 `§C1` 默认值禁止早停 ⇒ 顺序**永远不改任何东西** ⇒ 这一层无收益。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "C:/Users/19253/WorkBuddy AI/2026-10-05-14-16-35/repo")

from ldv.checks._framework import Report          # noqa: E402
from ldv.checks.retrieval import (                # noqa: E402
    _early_stop, _synth, _synth_spec, t1_equivalence, RETRIEVER_CODES,
)
from ldv.retriever import as_bar, retrieve        # noqa: E402
from ldv.core.tri import Tri                      # noqa: E402

for label, losing in (("正确插件（满足 §I2）", False), ("_LosingMerge（丢成员）", True)):
    kernel, plugin, q = _synth(with_losing_merge=losing)
    spec = _synth_spec(kernel)
    full = retrieve(kernel, plugin, {"keyset": as_bar(q)}, spec=spec)
    late_cover = frozenset().union(*full.late) if full.late else frozenset()
    lost = sorted(full.candidates & late_cover)

    rep_ok = Report(plugin="x", expects=RETRIEVER_CODES)
    t1_equivalence(rep_ok, full, kernel)
    rep_es = Report(plugin="x", expects=RETRIEVER_CODES)
    t1_equivalence(rep_es, _early_stop(full), kernel)

    print(f"{label}")
    print(f"    候选 {len(full.candidates)}｜后看块 {len(full.late)}｜"
          f"后看块里的真命中 {len(lost)}")
    print(f"    真实现      T1 = {rep_ok.assertions[0].result}")
    print(f"    「先看就收工」T1 = {rep_es.assertions[0].result}"
          f"{'   ← 早停在这里是**安全的**' if rep_es.assertions[0].result is Tri.YES else ''}")
    print()
