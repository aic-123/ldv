"""量：一次完整 `run_checks --no-probes` 里，**内核被建造了几次、各是谁建的**。

## 为什么要这一份

README / `MEASUREMENTS` 把「内核的重复建造（189.2 + 139.0 s）」记成检查层超线性里
**唯一没动的大头**。但「重复」这个词**预设了那两次建是同一次建** ——
而 `run_one` 里的三条装配（批建 / 先建 6 维护 n−6 / `cli` 切分）是**故意不同**的。

⇒ 先别信那个词。**数一遍**：`Kernel.build`（建根）与 `Kernel.insert`（插项）各被调几次、
分别由谁调。一次「全量建」= 1 次 `build` + n 次 `insert`。

## 计时块的边界

⚠️ 本脚本**不抄** `run_one` 的序列（抄会漂 —— 见 `_profile_runone_prod.py` 的教训），
   只包 `ldv.core.kernel.Kernel` 的两个方法，然后调**真的** `main()`。

跑法：
    LDV_CORPUS=openalex-n200 python outputs/_measure_build_duplication.py
    LDV_CORPUS=openalex-citations python outputs/_measure_build_duplication.py
"""
from __future__ import annotations

import sys
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent / "repo"
sys.path.insert(0, str(REPO))

import ldv.core.kernel as K  # noqa: E402
import ldv.run_checks as rc  # noqa: E402

#: 归因时跳过的「内部帧」—— 建造原语自己那一层不算「谁建的」。
_INTERNAL = ("core/kernel.py", "core\\kernel.py", "checks/_fixtures.py", "checks\\_fixtures.py")


def _caller() -> str:
    """最近的、不在 `_INTERNAL` 里的那一帧的函数名（`模块:函数`）。"""
    f = sys._getframe(2)
    while f is not None:
        fn = f.f_code.co_filename.replace("\\", "/")
        if not any(x.replace("\\", "/") in fn for x in _INTERNAL):
            mod = Path(fn).stem
            return f"{mod}:{f.f_code.co_name}"
        f = f.f_back
    return "?"


def install() -> dict:
    stats = {"build": Counter(), "insert": Counter(),
             "build_t": Counter(), "insert_t": Counter()}
    ob, oi = K.Kernel.build, K.Kernel.insert

    def build(self, *a, **k):
        t = time.perf_counter()
        try:
            return ob(self, *a, **k)
        finally:
            c = _caller()
            stats["build"][c] += 1
            stats["build_t"][c] += time.perf_counter() - t

    def insert(self, *a, **k):
        c = _caller()
        t = time.perf_counter()
        try:
            return oi(self, *a, **k)
        finally:
            stats["insert"][c] += 1
            stats["insert_t"][c] += time.perf_counter() - t

    K.Kernel.build, K.Kernel.insert = build, insert
    return stats


def main() -> int:
    stats = install()
    from ldv.checks._fixtures import load

    loaded = load()
    if loaded is None:
        print("⚠ 找不到语料 —— 设 `LDV_CORPUS`")
        return 0
    n = len(loaded[0])
    print(f"语料 {n} 项 / {sum(len(v) for v in loaded[1].values())} 边")
    print()

    # ⚠️ `main` 从 `argv[1:]` 开始读 ⇒ 第 0 位必须是**占位**的程序名，
    #    否则 `--no-probes` 会被当成 `argv[0]` **静默丢掉**，探针照跑。
    #    （本脚本第一版就踩了：探针开着，`rebuild` 多出 842 次全量建。）
    t0 = time.perf_counter()
    rc_code = rc.main(["_", "--no-probes"])
    wall = time.perf_counter() - t0

    nb = sum(stats["build"].values())
    ni = sum(stats["insert"].values())
    print()
    print("═══ 建造计数 ═══")
    print(f"  Kernel.build  调用 {nb} 次")
    print(f"  Kernel.insert 调用 {ni} 次")
    print(f"  ⇒ 一次「全量建」= 1 build + {n} insert")
    print(f"  ⇒ **全量建的等价次数** = insert / n = {ni} / {n} = **{ni / n:.2f}**")
    print(f"  墙钟 {wall:.2f} s｜退出码 {rc_code}")
    print()
    print("  按调用者（时间 = 建根 + insert 全部算在建造者头上）:")
    print(f"    {'调用者':34s}{'根次':>5}{'insert':>9}{'等价建':>8}{'秒':>10}{'占墙钟':>9}")
    allc = set(stats["build"]) | set(stats["insert"])
    tot_t = 0.0
    for c in sorted(allc, key=lambda x: -(stats["build_t"][x] + stats["insert_t"][x])):
        t = stats["build_t"][c] + stats["insert_t"][c]
        tot_t += t
        print(f"    {c:34s}{stats['build'][c]:>5}{stats['insert'][c]:>9}"
              f"{stats['insert'][c] / n:>8.2f}{t:>10.2f}{t / max(wall, 1e-9) * 100:>8.1f}%")
    print(f"    {'— 合计':34s}{nb:>5}{ni:>9}{ni / n:>8.2f}{tot_t:>10.2f}"
          f"{tot_t / max(wall, 1e-9) * 100:>8.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
