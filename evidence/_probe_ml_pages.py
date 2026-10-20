# -*- coding: utf-8 -*-
"""给每条候选引文定位：落在**哪个 `===PAGE n===` 段**里。

用法（从**工作区根**跑）：
    PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python outputs/_probe_ml_pages.py

做法：把源文按 `===PAGE n===` 切成段，每段各自归一化，再看引文的归一化形式
落在哪一段里。**引文本身不归一化以外的加工** —— 与 verify_quotes 同一套 norm()。
"""
import sys, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "repo", "docs", "prior-art"))
import verify_quotes as V   # noqa: E402

PRIOR = os.path.join(HERE, "..", ".prior-art")

# (源文件, 引文按读法写)
QUOTES = [
    # METIS
    ("metis.txt", "The graph G is first coarsened down to a few hundred vertices, a bisection of this much smaller graph is computed, and then this partition is projected back toward the original graph (finer graph)."),
    ("metis.txt", "A multilevel graph bisection algorithm consists of the following three phases."),
    ("metis.txt", "The graph G0 is transformed into a sequence of smaller graphs G1,G 2,...,G m"),
    ("metis.txt", "Partitioning phase. A 2-way partition Pm of the graph Gm"),
    ("metis.txt", "Uncoarsening phase. The partitionPm ofGm is projected back toG0"),
    ("metis.txt", "during the coarsening phase, the size of the graph is successively decreased"),
    ("metis.txt", "during the initial partitioning phase, a bisection of the smaller graph is computed"),
    ("metis.txt", "during the uncoarsening phase, the bisection is successively refined as it is projected to the larger graphs"),
    ("metis.txt", "Since maximal matchings are used to coarsen the graph, the number of vertices in Gi+1 cannot be less than half the number of vertices in Gi"),
    ("metis.txt", "it will require at least O(log(n/n\u2032)) coarsening phases to coarsen G0 down to a graph with n\u2032 vertices"),
    ("metis.txt", "If the ratio becomes lower than a threshold, then it is better to stop the coarsening phase."),
    ("metis.txt", "this type of pathological condition usually arises after many coarsening levels, in which case Gi is already fairly small"),
    ("metis.txt", "aborting the coarsening does not affect the overall performance of the algorithm"),
    ("metis.txt", "We investigate the effectiveness of many different choices for all three phases: coarsening, partition of the coarsest graph, and refinement."),
    # Hendrickson & Leland 1995
    ("hendrickson95.txt", "The entire algorithm can be implemented to execute in time proportional to the size of the original graph."),
    ("hendrickson95.txt", "Coarsening is advantageous because is that the number of possible partitions grows exponentially with the number of vertices in the graph"),
    ("hendrickson95.txt", "so a good partition of the coarse graph is much easier to find than a good partition of the original one"),
    ("hendrickson95.txt", "The price paid for this reduction in complexity is that only a small number of the possible fine graph partitions are represented and are therefore examinable on the coarse graph."),
    ("hendrickson95.txt", "by working on different levels, the local refinement scheme improves the partition on multiple scales"),
    ("hendrickson95.txt", "Until graph is small enough"),
    ("hendrickson95.txt", "All other things being equal, it is preferable to divide into as many sets at once as possible so as to limit the depth of the recursion."),
    # dmlgp
    ("dmlgp.txt", "The coarsening phase of MGP usually stops when kC nodes are left."),
    ("dmlgp.txt", "For large k, this breaks the assumption that the coarsest graph is small."),
    ("dmlgp.txt", "Thus, really expensive initial partitioners are infeasible at this level."),
    ("dmlgp.txt", "Once the number of nodes of a coarse graph falls below a certain threshold or the coarsening algorithm converges, initial partitioning computes a partition of the coarsest graph."),
    ("dmlgp.txt", "An idealized coarsening algorithm produces a graph hierarchy where the number of nodes is halved between two levels and the coarsest graph has 2C nodes."),
    ("dmlgp.txt", "it is sufficient to bipartition the coarsest graph G"),
    ("dmlgp.txt", "A coarse graph G"),
    ("dmlgp.txt", "is partitioned into"),
    # reslimit
    ("reslimit.txt", "We find that modularity optimization may fail to identify modules smaller than a scale which depends on the total number L of links of the network and on the degree of interconnectedness of the modules, even in cases where modules are unambiguously defined."),
    ("reslimit.txt", "by enforcing modularity optimization, the possible partitions of the system are explored at a coarse level, so that modules smaller than some scale may not be resolved"),
    ("reslimit.txt", "The resolution limit of modularity does not rely on particular network structures, but only on the comparison between the sizes of interconnected communities and that of the whole network"),
    ("reslimit.txt", "is not necessarily correlated with the actual community structure"),
    ("reslimit.txt", "where communities may be very heterogeneous in size, especially if the network is large"),
    ("reslimit.txt", "have an intrinsic resolution limit calls for a new theoretical framework which focuses on a local definition of community, regardless of its size"),
    ("reslimit.txt", "constraining modularity optimization on each single module"),
    ("reslimit.txt", "a procedure which is not safe but may give useful indications"),
    # lsmsurvey
    ("lsmsurvey.txt", "L remains static"),
    ("lsmsurvey.txt", "the number of levels for N entries can be approximated as"),
    ("lsmsurvey.txt", "Even for an append-mostly workload, the total number of levels will grow extremely slowly since the maximum number of entries that an LSM-tree can store increases exponentially with a factor of T as the number of levels increases."),
    ("lsmsurvey.txt", "If level L is already the configured maximum level, then the resulting component remains at level L."),
    ("lsmsurvey.txt", "Given N total entries, the largest level contains approximately"),
    ("lsmsurvey.txt", "entries since it is T times larger than the previous level"),
]


def pages(path):
    """把源文切成 [(页号, 归一化文本)]。`===PAGE n===` 之前的部分算第 1 页之前。

    ⚠️ **必须与 `load_sources()` 同一套按源分派** —— 否则 `hendrickson95` 的
    `/` 占位没被还原，引文会假报「无逐字命中」。第一次写这个探针时正是踩了这个坑。
    """
    name = os.path.basename(path)
    nz = lambda s: V.norm(s,                                   # noqa: E731
                          soft_first=(name in V.SOFT_FILES),
                          ocr_first=(name in V.OCR_FILES),
                          hend_first=(name in V.HENDRICKSON_FILES))
    raw = open(path, encoding="utf-8", errors="replace").read()
    parts = re.split(r"===PAGE (\d+)===", raw)
    out = []
    if parts[0].strip():
        out.append((None, nz(parts[0])))
    for k in range(1, len(parts) - 1, 2):
        out.append((int(parts[k]), nz(parts[k + 1])))
    return out


def main():
    cache = {}
    for name, raw in QUOTES:
        if name not in cache:
            cache[name] = pages(os.path.join(PRIOR, name))
        segs = cache[name]
        q = V.norm(raw)
        hits = [p for p, txt in segs if q in txt]
        if not hits:
            # 没逐字命中 ⇒ 报哪一段覆盖最高，方便判断是「写错了」还是「跨页了」
            best = max(((V.coverage(q, V.kgrams(txt), txt)[0], p) for p, txt in segs),
                       default=(0.0, None))
            print(f"[✗] {name:20s} 无逐字命中（最高覆盖比 {best[0]:.2f} @ 页 {best[1]}）  {raw[:60]}")
        else:
            print(f"[✓] {name:20s} PDF 页 {hits}  {raw[:64]}")


if __name__ == "__main__":
    main()
