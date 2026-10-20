import sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent / "repo"
sys.path.insert(0, str(ROOT))
from ldv.checks._fixtures import load
from ldv.checks.coverage import corpus_fingerprint
from ldv.checks.abstraction import load_spec_file, spec_for
from ldv.core.views import (coarsest_stable_refinement, partition_of, propagate,
                            propagate_naive, refines_partition, restrict_spec, stable)

nodes, edges, _ = load()
doc = load_spec_file()
which = str(doc["方向"])
spec, _ = spec_for(doc, which, corpus_fingerprint(nodes, edges))
q_full = coarsest_stable_refinement(spec)
U = list(spec.universe)
print(f"U = {len(U)}  全量重算 = {len(q_full)} 块\n")
print(f"{'丢掉的方向':<28} {'旧Q':>5} {'传播':>5} {'重算':>5} {'传播稳定':>8} {'传播⊑重算':>9} {'重算⊑Qext':>10}")
for label, drop in [
    ("D24（universe 最后一个）", frozenset({"D24"})),
    ("D22", frozenset({"D22"})),
    ("D10,D15,D22,D3（最大块）", frozenset({"D10","D15","D22","D3"})),
    ("全部叶（13 个）", frozenset(d for d in U if d not in {x for x,_ in spec.relation})),
]:
    sub = restrict_spec(spec, drop)
    q_old = coarsest_stable_refinement(sub)
    q_new = propagate(q_old, spec)
    ok, _ = stable(spec, q_new)
    # Q_ext = 旧Q + 新方向各自成块
    known = frozenset().union(*q_old)
    qext = partition_of(list(q_old) + [frozenset({d}) for d in spec.universe if d not in known])
    print(f"{label:<28} {len(q_old):>5} {len(q_new):>5} {len(q_full):>5} "
          f"{str(ok):>8} {str(refines_partition(q_full, q_new)):>9} "
          f"{str(refines_partition(qext, q_full)):>10}")
