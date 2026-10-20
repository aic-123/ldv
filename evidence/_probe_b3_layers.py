"""回答「全量 `b3` 还要多久」—— 按层抽样量单次 `penalty` 的耗时，再乘回调用数。

## 为什么不能直接量

全量 `b3` 已经在跑（>80 分钟没出结果）。再跑一遍要再等同样久。

## 做法

`b3` 的建表 = Σ_层 (层内方向数 × 项数) 次 `call_penalty`，**逐层独立**。
方向 `d` 的 payload 大小 ≈ `项数 / 层内方向数`；**payload 里的项**是 O(1)（距离 0），
**payload 外**的项才走 BFS。所以：

    该层估计耗时 ≈ 层内方向数 × (项数 − payload 大小) × 平均 BFS 单次耗时

## ⚠️ 第一版是错的，错在**只抽了一个锚点**

第一版每层只抽 1 个随机锚点集，于是「锚点大小都是 1」的 `2048` 层与 `3718` 层
量出 **314 µs vs 57.7 µs** —— 相差 5 倍。而这两层的调用分布**完全一样**
（都是「一个锚点、随机一项」）⇒ 它们应该相等，`3718` 层还应更贵（调用数 ×1.8）。
⇒ 那不是形状，是**抽样噪声**：随机锚点可不可达，方差极大。

⇒ 第二版：**每层抽多个锚点集**，且**只抽 payload 外**的项。

## ★ 用已知答案当对照组（这是能不能信它的唯一依据）

`1600` 项上新实现 `b3` 的**实测值是 45.43 s**（`outputs/_b3_scaling_new.txt`）。
⇒ 先拿 `--cap 1600` 跑本脚本，**估计必须落在实测附近**，否则这个估计器不成立，
  也就不许拿它去说 `3907` 要多久。

    LDV_CORPUS=openalex-citations python outputs/_probe_b3_layers.py 1600
    LDV_CORPUS=openalex-citations python outputs/_probe_b3_layers.py        # 全量

## 已知偏差（方向不确定，故必须靠对照组兜）

    ① 锚点是**合成**的（随机抽 `项数 / 层内方向数` 个），不是真实 payload。
       真实 payload = 该方向自己的成员，是按图上位置**聚在一起**的，不是均匀随机。
    ② 主跑与本次探针**同时在跑** ⇒ 两边都偏慢（估计偏**大**）。

用法：`python outputs/_probe_b3_layers.py [CAP] [DRAWS] [PICKS]`
"""
from __future__ import annotations

import random
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent / "repo"
sys.path.insert(0, str(REPO))

from ldv.checks._fixtures import items, load  # noqa: E402
from ldv.plugins.reach import ReachPlugin  # noqa: E402
from ldv.run_checks import cap_corpus  # noqa: E402


def layers_for(n: int) -> list[int]:
    """二叉树的逐层节点数：从 1 起翻倍，最后一层吃掉余数。

    合计必须是 `2n − 1`（二叉树节点数）。这一条由 `main()` 当场验，
    并与实测日志（`outputs/_b3_scaling_new.txt` 的「各层方向数」）对账。
    """
    total = 2 * n - 1
    out: list[int] = []
    acc, cur = 0, 1
    while acc + 2 * cur <= total:
        acc += cur
        out.append(cur)
        cur *= 2
    out.append(total - acc)
    return out


class _Fake:
    """`penalty` 只读 `d.payload` —— 造一个只带这个属性的壳就够。"""

    def __init__(self, payload):
        self.payload = payload
        self.did = "probe"


def main(argv: list[str]) -> int:
    cap = int(argv[1]) if len(argv) > 1 else 0
    draws = int(argv[2]) if len(argv) > 2 else 8      # 每层抽几个锚点集
    picks = int(argv[3]) if len(argv) > 3 else 30     # 每个锚点集抽几个「锚点外」的项

    loaded = load()
    if loaded is None:
        print("⚠ 找不到语料 —— 报「跳过」，不报「通过」")
        return 0
    if cap:
        loaded, cut = cap_corpus(loaded, cap)
        print(f"⚠ 已截到前 {cap} 项（截掉 {cut} 项）")
    nodes, edges, _ = loaded
    n = len(nodes)
    layers = layers_for(n)
    if sum(layers) != 2 * n - 1:
        print(f"✗ 层大小合计 {sum(layers)} ≠ 2×{n}−1 = {2 * n - 1} —— 推导错了，停")
        return 2
    print(f"语料 {n} 项 / {sum(len(v) for v in edges.values())} 边"
          f" · 每层 {draws} 个锚点集 × 每个抽 {picks} 项")
    print(f"推导的各层方向数 {'/'.join(map(str, layers))}（合计 {sum(layers)} = 2n−1）")

    plug = ReachPlugin(edges)
    it = items(nodes)
    ids = sorted(it)
    rng = random.Random(20261006)

    print()
    print(f"{'层内方向数':>10}{'锚点大小':>10}{'调用数':>9}{'平均 µs':>11}{'估计该层':>12}{'占总量':>9}")
    print("─" * 64)
    total = 0.0
    per_layer: list[tuple[int, float]] = []
    for L in layers:
        k = max(1, n // L)
        calls = 0
        dt = 0.0
        for _ in range(draws):
            anchors = frozenset(rng.sample(ids, k))
            d = _Fake(anchors)
            #: ⚠️ 必须排掉**这一轮的**锚点 —— 锚点内的项距离恒为 0（O(1)），
            #:    把它们混进来会把平均耗时**压低**（第一版就栽在这类偏差上）。
            pool = [i for i in ids if i not in anchors]
            picked = rng.sample(pool, min(picks, len(pool)))
            t = time.perf_counter()
            for i in picked:
                plug.penalty(d, it[i])
            dt += time.perf_counter() - t
            calls += len(picked)
        per_call = dt / max(calls, 1)
        est = per_call * L * (n - k)
        total += est
        per_layer.append((L, est))
        print(f"{L:>10}{k:>10}{calls:>9}{per_call * 1e6:>11.1f}{est:>11.1f}s")

    print("─" * 64)
    print(f"合计估计 **{total / 60:.1f} 分钟**（{total:.0f} s）")
    print()
    print("各层占总量（从大到小）：")
    for L, est in sorted(per_layer, key=lambda x: -x[1]):
        if est / total > 0.001:
            print(f"  {L:>6} 个方向   {est / 60:>8.1f} 分钟   {est / total * 100:>5.1f}%")
    print()
    print("⚠️ 估计，不是读数。偏差见脚本开头；**先拿 `--cap 1600` 与实测 45.43 s 对照**。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
