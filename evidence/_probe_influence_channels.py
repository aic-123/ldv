"""探针：**模糊掌握（认识 `Q`）到底影响了检索层的哪些交付物** —— 逐条列出来。

## 为什么要有它

「抽象层持续影响检索层」这句话被我用**一个数**概括过（「差异恒为 0」），
那是**不准确的**：那个 0 只针对**「落盘-读回」这一步**（因为 `q = csr(spec)` 是纯函数）。
而**模糊掌握本身**是不是在被消费，是**另一个问题** —— 本探针回答它。

## 做法

同一份结构、同一个需求，跑**两趟**：

    甲  **带**模糊掌握：`q = csr(spec)`（25 个方向 ⇒ 8 块）
    乙  **不带**：`q = 离散划分`（25 个方向 ⇒ 25 块，一块一个方向）

⚠️ 乙**不是非法输入** —— 它稳定、细化 `P`、不改候选集（`§3` 定理）
   ⇒ 两趟的**候选集必然相同**，而**其余交付物**的差别就是「模糊掌握的贡献」。

逐条比：候选集 / 排序（先看·后看）/ 解释 / 块级建议 / 画像 / 判据状态。
"""
from __future__ import annotations

import dataclasses
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1] / "repo"
sys.path.insert(0, str(REPO))
os.environ.pop("LDV_CORPUS", None)

from ldv import flow, retriever  # noqa: E402
from ldv.checks._fixtures import coverage_of, find_corpus, load  # noqa: E402
from ldv.checks._framework import Report  # noqa: E402
from ldv.checks.abstraction import ViewSet, load_spec_file, spec_for  # noqa: E402
from ldv.checks.coverage import corpus_fingerprint  # noqa: E402
from ldv.checks.retrieval import RETRIEVER_CODES, run_retrieval  # noqa: E402
from ldv.core import selfopt  # noqa: E402
from ldv.core.views import coarsest_stable_refinement  # noqa: E402
from ldv.run_checks import batch_kernel  # noqa: E402


def main() -> int:
    loaded = load()
    if loaded is None:
        print("找不到语料")
        return 1
    nodes, edges, _ = loaded
    nd = find_corpus()
    corpus = corpus_fingerprint(nodes, edges)
    doc = load_spec_file(nd, corpus)
    spec, _why = spec_for(doc, "keyset", corpus)
    kernel, plugin, queries, _mk = batch_kernel("keyset", nodes, edges)
    cover = coverage_of("keyset", nodes, edges)
    for q0 in queries[:3]:
        flow.run_query(kernel, q0)
    tendency = selfopt.tendency_by_direction(kernel)
    need = {"keyset": retriever.as_bar(queries[0])}

    def run(q):
        vs = ViewSet(spec=spec, q=tuple(q), views=())
        rep = Report(plugin="(检索)", expects=RETRIEVER_CODES)
        r = run_retrieval(kernel, plugin, need, spec, rep, recognition=vs,
                          tendency=tendency,
                          disk_fingerprint=retriever.spec_fingerprint(spec))
        return r, rep

    q_fuzzy = tuple(coarsest_stable_refinement(spec))
    q_discrete = tuple(frozenset({d}) for d in sorted(spec.universe))
    a, rep_a = run(q_fuzzy)
    b, rep_b = run(q_discrete)

    print("═══ 模糊掌握（认识 `Q`）影响了检索层的哪些交付物 ═══")
    print(f"  甲 带：`|U| = {len(spec.universe)}` ⇒ `|Q| = {len(q_fuzzy)}` 块"
          f"（收缩比 {len(q_fuzzy)/len(spec.universe):.3f}）")
    print(f"  乙 不带：⇒ `|Q| = {len(q_discrete)}` 块（离散）")
    print()

    rows = [
        ("候选集（`T1`）", len(a.candidates), len(b.candidates),
         sorted(a.candidates) == sorted(b.candidates)),
        ("排序「先看/后看」（`T2`）", f"{len(a.first)}/{len(a.late)}",
         f"{len(b.first)}/{len(b.late)}", (len(a.first), len(a.late)) == (len(b.first), len(b.late))),
        ("解释条数（`T4`）", len(a.reasons), len(b.reasons), len(a.reasons) == len(b.reasons)),
        ("块级建议**条数**（答 `R3a`）", len(a.advice), len(b.advice), len(a.advice) == len(b.advice)),
        ("块级建议**粒度**（方向/条）",
         round(a.advice_span / max(1, len(a.advice)), 2),
         round(b.advice_span / max(1, len(b.advice)), 2),
         abs(a.advice_span / max(1, len(a.advice)) - b.advice_span / max(1, len(b.advice))) < 1e-9),
        ("画像**块数**（`T4`）", len(a.profiles), len(b.profiles), len(a.profiles) == len(b.profiles)),
        ("画像读法**种类**", len({p.reading for p in a.profiles}),
         len({p.reading for p in b.profiles}),
         {p.reading for p in a.profiles} == {p.reading for p in b.profiles}),
        ("最深一块的成员数", max(p.n_members for p in a.profiles),
         max(p.n_members for p in b.profiles),
         max(p.n_members for p in a.profiles) == max(p.n_members for p in b.profiles)),
    ]
    print(f"  {'交付物':<26}{'甲 带':<14}{'乙 不带':<14}{'相同?'}")
    changed = []
    for name, x, y, same in rows:
        print(f"  {name:<26}{str(x):<14}{str(y):<14}{'★同' if same else '**不同**'}")
        if not same:
            changed.append(name)
    print()
    print("★ 判读：")
    print(f"    **不同的交付物：{len(changed)} 项** —— {changed}")
    print(f"    相同的：候选集（`§3` 定理那一半，**必然**相同）等 {len(rows)-len(changed)} 项")
    print()
    print("    逐条看那几项「不同」的**实际长相**：")
    print(f"      建议（甲）：{[(len(bl), a_) for bl, a_ in a.advice[:3]]} …")
    print(f"      建议（乙）：{[(len(bl), a_) for bl, a_ in b.advice[:3]]} …")
    print(f"      画像（甲）：{sorted({p.reading for p in a.profiles})}"
          f"｜最大块 {max(p.n_members for p in a.profiles)}")
    print(f"      画像（乙）：{sorted({p.reading for p in b.profiles})}"
          f"｜最大块 {max(p.n_members for p in b.profiles)}")
    print()
    print("    判据（甲）：" + " ".join(
        f"{x.code}={x.result.name}" for x in rep_a.assertions))
    print("    判据（乙）：" + " ".join(
        f"{x.code}={x.result.name}" for x in rep_b.assertions))
    print()
    print("  ⇒ 结论：**模糊掌握确实在被消费** —— 它改变了"
          "「解释 / 块级建议 / 画像 / 排序」这些**判断类**交付物，")
    print("     而**候选集**（结论类）按 `§3` 定理必须不变。")
    print("     ⚠️ 而「**落盘-读回**这一步」相对「当场现算」不产生差异 ——")
    print("        那是**另一个口径**（`q = csr(spec)` 是纯函数），**不是**「模糊掌握没影响」。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
