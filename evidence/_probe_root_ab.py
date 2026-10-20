"""单变量对照：`cli._assemble` 的 reach 根 payload 用「全集」还是「已知子集」。

假设：`D2 覆盖漏 8` 的成因是 `cli` 用 `frozenset(ids)`（全语料）当根，
      而建树时只有 `init_items`（269 项）已知。

用法（cwd = repo）：LDV_CORPUS=openalex-small python ../outputs/_probe_root_ab.py
"""
import sys

sys.path.insert(0, ".")

from ldv.checks._fixtures import load, coverage_of, items
from ldv.checks.coverage import cover_leak_profile
from ldv.core.kernel import Kernel
from ldv.plugins.reach import ReachPlugin
from ldv.flow import insert_items

loaded = load()
nodes, edges, _ = loaded
ids = sorted(nodes)
all_items = items(nodes)

print(f"语料：{len(ids)} 项｜先建 269、再维护 12\n")
print(f"{'根 payload':<28} {'批建后漏':>8} {'维护后漏':>8}")
for label, mk_root in (
    ("frozenset(全部 281 个 id)", lambda known: frozenset(ids)),
    ("frozenset(已知的 269 个 id)", lambda known: frozenset(known)),
):
    hold = set(ids[-12:])
    init = [i for i in ids if i not in hold]
    plug = ReachPlugin(edges)
    kernel = Kernel(plug, {i: all_items[i] for i in init})
    kernel.build(mk_root(init))
    for i in init:
        kernel.insert(i)
    cover = coverage_of("reach", nodes, edges)
    a = cover_leak_profile(kernel, cover)["漏项数"]
    insert_items(kernel, [(i, all_items[i]) for i in ids if i in hold])
    b = cover_leak_profile(kernel, cover)["漏项数"]
    print(f"{label:<28} {a:>8} {b:>8}")
