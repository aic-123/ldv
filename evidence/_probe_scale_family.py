# -*- coding: utf-8 -*-
"""§4.6 G2 的实体证据：**规模族上的发展读数**（2026-10-22）。

同一个方向函数（keyset），在一族**规模不同**的材料上各建一次树，记录：
`|U| / 方向数 / 层数 / 最大叶 / 键数 / 建树耗时`。产出「最大叶 vs |U|」的曲线。

## 三条纪律（写死在这里，不是注释）

1. **跨语料不是单变量对照** —— 材料形状不同 ⇒ 只读**量级与趋势**，不读因果。
2. 这是**读数**，不进退出码、不合成、不排序（`docs/评估标准.md` 的「不作分」）。
3. 时间预算：默认 8 份（1 005 – 82 168 项）；100k 那份与重复语料（同尺寸的两份
   语料只取一份）不跑，**如实记**——规模族上端缺一点，比假装画满整条曲线好。

用法：python evidence/_probe_scale_family.py
输出：stdout（本趟留档见 `_probe_scale_family.out.txt`）。
"""
from __future__ import annotations

import gc
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import entry as E                      # noqa: E402
from ldv.corpus import loader as LD    # noqa: E402

#: 规模族：1 005 – 30 000 项（六份）。⚠️ 实测：30k 一份建树 196 s；
#: 58k（ext-brightkite）单份 **超过 10 分钟**被时间预算砍掉、82k（ext-slashdot）
#: 同理 ⇒ 上端两点**如实缺**（规模族的上端缺一点，比假装画满整条曲线好）。
#: 要补跑：`--corpora ext-brightkite`（预计 >10 min，别塞进同一条命令）。
CORPORA = ["ext-email", "ext-bitcoin", "ext-wiki-vote", "ext-coauth",
           "ext-astroph", "ext-web-stanford-30k"]


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="规模族读数（G2）")
    ap.add_argument("--corpora", default=",".join(CORPORA),
                    help="逗号分隔的语料名（默认 8 份规模族）")
    a = ap.parse_args()
    names = [x.strip() for x in a.corpora.split(",") if x.strip()]
    rows = []
    print("=" * 78)
    print("G2 规模族：同一方向函数（keyset）在不同规模材料上的读数")
    print("=" * 78)
    print(f"  {'语料':26s} {'|U|':>7s} {'方向数':>5s} {'层数':>4s} "
          f"{'最大叶':>6s} {'键数':>5s} {'建树s':>6s} {'叶/项':>6s}")
    for name in names:
        t0 = time.time()
        try:
            app = E.App(name)
        except Exception as e:                      # 语料不在 / 坏了：如实记，不猜
            print(f"  {name:26s} ✗ {type(e).__name__}: {str(e)[:40]}")
            continue
        dt = time.time() - t0
        n = len(app.nodes)
        row = {"语料": name, "项": n, "方向数": app.tree["方向数"],
               "层数": app.tree["层数"], "最大叶": app.tree["最大叶"],
               "键数": len(app.all_keys), "建树s": round(dt, 1),
               "叶每项": round(app.tree["最大叶"] / n, 4) if n else 0.0}
        rows.append(row)
        print(f"  {name:26s} {n:7d} {row['方向数']:5d} {row['层数']:4d} "
              f"{row['最大叶']:6d} {row['键数']:5d} {dt:6.1f} {row['叶每项']:6.3f}")
        # 释放：App 持有 nodes/edges/kernel；loader 的解析缓存也清掉（只清缓存，不碰盘）
        del app
        LD._NODE_CACHE.clear()                      # noqa: SLF001（探针：清解析缓存）
        gc.collect()

    print()
    print("  ⚠️ 跨语料**不是单变量对照**（材料形状不同）⇒ 只读量级与趋势，不读因果。")
    print("  ⚠️ 这是读数：不进退出码、不合成、不排序（不作分红线）。")
    if len(rows) >= 2:
        lo, hi = rows[0], rows[-1]
        print(f"  量级：|U| {lo['项']} → {hi['项']}（×{hi['项']/lo['项']:.0f}）"
              f"｜最大叶 {lo['最大叶']} → {hi['最大叶']}"
              f"（×{hi['最大叶']/max(1, lo['最大叶']):.0f}）"
              f"｜方向数 {lo['方向数']} → {hi['方向数']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
