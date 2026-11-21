"""逐块量 **生产 `run_one` 本身** 的开销 —— 不抄一份序列，靠包一层计时。

## 为什么不用手抄的剖面脚本

`outputs/_profile_runone.py` 是 `run_one` 的**逐行副本**。副本会漂：
它第 95 行还在调 `leak_obs`（生产早改成复用两个 profile 了）⇒
**它量的是另一个程序**，而两个程序的名字长得一模一样。

⇒ 这一脚本不复制任何序列，只把 `ldv.run_checks` 模块里的**那几个函数名**
包一层计时，然后调**真的** `run_one`。剖面与生产同源，**构造上不可能漂**。

## 量什么

`run_one(which, loaded, probes=False)` —— 即 `--no-probes` 那一档
（两条探针 `增量≡全量` / `规范重建` 本就不跑）。

⚠️ 计时是**按顶层调用**记的：被包住的函数若内部又调了被包住的函数，
时间会**同时**记在两层上。这里 `NAMES` 里没有嵌套关系（`cover` 没进名单），
所以各项可直接相加。

用法：
    LDV_CORPUS=openalex-citations python outputs/_profile_runone_prod.py reach
    LDV_CORPUS=openalex-small      python outputs/_profile_runone_prod.py keyset

    LDV_PROFILE_VERBOSE=1 …        # 每块跑完就印一行（全量上一小时起，别干等）
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent / "repo"
sys.path.insert(0, str(REPO))

import ldv.run_checks as rc  # noqa: E402
from ldv.checks._fixtures import load  # noqa: E402

#: `run_one` 本体里出现的模块级函数名（探针那两条不列 —— 本趟不跑）。
NAMES = [
    "coverage_of", "batch_kernel", "maintenance_kernel",
    "b1_no_false_negative", "b2_merge_covers", "b3_penalty_comparable",
    "b4_split_is_partition", "b5_decode_covers", "b6_signal_not_collapsed",
    "b7_witness_complete", "b8_invalidation_local", "b9_ledger_append_only",
    "b10_three_states_separable", "b11_propensity_complete",
    "b13_rank_well_founded", "b15_reproducible", "b16_members_covered",
    "b17_leaf_is_equivalence_class", "b18_cover_leak_baseline", "b19_progress_guard",
    "soundness_profile", "cover_leak_profile", "progress_profile", "equiv_classes",
    "false_positive_profile", "cover_nesting_profile", "absorption_profile",
]


def install(stats: dict) -> int:
    #: `LDV_PROFILE_VERBOSE=1` ⇒ **每块跑完就印一行**。
    #: ⚠️ 为什么要有这一档：全量上 `run_one` 是**一小时以上**，而默认只在最后统一印表
    #:    ⇒ 跑了 60 分钟还看不到「贵在哪」。**看不见进度 = 只能干等。**
    verbose = os.environ.get("LDV_PROFILE_VERBOSE") == "1"
    n_ok = 0
    for name in NAMES:
        fn = getattr(rc, name, None)
        if fn is None:
            print(f"⚠ `ldv.run_checks` 里没有 `{name}` —— 名单过期了，**没量到它**")
            continue
        n_ok += 1

        def wrap(fn=fn, name=name):
            def inner(*a, **k):
                t = time.perf_counter()
                try:
                    return fn(*a, **k)
                finally:
                    d = time.perf_counter() - t
                    s = stats.setdefault(name, [0.0, 0])
                    s[0] += d
                    s[1] += 1
                    if verbose:
                        print(f"    · {name:<30}{d:>9.2f}s"
                              f"   （累计 {s[0]:>9.2f}s / {s[1]} 次）", flush=True)
            return inner

        setattr(rc, name, wrap())
    return n_ok


def main(argv: list[str]) -> int:
    which = argv[1] if len(argv) > 1 else "reach"
    loaded = load()
    if loaded is None:
        print("⚠ 找不到语料 —— 报「跳过」，不报「通过」")
        return 0
    nodes = loaded[0]
    print(f"语料 {len(nodes)} 项 / {sum(len(v) for v in loaded[1].values())} 边｜方向 {which}"
          f"｜`--no-probes` 档（探针不跑）")
    print()

    stats: dict[str, list] = {}
    n_ok = install(stats)
    print(f"包了 {n_ok} / {len(NAMES)} 个名字")
    print()

    t0 = time.perf_counter()
    rep = rc.run_one(which, loaded, probes=False)
    wall = time.perf_counter() - t0

    total = sum(v[0] for v in stats.values())
    print(f"{'块':<28}{'合计':>10}{'次数':>6}{'占':>8}")
    print("─" * 54)
    for name, (d, c) in sorted(stats.items(), key=lambda kv: -kv[1][0]):
        print(f"{name:<28}{d:>10.2f}{c:>6}{d / max(wall, 1e-9) * 100:>7.1f}%")
    print("─" * 54)
    print(f"{'合计':<28}{total:>10.2f}{'':>6}{total / max(wall, 1e-9) * 100:>7.1f}%")
    print(f"{'run_one 墙钟':<28}{wall:>10.2f}")
    print()
    print(f"判据红 {len(rep.red)} 条｜跳过 {len(rep.skipped)} 条｜计数 {rep.counts}")
    print()
    print("⚠️ **同一个数字在这台机器上重复跑会差 ±40%** —— 比大小前先看两条读数，")
    print("   别把一次读数当常数。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
