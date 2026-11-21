"""探针：**新谓词下 `B19` 能不能红？** —— 为改注入夹具找一个「基线绿 + 注入红」的构造。

## 背景

`B19` 的谓词 2026-10-07 从「`细化量 ≤ 0`（父 − max子）」改成「**没有任何一个子严格更小**」。
改完注入夹具就红了（不是判据红，是**夹具**红）：原来的注入「**一个**子退回父 payload」
在新谓词下**不算**没变细 —— 另一侧的子仍然严格更小。

试过的两条路都**不成立**，而且理由是**构造性**的（写在这里免得下一个人重走）：

    路一  注入「**每个**子都退回父 payload」
          ⇒ 两个子的代价都是 0 ⇒ 内核把成员全判给靠前那个
          ⇒ 另一侧为空 ⇒ `§K2` 判「分不开」⇒ **这一层根本不建** ⇒ 判据「未展开」
          ⚠️ 根因：`reach` 的 `代价 == 0 ⟺ 项 ∈ payload`（**不是** ∈ 覆盖），
             所以「两侧覆盖都 ⊇ 父」时，成员在两侧**都**是 0 分 ⇒ 必然平手。
    路二  注入「把 payload 切成两半」
          ⇒ 覆盖 = `∪ rev[锚点]`；两半都 ⊇ 父 ⟺ 两半各自都**撑满**父的 rev 并
          ⇒ 这要求图里**存在两个反向锥相同**的锚点（= 同一强连通分量）
          ⇒ 而**只要图里有这种冗余，原插件按 id 取中点也会撞上它**
          ⇒ **基线就红**，过不了「基线绿 + 注入红」

⇒ 所以注入**不能**是「改插件」，只能是「**改语料**」：判据要抓的那个现象是
   **图的性质**（反向锥冗余），不是插件代码的毛病。本探针量三档：

    ① 链（无冗余）        —— 期望**绿**（当基线）
    ② 环 N=2048（全冗余） —— 期望**红**（当注入）
    ③ 环 N=1024           —— 看阈值 N=10 卡在哪

    cd repo
    python ../outputs/_probe_b19_injection.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "repo"))

from ldv.checks._fixtures import coverage_of, items as make_items  # noqa: E402
from ldv.checks.coverage import PROGRESS_N, progress_profile  # noqa: E402
from ldv.core.kernel import Kernel  # noqa: E402
from ldv.corpus.loader import Node  # noqa: E402
from ldv.plugins.reach import ReachPlugin  # noqa: E402


def chain(n: int):
    ids = [f"c{i:05d}" for i in range(n)]
    nodes = {i: Node(id=i, fields={"type": i}, keys=frozenset({i})) for i in ids}
    edges = {i: (frozenset({ids[k + 1]}) if k + 1 < n else frozenset())
             for k, i in enumerate(ids)}
    return nodes, edges


def cycle(n: int):
    ids = [f"c{i:05d}" for i in range(n)]
    nodes = {i: Node(id=i, fields={"type": i}, keys=frozenset({i})) for i in ids}
    edges = {ids[k]: frozenset({ids[(k + 1) % n]}) for k in range(n)}
    return nodes, edges


def measure(name: str, nodes, edges, mutate=None) -> None:
    t0 = time.time()
    plug = ReachPlugin(edges)
    if mutate is not None:
        plug = mutate(plug)
    k = Kernel(plug, make_items(nodes))
    k.build(frozenset(nodes))
    for nid in sorted(nodes):
        k.insert(nid)
    prof = progress_profile(k, coverage_of("reach", nodes, edges))
    dt = time.time() - t0
    verdict = "红" if prof["最长没变细连续段"] >= PROGRESS_N else "绿"
    print(f"  {name:22s} 项 {len(nodes):5d}｜已展开 {prof['已展开方向数']:5d}"
          f"｜没变细 {prof['没变细展开']:5d}｜最长连续段 {prof['最长没变细连续段']:3d}"
          f"｜切得不匀 {prof['切得不匀展开']:5d}｜B19 {verdict}｜{dt:6.2f} s")


class _SplitOffOne:
    """注入：每次只**切掉一个**锚点，其余全给另一侧。

    ⇒ 覆盖完全不变（环上任意非空子集的反向锥都是**整个环**）
    ⇒ 每次展开都「没变细」，而链长 = 项数 − 1（**不靠 log2**）。
    """

    def __init__(self, inner):  # noqa: ANN001
        self.inner = inner

    def __getattr__(self, k):  # noqa: ANN201
        return getattr(self.inner, k)

    def split(self, parent, items):  # noqa: ANN001, ANN201
        ids = sorted(str(it["id"]) for it in items)
        if len(ids) < 2:
            return None
        return (frozenset({ids[0]}), frozenset(ids[1:]))


def main() -> int:
    print(f"=== B19 注入候选（N = {PROGRESS_N}）===")
    measure("① 链 32（基线）", *chain(32))
    measure("② 环 16（基线）", *cycle(16))
    measure("③ 环 16 + 每次切一个（注入）", *cycle(16), mutate=_SplitOffOne)
    measure("④ 环 12 + 每次切一个", *cycle(12), mutate=_SplitOffOne)
    measure("⑤ 环 8 + 每次切一个", *cycle(8), mutate=_SplitOffOne)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
