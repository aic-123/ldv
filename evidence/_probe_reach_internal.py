"""实测：`reach` 出路 (1)（根记账）落地之后，**内部**方向的越界还剩多少？

    cd outputs
    LDV_CORPUS=openalex-small PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python _probe_reach_internal.py

## 为什么要问这个

设计文档 `§10.2 A′` 说「它要治的是**剩下的内部**越界（把根改成全集之后仍在的那一批）」
—— 而落地的是**出路 (1) 根记账**（不是「根改成全集」）。两者不是同一个东西：

    出路 (1) 根记账   根覆盖之外的项**不塞进结构** ⇒ 它们**不在** `members` 里
                      ⇒ 越界数里看不见它们（记在 `stats()['根覆盖之外']`）

⇒ 所以「维护路径越界 0/33」这个读数**只说明根那一层修好了**，
   内部方向是不是还剩越界，要**分方向**量才知道 —— A′ 是否还有活靶子，取决于它。

判据（本探针要能红）：只要有任何**非根**方向的 `members ⊄ 覆盖`，
就必须列出来；一条都没有才说明 A′ 的靶子是空的。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "repo"))

from ldv.checks._fixtures import (  # noqa: E402
    build_incremental,
    coverage_of,
    load,
    make_builder,
)


def over_reach(kernel, cover) -> list[tuple[str, int, int, int]]:
    """返回 [(方向 id, rank, 成员数, 越界数), …] —— 越界 = members − 覆盖。"""
    out = []
    for d in kernel.all_directions():
        mem = set(kernel.members_of(d))
        cov = set(cover(d.payload))
        out.append((d.did, d.rank, len(mem), len(mem - cov)))
    return out


def main() -> None:
    loaded = load()
    if loaded is None:
        print("语料不在")
        return
    nodes, edges, _ = loaded
    ids = sorted(nodes)
    cover = coverage_of("reach", nodes, edges)

    print(f"语料：{len(nodes)} 项 / {sum(len(v) for v in edges.values())} 边\n")

    for tag, k in (
        ("一次建完", make_builder("reach", nodes, edges)(ids)[0]),
        ("维护 30（先建 6）", build_incremental(make_builder("reach", nodes, edges),
                                                nodes, ids[:6], ids[6:])),
    ):
        rows = over_reach(k, cover)
        total_mem = sum(r[2] for r in rows)
        total_bad = sum(r[3] for r in rows)
        n_root = sum(r[3] for r in rows if r[1] == 0)
        n_inner = sum(r[3] for r in rows if r[1] > 0)
        print(f"【{tag}】")
        print(f"  方向数 {len(rows)}  成员总数 {total_mem}  越界合计 {total_bad}"
              f"   （根那一层 {n_root} ｜ **内部 {n_inner}**）")
        stats = k.stats() if hasattr(k, "stats") else {}
        if "根覆盖之外" in stats:
            print(f"  stats()['根覆盖之外'] = {stats['根覆盖之外']}")
        bad_rows = [r for r in rows if r[3] > 0]
        if bad_rows:
            print("  有越界的方向（最多列 10 个）：")
            for did, rank, mem, bad in sorted(bad_rows, key=lambda r: -r[3])[:10]:
                print(f"    {did}  rank {rank}  成员 {mem}  越界 {bad}")
        else:
            print("  ⇒ **没有任何方向越界**")
        print()


if __name__ == "__main__":
    main()
