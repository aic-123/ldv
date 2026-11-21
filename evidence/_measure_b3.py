"""`B3` 那个三重循环值多少时间 —— 在**同一份内核**上比旧实现与新实现。

## 为什么单量这一条

全量 3907 项上 `run_checks --no-probes` 跑不完（>71 分钟，主动停掉）。
逐块量下来，**最大的不是覆盖族**（那个只有 0.69 s），而是 `B3`：

    `b3_penalty_comparable` 原来是三层循环：层内两两配对、每对逐项比
    ⇒ O(层内方向数² × 项数)

而 `B3` 是**判据**（`B1–B19`）⇒ `--no-probes` **关不掉它**。

## 这一脚本量什么

同一份内核、同一个插件，**只有 `b3` 的实现在变**：

    旧  三重循环（改动前逐字）
    新  生产实现（循环已删，理由见 `checks/contract.py`）

并且把那个循环的**精确迭代次数**算出来（不是估的）：

    Σ_层 C(层内方向数, 2) × 项数

用法：
    LDV_CORPUS=openalex-small python outputs/_measure_b3.py
    LDV_CORPUS=openalex-citations LDV_B3_OLD=1 python outputs/_measure_b3.py 800
                                                       ^^^ 可选：先截到前 N 项
    LDV_B3_OLD=1 才会跑旧实现 —— **全量上那是小时级，默认不跑**
"""
from __future__ import annotations

import os
import sys
import time
from math import comb
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent / "repo"
sys.path.insert(0, str(REPO))

from ldv.checks._fixtures import build_reach, load  # noqa: E402
from ldv.checks._framework import Report  # noqa: E402
from ldv.checks.contract import b3_penalty_comparable  # noqa: E402
from ldv.core.interfaces import call_penalty  # noqa: E402
from ldv.run_checks import cap_corpus  # noqa: E402


def ref_b3(kernel, plugin) -> None:
    """**改动前逐字**的 `b3_penalty_comparable`（含那个三重循环）。"""
    bad: list[str] = []
    checked = 0
    for rank in sorted({d.rank for d in kernel.all_directions()}):
        layer = [d for d in kernel.all_directions() if d.rank == rank]
        if len(layer) < 2:
            continue
        table: dict[str, list[float]] = {}
        for d in layer:
            vals: list[float] = []
            for i in sorted(kernel.items):
                try:
                    vals.append(call_penalty(plugin, d, kernel.items[i]))
                except Exception as exc:  # noqa: BLE001
                    bad.append(f"{d.did} 的代价抛异常：{type(exc).__name__}: {exc}")
                    vals.append(float("nan"))
            table[d.did] = vals
            checked += len(vals)
            if any(v != v for v in vals):
                bad.append(f"{d.did} 的代价里有 NaN（不可比）")
        ids = sorted(table)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                for a, b in zip(table[ids[i]], table[ids[j]]):
                    if not (a < b or a == b or a > b):
                        bad.append(f"{ids[i]} vs {ids[j]} 的一项不可比：{a} / {b}")
                        break
    print(f"      旧实现 bad={len(bad)} checked={checked}")


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
    print(f"语料 {n} 项 / {sum(len(v) for v in edges.values())} 边", flush=True)

    t = time.perf_counter()
    kernel, plugin = build_reach(nodes, edges)
    print(f"建内核 {time.perf_counter() - t:.2f} s"
          f"（{len(kernel.all_directions())} 个方向）", flush=True)

    ranks: dict[int, int] = {}
    for d in kernel.all_directions():
        ranks[d.rank] = ranks.get(d.rank, 0) + 1
    layers = sorted(ranks.items())
    print(f"各层方向数 " + "/".join(str(c) for _, c in layers), flush=True)

    pairs = sum(comb(c, 2) for _, c in layers if c >= 2)
    iters = pairs * n
    print(f"层内配对数 ΣC(L,2) = {pairs:,}｜× 项数 {n} ⇒ 三重循环迭代 **{iters:,}** 次",
          flush=True)

    print()
    print("新（生产：循环已删）…", flush=True)
    t = time.perf_counter()
    b3_penalty_comparable(kernel, plugin, Report(plugin="x", expects=("B3",)))
    t_new = time.perf_counter() - t
    print(f"      新 {t_new:.2f} s", flush=True)

    if os.environ.get("LDV_B3_OLD") == "1":
        print("旧（三重循环）…", flush=True)
        t = time.perf_counter()
        ref_b3(kernel, plugin)
        t_old = time.perf_counter() - t
        print(f"      旧 {t_old:.2f} s   ⇒ ×{t_old / max(t_new, 1e-9):.1f}", flush=True)
    else:
        print("（**没跑旧实现** —— 全量上那是小时级。要看就设 `LDV_B3_OLD=1`）")
        print(f"  ⇒ 旧实现要跑 {iters:,} 次迭代，而新实现不跑它。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
