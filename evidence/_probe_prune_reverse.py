# -*- coding: utf-8 -*-
"""「跳过否块」这件事**有人守着吗**？—— 三态反向对照（`§C3` 的规矩）。

2026-10-14 起 `步骤 T3` 会跳过「后看」的块（`run_in_order(..., skip=...)`）。
⇒ 必须当场证明两件事，否则「跳过」是**无人看守**的（本仓库最忌的形状）：

    (甲) **跳错方向时 `T1` 会红**（`§K8` 假阴禁止那一侧）
    (乙) **不接线时新守卫会红**（否则新守卫是恒真的）

## 三态（必须两两不同）

    ① 真实现（只跳「后看」的块）          ⇒ `T1` **绿**，且 `scanned` **严格少于**全部块
    ② 无依据地跳（把**全部**块都跳）      ⇒ `T1` **红**（漏候选）
    ③ 不跳（`skip` = 空 = 2026-10-09 那版）⇒ `T1` 绿，但 `scanned` **==** 全部块

## 两条判定路径（都走**判据自己**，不是自己 print 两边让人眼对）

    `T1` 本身    → `checks/retrieval.py::t1_equivalence`（`Report` + `result_of`）
    新守卫那一条 → `run_tests` 里那句 `any(len(rr.scanned) < len(rr.first)+len(rr.late))`
                   —— 这里用**同款表达式**，且用**真跑**出来的 `scanned`

## (乙) 为什么要**回退真源码**

⚠️ 不是 monkeypatch 假实现 —— 那只能证明「守卫认字符串」。
   本探针把 `ldv/retriever.py` 里**那一行接线**改回 `skip = frozenset()`，
   在**子进程**里跑一遍（`_probe_prune_revert.py`），
   跑完**字节级还原**并比 `sha256`。

跑法：python outputs/_probe_prune_reverse.py
"""
from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

REPO = Path(r"C:/Users/19253/WorkBuddy AI/2026-10-05-14-16-35/repo")
sys.path.insert(0, str(REPO))
os.chdir(REPO)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from ldv import retriever  # noqa: E402
from ldv.checks._fixtures import build_keyset, coverage_of, keyset_queries, load  # noqa: E402
from ldv.checks._framework import Report, result_of  # noqa: E402
from ldv.checks.abstraction import build_views  # noqa: E402
from ldv.checks.retrieval import t1_equivalence  # noqa: E402
from ldv.core import view_persist  # noqa: E402
from ldv.core.tri import Tri  # noqa: E402
from ldv.core.views import ViewSpec  # noqa: E402
from ldv.tools.gen_view_spec import build_spec  # noqa: E402

PY = r"C:/Users/19253/.workbuddy-ai/binaries/python/versions/3.13.12/python.exe"
BAR = "═" * 100

nodes, edges, _ = load()
kernel, plugin = build_keyset(nodes)
specd, _ = build_spec("keyset", nodes, edges)
spec = ViewSpec(universe=tuple(specd["universe"]),
                partition=tuple(frozenset(b) for b in specd["partition"]),
                relation=frozenset(tuple(e) for e in specd["relation"]))
cover = coverage_of("keyset", nodes, edges)
rec = view_persist.from_dict(
    view_persist.to_dict(build_views(kernel, spec, cover, plugin)), kernel, cover, plugin)
# ★ 夹具：必须挑一条**真有「后看」的块**的查询 —— 否则「少扫」无从谈起。
#   ⚠️ `keyset_queries(nodes)[0]`（`有 evidence_status=有争议`）恰好 `late` 为空
#      —— 本探针第一版就栽在这里（跑出来 `先看 10 / 后看 0`）。
r = None
for _q in keyset_queries(nodes):
    _k, _p = build_keyset(nodes)      # 每条查询用**新内核**（`expand` 会就地改树）
    _rr = retriever.retrieve(_k, _p, {"keyset": retriever.as_bar(_q)},
                             spec=spec, recognition=rec)
    if _rr.late:
        r, kernel, plugin = _rr, _k, _p
        break
if r is None:
    print("★ 夹具不成立：没有一条查询有「后看」的块 ⇒ 停手")
    sys.exit(2)

all_blocks = r.first + r.late
n_all = len(all_blocks)
skip_ok = frozenset().union(*r.late) if r.late else frozenset()
skip_bad = frozenset().union(*all_blocks)
skip_none = frozenset()

print(BAR)
print("「跳过否块」的三态反向对照 —— 跳错会不会被 `T1` 抓住？不接线会不会被新守卫抓住？")
print(BAR)
print(f"语料 {len(nodes)} 项｜需求 `{r.query.label}`｜|Q| = {n_all} 块"
      f"（先看 {len(r.first)} / 后看 {len(r.late)}）｜候选 {len(r.candidates)}")


def judge_t1(cands: frozenset[str]) -> Tri:
    """走**判据自己的**判定路径。"""
    rep = Report(plugin="(检索)", expects=("T1",))
    t1_equivalence(rep, replace(r, candidates=cands), kernel)
    return result_of(rep, "T1")


