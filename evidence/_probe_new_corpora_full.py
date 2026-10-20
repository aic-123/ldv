# -*- coding: utf-8 -*-
"""用新的小语料全面测试（`run_checks` + `test_injections` 换语料跑）。

语料选择原则：**小而完整**（有 `view_spec.json`）、**从没进过任何校验/实验**的。
这是「换语料跑是额外信息」那条契约边界的实践（注入验证是语料绑定的）。

判据：新语料上 **红必须为 0**；若红 ⇒ 如实记（语料问题 vs 真缺陷，分开报）。

跑法：python outputs/_probe_new_corpora_full.py
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent / "repo"
PY = r"C:/Users/19253/.workbuddy-ai/binaries/python/versions/3.13.12/python.exe"

#: 9 份新语料：3 份边界（1/4/12 节点）+ 4 份领域小语料 + 2 份切片
CORPORA = [
    ("edge-single", 1, "边界：单节点"),
    ("edge-tiny", 4, "边界：极小图"),
    ("edge-zero", 12, "边界：零边"),
    ("pypi-deps", 60, "软件依赖"),
    ("gh-langs", 56, "开源生态"),
    ("crossref-journals", 74, "学术出版"),
    ("arxiv-taxonomy", 155, "学术分类"),
    ("snap-email-eu-mini", 120, "邮件（ext-email 切片）"),
    ("snap-hepth-dag", 1200, "引用 DAG（切片）"),
]


def _run(cmd: list[str], env_extra: dict, timeout: int = 300) -> tuple[int, str]:
    env = dict(os.environ)
    env.update(env_extra)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           timeout=timeout, cwd=str(REPO), env=env)
        return p.returncode, (p.stdout or "") + (p.stderr or ""), time.time() - t0
    except subprocess.TimeoutExpired:
        return -1, f"超时（>{timeout}s）", time.time() - t0


def main() -> int:
    rows = []
    for name, nodes, desc in CORPORA:
        d = REPO / "_data" / name
        assert (d / "view_spec.json").exists(), f"{name} 缺 view_spec"
        print(f"\n═══ {name}（{nodes} 节点 · {desc}）═══")
        # ── run_checks ──────────────────────────────────────────────
        rc, out, dt = _run([PY, "-m", "ldv.run_checks", "--no-probes"],
                           {"LDV_CORPUS": name})
        m = re.search(r"断言 (\d+) 条：过 (\d+)，红 (\d+)，跳过 (\d+)", out)
        if m:
            a, p, r, s = (int(x) for x in m.groups())
            print(f"  run_checks: 断言 {a}｜过 {p}｜红 {r}｜跳过 {s}（{dt:.1f}s）")
        else:
            a = p = r = s = -1
            print(f"  run_checks: **没解析到断言行**（rc={rc}）—— {out[-200:]}")
        # ── test_injections ─────────────────────────────────────────
        # ⚠️ 判据不是 rc：**换语料上注入有红是预期行为**（「注入验证是语料绑定的」
        #    契约边界 —— README「已知的未做」第 8 条）。真正的运行错误是「崩」。
        rc2, out2, dt2 = _run([PY, "-m", "ldv.tests.test_injections"],
                              {"LDV_CORPUS": name})
        m2 = re.search(r"真没通过 (\d+) · 判不了 (\d+) · 崩 (\d+)（共 (\d+) 条）", out2)
        if m2:
            n_fail, n_unk, n_crash, n_all = (int(x) for x in m2.groups())
            print(f"  test_injections: 真没通过 {n_fail}｜判不了 {n_unk}｜崩 {n_crash}"
                  f"（共 {n_all}，rc={rc2}，{dt2:.1f}s）"
                  + ("  ← **崩了，运行错误**" if n_crash else ""))
        else:
            n_fail = n_unk = n_crash = -1
            print(f"  test_injections: **没解析到汇总行**（rc={rc2}）—— {out2[-160:]}")
        rows.append({"语料": name, "节点": nodes, "说明": desc,
                     "断言": a, "过": p, "红": r, "跳过": s,
                     "真没通过": n_fail, "判不了": n_unk, "崩": n_crash,
                     "耗时s": round(dt, 1)})

    # ── 汇总 ────────────────────────────────────────────────────────
    print("\n\n═══ 汇总 ═══")
    print(f"{'语料':22s}{'节点':>6s}{'断言':>5s}{'红':>3s}{'跳过':>4s}{'真没通过':>8s}{'判不了':>6s}{'崩':>3s}{'耗时':>7s}")
    bad = []
    for r in rows:
        print(f"{r['语料']:22s}{r['节点']:6d}{r['断言']:5d}{r['红']:3d}"
              f"{r['跳过']:4d}{r['真没通过']:8d}{r['判不了']:6d}{r['崩']:3d}{r['耗时s']:7.1f}")
        if r["红"] != 0 or r["崩"] != 0:
            bad.append(r["语料"])
    print()
    if bad:
        print(f"⚠️ 运行错误（run_checks 红≠0 或注入崩≠0）的语料：{bad}")
    else:
        print("✅ 9 份新语料全部：run_checks 红 0、注入崩 0")
        print("   （注入「真没通过 / 判不了」是**语料绑定**的预期读数，不是缺陷 —— "
              "README「已知的未做」第 8 条）")

    with io.open(HERE / "_probe_new_corpora_full.rows.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print("留档：outputs/_probe_new_corpora_full.rows.json")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
