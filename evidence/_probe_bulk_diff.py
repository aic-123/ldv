# -*- coding: utf-8 -*-
"""诊断：批量 vs 逐个，**逐方向**比成员集 —— 差的是「少成员」还是「结构不同」。

跑法：python outputs/_probe_bulk_diff.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent / "repo"
sys.path.insert(0, str(REPO))
os.chdir(REPO)

from ldv.core.kernel import Kernel                      # noqa: E402
from ldv.corpus.loader import load_nodes                # noqa: E402
from ldv.plugins.keyset import KeysetPlugin             # noqa: E402


def build(nodes, bulk: bool):
    items = {i: n.as_item() for i, n in nodes.items()}
    k = Kernel(KeysetPlugin(), items)
    k.build(k.plugin.merge([]))
    ids = sorted(items)
    if bulk:
        k.bulk_insert(ids)
    else:
        for iid in ids:
            k.insert(iid)
    return k


def main() -> int:
    loaded = None
    from ldv.checks._fixtures import load as _load
    loaded = _load()
    nodes = loaded[0]
    print(f"默认语料 {len(nodes)} 节点")

    k1, k2 = build(nodes, False), build(nodes, True)
    d1, d2 = {d.did: d for d in k1.all_directions()}, {d.did: d for d in k2.all_directions()}
    print(f"方向数：逐个 {len(d1)}｜批量 {len(d2)}")

    # ① 方向集合与父子关系
    only1 = sorted(set(d1) - set(d2))
    only2 = sorted(set(d2) - set(d1))
    print(f"只在逐个里：{only1}｜只在批量里：{only2}")
    par_diff = [(did, d1[did].parent, d2[did].parent) for did in sorted(set(d1) & set(d2))
                if d1[did].parent != d2[did].parent]
    print(f"父不同的方向：{par_diff}")

    # ② 逐方向成员差
    print(f"\n{'方向':>6s}{'逐个成员':>10s}{'批量成员':>10s}  差异")
    total_miss = 0
    for did in sorted(set(d1) & set(d2)):
        m1, m2 = set(k1.members_of(d1[did])), set(k2.members_of(d2[did]))
        if m1 != m2:
            miss, extra = sorted(m1 - m2), sorted(m2 - m1)
            total_miss += len(miss)
            print(f"{did:>6s}{len(m1):>10d}{len(m2):>10d}  批量少 {len(miss)} {miss[:4]}"
                  f"｜批量多 {len(extra)} {extra[:4]}")
    print(f"\n批量共少 {total_miss} 个成员（若全是「少」而没有「多」，说明批量漏了 insert 的补写）")

    # ③ 子方向覆盖是否已含全部成员（看漏在哪一层）
    for did in sorted(set(d1) & set(d2)):
        if did not in d2:
            continue
        kids = k2.children_of(d2[did])
        if not kids:
            continue
        union = set()
        for c in kids:
            union |= set(k2.members_of(c))
        d_members = set(k2.members_of(d2[did]))
        if d_members - union:
            print(f"  {did}：{len(d_members - union)} 个成员不在任何子方向里"
                  f"（批量版没补）—— 例 {sorted(d_members - union)[:3]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
