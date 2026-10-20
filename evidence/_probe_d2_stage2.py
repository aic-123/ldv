"""探针：`sequence` / n100 上，三个阶段逐对摊开**两个口径**，并定位 W2995022099。

用法（cwd = repo）：python ../outputs/_probe_d2_stage2.py <语料名> [方向] [holdout]
"""
import sys

sys.path.insert(0, ".")

from ldv.checks._fixtures import load
from ldv.cli import _assemble
from ldv.flow import insert_items, remove_items

corpus = sys.argv[1] if len(sys.argv) > 1 else "openalex-n100"
which = sys.argv[2] if len(sys.argv) > 2 else "sequence"
holdout = int(sys.argv[3]) if len(sys.argv) > 3 else 12

nodes, edges, _ = load()
kernel, queries, new, removable, cover = _assemble(which, nodes, edges, holdout, holdout)


def dump(tag):
    print(f"\n──── {tag}：{len(kernel.items)} 项在树，{len(kernel.all_directions())} 方向 ────")
    rows = []
    for d in kernel.all_directions():
        kids = kernel.children_of(d)
        if not kids:
            continue
        pm = set(kernel.members_of(d))
        um = set()
        for k in kids:
            um |= set(kernel.members_of(k))
        stay = set(kernel.stayed_of(d))
        cp = set(cover(d.payload))
        uc = set()
        for k in kids:
            uc |= set(cover(k.payload))
        rows.append({
            "d": d.did, "kids": [k.did for k in kids], "rank": d.rank,
            "mem": len(pm), "umem": len(um), "miss": sorted(pm - um), "stay": sorted(stay),
            "cov": len(cp), "ucov": len(uc), "cmiss": sorted(cp - uc),
            "plen": len(d.payload), "kplen": [len(k.payload) for k in kids],
        })
    print(f"  {'父':<6}{'子':<16}{'秩':>3}{'|M|':>5}{'|∪M|':>6}{'归属漏':>7}"
          f"{'滞留':>5}{'|C|':>6}{'|∪C|':>7}{'语义漏':>7}")
    for r in rows:
        flag = " ★" if r["cmiss"] else ""
        print(f"  {r['d']:<6}{str(r['kids']):<16}{r['rank']:>3}{r['mem']:>5}{r['umem']:>6}"
              f"{len(r['miss']):>7}{len(r['stay']):>5}{r['cov']:>6}{r['ucov']:>7}"
              f"{len(r['cmiss']):>7}{flag}")
    tot_m = sum(len(r["miss"]) for r in rows)
    tot_c = sum(len(r["cmiss"]) for r in rows)
    print(f"  ── 合计：归属漏 {tot_m} 项｜语义漏 {tot_c} 项"
          f"｜滞留 {sum(len(r['stay']) for r in rows)} 项")
    for r in rows:
        if r["cmiss"]:
            print(f"     ★ {r['d']}（子 {r['kids']}）语义漏 {r['cmiss'][:5]}"
                  f"；归属漏 {r['miss'][:5]}；滞留 {r['stay'][:5]}"
                  f"；payload {r['plen']} / 子 {r['kplen']}")
            for x in r["cmiss"][:3]:
                print(f"       · {x} 在树={x in kernel.items}"
                      f" 在members(父)={x in set(kernel.members_of(kernel.direction(r['d'])))}"
                      f" 在滞留={x in set(kernel.stayed_of(kernel.direction(r['d'])))}")
    return tot_m, tot_c


dump("A 批建后")
insert_items(kernel, new)
dump(f"B 维护 {len(new)} 项后")
remove_items(kernel, removable, cover=cover)
dump(f"B′ 删 {len(removable)} 项后")
