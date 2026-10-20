"""停止条件 6 的复裁依据：给「一张视图装多少成员」**加上界**，会打红哪一条承重判据？

文档（抽象层 §10 停止条件 6）原来写：

    加了就要回答「超过了怎么办」：拆块？拆了就**不稳**，`§A2` 红

本探针把这句话**实测**一遍。做法：

    基线    `Q = csr(spec)`（真实现）            ⇒ 预期 `§A2` 绿 / `§A3` 绿
    加上界  把**每个**超过 `bound` 的块按字典序切成 ≤ bound 的小块
            （这正是「超过上界就拆块」那条处置的实现）⇒ 看 `§A2` / `§A3` 各报什么

★ 还要看**红的是哪一条**，以及**跨半块的边数**（机制读数）：
  两个半块之间**没有边** ⇒ 拆开之后每一半对 `E⁻¹(另一半)` 仍是「整进整出」
  ⇒ **仍然稳定**（但**不再是最粗**）⇒ 那时 `§A2` 是**绿**的。

跑法（默认语料 / 指定语料 / 额外算一条 `reach` 声明）：
    cd repo && python ../outputs/_probe_member_bound.py
    cd repo && LDV_CORPUS=snap-email-eu-mini python ../outputs/_probe_member_bound.py
    cd repo && LDV_PROBE_EXTRA=openalex-small:reach python ../outputs/_probe_member_bound.py

⚠️ `LDV_PROBE_EXTRA` 那条**不落盘**：`build_spec` 当场算一份声明出来，
   所以它不会往任何语料目录里写文件。
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "repo")))

from ldv.checks._fixtures import find_corpus, load           # noqa: E402
from ldv.checks._framework import Report, Tri                # noqa: E402
from ldv.checks.abstraction import (                         # noqa: E402
    ViewSpec, a2_stable, a3_coarsest, load_spec_file, spec_for,
)
from ldv.checks.coverage import corpus_fingerprint           # noqa: E402
from ldv.core.views import coarsest_stable_refinement, stable  # noqa: E402


def result_of(rep: Report, code: str) -> Tri:
    for a in rep.assertions:
        if a.code == code:
            return a.result
    return Tri.UNEXPANDED


def bound_partition(q, bound: int) -> tuple[frozenset[str], ...]:
    """「超过上界就拆块」的实现：每个块按**字典序**切成 ≤ `bound` 的小块。

    ⚠️ 字典序是**任意的** —— 而正是这一点说明：处置一旦要「任意」，
       就已经不是在算那个唯一解了。
    """
    out: list[frozenset[str]] = []
    for block in q:
        xs = sorted(block)
        if len(xs) <= bound:
            out.append(block)
            continue
        for i in range(0, len(xs), bound):
            out.append(frozenset(xs[i:i + bound]))
    return tuple(out)


def split_shape(spec, q, bound: int) -> tuple[int, int]:
    """拆法形状：**被拆的块数** / **拆出来的半块数**。"""
    n_split = 0
    n_parts = 0
    for block in q:
        if len(block) <= bound:
            continue
        n_split += 1
        n_parts += math.ceil(len(block) / bound)
    return n_split, n_parts


def run_one(spec, q, label: str) -> dict:
    rep = Report(plugin="(探针)")
    a2_stable(spec, q, rep)
    a3_coarsest(spec, q, rep)
    a2, a3 = result_of(rep, "A2"), result_of(rep, "A3")
    print(f"    {label:24s} 块数 {len(q):3d}｜最大块 {max(len(b) for b in q):4d}"
          f"｜`§A2` {a2.name:11s}｜`§A3` {a3.name}")
    return {"q": len(q), "max": max(len(b) for b in q), "A2": a2, "A3": a3}


def experiment(spec, label: str, bad: list[str]) -> None:
    U = len(spec.universe)
    q = coarsest_stable_refinement(spec)
    bound = max(1, math.ceil(U / 5))
    print(f"  {label}｜|U| = {U}｜上界取 `ceil(|U|/5)` = {bound}"
          f"｜外生边 {len(spec.relation)} 条")
    base = run_one(spec, q, "基线（`csr`，真实现）")
    qb = bound_partition(q, bound)
    bd = run_one(spec, qb, f"加上界（拆到 ≤ {bound}）")
    n_split, n_parts = split_shape(spec, q, bound)
    # ⚠️ 反例**用判据自己的那一份**（`core/views.stable`），不自己编一个代理读数。
    #    我先试过两条代理（「跨半块的边」/「半块内自指入边匀不匀」），**两条都被
    #    实测推翻**（四例里前者的计数全是 0，后者也解释不了 `§A2` 的红绿）。
    #    ⇒ 读数就用权威的那份：`§A2` 的反例本来就是为了「指得出是哪两个元素」才这么写的。
    _ok, examples = stable(spec, qb)
    print(f"    · 拆法：{n_split} 个块被拆成 {n_parts} 个半块；"
          f"`§A2` 反例 {len(examples)} 条"
          + (f"，例：{examples[0][:70]}…" if examples else "（无）"))
    if base["A2"] is not Tri.YES or base["A3"] is not Tri.YES:
        bad.append(f"{label}：基线不是双绿（探针口径有问题）")
    if bd["A3"] is not Tri.NO:
        bad.append(f"{label}：加上界之后 `§A3` 不是红（{bd['A3'].name}）")
    print()


def main() -> int:
    bad: list[str] = []
    loaded = load()
    if loaded is None:
        print("语料目录不在 ⇒ 跳过")
        return 2
    nodes, edges, _ = loaded
    nd = find_corpus()
    corpus = corpus_fingerprint(nodes, edges)
    doc = load_spec_file(nd, corpus)
    print(f"【甲】用**已提交的声明**｜语料 {os.environ.get('LDV_CORPUS', '(默认)')}"
          f"｜{len(nodes)} 项")
    print()
    for which in ("keyset", "reach", "sequence"):
        spec, why = spec_for(doc, which, corpus)
        if spec is None or not spec.universe:
            print(f"  {which:9s} ⚠️ 没有可判的声明（{why}）⇒ 跳过")
            continue
        experiment(spec, f"{which:9s}", bad)

    extra = os.environ.get("LDV_PROBE_EXTRA", "")
    if extra:
        from ldv.tools.gen_view_spec import build_spec           # noqa: E402
        name, _, which = extra.partition(":")
        old = os.environ.get("LDV_CORPUS")
        os.environ["LDV_CORPUS"] = name
        try:
            got = load()
        finally:
            if old is None:
                os.environ.pop("LDV_CORPUS", None)
            else:
                os.environ["LDV_CORPUS"] = old
        if got is None:
            print(f"【乙】语料 `{name}` 不在 ⇒ 跳过")
        else:
            n2, e2, _ = got
            print(f"【乙】**当场算**一份声明（不落盘）｜语料 `{name}`｜{len(n2)} 项"
                  f"｜方向 `{which}`")
            print()
            raw, _notes = build_spec(which, n2, e2)
            spec = ViewSpec(
                universe=tuple(raw["universe"]),
                partition=tuple(frozenset(b) for b in raw["partition"]),
                relation=frozenset(tuple(e) for e in raw["relation"]),
            )
            experiment(spec, f"{which:9s}", bad)

    print("═" * 70)
    if bad:
        print("⚠️ 探针的预期被打破：")
        for b in bad:
            print("   ✗", b)
        return 1
    print("结论：基线双绿；**加上界之后 `§A3` 一律红**（`§A2` 红不红取决于拆法稳不稳）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
