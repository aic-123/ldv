# -*- coding: utf-8 -*-
"""实验：**新增一个维度**（材料里已有、但没当键用的字段），看结构会不会真的变细。

跑法（cwd = repo）：
    LDV_CORPUS=ext-coauth python ../outputs/_probe_new_dimension.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, ".")

from ldv.checks._fixtures import find_corpus              # noqa: E402
from ldv.corpus.loader import ENUM_FIELDS, load_edges, load_nodes  # noqa: E402
from ldv.core.kernel import Kernel                       # noqa: E402
from ldv.plugins.keyset import KeysetPlugin              # noqa: E402


def shape(nodes) -> tuple[int, int, float]:
    items = {i: x.as_item() for i, x in nodes.items()}
    k = Kernel(KeysetPlugin(), items)
    k.build(k.plugin.merge([]))
    k.bulk_insert(sorted(items))
    ds = k.all_directions()
    leaves = [d for d in ds if not k.children_of(d)]
    mx = max((len(k.members_of(d)) for d in leaves), default=0)
    return len(ds), mx, round(mx / max(1, len(nodes)) * 100, 1)


def main() -> int:
    name = os.environ.get("LDV_CORPUS") or "(默认)"
    root = find_corpus()
    if root is None:
        print(f"{name}: 找不到语料")
        return 1
    nodes = load_nodes(root)

    # 找「有 2..n-1 个取值」的字符串字段（**没进** ENUM_FIELDS 的）
    vals: dict[str, set[str]] = {}
    for n in nodes.values():
        for f, v in n.fields.items():
            if isinstance(v, str) and v and f not in ENUM_FIELDS:
                vals.setdefault(f, set()).add(v)
    cand = {f: v for f, v in vals.items()
            if 2 <= len(v) <= max(2, len(nodes) - 1) and f not in ("id",)}
    if not cand:
        print(f"{name}\t节点 {len(nodes)}\t**没有可用作新维度的字段**（全都在 ENUM_FIELDS 里或只有一个取值）")
        return 0

    # 挑「取值数最多」的那个字段（最能切）；再挑它里面最平衡的取值
    field = max(cand, key=lambda f: (len(cand[f]), f))
    best_val, best = "", -1.0
    for v in sorted(cand[field]):
        hit = sum(1 for n in nodes.values() if n.fields.get(field) == v)
        d = min(hit, len(nodes) - hit) / len(nodes)
        if d > best:
            best_val, best = v, d

    before = shape(nodes)
    key = f"{field}={best_val}"
    for n in nodes.values():
        if n.fields.get(field) == best_val:
            n.keys = frozenset(set(n.keys) | {key})
    after = shape(nodes)
    print(f"{name}\t节点 {len(nodes)}\t字段 `{field}`（{len(cand[field])} 个取值）"
          f"\t新维度 `{key}`（区分度 {best:.3f}）"
          f"\t方向 {before[0]} → **{after[0]}**\t最大叶 {before[1]} → **{after[1]}**"
          f"（叶占比 {before[2]}% → {after[2]}%）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
