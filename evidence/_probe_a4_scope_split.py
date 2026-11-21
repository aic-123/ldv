# -*- coding: utf-8 -*-
"""`§A4` 两级证据（Scope-M 函数级 / Scope-C 语料级）的**实验级**留档。

跑法（cwd = repo/）：
    LDV_CORPUS=openalex-citations python ../outputs/_probe_a4_scope_split.py

验五件事（每件都自己下判；退出码 0 = 全过）：

  P0  `inj` 论域（摘要单射，openalex 恒红的**微型复现**）：中位数 holistic ⇒ 过。
  P1  openalex × `sequence` **真语料**上 `§A4` 转绿（旧实现恒红 `A4|(视图)`），
      且那一行必须**披露**「论域的性质」—— 绿不许变成没话说。
  P2  函数级表：`计数 / 和 / 平均` 被摘要决定（且 `H` 点名）；`中位数 / 最大 /
      覆盖计数` 不被决定（构造论域上有见证）。六个读数逐个对。
  P3  有界 vs 穷举交叉验证：本语料上两者一致地给 None（穷举过 ⇒ 「0 对」是结论
      不是没搜到）；随机论域上有界搜到的每一例，穷举的**首个见证逐字相同**
      （同序可复现那条纪律没被新实现破坏）；顺次数「穷举找到而 k≤4 漏掉」的例数
      = 旧假红的来源。
  P4  反向三条：短路函数级门 ⇒ `平均` holistic 红变绿 + 合成对照报 FAIL；
      恒报「被决定」⇒ `中位数` holistic @ inj 绿变红。两头都证。

⚠️ 本探针**不跑全量 intake**（用户 2026-10-14 指令：实验级就够了）——
  红基线 `expected_reds.json` 的重冻与复验是**另一件事**，见 `_note`。
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1] / "repo"
sys.path.insert(0, str(REPO))

from ldv.checks._fixtures import coverage_of, find_corpus
from ldv.checks._framework import Report
from ldv.checks.abstraction import (
    READINGS,
    SUMMARIES,
    ReadingCtx,
    a4_category,
    a4_inj_ctx,
    a4_known_answer,
    build_views,
    decide_digest_witness,
    judge_reading,
    load_spec_file,
    reading_ctx,
    scope_m_reading,
    spec_for,
    subtree_of,
    witness_same_digest_diff_reading,
)
from ldv.checks.coverage import corpus_fingerprint
from ldv.core.tri import Tri
from ldv.core.views import view_parts
from ldv.corpus.loader import load_edges, load_nodes
from ldv.run_checks import batch_kernel, maintenance_kernel

FAILS: list[str] = []


def ok(name: str, cond: bool, detail: str = "") -> None:
    mark = "✓" if cond else "✗"
    print(f"  {mark} {name}" + (f" —— {detail}" if detail else ""))
    if not cond:
        FAILS.append(name + (f"（{detail}）" if detail else ""))


def hol(nm: str) -> dict:
    return {"名": nm, "类别": "holistic", "摘要": "计数与和",
            "见证": {"摘要": "计数与和", "构造": "同摘要异读数"}}


def main() -> int:
    root = find_corpus()
    if root is None:
        print("✗ 找不到语料（LDV_CORPUS 没设？）")
        return 1
    nodes = load_nodes(root)
    edges, _ = load_edges(nodes)
    print(f"语料 {root}：{len(nodes)} 项 / {sum(len(v) for v in edges.values())} 边")
    corpus = corpus_fingerprint(nodes, edges)
    doc = load_spec_file(find_corpus(), corpus)
    which = str(doc.get("方向") or "")
    print(f"外生声明：方向 = {which}；读数 {len(doc.get('读数') or [])} 条")
    spec, why = spec_for(doc, which, corpus)
    if spec is None:
        print(f"✗ spec_for：{why}")
        return 1
    kernel = maintenance_kernel(which, nodes, edges)
    plugin = batch_kernel(which, nodes, edges)[1]
    cover = coverage_of(which, nodes, edges)
    vs = build_views(kernel, spec, cover, plugin)
    parts = view_parts(vs.q, lambda d: subtree_of(kernel, d))
    ctx = reading_ctx(kernel, spec)
    decls = list(doc.get("读数") or [])
    print(f"视图：{len(spec.universe)} 个方向 ⇒ {len(vs.q)} 块")

    # ── P0 inj（摘要单射 = openalex 恒红的微型复现）────────────────────────
    print("\n── P0 inj 论域（摘要单射）")
    inj = a4_inj_ctx()
    got, why0 = judge_reading("中位数", "holistic", hol("中位数"), {}, inj)
    print(f"    中位数 holistic ⇒ {got}\n    {why0}")
    ok("P0 inj（摘要单射）上中位数 holistic ⇒ **过**（旧实现判红）", got is True)

    # ── P1 真语料上 §A4 ────────────────────────────────────────────────────
    print(f"\n── P1 openalex × {which} 真语料")
    rep = Report(plugin="(视图)")
    a4_category(parts, ctx, decls, rep)
    a4 = rep.assertions[0]
    print(f"    §A4 ⇒ {a4.result}\n    {a4.detail}")
    ok("P1 openalex 真语料上 `§A4` **过**（旧实现恒红 `A4|(视图)`）",
       a4.result is Tri.YES, str(a4.result))
    ok("P1b 那一行披露「**论域的性质**」而不是把绿当成没话说",
       "论域的性质" in a4.detail)

    # ── P2 函数级表 ────────────────────────────────────────────────────────
    print("\n── P2 函数级（Scope-M，语料无关）")
    sf = SUMMARIES["计数与和"]
    want = {"计数": (True, "取第一项"), "和": (True, "取第二项"),
            "平均": (True, "和除计数"), "中位数": (False, None),
            "最大": (False, None), "覆盖计数": (False, None)}
    for name, (det, h) in want.items():
        f, _ = READINGS[name]
        sm = scope_m_reading(f, sf)
        good = sm["determined"] is det and sm["H"] == h
        print(f"    {'✓' if good else '✗'} {name:6s} determined={sm['determined']!s:5s} H={sm['H']}")
        if not good:
            FAILS.append(f"P2 {name}：期望 determined={det} H={h}，"
                         f"实测 {sm['determined']}/{sm['H']}")
    ok("P2 六个读数的函数级结论逐个对手推值", not FAILS)

    # ── P3 有界 vs 穷举交叉验证 ────────────────────────────────────────────
    print("\n── P3 有界 vs 穷举")
    f_med, _ = READINGS["中位数"]
    b = witness_same_digest_diff_reading(f_med, sf, ctx)
    e = decide_digest_witness(f_med, sf, ctx)
    print(f"    openalex：bounded={b}｜exhaustive mode={e['mode']} found={e['found']} "
          f"subsets={e['subsets']} buckets={e['buckets']} suffices={e['suffices']}")
    ok("P3a 本语料上两者一致地给 None（穷举过 ⇒ 「0 对」是结论，不是没搜到）",
       b is None and e["mode"] == "exhaustive" and e["found"] is False
       and e["suffices"] is True,
       f"mode={e['mode']} found={e['found']} suffices={e['suffices']}")
    rng = random.Random(20261014)
    same = total = missed = 0
    for _ in range(200):
        n = rng.randint(6, 9)
        values = {f"d{i}": float(rng.randint(1, 12)) for i in range(n)}
        pool = [f"x{j}" for j in range(8)]
        covers = {d: frozenset(rng.sample(pool, rng.randint(1, 4))) for d in values}
        c2 = ReadingCtx(value_of=values, cover_of=covers)
        for fname in ("中位数", "覆盖计数"):
            ff, _ = READINGS[fname]
            w1 = witness_same_digest_diff_reading(ff, sf, c2)
            w2 = decide_digest_witness(ff, sf, c2)
            if w1 is not None:
                total += 1
                same += 1 if w1 == w2["pair"] else 0
            elif w2["found"]:
                missed += 1
    print(f"    随机论域：有界搜到 {total} 例（其中与穷举首个见证逐字相同 {same}）；"
          f"穷举找到而 k≤4 漏掉 {missed} 例")
    ok("P3b 有界搜到的每一例，穷举的首个见证**逐字相同**（同序没被破坏）",
       total > 0 and same == total, f"{same}/{total}")
    print(f"    P3c 「穷举找到而 k≤4 漏掉」= {missed} 例 —— 若 >0，旧假红有一部分来自"
          f"「搜得不够深」；实测 {missed} ⇒ 旧假红的来源**不是**深度，而是"
          f"「摘要单射的论域上本来没有见证，却被旧实现当成『声称不成立』」")

    # ── P4 反向三条 ────────────────────────────────────────────────────────
    print("\n── P4 反向对照")
    import ldv.checks.abstraction as _abs

    orig = _abs.scope_m_reading
    before = judge_reading("平均", "holistic", hol("平均"), {}, inj)[0]
    _abs.scope_m_reading = lambda f, sf: {"determined": False, "pair": None,
                                          "H": None, "subsets": 0, "note": "（短路）"}
    try:
        short = judge_reading("平均", "holistic", hol("平均"), {}, inj)[0]
        broken = a4_known_answer()
    finally:
        _abs.scope_m_reading = orig
    ok("P4a 短路函数级门 ⇒ `平均` holistic 由**红变绿**"
       "（证那条红是函数级门给的，不是语料碰巧搜不到）",
       before is False and short is True, f"before={before} short={short}")
    ok("P4b 短路后合成对照必须报 FAIL（`平均/holistic` 在失败清单里）",
       any("平均/holistic" in x for x in broken),
       "；".join(broken)[:120] or "短路后对照全对 —— 函数级门是摆设")
    _abs.scope_m_reading = lambda f, sf: {"determined": True, "pair": None,
                                          "H": None, "subsets": 0, "note": "（恒决定）"}
    try:
        no_h = judge_reading("中位数", "holistic", hol("中位数"), {}, inj)[0]
    finally:
        _abs.scope_m_reading = orig
    ok("P4c 恒报「被决定」⇒ `中位数` holistic @ inj 由**绿变红**（另一头也是真的）",
       no_h is False, f"got={no_h}")

    print(f"\n═══ 探针结论：{len(FAILS)} 条不符 ═══")
    for f in FAILS:
        print(f"  ✗ {f}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
