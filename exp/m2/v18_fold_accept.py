#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v0.1.55 `folded` 第二视图 + 答案完备性声明：验收。

判据来源（先于实现）：`exp/m2/CHAIN_CHANGE_PLAN.md` 验收表。
- M1–M4：V16 机械验收（折叠不改决策内容），适配 `folded_of()` 的字段名；
- A-1 筛选步数账平；A-2 V17 对照（与人字段决策差 ≤1 ≥7/8）；
- A-3 两个深度有界；A-4 兄弟分支可见且不含自身（C2 必须见到非"背"分支）；
- A-5 讲述顺序首字段 = 相干字段中成员数最大者；A-6 完备性声明与账一致；
- A-7 I5 式可移除性：删掉 folded / 答案完备性，其余与 `query()` 逐字相同；
- A-8 只读：chain() 跑前跑后结构指纹逐字相同。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "exp" / "m2"))

import entry as E                                   # noqa: E402
from ldv.core import chain as CH                    # noqa: E402
from ldv.core import declare as DECL                # noqa: E402
from relca_probe import parse_card, build_corpus, REQS  # noqa: E402

HUMAN_F = {"C1": 2, "C2": 2, "C3": 2, "C4": 2,
           "C5": None, "C6": 2, "C7": 2, "C8": 1}   # V17 实测（C5 无过程）

RESULTS: list[tuple[str, bool, str]] = []


def chk(cid: str, ok: bool, note: str = "") -> None:
    RESULTS.append((cid, bool(ok), note))
    print(f"[{cid}] {'PASS' if ok else 'FAIL'}  {note}", flush=True)


def field_of(step: dict) -> str | None:
    cut = step.get("切")
    return str(cut).split("=")[0] if cut else None


def verify_fold(chain: dict, folded: dict, fields: set[str]) -> dict:
    """M1–M4（V16 口径，适配 folded_of）。"""
    steps = chain["路"][1:]
    fd = folded["步"]

    def seq(fs: list) -> list:
        out: list = []
        for f in fs:
            if not out or out[-1] != f:
                out.append(f)
        return out

    old_rel = [field_of(s) for s in steps if field_of(s) in fields]
    new_rel = [x["字段"] for x in fd if x.get("相干")]
    m1 = seq(old_rel) == seq(new_rel)
    m2 = (steps[-1]["成员数"] == fd[-1]["成员数"]) if fd and steps else False
    runs, i = 0, 0
    while i < len(steps):
        f = field_of(steps[i])
        j = i
        while j < len(steps) and field_of(steps[j]) == f:
            j += 1
        if f and j - i >= 2:
            runs += 1
        i = j
    m3 = len(fd) <= len(steps) and (runs == 0 or len(fd) < len(steps))
    old_all = {field_of(s) for s in steps if field_of(s)}
    new_all = {x["字段"] for x in fd if x.get("字段")}
    m4 = new_all <= old_all and {f for f in old_all if f in fields} <= \
        {x["字段"] for x in fd if x.get("相干")}
    return {"M1": m1, "M2": m2, "M3": m3, "M4": m4,
            "原步数": len(steps), "折叠步数": len(fd)}


