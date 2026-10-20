# -*- coding: utf-8 -*-
"""把候选引文逐条过一遍 verify_quotes 的归一化 + 覆盖判据。

用法（从**工作区根**跑）：
    PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python outputs/_probe_ml_quotes.py

**这只是读数探针，不是判据** —— 判据是 `docs/prior-art/verify_quotes.py`
在交付物本身上跑出来的那个数。
"""
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "repo", "docs", "prior-art"))
import verify_quotes as V   # noqa: E402

# (来源文件, 引文原文 —— 按读法写)
CAND = [
    # ---- METIS (Karypis & Kumar 1998) ----
    ("metis.txt", "a sequence of smaller graphs"),
    ("metis.txt", "Since maximal matchings are used to coarsen the graph, the number of vertices in"),
    ("metis.txt", "cannot be less than half the number of vertices in"),
    ("metis.txt", "it will require at least O(log(n/n0)) coarsening phases"),
    ("metis.txt", "If the ratio becomes lower than a threshold, then it is better to stop the coarsening phase."),
    ("metis.txt", "this type of pathological condition usually arises after many coarsening levels"),
    ("metis.txt", "aborting the coarsening does not affect the overall performance of the algorithm"),
    ("metis.txt", "the graph is coarsened down to a few hundred vertices"),
    ("metis.txt", "a partition of this small graph is obtained"),
    ("metis.txt", "projected back towards the original graph"),
    ("metis.txt", "successively refined using a refinement heuristic"),
    # ---- Hendrickson & Leland 1995 ----
    ("hendrickson95.txt", "The entire algorithm can be implemented to execute in time proportional to the size of the original graph."),
    ("hendrickson95.txt", "The price paid for this reduction in complexity is that only a small number of the possible"),
    ("hendrickson95.txt", "graph partitions are represented and are therefore examinable on the coarse graph"),
    ("hendrickson95.txt", "by working on different levels, the local refinement scheme improves the partition on multiple scales"),
    ("hendrickson95.txt", "Until graph is small enough"),
    ("hendrickson95.txt", "it is preferable to divide into as many sets at once as possible so as to limit the depth of the recursion"),
    ("hendrickson95.txt", "Coarsening is advantageous because"),
    ("hendrickson95.txt", "the number of possible partitions grows exponentially with the number of vertices in the graph"),
    ("hendrickson95.txt", "a good partition of the coarse graph is much easier to find than a good partition of the original one"),
    ("hendrickson95.txt", "All other things being equal, it is preferable to divide into as many sets at once as possible"),
    # ---- dmlgp (Gottesbueren et al. 2023) ----
    ("dmlgp.txt", "The coarsening phase of MGP usually stops when kC nodes are left."),
    ("dmlgp.txt", "For large k, this breaks the assumption that the coarsest graph is small."),
    ("dmlgp.txt", "Thus, really expensive initial partitioners are infeasible at this level."),
    ("dmlgp.txt", "Once the number of nodes of a coarse graph falls below a certain threshold or the coarsening algorithm converges, initial partitioning computes a partition of the coarsest graph."),
    ("dmlgp.txt", "An idealized coarsening algorithm produces a graph hierarchy where the number of nodes is halved between two levels and the coarsest graph has 2C nodes."),
    ("dmlgp.txt", "it is sufficient to bipartition the coarsest graph"),
    ("dmlgp.txt", "A coarse graph"),
    ("dmlgp.txt", "is partitioned into"),
    ("dmlgp.txt", "blocks"),
    # ---- 分辨率极限 (Fortunato & Barthelemy 2006) ----
    ("reslimit.txt", "We find that modularity optimization may fail to identify modules smaller than a scale which depends on the total number L of links of the network and on the degree of interconnectedness of the modules, even in cases where modules are unambiguously defined."),
    ("reslimit.txt", "by enforcing modularity optimization, the possible partitions of the system are explored at a coarse level, so that modules smaller than some scale may not be resolved"),
    ("reslimit.txt", "The resolution limit of modularity does not rely on particular network structures, but only on the comparison between the sizes of interconnected communities and that of the whole network"),
    ("reslimit.txt", "is not necessarily correlated with the actual community structure"),
    ("reslimit.txt", "where communities may be very heterogeneous in size, especially if the network is large"),
    ("reslimit.txt", "have an intrinsic resolution limit calls for a new theoretical framework which focuses on a local definition of community, regardless of its size"),
    ("reslimit.txt", "constraining modularity optimization on each single module"),
    ("reslimit.txt", "a procedure which is not safe but may give useful indications"),
    # ---- LSM 综述 (Luo & Carey) ----
    ("lsmsurvey.txt", "L remains static"),
    ("lsmsurvey.txt", "the number of levels for N entries can be approximated as"),
    ("lsmsurvey.txt", "Even for an append-mostly workload, the total number of levels will grow extremely slowly since the maximum number of entries that an LSM-tree can store increases exponentially with a factor of T as the number of levels increases."),
    ("lsmsurvey.txt", "If level L is already the configured maximum level, then the resulting component remains at level L."),
    ("lsmsurvey.txt", "Given N total entries, the largest level contains approximately"),
    ("lsmsurvey.txt", "entries since it is T times larger than the previous level"),
]


def main():
    srcs = V.load_sources()
    print(f"源文 {len(srcs)} 份｜k={V.K}｜覆盖门槛 {V.COVER_MIN}\n")
    grams = set()
    for s in srcs.values():
        grams |= V.kgrams(s)
    flat = "".join(srcs.values())

    ok = bad = 0
    for name, raw in CAND:
        if name not in srcs:
            print(f"✗ 源不在语料里：{name}")
            continue
        q = V.norm(raw)
        worst, notes = 1.0, []
        for frag in V.fragments(q):
            if len(frag) < V.MIN_FRAG:
                continue
            ratio, g = V.coverage(frag, grams, flat)
            worst = min(worst, ratio)
            if ratio < V.COVER_MIN:
                notes.append(f"覆盖比 {ratio:.2f}")
            if g:
                notes.append("缺口：" + " ‖ ".join(x[:70] for x in g))
        if notes:
            bad += 1
            print(f"[红] {name}  最低覆盖比 {worst:.2f}  ← {raw[:60]}")
            for x in notes[:3]:
                print(f"      {x}")
        else:
            ok += 1
            print(f"[过] {name}  {raw[:70]}")
    print(f"\n合计：过 {ok}｜红 {bad}")


if __name__ == "__main__":
    main()
