"""把「一块 cover 度量 115.8 s」拆开：**这 115.8 s 里有 103.7 s 不是它。**

## 为什么要拆

`outputs/_measure_fullscale_blocks.py` 里那一行是：

    T("soundness_profile 批建",
      lambda: soundness_profile(mk(ids)[0], coverage_of("reach", nodes, edges))["越界成员数"])

`mk(ids)[0]` —— **批建内核**。而同一个脚本第 75 行量过它：**103.67 s**。
⇒ 那 115.76 s 的标签写着「soundness_profile」，装的却是
**「建内核 + 跑 profile」**。**账记在了错的地方。**

而 README 从它推出「这一族在 `run_one` 里有七八块 ⇒ 千秒级」——
**把一个含建造的读数 ×8 了。**

## 这一脚本量什么

同一个进程、同一份语料、**只有计时范围在变**（三臂）：

    臂 A  `mk(ids)[0]`                       —— 建造本身
    臂 B  `soundness_profile(预建内核, c)`    —— profile 本身
    臂 C  逐字复制旧脚本那一行（含重建）        —— 旧读数

判据：**臂 C ≈ 臂 A + 臂 B** ⇒ 旧读数确实是两件事加在一起。

用法：
    LDV_CORPUS=openalex-citations python outputs/_measure_soundness_split.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "repo"))

from ldv.checks._fixtures import coverage_of, load, make_builder  # noqa: E402
from ldv.checks.coverage import soundness_profile  # noqa: E402


def main() -> int:
    loaded = load()
    if loaded is None:
        print("⚠ 找不到语料 —— 报「跳过」，不报「通过」")
        return 0
    nodes, edges, _ = loaded
    ids = sorted(nodes)
    which = os.environ.get("LDV_WHICH", "reach")
    print(f"语料 {len(nodes)} 项 / {sum(len(v) for v in edges.values())} 边｜方向 {which}")
    print()

    mk = make_builder(which, nodes, edges)
    cover = coverage_of(which, nodes, edges)

    t = time.perf_counter()
    ker = mk(ids)[0]
    t_build = time.perf_counter() - t
    print(f"臂 A  `mk(ids)[0]` 建造                     {t_build:8.2f} s"
          f"  （{len(ker.all_directions())} 个方向）")

    t = time.perf_counter()
    prof = soundness_profile(ker, cover)
    t_prof = time.perf_counter() - t
    print(f"臂 B  `soundness_profile(预建内核)`          {t_prof:8.2f} s"
          f"  （越界成员数 {prof['越界成员数']}）")

    t = time.perf_counter()
    soundness_profile(mk(ids)[0], coverage_of(which, nodes, edges))["越界成员数"]
    t_old = time.perf_counter() - t
    print(f"臂 C  旧脚本那一行（**含重建**）              {t_old:8.2f} s")
    print()

    s = t_build + t_prof
    print(f"臂 A + 臂 B = {s:.2f} s   臂 C = {t_old:.2f} s"
          f"   差 {abs(t_old - s):.2f} s（{abs(t_old - s) / max(t_old, 1e-9) * 100:.1f}%）")
    print()
    print(f"⇒ 旧读数里**不属于 profile 的**部分：{t_build:.2f} s"
          f"（占 {t_build / max(t_old, 1e-9) * 100:.0f}%）")
    print(f"⇒ 真正的 profile 只有 {t_prof:.2f} s —— 而 README 把它当「一块 cover 度量」")
    print("   再乘 8 块推出「千秒级」。**含建造的读数不能按块相乘。**")
    print()
    print("⚠️ 另一处独立的干净读数（`outputs/_measure_cover_blocks.py`）：")
    print("   整族四条块 × 两条路径，旧 49.71 s → 新 0.69 s（×72.4），遍历仅 0.06 s。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