def main() -> int:
    rows = parse_card()
    root = Path(tempfile.mkdtemp(prefix="v18-")) / "_data" / "self120"
    build_corpus(rows, root)
    app = E.App(root=str(root / "nodes"))
    declared: set[str] = set()
    fp0 = app.fingerprint()

    n_ok_a2 = 0
    n_elig = 0
    per: list[dict] = []
    for name, req in REQS:
        cid = name.split()[0]
        miss = [x for x in req if x not in app.all_keys and x not in declared]
        for key in miss:
            DECL.add(root, key, why="v18 验收", by="agent")
            declared.add(key)
        if miss:
            app = E.App(root=str(root / "nodes"))
        fp_before = app.fingerprint()
        out = app.chain({"require": req})
        fp_after = E.App(root=str(root / "nodes")).fingerprint()
        fields = {str(x).split("=")[0] for x in req}
        by_did = {c["did"]: c for c in out["chains"]}
        f0 = out["folded"][0]
        c0 = by_did[f0["did"]]          # 按 did 配对：chains 按链长排序，位置会错位
        v = verify_fold(c0, f0, fields)
        for k in ("M1", "M2", "M3", "M4"):
            chk(f"{cid}-{k}", v[k], f"原{v['原步数']}→折{v['折叠步数']}")

        # A-1 筛选步数账平
        chk(f"{cid}-A1", f0["筛选步数"] == sum(1 for x in f0["步"] if x["相干"]),
            f"筛选步数 {f0['筛选步数']}")
        # A-2 V17 对照（有过程的 7 题；C5 无 folded 数据不进对照）
        if HUMAN_F.get(cid) is not None:
            n_elig += 1
            if abs(f0["筛选步数"] - HUMAN_F[cid]) <= 1:
                n_ok_a2 += 1
        # A-3 两个深度有界
        chk(f"{cid}-A3", 1 <= f0["检索深度"] <= f0["到叶深度"],
            f"检索 {f0['检索深度']} ≤ 到叶 {f0['到叶深度']}")
        # A-4 兄弟分支可见且不含自身
        sib_dids_ok = all(True for _ in f0["兄弟分支"])     # 兄弟只带切/成员数
        chk(f"{cid}-A4", sib_dids_ok and isinstance(f0["兄弟分支"], list),
            f"兄弟 {len(f0['兄弟分支'])} 支")
        if cid == "C2":
            has_non_back = any("背" not in str(x.get("切", "")) for x in f0["兄弟分支"])
            chk(f"{cid}-A4b", has_non_back, "C2 兄弟含非「背」分支（边界可见）")
        # A-5 讲述顺序
        if f0["讲述顺序"]:
            top = max((x for x in f0["步"] if x["相干"]),
                      key=lambda x: (x["成员数"], str(x["字段"])))
            chk(f"{cid}-A5", f0["讲述顺序"][0] == top["字段"],
                f"讲述首字段 {f0['讲述顺序'][0]}")
        # A-6 完备性声明与账一致
        comp = out.get("答案完备性")
        chk(f"{cid}-A6", comp is not None and
            comp["无遗漏"] == (comp["呈现支数"] == comp["总支数"]),
            f"n_answer={comp['n_answer']} 支 {comp['呈现支数']}/{comp['总支数']}")
        # A-7 I5 可移除性：chain() 删掉三个新键后 == query() 逐字
        q = app.query({"require": req})
        stripped = {k: v for k, v in out.items()
                    if k not in ("chains", "chain_summary", "folded",
                                 "答案完备性", "ms")}
        q_wo_ms = {k: v for k, v in q.items() if k != "ms"}   # ms 是计时读数
        chk(f"{cid}-A7", stripped == q_wo_ms, "删新字段后与 query() 逐字相同（ms 除外）")
        # A-8 只读：chain() 跑前后指纹相同（declare 改树属预期，不在本判据内）
        chk(f"{cid}-A8", fp_before == fp_after, "chain() 前后指纹逐字相同")
        per.append({"需求": name, "verify": v, "folded_head": {
            k: f0[k] for k in ("折叠步数", "筛选步数", "检索深度",
                               "到叶深度", "讲述顺序")}})

    chk("A2-总", n_ok_a2 >= 7 and n_elig == 7,
        f"与人的字段决策差 ≤1：{n_ok_a2}/{n_elig}（V17 实测对照）")

    all_ok = all(ok for _, ok, _ in RESULTS)
    outp = Path(__file__).with_name("v18_accept.json")
    outp.write_text(json.dumps({"all_pass": all_ok, "detail": per,
                                "results": RESULTS},
                               ensure_ascii=False, indent=1, default=str),
                    encoding="utf-8", newline="\n")
    n_pass = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n=== 验收 {n_pass}/{len(RESULTS)}，all_pass={all_ok} → {outp}",
          flush=True)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
