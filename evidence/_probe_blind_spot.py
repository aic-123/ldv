# -*- coding: utf-8 -*-
"""量 `verify_quotes` 的**盲区有多大** —— 异域编造 vs 同域编造 vs 一词漂移。

用法（从**工作区根**跑）：
    PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python outputs/_probe_blind_spot.py

**为什么要有这个探针**：`verify_quotes` 的判据是「覆盖比 ≥ 0.9 且无 ≥ 25 字符连续缺口」。
在 46 份源文 / 160 万+ 个 12-gram 的规模下，这个判据**必然**有盲区 ——
问题不是「有没有盲区」，而是「**盲区在哪一侧、有多大**」。
不量它，就会把「覆盖比 = 1.0」读成「这句话有出处」。

读数写进 `docs/prior-art/README.md` 的「顺带一条已知性质」那一节。
**这只是读数探针，不是判据。**
"""
import sys, os

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "repo", "docs", "prior-art"))
import verify_quotes as V   # noqa: E402

#: 第七批带进来的五篇 —— 用来做「加源前后的盲区对照」
BATCH7 = ["metis.txt", "dmlgp.txt", "reslimit.txt", "lsmsurvey.txt", "hendrickson95.txt"]

异域 = [
    "Quantum entanglement in the multilevel hierarchy provably reduces the spectral bisection bandwidth.",
    "The chef prepared a delicate souffle while the orchestra tuned their instruments backstage.",
    "Bananas are yellow because carotenoid pigments absorb blue light in the visible spectrum.",
    "The defendant pleaded not guilty to three counts of securities fraud in federal court.",
]
同域 = [
    "Coarsening is advantageous because the coarse graph can be solved in polynomial time and the hierarchy is guaranteed to be balanced.",
    "The multilevel algorithm reduces the total number of levels by increasing the coarsening ratio beyond the threshold.",
    "Local refinement is applied during the uncoarsening phase to reduce the edge cut at every level of the hierarchy.",
]
漂移 = [
    # 真句（基线）—— 它必须过，否则说明源没进语料
    "The price paid for this reduction in complexity is that only a small number of the possible fine graph partitions are represented and are therefore examinable on the coarse graph.",
    # 同句换两个实义词
    "The price paid for this reduction in complexity is that only a small number of the possible fine graph partitions are enumerated and are therefore eliminable on the coarse graph.",
]


def build(srcs, exclude=()):
    d = {k: v for k, v in srcs.items() if k not in exclude}
    g = set()
    for s in d.values():
        g |= V.kgrams(s)
    return g, "".join(d.values())


def cov(q, g, flat):
    worst = 1.0
    for frag in V.fragments(V.norm(q)):
        if len(frag) < V.MIN_FRAG:
            continue
        worst = min(worst, V.coverage(frag, g, flat)[0])
    return worst


def main():
    srcs = V.load_sources()
    corpora = [
        ("全语料", ()),
        ("去掉第七批 5 篇", tuple(BATCH7)),
    ]
    for name, ex in corpora:
        g, flat = build(srcs, ex)
        print(f"\n########## {name}｜源 {len(srcs) - len(ex)} 份｜12-gram {len(g)} ##########")
        for title, cases in (("异域编造（应当全红）", 异域),
                             ("同域编造（本领域措辞，整句是编的）", 同域),
                             ("一词漂移 / 真句基线", 漂移)):
            print(f"  --- {title} ---")
            for q in cases:
                c = cov(q, g, flat)
                print(f"    {c:.2f} {'过' if c >= V.COVER_MIN else '红'}  {q[:74]}")


if __name__ == "__main__":
    main()
