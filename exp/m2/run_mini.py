# -*- coding: utf-8 -*-
"""M2 迷你版：**只验一件事 —— 时间能不能被接管**。

不跑判据表，不灌新材料，不出题。本脚本回答的问题只有一个：

    把 90 天压成 90 个日步之后，读数还是不是「记录的年龄」，
    还是已经变成「权重反向增长」的一团乱数？

## 为什么值得单独跑一次

`exp/m2/PREREG.md` §4.1 记了 S0 的实测结果：修之前有 4 处直接读 wall clock，
其中 `promote` 那处**连 `at` 参数都没有**。修完必须验两件事：

1. 写入 / 读取**都**跟着 `clock.frozen()` 走（不是只有一半）；
2. 时钟回拨时权重**夹到 1.0**，不许 > 1 —— 那是「炸」的唯一形态。

## 这个脚本**不是**什么

- 不是 P1–P11 的判据（那要 90 天 + 人类基线 + 18 格网格）；
- 不产出「像不像人」的任何结论。它只产出「时间压缩可不可用」。
- 事件是**随机抽键**合成的，不是真实问题 —— 所以它的读数不能进 RESULTS，
  只能用来判断「要不要继续投入」。

用法：

    python3.12 exp/m2/run_mini.py --corpus /path/to/_data/self --days 10
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ldv.core import clock                                  # noqa: E402
from ldv.tools import usage_store                           # noqa: E402

T0 = datetime(2026, 1, 1, 9, 0, 0)


def _prepare(corpus: Path, tmp: Path) -> Path:
    """把语料复制进 `<tmp>/_data/<name>/nodes` —— 两件事都要：

    ① **只读**：绝不往用户真实语料里写实验记录（那是伪造「经历」的一半）；
    ② 路径里必须有 `_data`：`App` 只在 `_data/` 下启用使用记录
       （内置语料目录也叫 `nodes`，按名字判会把仓库当成可写目标）。
    """
    dst = tmp / "_data" / "mini"
    shutil.copytree(corpus, dst)
    for sub in ("usage", "usage/_staging"):
        p = dst / sub
        if p.is_dir():
            shutil.rmtree(p)
    return dst / "nodes"


def _key_pool(app) -> list[str]:
    """只抽**非唯一**的键（出现 ≥3 次）—— 唯一键抽出来命中 1 项，测不到结构。"""
    from collections import Counter
    c = Counter()
    for ks in app.keys_of.values():
        c.update(ks)
    return sorted(k for k, n in c.items() if n >= 3)


def _run(app, *, days: int, seed: int, advance: bool,
         key_pool: list[str]) -> tuple[list[dict], dict]:
    """跑一组。`advance=False` ⇒ 时钟钉在 T0（A 组）；True ⇒ 逐日推进（C 组）。"""
    from entry import App  # noqa: F401  （只为保证导入路径已就绪）

    rng = random.Random(seed)
    used: set[frozenset] = set()
    rows: list[dict] = []
    per_day: list[list[str]] = []          # 每天写进去的 did（用于「最老一条」追踪）

    for day in range(days):
        now = T0 + timedelta(days=day) if advance else T0
        dids_today: list[str] = []
        with clock.frozen(now):
            n_events = rng.randint(3, 6)
            made = 0
            guard = 0
            while made < n_events and guard < n_events * 12:
                guard += 1
                req = frozenset(rng.sample(key_pool, 2))
                if req in used:
                    continue
                used.add(req)
                out = app.query({"require": sorted(req)})
                made += 1
                if not out.get("ok"):
                    continue
                shown = out.get("nodes") or []
                # 一次查询里下钻前 1–2 支（模拟「看了、挑了两支看下去」）
                for node in shown[:2]:
                    r = app.drill(node["did"], req=sorted(req))
                    if r.get("ok") and r.get("recorded"):
                        dids_today.append(node["did"])
            tend = usage_store.aged_tendency(app._usage_dir)
            # ★ 同一天再算一次：冻结下必须**逐字相同**，否则「可复现」是假的
            repro = tend == usage_store.aged_tendency(app._usage_dir)
            # ⚠️ 这里必须分开两个量（第一版混了，判据全红）：
            #    `aged_tendency[d]` = **累计证据** Σ(强度×w)，可以 > 1；
            #    `decay_weight`    = **单条权重** w ∈ (0,1]。
            recs = usage_store.load(app._usage_dir)
            ages, ws = [], []
            for rec in recs:
                if not rec.get("at"):
                    continue
                ages.append((now - datetime.fromisoformat(rec["at"])).total_seconds() / 86400.0)
                ws.append(usage_store.decay_weight(rec["at"], now=now))
            rows.append({
                "day": day,
                "n_rec": len(tend),
                "n_lines": len(recs),
                "sum": round(sum(tend.values()), 4),
                "max_w": round(max(ws), 4) if ws else 0.0,
                "min_w": round(min(ws), 4) if ws else 0.0,
                "min_age": round(min(ages), 2) if ages else 0.0,
                "max_age": round(max(ages), 2) if ages else 0.0,
                "n_today": len(dids_today),
                "repro": repro,
            })
        per_day.append(dids_today)

    # ★ 最老一条的权重 vs 公式：只取第 0 天写过的那批
    first = per_day[0][0] if per_day and per_day[0] else None
    check = {}
    if first:
        end = T0 + timedelta(days=days - 1) if advance else T0
        ats = [r["at"] for r in usage_store.load(app._usage_dir)
               if r.get("did") == first and r.get("at")]
        w = usage_store.decay_weight(min(ats), now=end) if ats else None
        age = (days - 1) if advance else 0
        check = {"did": first, "w": w, "age": age,
                 "formula": round(0.5 ** (age / usage_store.HALF_LIFE_DAYS), 6)}
    return rows, check


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True, help="语料目录（含 nodes/），会被**复制**，不写原处")
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    src = Path(args.corpus).expanduser().resolve()
    if not (src / "nodes").is_dir():
        print(f"✘ {src} 下没有 nodes/ —— 这不是语料目录")
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="ldv-m2-mini-"))
    try:
        node_dir = _prepare(src, tmp)
        from entry import App
        clock.reset()
        app = App(root=node_dir)

        keys = _key_pool(app)
        # 两组用**同一批键、同一个种子**，只差时钟 —— 否则比的不是时间
        print(f"语料 {src.name}｜节点 {len(app.nodes)}｜可用键 {len(keys)}｜"
              f"方向 {app.tree['方向数']}｜天 {args.days}｜种子 {args.seed}")
        print()

        out = {}
        for name, advance in (("A 冻结", False), ("C 时间", True)):
            # 每组从干净起点开始：清掉**使用记录**，重建 App（倾向是 init 时算的）
            # ⚠️ `_usage_dir` 是**语料根**（`usage/` 在它下面），不是 `usage/` 本身 ——
            #    直接 rmtree 会把整个语料删掉（本脚本第一版就是这么炸的）。
            usage_store.clear(app._usage_dir)
            app = App(root=node_dir)
            clock.reset()
            rows, check = _run(app, days=args.days, seed=args.seed,
                               advance=advance, key_pool=keys)
            out[name] = (rows, check)

        hdr = f"{'天':>3} | {'记录':>4} | {'方向':>4} | {'证据和':>8} | {'最小w':>7} | {'最大w':>7} | {'最老(天)':>8}"
        for name in ("A 冻结", "C 时间"):
            rows, check = out[name]
            print(f"== {name} ==")
            print(hdr)
            print("-" * len(hdr))
            for r in rows:
                print(f"{r['day']:>3} | {r['n_lines']:>4} | {r['n_rec']:>4} | {r['sum']:>8.4f} | "
                      f"{r['min_w']:>7.4f} | {r['max_w']:>7.4f} | {r['max_age']:>8.2f}")
            if check:
                print(f"  首日那支 {check['did']}：w={check['w']}｜"
                      f"年龄 {check['age']} 天｜公式 {check['formula']}")
            print()

        # ── 判据：只判「能不能用」，不判「像不像人」 ─────────────────────
        rowsA, chkA = out["A 冻结"]
        rowsC, chkC = out["C 时间"]
        lastC = rowsC[-1]
        print("== 判据（全部只关于「时间接管是否可用」）==")

        checks = []

        def ok(name, cond, detail=""):
            checks.append((name, bool(cond), detail))
            print(f"  {'✓' if cond else '✗'} {name}" + (f"  —— {detail}" if detail and not cond else ""))

        ok("M1 年龄从不取负（写入与读取同一时间源）",
           all(r["min_age"] >= 0 for r in rowsA + rowsC),
           f"A:{[r['min_age'] for r in rowsA]} C:{[r['min_age'] for r in rowsC]}")

        ok("M2 单条记录权重恒在 (0,1]（没有反向增长）",
           all(0 < r["min_w"] and r["max_w"] <= 1.0 for r in rowsA + rowsC if r["n_lines"]),
           f"C 组最大 w={max([r['max_w'] for r in rowsC] or [0])}")

        ok("M3 A 组冻结 ⇒ 每条权重都是 1.000（时间确实没动）",
           all(r["min_w"] == 1.0 and r["max_w"] == 1.0 for r in rowsA if r["n_lines"]),
           f"{[(r['day'], r['min_w'], r['max_w']) for r in rowsA]}")

        ok("M4 C 组首日那条的权重 = 0.5^(年龄/30)（半衰期口径没漂）",
           chkC.get("w") is not None and abs(chkC["w"] - chkC["formula"]) < 1e-6,
           f"实测 {chkC.get('w')} vs 公式 {chkC.get('formula')}")

        ok("M7 同一天 C 组证据和 < A 组（时间真的扣了分，不是摆设）",
           lastC["sum"] < rowsA[-1]["sum"],
           f"C={lastC['sum']} vs A={rowsA[-1]['sum']}")

        ok("M5 同一天的读数可重复（冻结下重算两次逐字相同）",
           all(r["repro"] for r in rowsA + rowsC),
           f"不可复现的天：{[r['day'] for r in rowsC if not r['repro']]}")

        ok("M6 时钟回拨 ⇒ 权重夹到 1.0（不 >1）",
           _backward_clamp(node_dir, keys, args.seed, args.days),
           "写入在 T0+N 天、读在 T0")

        bad = [n for n, c, _ in checks if not c]
        print()
        print(f"结论：{len(checks) - len(bad)}/{len(checks)} 通过"
              + ("" if not bad else f"；未过：{bad}"))
        print("⚠️ 本脚本的读数**不进 RESULTS**：事件是随机抽键合成的，不是真实问题。")
        print("   它只回答「时间压缩能不能用」，不回答「像不像人」。")
        return 0 if not bad else 1
    finally:
        clock.reset()
        shutil.rmtree(tmp, ignore_errors=True)


def _backward_clamp(node_dir: Path, keys: list[str], seed: int, days: int) -> bool:
    """独立小场景：记录写在 T0+days，读数放回 T0 ⇒ 权重必须夹到 1.0。"""
    d = Path(tempfile.mkdtemp(prefix="ldv-m2-clamp-"))
    try:
        future = (T0 + timedelta(days=days)).isoformat(timespec="microseconds")
        usage_store.append(d, did="DX", outcome=1.0, propensity=1.0, qfp="clamp", at=future)
        w = usage_store.decay_weight(future, now=T0)
        aged = usage_store.aged_tendency(d, now=T0).get("DX")
        return w == 1.0 and aged == 1.0
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
