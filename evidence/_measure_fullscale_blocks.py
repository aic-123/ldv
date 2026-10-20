"""`run_checks --no-probes` 在**全量 3907 项**上的逐块计时（reach）。

    跑法：PYTHONIOENCODING=utf-8 LDV_CORPUS=openalex-citations \
          python -u outputs/_measure_fullscale_blocks.py

## 为什么要单跑这一份

`outputs/_measure_cover_cost.py` 在 **281 项**上量出「cover 那一族不是瓶颈」（八块合计 0.11 s），
于是当时把「cover 重算是瓶颈」这个假设判成**不成立**。

全量上再量一次，结论**翻过来了**：**一块** cover 度量就是 115.8 s。
⇒ 两个读数都对，但**只有带上尺度才不误导人**。

## 读数（3907 项 / 24156 边 / reach）

    coverage_of(reach) 建闭包        0.80 s
    equiv_classes(reach) [SCC]       1.04 s
    build 批建内核                 103.67 s   （7813 个方向）
    build 维护内核                  78.10 s   （2101 个方向）
    soundness_profile 批建         115.76 s   ← ⚠️ **这一行含内核重建**，见下
    B8(reach)                      145.19 s
    B15(reach)                     309.71 s

⇒ 这七块合计 **753.9 s ≈ 12.6 分钟**。

## ⚠️ 那张表里有一行是错的（已修，记录在此）

`soundness_profile 批建` 那一行原来是：

    T("soundness_profile 批建",
      lambda: soundness_profile(mk(ids)[0], coverage_of("reach", nodes, edges))["越界成员数"])

`mk(ids)[0]` —— **批建内核**。同一个脚本上面刚量过它：**103.67 s**。
⇒ 那 115.76 s 的标签写着 profile，装的是**建内核 + 跑 profile**。

`outputs/_measure_soundness_split.py` 单独验过（同一进程、只有计时范围在变）：

    臂 A `mk(ids)[0]` 建造      75.32 s
    臂 B profile（内核预建）      0.45 s
    臂 C 旧那一行（含重建）       78.31 s      A + B = 75.78 ≈ C（差 3.2%）

⇒ **真正的 profile 是 0.45 s**，不是 115.76 s。而 README 曾从它推出
「这一族有七八块 ⇒ 千秒级」—— **含建造的读数不能按块相乘。**

⚠️ 由此还有一条：**计时块的边界写错，账就记到别的模块头上，而且看不出来** ——
   数字是个正常的大数，不像报错那样会自己喊。**下面这一行已改成内核预建。**

⚠️ 也别把账全记在 `B8` / `B15` 上：在 **281 项**上它们确实占 ~40%，
   但在 **3907 项**上，**内核的重复建造（两次基础建 + `B15` 里 3 次整建）**更大。
   **同一个问题在两个尺度上有两个答案。**
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "repo"))

from ldv.checks._fixtures import (  # noqa: E402
    build_incremental, coverage_of, equiv_classes, load, make_builder, make_reach_root,
)
from ldv.checks._framework import Report  # noqa: E402
from ldv.checks.coverage import soundness_profile  # noqa: E402
from ldv.checks.structure import b8_invalidation_local, b15_reproducible  # noqa: E402


def main() -> int:
    loaded = load()
    if loaded is None:
        print("找不到语料 —— 设 `LDV_CORPUS`")
        return 0
    nodes, edges, _ = loaded
    ids = sorted(nodes)
    print(f"n={len(nodes)} 边={sum(len(v) for v in edges.values())}", flush=True)

    def T(label: str, fn) -> None:  # noqa: ANN001
        t = time.time()
        try:
            r = fn()
        except Exception as exc:  # noqa: BLE001
            r = f"{type(exc).__name__}: {exc}"
        print(f"  {label:32s} {time.time() - t:8.2f}s  {str(r)[:46]}", flush=True)

    T("coverage_of(reach) [闭包]", lambda: type(coverage_of("reach", nodes, edges)).__name__)
    T("equiv_classes(reach) [SCC]", lambda: len(equiv_classes("reach", nodes, edges)))
    mk = make_builder("reach", nodes, edges)
    T("build 批建内核", lambda: len(mk(ids)[0].all_directions()))
    T("build 维护内核",
      lambda: len(build_incremental(mk, nodes, ids[:6], ids[6:]).all_directions()))
    # ⚠️ 内核**预建**，计时块里只剩 profile —— 原来写的是 `soundness_profile(mk(ids)[0], …)`，
    #    那会把 103.67 s 的建造算进 profile 头上（见模块 docstring）。
    ker_b = mk(ids)[0]
    cover_r = coverage_of("reach", nodes, edges)
    T("soundness_profile 批建（内核预建）",
      lambda: soundness_profile(ker_b, cover_r)["越界成员数"])
    T("B8(reach)",
      lambda: b8_invalidation_local(make_reach_root(nodes, edges), ids, Report(plugin="x")))
    T("B15(reach)",
      lambda: b15_reproducible(make_reach_root(nodes, edges), ids, Report(plugin="x")))
    print()
    print("  ⚠️ 这七块只是 `run_one('reach')` 的一部分。见模块 docstring。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
