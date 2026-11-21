"""曝光模型标定 —— 从 **Open Bandit Dataset** 的真实日志量「位次 → 曝光」。

    python outputs/_measure_exposure.py [campaign ...]

## 为什么这份料能标定曝光模型

OBD 是 ZOZOTOWN 的推荐位 A/B 测试日志（CC BY 4.0，Saito et al. 2020, arXiv:2008.07146）。
两套行为策略：`random` 与 `bts`。

    `random`  每个位置从 N 个商品里**均匀随机**抽一个 ⇒ **位置是随机分配的**
              ⇒ `P(点击 | 位次)` 里的「相关性」这一项**与位次独立**
              ⇒ 位次效应是**因果可识别**的 ⇒ 它就是曝光曲线（差一个常数）

    `bts`     位置由策略挑 ⇒ 位置与商品质量**相关** ⇒ 直接量 `P(点击|位次)` 是**混淆的**。
              它是**对照组**：两条曲线不一样，正说明「不加倾向加权就会把偏差带进去」。

⚠️ 设计里那条 `1 / 位次` 是**拍的**（§8.1.1），而这里量到的位次只有 **1/2/3** ——
   量程之外的形状仍是外推。这条必须写在结论里，不能只报拟合出的数。
"""

from __future__ import annotations

import csv
import io
import math
import sys
import zipfile
from collections import Counter
from pathlib import Path

ZIP = Path(__file__).resolve().parent.parent / "repo" / "_data" / "open-bandit" / "open_bandit_dataset.zip"


def scan(member: str) -> tuple[Counter, Counter]:
    """流式扫一遍，只取 position / click 两列。"""
    imp: Counter = Counter()
    clk: Counter = Counter()
    z = zipfile.ZipFile(ZIP)
    with z.open(member) as fh:
        rd = csv.reader(io.TextIOWrapper(fh, encoding="utf-8", newline=""))
        head = next(rd)
        try:
            ip, ic = head.index("position"), head.index("click")
        except ValueError:
            raise SystemExit(f"{member}: 表头不对 {head[:8]}")
        for row in rd:
            if len(row) <= ic:
                continue
            p = row[ip]
            imp[p] += 1
            if row[ic] == "1":
                clk[p] += 1
    return imp, clk


def report(tag: str, imp: Counter, clk: Counter) -> dict:
    ks = sorted(imp, key=lambda s: int(s))
    total_i = sum(imp.values())
    total_c = sum(clk.values())
    print(f"\n═══ {tag} ═══")
    print(f"  展示 {total_i:,} 次 ｜ 点击 {total_c:,} 次 ｜ 总 CTR {total_c / total_i:.5f}")
    print(f"  {'位次':>4} {'展示':>12} {'点击':>9} {'CTR':>10} "
          f"{'相对第1位':>10} {'1/k 预测':>10} {'实测/预测':>10}")
    base = clk[ks[0]] / imp[ks[0]]
    out = {"tag": tag, "展示": total_i, "点击": total_c, "位次": {}}
    for k in ks:
        ctr = clk[k] / imp[k]
        rel = ctr / base
        pred = 1.0 / int(k)
        print(f"  {k:>4} {imp[k]:>12,} {clk[k]:>9,} {ctr:>10.6f} "
              f"{rel:>10.4f} {pred:>10.4f} {rel / pred:>10.3f}")
        out["位次"][int(k)] = {"展示": imp[k], "点击": clk[k], "CTR": ctr,
                               "相对": rel, "1/k": pred, "实测除预测": rel / pred}
    # 幂律拟合  CTR(k) = CTR(1) * k^-alpha   （log-log 最小二乘，只有 len(ks) 个点）
    xs = [math.log(int(k)) for k in ks]
    ys = [math.log(clk[k] / imp[k] / base) for k in ks]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    alpha = -sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den if den else float("nan")
    print(f"  幂律拟合：CTR(k) ∝ k^(-{alpha:.4f})   （只有 {n} 个点，"
          f"{'这是 1/k 的形态' if abs(alpha - 1) < 0.25 else '**不是 1/k**'}）")
    out["alpha"] = alpha
    return out


def main() -> int:
    which = sys.argv[1:] or ["random/all", "random/men", "random/women",
                             "bts/men", "bts/women"]
    res = []
    for w in which:
        pol, camp = w.split("/")
        member = f"open_bandit_dataset/{pol}/{camp}/{camp}.csv"
        print(f"\n扫描 {member} …", flush=True)
        imp, clk = scan(member)
        res.append(report(f"{pol} · {camp}", imp, clk))
    print("\n═══ 汇总：位次 2 / 3 相对位次 1 的曝光比 ═══")
    print(f"  {'策略·活动':<16} {'实测 1:2:3':>22} {'1/k 预测 1:2:3':>18} {'α':>8}")
    for r in res:
        d = r["位次"]
        if not {1, 2, 3} <= set(d):
            continue
        m = f"1 : {d[2]['相对']:.3f} : {d[3]['相对']:.3f}"
        print(f"  {r['tag']:<16} {m:>22} {'1 : 0.5 : 0.333':>18} {r['alpha']:>8.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
