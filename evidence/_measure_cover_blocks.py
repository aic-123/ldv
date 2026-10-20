"""全量上**逐块**计时：覆盖族这七八块，各自的时间花在哪。

## 为什么非逐块不可

`README` 的「已知的未做」里写着「一块 cover 度量 115.8 s ← 而这一族在 `run_one`
里有七八块」，据此把覆盖族记成最大的一块。**那是从块时间推的，不是从 oracle 推的。**

而 oracle 自己的成本这次量出来了（`outputs/_measure_cover_dedup_equiv.py`）：

    3907 项 / 24156 边    reach 的 39650 次调用：**72.7 s → 0.48 s**

⇒ 如果整族只有 ~75 s，那 115.8 s 里**大部分不是 oracle**，是别的东西。
**「块贵」与「oracle 贵」是两件事** —— 不逐块拆开，就会把优化做在错的地方。

## 这一脚本量什么

每个块两趟，**只有 `cover` 在变**（同一份内核、同一批 payload）：

    臂 旧  `fwd[x] & anchors`（改动前逐字）
    臂 新  反向闭包 + 去重（生产）

并且把每块**不调 `cover` 的那部分**单独量出来 ——
`kernel.all_directions()` / `members_of` / `children_of` 的遍历开销。
两块一减，就知道钱花在「遍历」还是「oracle」上。

用法：
    LDV_CORPUS=openalex-citations python outputs/_measure_cover_blocks.py
    LDV_CORPUS=openalex-small python outputs/_measure_cover_blocks.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "repo"))

from ldv.checks._fixtures import (  # noqa: E402
    _forward_closure, build_incremental, coverage_of, load, make_builder,
)
from ldv.checks.coverage import (  # noqa: E402
    b16_members_covered, cover_leak_profile, progress_profile, soundness_profile,
)
from ldv.checks._framework import Report  # noqa: E402
from ldv.run_checks import MAINT_INIT, batch_kernel, maintenance_kernel  # noqa: E402


def old_cover(which, nodes, edges):
    """改动前逐字 —— 只对 `reach` 有意义，另两个方向本来就一样。"""
    if which != "reach":
        return coverage_of(which, nodes, edges, dedup=False)
    fwd = _forward_closure(edges, universe=nodes)

    def cover(payload):
        anchors = set(payload)
        return frozenset(x for x in nodes if fwd.get(x, frozenset()) & anchors)

    return cover


def timeit(fn, reps: int = 1) -> float:
    best = float("inf")
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t0)
    return best


def traversal_only(kernel) -> None:
    """**不调 `cover`** 的遍历开销 —— 每个块都要走一遍的那些调用。"""
    for d in kernel.all_directions():
        kernel.members_of(d)
    for d in kernel.all_directions():
        if kernel.children_of(d):
            for _ in kernel.children_of(d):
                pass


def main() -> int:
    loaded = load()
    if loaded is None:
        print("⚠ 找不到语料 —— 报「跳过」，不报「通过」")
        return 0
    nodes, edges, _ = loaded
    n = len(nodes)
    which = os.environ.get("LDV_WHICH", "reach")
    print(f"语料 {n} 项 / {sum(len(v) for v in edges.values())} 边｜方向 {which}")
    print()

    t0 = time.perf_counter()
    kernel, _, _, _ = batch_kernel(which, nodes, edges)
    inc = maintenance_kernel(which, nodes, edges)
    t_build = time.perf_counter() - t0
    print(f"建两个内核 {t_build:.2f} s"
          f"（批建 {len(kernel.all_directions())} 方向 / 维护 {len(inc.all_directions())} 方向）")
    print()

    old = old_cover(which, nodes, edges)
    new = coverage_of(which, nodes, edges)

    t_trav = timeit(lambda: (traversal_only(kernel), traversal_only(inc)))

    rows: list[tuple[str, float, float]] = []

    def block(name, fn):
        t_old = timeit(lambda: fn(old))
        t_new = timeit(lambda: fn(new))
        rows.append((name, t_old, t_new))

    block("健全性（两条路径）",
          lambda c: (soundness_profile(kernel, c), soundness_profile(inc, c)))
    block("B16（两条路径）",
          lambda c: (b16_members_covered(kernel, c, Report(plugin="x"), path="批建"),
                     b16_members_covered(inc, c, Report(plugin="x"), path="维护")))
    block("覆盖不漏（两条路径）",
          lambda c: (cover_leak_profile(kernel, c), cover_leak_profile(inc, c)))
    block("进步量（批建）", lambda c: progress_profile(kernel, c))

    print(f"{'块':<22}{'旧':>10}{'新':>10}{'倍数':>9}")
    print("─" * 53)
    tot_old = tot_new = 0.0
    for name, a, b in rows:
        tot_old += a
        tot_new += b
        print(f"{name:<22}{a:>10.2f}{b:>10.2f}{a / max(b, 1e-9):>8.1f}×")
    print("─" * 53)
    print(f"{'合计':<22}{tot_old:>10.2f}{tot_new:>10.2f}{tot_old / max(tot_new, 1e-9):>8.1f}×")
    print()
    print(f"其中**不调 cover** 的遍历（两条路径各走一遍）：{t_trav:.2f} s")
    print(f"⇒ 旧：oracle 占 {tot_old - t_trav:.2f} s，遍历占 {t_trav:.2f} s")
    print(f"⇒ 新：oracle 占 {tot_new - t_trav:.2f} s，遍历占 {t_trav:.2f} s")
    print()
    print("⚠️ **「块贵」与「oracle 贵」是两件事。** 上面这一拆就是为了不让")
    print("   「优化 oracle」看起来像「优化了整块」—— 遍历那一份不受影响。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
