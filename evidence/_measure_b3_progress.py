"""全量 `b3` —— **可观测**版。包装生产 `call_penalty`，**不抄一份序列**。

## 为什么要有这个

`outputs/_measure_b3.py` 的全量那一段跑过两轮都没完（`1h02m` / `1h23m41s`），
而且**全程没有任何输出** —— 被停掉时只知道「没跑完」，不知道跑到哪一层、
不知道还剩多久。这正是本仓库那条中心反模式的形状：**空转与通过长得一模一样**。

## 做法

**不抄 `b3_penalty_comparable` 的序列**（抄了就会与生产漂，`run_checks.py` 里
专门记过这条：`_profile_runone_prod.py` 是**包**生产函数，不是抄一份）。

改成**把 `ldv.checks.contract.call_penalty` 换成一个会计数的壳** ——
生产函数每一层、每一方向、每一项都要调它 ⇒ 计数器就是**真实进度**。

    总调用数 = Σ_层 (层内方向数 × 项数)      ← 由内核直接算出来，不是估的
    ⇒ 每 `EVERY` 次打印「已调 / 总量 / 百分比 / 耗时 / 速率 / ETA / **当前在第几层**」

## 用法

    LDV_CORPUS=openalex-citations python outputs/_measure_b3_progress.py
    LDV_CORPUS=openalex-citations python outputs/_measure_b3_progress.py 1600   # 对照组

⚠️ 包装会给每次调用加一个 Python 帧 ⇒ 计时略高于裸跑。差值由**对照组**量出来
（`1600` 项裸跑是 **45.43 s**），跑完要报这个差，别把包装后的数当裸跑的数。
"""
from __future__ import annotations

import bisect
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent / "repo"
sys.path.insert(0, str(REPO))

import ldv.checks.contract as C  # noqa: E402
from ldv.checks._fixtures import build_reach, load  # noqa: E402
from ldv.checks._framework import Report  # noqa: E402
from ldv.run_checks import cap_corpus  # noqa: E402

EVERY = 250_000


def main(argv: list[str]) -> int:
    cap = int(argv[1]) if len(argv) > 1 else 0
    loaded = load()
    if loaded is None:
        print("⚠ 找不到语料 —— 报「跳过」，不报「通过」")
        return 0
    if cap:
        loaded, cut = cap_corpus(loaded, cap)
        print(f"⚠ 已截到前 {cap} 项（截掉 {cut} 项）")
    nodes, edges, _ = loaded
    n = len(nodes)

    t = time.perf_counter()
    kernel, plugin = build_reach(nodes, edges)
    print(f"语料 {n} 项 / {sum(len(v) for v in edges.values())} 边"
          f"｜建内核 {time.perf_counter() - t:.2f} s（{len(kernel.all_directions())} 个方向）",
          flush=True)

    # --- 逐层预算：`b3` 就是按这个顺序跑的（rank 升序，跳过 <2 个方向的层）------
    by_rank: dict[int, int] = {}
    for d in kernel.all_directions():
        by_rank[d.rank] = by_rank.get(d.rank, 0) + 1
    steps: list[tuple[int, int]] = []      # (累计调用数, 层内方向数)
    acc = 0
    for rank in sorted(by_rank):
        L = by_rank[rank]
        if L < 2:
            continue
        acc += L * n
        steps.append((acc, L))
    total = acc
    bounds = [s[0] for s in steps]
    print(f"建表总调用数 **{total:,}**（{len(steps)} 层有 ≥2 个方向）", flush=True)
    print(f"各层方向数 {'/'.join(str(s[1]) for s in steps)}", flush=True)

    # --- 包装生产 `call_penalty` ---------------------------------------------
    real = C.call_penalty
    state = {"n": 0, "t0": 0.0, "layer": -1}

    def counted(plugin_, d, item):  # noqa: ANN001, ANN202
        k = state["n"] = state["n"] + 1
        if k % EVERY == 0:
            el = time.perf_counter() - state["t0"]
            rate = k / el if el else 0.0
            eta = (total - k) / rate if rate else 0.0
            li = bisect.bisect_left(bounds, k)
            tag = ""
            if li != state["layer"]:
                state["layer"] = li
                tag = f"  ← 进入第 {li + 1}/{len(steps)} 层（层内方向数 {steps[li][1]}）"
            print(f"    · {k:>12,} / {total:,}  {k / total * 100:5.1f}%  "
                  f"已用 {el / 60:5.1f} 分  速率 {rate:,.0f}/s  ETA {eta / 60:5.1f} 分{tag}",
                  flush=True)
        return real(plugin_, d, item)

    C.call_penalty = counted
    state["t0"] = time.perf_counter()
    try:
        C.b3_penalty_comparable(kernel, plugin, Report(plugin="x", expects=("B3",)))
    finally:
        C.call_penalty = real
    dt = time.perf_counter() - state["t0"]

    print()
    print(f"新（生产：循环已删，**包装计时**）{dt:.2f} s ｜ 调用 {state['n']:,} / 预算 {total:,}")
    print(f"⚠️ 包装给每次调用多一个 Python 帧 ⇒ 比裸跑略慢。"
          f"对照：1600 项裸跑 45.43 s，本脚本同规模给出的是包装值。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