# ── ① 真实现 ─────────────────────────────────────────────────────────────────
cand_ok = frozenset(kernel.query(r.query, skip=skip_ok).yes)
t1_ok = judge_t1(cand_ok)
scanned_ok = retriever.run_in_order(kernel, r.query, all_blocks, skip=skip_ok)[1]

# ── ② 无依据地跳（全部块都跳） ───────────────────────────────────────────────
cand_bad = frozenset(kernel.query(r.query, skip=skip_bad).yes)
t1_bad = judge_t1(cand_bad)

# ── ③ 不跳（回退版） ─────────────────────────────────────────────────────────
cand_none = frozenset(kernel.query(r.query, skip=skip_none).yes)
t1_none = judge_t1(cand_none)
scanned_none = retriever.run_in_order(kernel, r.query, all_blocks, skip=skip_none)[1]

print(f"\n{'态':<4}{'skip':<26}{'候选':>6}{'T1':>6}{'跑过的块':>10}  判定")
print("─" * 100)
print(f"{'①':<4}{'后看（真实现）':<26}{len(cand_ok):>6}{t1_ok.name:>6}"
      f"{len(scanned_ok):>6}/{n_all:<4}  "
      f"{'绿 ✓' if t1_ok is Tri.YES else '**不是绿** ✗'}，"
      f"少扫={'是 ✓' if len(scanned_ok) < n_all else '**否 ✗**'}")
print(f"{'②':<4}{'全部块（无依据）':<26}{len(cand_bad):>6}{t1_bad.name:>6}"
      f"{'—':>6}{'':<5}  {'红 ✓' if t1_bad is Tri.NO else '**没红 ✗**'}"
      f"（漏 {len(cand_ok - cand_bad)} 个候选）")
print(f"{'③':<4}{'空（不跳 / 回退版）':<26}{len(cand_none):>6}{t1_none.name:>6}"
      f"{len(scanned_none):>6}/{n_all:<4}  "
      f"{'绿 ✓' if t1_none is Tri.YES else '**不是绿** ✗'}，"
      f"少扫={'**否 ✗**' if len(scanned_none) == n_all else '是'}")

new_guard_ok = len(scanned_ok) < n_all
new_guard_none = len(scanned_none) < n_all
print(f"\n★ 新守卫那句表达式（`run_tests` ④ 第二条）：")
print(f"    真实现 ⇒ `少扫真的发生了` = **{new_guard_ok}**（应 True）")
print(f"    回退版 ⇒ `少扫真的发生了` = **{new_guard_none}**（应 False ⇒ 那一条会红）")

# ── (乙) 源码级回退：真改文件、子进程跑、字节级还原 ──────────────────────────
print(f"\n{BAR}\n(乙) 源码级回退 —— 把接线改回 `skip = frozenset()`，在子进程里跑，跑完字节级还原\n{BAR}")
TARGET = REPO / "ldv" / "retriever.py"
orig = TARGET.read_bytes()
sha0 = hashlib.sha256(orig).hexdigest()
OLD = b"    skip = frozenset().union(*late) if late else frozenset()"
NEW = b"    skip = frozenset()"
n_hit = orig.count(OLD)
print(f"  目标行出现次数：{n_hit}（必须是 1 —— 否则回退的**不是我改的那一行**）")
assert n_hit == 1, "锚点不唯一 —— 停手，别改"
TARGET.write_bytes(orig.replace(OLD, NEW))
try:
    p = subprocess.run([PY, str(REPO.parent / "outputs" / "_probe_prune_revert.py")],
                       capture_output=True, text=True, encoding="utf-8", cwd=str(REPO))
    print(f"  子进程（回退版）输出：{p.stdout.strip()}")
    if p.stderr.strip():
        print(f"  ⚠️ stderr：{p.stderr.strip()[:300]}")
    reverted = p.stdout.strip()
finally:
    TARGET.write_bytes(orig)          # ★ 字节级还原
sha1 = hashlib.sha256(TARGET.read_bytes()).hexdigest()
print(f"  还原后 sha256 一致：**{sha0 == sha1}**（{sha0[:16]}…）")

# ── 总判 ─────────────────────────────────────────────────────────────────────
print(f"\n{BAR}\n总判\n{BAR}")
checks = [
    ("(甲) 真实现下 `T1` 绿", t1_ok is Tri.YES),
    ("(甲) 真实现下**真的少扫**了", len(scanned_ok) < n_all),
    ("(甲) 跳错（全跳）时 `T1` **红**", t1_bad is Tri.NO),
    ("(甲) 跳错**真的漏了候选**（不是判据自己造的空集）", len(cand_bad) < len(cand_ok)),
    ("(甲) 不跳时 `T1` 仍绿（②的红不是「随便什么都红」）", t1_none is Tri.YES),
    ("(乙) 回退后 `scanned == 全部块`（新守卫会红）", "少扫=否" in reverted),
    ("(乙) 回退跑完**字节级还原**（sha256 一致）", sha0 == sha1),
]
bad = 0
for name, okv in checks:
    print(f"  {'✓' if okv else '✗'} {name}")
    bad += (not okv)
print(f"\n⇒ 断言 {len(checks)} 条，不成立 **{bad}** 条")
sys.exit(1 if bad else 0)
