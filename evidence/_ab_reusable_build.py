"""同进程、背靠背的单变量 A/B：**装配缓存开 / 关**对 `run_checks --no-probes` 墙钟的影响。

## 为什么必须同进程背靠背

跨两趟比墙钟**不可靠** —— 本仓库自己记过「同一个数字在这台机器上重复跑会差 ±40%」
（`_profile_runone_prod.py` 末尾）。上面那两趟（改前 1052.26 s / 改后 751.61 s）
是**隔了四十分钟**跑的，中间机器状态变过。

⇒ 这里把**两条臂放进同一个进程**，交替跑（关·开·开·关）以抵消漂移，
而且**只有 `reusable_build` 这一个函数在变** —— 其余一字不动。

## 两条臂

    关  `reusable_build` 换成 `lambda tag, nodes, edges, produce: produce()`
        ⇒ 每次调用都真的建（改前的行为）
    开  真的 `_fixtures.reusable_build` ⇒ 建一次 + 发深拷贝

跑法：
    LDV_CORPUS=snap-email-eu-mid python outputs/_ab_reusable_build.py
"""
from __future__ import annotations

import contextlib
import io
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent / "repo"
sys.path.insert(0, str(REPO))

import ldv.cli as cli  # noqa: E402
import ldv.run_checks as rc  # noqa: E402
from ldv.checks import _fixtures as F  # noqa: E402
from ldv.core.kernel import Kernel  # noqa: E402

REAL = F.reusable_build


def always_build(tag, nodes, edges, produce):        # noqa: ANN001 —— 「关」那一臂
    return produce()


def arm(on: bool) -> tuple[float, int, int]:
    """跑一趟 `main()`，返回 `(墙钟, 真建次数, 退出码)`。"""
    F.clear_build_cache()
    fn = REAL if on else always_build
    F.reusable_build = fn
    rc.reusable_build = fn
    cli.reusable_build = fn

    counts = {"n": 0}
    ob = Kernel.build

    def counted(self, *a, **k):
        counts["n"] += 1
        return ob(self, *a, **k)

    Kernel.build = counted
    try:
        buf = io.StringIO()
        t0 = time.perf_counter()
        with contextlib.redirect_stdout(buf):
            code = rc.main(["_", "--no-probes"])
        wall = time.perf_counter() - t0
    finally:
        Kernel.build = ob
    return wall, counts["n"], code


def main() -> int:
    loaded = F.load()
    if loaded is None:
        print("⚠ 找不到语料 —— 设 `LDV_CORPUS`")
        return 0
    n = len(loaded[0])
    print(f"语料 {n} 项 / {sum(len(v) for v in loaded[1].values())} 边")
    print("顺序：关 · 开 · 开 · 关（交替，抵消漂移）")
    print()
    print(f"  {'臂':6s}{'墙钟':>10s}{'真建次数':>10s}{'退出码':>8s}")
    offs, ons = [], []
    for i, on in enumerate([False, True, True, False]):
        wall, nb, code = arm(on)
        print(f"  {'开' if on else '关':6s}{wall:>10.2f}{nb:>10d}{code:>8d}")
        (ons if on else offs).append(wall)
    print()
    m_off, m_on = sum(offs) / len(offs), sum(ons) / len(ons)
    print(f"  关（改前）平均 {m_off:8.2f} s")
    print(f"  开（改后）平均 {m_on:8.2f} s")
    print(f"  ⇒ 省 **{m_off - m_on:.2f} s ＝ {(1 - m_on / m_off) * 100:.1f}%**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
