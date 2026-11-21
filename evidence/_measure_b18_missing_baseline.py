"""单变量对照：**baseline 文件不见了**时，`B18` 报的是什么？

    跑法：PYTHONIOENCODING=utf-8 python outputs/_measure_b18_missing_baseline.py

## 为什么要单独量

`B18` 的第 0 条（语料指纹）修的是「**换了一份语料**被报成新增违规」。
修的时候顺手把「`语料` 为空」也算进「判不了」—— 但**没有量过**旧行为到底是什么。
一句「旧代码会报绿」是**推理**，不是读数。这份脚本把它变成读数。

## 单变量在哪

同一份 `obs`、同一份「基线文件不存在」的输入，**只有守卫的版本在变**：

    旧（HEAD 的 `coverage.py`，逐字复制在下面）
    新（工作区）

⇒ 差异只有一个变量：守卫版本。这才叫对照。

## 两条臂都要跑 —— 因为旧行为**分两种**，取决于语料有没有既存违规

    臂 A  `keyset` / `reach`：观测全 0、基线的默认值也全 0 ⇒ 旧代码 **绿**
    臂 B  加上 `sequence|maintenance`（既存 16 漏）⇒ 旧代码 **红**

⚠️ 只跑一条臂会得出一个**半对**的结论。第一版脚本只跑了臂 B，于是印出
   「旧代码报绿」—— **与读数相反**。两条臂一起跑，才看得出旧行为的全貌：
   **「基线不见了」被拆成了「绿」和「红」两种假象，而且都不说「基线不见了」。**

⚠️ 旧版本是**逐字复制**的（`git show HEAD:ldv/checks/coverage.py`），不是重写。
   重写一遍再对照，对照的是「我的重写」而不是「旧代码」。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "repo"))

from ldv.checks._fixtures import (  # noqa: E402
    build_incremental, build_keyset, coverage_of, load, make_builder,
)
from ldv.checks._framework import Report  # noqa: E402
from ldv.checks.coverage import (  # noqa: E402
    b18_cover_leak_baseline, corpus_fingerprint, cover_leak_profile, load_baseline,
)


def old_guard(obs: dict, rep: Report, baseline: dict | None = None) -> None:
    """★ HEAD（v0.1.10）的 `b18_cover_leak_baseline` **逐字复制**，只去掉 docstring。"""
    base = (baseline if baseline is not None else load_baseline()).get("基线", {})
    bad: list[str] = []
    tight: list[str] = []
    checked = 0
    for key in sorted(obs):
        o = obs[key]
        b = base.get(key, {"漏项数": 0, "漏的对数": 0})
        checked += 1
        if o["漏项数"] > b["漏项数"]:
            bad.append(f"{key} 新增覆盖不漏：{o['漏项数']} 漏（baseline {b['漏项数']}）"
                       f"—— {o['明细'][:1]}")
        if b["漏项数"] > 0 and o["漏项数"] == 0:
            bad.append(f"{key} 的 baseline 条目**不再发生**（baseline {b['漏项数']} 漏）"
                       f"⇒ 该收紧 baseline，不要留着当永久豁免")
        if 0 < o["漏项数"] < b["漏项数"]:
            tight.append(f"{key} {b['漏项数']}→{o['漏项数']}")
    if checked == 0:
        rep.add("B18", "覆盖不漏", "未展开", "没有可比的父子对")
        return
    detail = ("；".join(bad) if bad
              else f"{checked} 个「方向|路径」都没有新增覆盖不漏"
                   + (f"；可收紧：{tight}" if tight else ""))
    rep.add("B18", "覆盖不漏：覆盖(父) ⊆ ∪覆盖(子)（baseline 守卫：新增红、条目失效红）",
            "否" if bad else "是", detail)


def main() -> int:
    loaded = load()
    if loaded is None:
        print("找不到语料")
        return 0
    nodes, edges, _ = loaded
    fp = corpus_fingerprint(nodes, edges)

    # 真语料上的真实观测 —— 两条守卫吃的是**同一份** obs。
    obs: dict = {}
    for which in ("keyset", "reach", "sequence"):
        cover = coverage_of(which, nodes, edges)
        ids = sorted(nodes)
        batch = build_keyset(nodes)[0] if which == "keyset" else None
        if which == "reach":
            from ldv.checks._fixtures import build_reach
            batch = build_reach(nodes, edges)[0]
        if which == "sequence":
            from ldv.checks._fixtures import build_sequence
            batch = build_sequence(nodes, edges)[0]
        inc = build_incremental(make_builder(which, nodes, edges), nodes, ids[:6], ids[6:])
        obs[f"{which}|batch"] = cover_leak_profile(batch, cover)
        obs[f"{which}|maintenance"] = cover_leak_profile(inc, cover)

    print(f"语料 {len(nodes)} 项 / {sum(len(v) for v in edges.values())} 边；指纹 {fp}")
    print(f"真实观测：{ {k: v['漏项数'] for k, v in obs.items()} }")
    print()

    # ── 输入：**基线文件不存在**（`load_baseline()` 返回 {}）────────────────
    missing: dict = {}
    #: 臂 A = 没有既存违规的那些方向；臂 B = 全量。
    arms = {
        "A  只有 `keyset`/`reach`（既存违规 0 处）":
            {k: v for k, v in obs.items() if not k.startswith("sequence")},
        "B  全量（含 `sequence|maintenance` 既存 16 漏）": obs,
    }

    verdicts: dict[str, tuple[str, str]] = {}
    for name, arm in arms.items():
        r_old = Report(plugin="(对照)")
        old_guard(arm, r_old, missing)
        a_old = r_old.assertions[-1]
        r_new = Report(plugin="(对照)")
        b18_cover_leak_baseline(arm, r_new, fp, missing)
        a_new = r_new.assertions[-1]
        verdicts[name] = (a_old.result, a_new.result)
        print(f"输入：baseline 文件不存在（`{{}}`）｜ {name}")
        print(f"  旧（HEAD 逐字）  {a_old.result}  {a_old.detail[:66]}")
        print(f"  新（工作区）      {a_new.result}  {a_new.detail[:66]}")
        print()

    print("⇒ 读数（**不是结论先写、再找证据**）：")
    for name, (old, new) in verdicts.items():
        print(f"   旧 {old} → 新 {new}   ｜ {name}")
    print()
    print("   旧行为**分两种**，取决于语料有没有既存违规 ——")
    print("     既存违规 0 处  ⇒ 报「绿」（无基线可对，一切照 0 比 ⇒ 恒绿）")
    print("     有既存违规    ⇒ 报「红」，理由是「新增覆盖不漏」")
    print("   **两条都不说「基线不见了」** —— 而「基线不见了」既不是绿也不是违规，")
    print("   是**判不了**。这正是本仓库一直在防的形状：")
    print("   「换了一份语料」/「基线被删了」/「新增违规」三者长得一模一样。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
