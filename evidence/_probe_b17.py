"""查 `B17` 在外部语料上变红的根因（`reach` 方向）。

问三件事，分开问：
  ① 被拆开的那些等价类，成员在 oracle 眼里**真的等价**吗？
     （`_mutually_reachable` = 强连通分量；真正的不可分 = **正向闭包相同**）
  ② 它们在核心里走了**同一条路**吗？在哪一层分岔？
  ③ 换一个 oracle（正向闭包）会怎样？

跑法：PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python outputs/_probe_b17.py <语料名>
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent / "repo"
sys.path.insert(0, str(REPO))

from ldv.checks._fixtures import (  # noqa: E402
    _forward_closure, _mutually_reachable, build_reach, equiv_classes, load,
)
from ldv.checks.equivalence import _leaves, _split_classes  # noqa: E402
from ldv.core.interfaces import call_hit, call_penalty  # noqa: E402
from ldv.core.kernel import Query  # noqa: E402


def main(argv: list[str]) -> int:
    name = argv[1] if len(argv) > 1 else "openalex-small"
    os.environ["LDV_CORPUS"] = name
    nodes, edges, _ = load()
    print(f"语料 {name}：{len(nodes)} 项 / {sum(len(v) for v in edges.values())} 边", flush=True)

    kernel, plug = build_reach(nodes, edges)
    scc = _mutually_reachable(edges)
    fwd = _forward_closure(edges)

    # ── ① oracle 到底多细：SCC vs 正向闭包 ────────────────────────────────
    scc_groups: dict[frozenset[str], list[str]] = {}
    for i, c in scc.items():
        scc_groups.setdefault(c, []).append(i)
    fwd_groups: dict[frozenset[str], list[str]] = {}
    for i, c in fwd.items():
        fwd_groups.setdefault(c, []).append(i)
    n_scc_multi = sum(1 for g in scc_groups.values() if len(g) > 1)
    n_fwd_multi = sum(1 for g in fwd_groups.values() if len(g) > 1)
    max_scc = max((len(g) for g in scc_groups.values()), default=0)
    max_fwd = max((len(g) for g in fwd_groups.values()), default=0)
    print(f"\n① oracle 的粗细：")
    print(f"   SCC（强连通分量）  非单点组 {n_scc_multi} 个，最大 {max_scc}")
    print(f"   正向闭包（真·不可分）非单点组 {n_fwd_multi} 个，最大 {max_fwd}")
    # SCC 比正向闭包细多少
    same = sum(1 for g in scc_groups.values() if len({fwd[i] for i in g}) == 1)
    print(f"   {len(scc_groups)} 个 SCC 里，有 {same} 个的正向闭包完全一致"
          f"（⇒ 那些组**确实是**不可分的）")

    # ── ② 被拆开的等价类 ─────────────────────────────────────────────────
    leaves = _leaves(kernel)
    torn = _split_classes(kernel, leaves, scc)
    print(f"\n② `B17(b)` 说被拆开的等价类：{len(torn)} 个", flush=True)
    for c in torn[:4]:
        ms = sorted(c)
        print(f"\n   类 {ms}")
        print(f"     正向闭包相同？ {len({fwd[i] for i in ms}) == 1}"
              f"（闭包大小 {[len(fwd[i]) for i in ms]}）")
        for i in ms:
            cone = kernel.cone(i)
            leaf = [d.did for d in leaves if i in kernel.members_of(d)]
            print(f"     {i}: 锥 {'→'.join(cone)}  落在叶 {leaf}")
        # 从根开始找分岔点
        common = None
        for i in ms:
            p = set(kernel.cone(i))
            common = p if common is None else (common & p)
        print(f"     共同锥前缀（交集）: {sorted(common, key=lambda s: int(s[1:]))}")

    # ── ③ 用正向闭包当 oracle，还红吗 ─────────────────────────────────────
    torn_fwd = _split_classes(kernel, leaves, fwd)
    print(f"\n③ 换 oracle（正向闭包）后被拆开的类：{len(torn_fwd)} 个"
          f"（原 SCC oracle 下 {len(torn)} 个）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
