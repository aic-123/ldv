"""建内核的 A/B —— 缓存到底参不参与建造、参进去是快还是慢。

## 要回答的问题

`MEASUREMENTS` 结果八 ⑦ 与 `README` ⑥″ 里原来写着

    建内核**不变**：103.49 s（缓存不参与建造）

**这句话没有读数撑着**：那个 103.49 s 是改造**之前**量的，改造**之后**没量过。
而且「缓存不参与建造」是**读代码**得出的结论 —— 但 `split` / `merge` 里
`reach.signature` 每层每方向都要调，`signature` **是**带缓存的，
`_sinks` 也从「每次重算」提到了 `__init__`。所以这句话**可能是假的**。

## 三臂

    HEAD      改造前的 `reach.py`（缓存键 `(item, payload)`，`_sinks` 每次重算）
    新·缓存开  改造后（默认 `cache=True`）
    新·缓存关  改造后但 `cache=False`（把缓存摘掉，只留算法改动）

三臂的**内核必须逐字节相同** —— 否则测的就不是「缓存的开销」，是「建出了不同的树」。
所以每臂都算一个结构指纹并比对。

## 用法

    LDV_CORPUS=openalex-citations python outputs/_measure_kernel_build.py 800
    LDV_CORPUS=openalex-citations python outputs/_measure_kernel_build.py 3907 --arm new-on
    LDV_CORPUS=openalex-citations python outputs/_measure_kernel_build.py 3907 --arm new-off

⚠️ 换 `reach.py` 由**外面**做（`git show HEAD:…` / 从备份还原），
   本脚本只负责「在这个版本的代码上计时 + 打指纹」。
"""
from __future__ import annotations

import gc
import hashlib
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent / "repo"
sys.path.insert(0, str(REPO))

from ldv.checks._fixtures import build_reach, load  # noqa: E402
from ldv.run_checks import cap_corpus  # noqa: E402


def kernel_fingerprint(k) -> str:
    """方向图的指纹：每个方向的 (id, 父, 成员, 载荷)。

    ⚠️ 载荷先 `sorted` 再 `repr` —— `frozenset` 的 `repr` 顺序取决于
    **进程级哈希随机化**（`PYTHONHASHSEED`），两个**不同进程**里同一个
    frozenset 能打出两种顺序。不排就变成「拿一个会随进程变的东西当指纹」，
    三臂一比全不等，还会被读成「缓存建出了不同的树」。
    """
    rows = []
    dirs = k.all_directions()
    parents_of: dict[str, list[str]] = {d.did: [] for d in dirs}
    for p in dirs:
        for c in k.children_of(p):
            parents_of[c.did].append(p.did)
    for d in dirs:
        rows.append((
            d.did,
            tuple(sorted(parents_of[d.did])),
            tuple(sorted(k.members_of(d))),
            repr(sorted(d.payload, key=repr)),
        ))
    rows.sort()
    blob = json.dumps(rows, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def main(argv: list[str]) -> int:
    cap = 0
    arm = "unknown"
    rounds_n = 3
    for a in argv[1:]:
        if a.startswith("--arm="):
            arm = a.split("=", 1)[1]
        elif a.startswith("--rounds="):
            rounds_n = int(a.split("=", 1)[1])
        elif a.isdigit():
            cap = int(a)

    loaded = load()
    if loaded is None:
        print("⚠ 找不到语料 —— 报「跳过」，不报「通过」")
        return 0
    if cap:
        loaded, cut = cap_corpus(loaded, cap)
    nodes, edges, _ = loaded
    n = len(nodes)

    if arm == "new-off":
        # 把 `ReachPlugin` 换成一个默认 `cache=False` 的子类。
        # `build_reach` 里是 `from ..plugins.reach import ReachPlugin`（调用时取属性），
        # 所以改模块属性就能生效。
        import ldv.plugins.reach as R

        class _NoCache(R.ReachPlugin):          # type: ignore[misc, valid-type]
            def __init__(self, edges, traverse_budget: int = 10_000, cache: bool = False):
                super().__init__(edges, traverse_budget=traverse_budget, cache=False)

        R.ReachPlugin = _NoCache               # type: ignore[assignment]

    rounds = []
    k = None
    for _ in range(rounds_n):
        gc.collect()          # 去掉「上一轮留下的垃圾」对本轮的影响（见下方 ⚠️）
        t = time.perf_counter()
        k, plug = build_reach(nodes, edges)
        rounds.append(time.perf_counter() - t)
    assert k is not None
    fp = kernel_fingerprint(k)

    best = min(rounds)
    spread = max(rounds) - min(rounds)
    print(f"[{arm}] 语料 {n} 项 / 边 {sum(len(v) for v in edges.values())}")
    print(f"[{arm}] {rounds_n} 轮: " + " / ".join(f"{r:.2f} s" for r in rounds) + f"（极差 {spread:.2f} s）")
    print(f"[{arm}] 最快 {best:.2f} s")
    print(f"[{arm}] 结构指纹 {fp}")
    st = k.stats()
    print(f"[{arm}] 方向 {st.get('方向')} / 叶 {st.get('叶')} / 滞留 {st.get('滞留')}")
    if hasattr(plug, "cache_stats"):
        print(f"[{arm}] 插件计数 {json.dumps(plug.cache_stats(), ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
