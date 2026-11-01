#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M0 阶段一（补跑）：W1–W10 接入验证 + F6/F7 端到端遗忘 + BR buffer_real 等价 + X4R 口径复判。

判据来源：exp/m0/PREREG.md §3/V3（先写后跑）。被测：内核 ldv @ 291dad5。
旧探针不动（README 写明理由：改量具等于改结论）；本文件是**新建版**，
语料与 require 换成仓库里实际存在的（旧版依赖的 influence 键已随语料重建消失）。

用法：python exp/m0/probe_remaining.py [W F X BR]
退出码：有 FAIL ⇒ 1。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import entry as E                                    # noqa: E402
from ldv.corpus import predicates as PR              # noqa: E402
from ldv.corpus.index import load_nodes_indexed      # noqa: E402
from ldv.corpus.loader import load_edges, load_nodes  # noqa: E402
from ldv.tools import staging as ST                  # noqa: E402
from ldv.tools import usage_store as US              # noqa: E402

NOW = datetime(2026, 11, 1, 12, 0, 0)
REQ: list[str] = []                                  # 实测 39 支、4037 项
RESULTS: list[tuple[str, bool]] = []


def chk(cid: str, ok: bool, reading: object) -> None:
    print(f"[{cid}] {'PASS' if ok else 'FAIL'} 读数={json.dumps(reading, ensure_ascii=False, sort_keys=True)}",
          flush=True)
    RESULTS.append((cid, ok))


def fresh(src: str, name: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix=f"m0r-{name}-")) / "_data" / name
    shutil.copytree(REPO / "_data" / src, root)
    return root


def shape(app) -> dict:
    return {"n_nodes": len(app.nodes), "n_keys": len(app.all_keys),
            "tree": dict(app.tree), "fp": app.fingerprint()}


def ans_of(r: dict) -> tuple:
    return (tuple(r.get("answer_ids") or ()), r.get("n_answer"), r.get("n_branches"))


# ── W1–W10：接入验证（等价旧探针语义，语料换实际可用的）───────────────
def run_W() -> None:
    root = fresh("openalex-citations", "w")
    a0 = E.App(root=str(root / "nodes"), usage_now=NOW)
    s0 = shape(a0)
    r0 = a0.query({"require": REQ})
    chk("W1", r0.get("ordered_by") == "default",
        {"ordered_by": r0.get("ordered_by"), "n_branches": r0.get("n_branches"),
         "n_answer": r0.get("n_answer")})

    batches = US.stage_load(root)
    n_shown = len(batches[0]["shown"]) if batches else 0
    chk("W2", len(batches) == 1 and n_shown == r0["n_branches"],
        {"batches": len(batches), "n_shown": n_shown, "n_branches": r0["n_branches"]})

    chk("W3", US.load(root) == [] and a0.usage_loaded == 0
        and r0.get("ordered_by") == "default",
        {"records": len(US.load(root)), "usage_loaded": a0.usage_loaded,
         "ordered_by": r0.get("ordered_by")})

    did0 = r0["nodes"][0]["did"]
    dr = a0.drill(did0, REQ)
    left = US.stage_load(root)
    left_n = len(left[0]["shown"]) if left else 0
    chk("W4", dr.get("recorded") is True and len(US.load(root)) == 1
        and all(did0 not in b["shown"] for b in left),
        {"recorded": dr.get("recorded"), "records": len(US.load(root)),
         "stage_left": left_n, "note": str(dr.get("record_note"))[:60]})

    a1 = E.App(root=str(root / "nodes"), usage_now=NOW)
    s1 = shape(a1)
    chk("W5", s0 == s1, {"before": {k: s0[k] for k in ("n_nodes", "n_keys", "fp")},
                         "after": {k: s1[k] for k in ("n_nodes", "n_keys", "fp")},
                         "tree_equal": s0["tree"] == s1["tree"]})

    r1 = a1.query({"require": REQ})
    chk("W6", a1.usage_loaded > 0 and r1.get("ordered_by") == "usage",
        {"usage_loaded": a1.usage_loaded, "ordered_by": r1.get("ordered_by"),
         "tendency_n": len(a1._tendency)})

    chk("W7", ans_of(r0) == ans_of(r1),
        {"n_answer": [r0.get("n_answer"), r1.get("n_answer")],
         "n_branches": [r0.get("n_branches"), r1.get("n_branches")],
         "answer_equal": ans_of(r0)[0] == ans_of(r1)[0]})

    qfp = a1._last_qfp
    again = US.append(root, did=did0, outcome=1.0, propensity=0.5, qfp=qfp, note="drill")
    chk("W8", again.get("written") is False, {"why": str(again.get("why"))[:60]})

    bad = US.promote(root, qfp=qfp, did="D_NOT_THERE", propensity=0.5)
    chk("W9", bad.get("written") is False, {"why": str(bad.get("why"))[:60]})

    out = a1.clear_usage()
    r2 = a1.query({"require": REQ})
    chk("W10", out.get("deleted", 0) >= 1 and r2.get("ordered_by") == "default",
        {"deleted": out.get("deleted"), "staged": out.get("staged"),
         "ordered_by": r2.get("ordered_by")})


# ── F6/F7：端到端遗忘 ────────────────────────────────────────────────
def run_F() -> None:
    root = fresh("openalex-citations", "f")
    a0 = E.App(root=str(root / "nodes"), usage_now=NOW)
    r0 = a0.query({"require": REQ})
    did0 = r0["nodes"][0]["did"]
    a0.drill(did0, REQ)
    t_fresh = US.aged_tendency(root, now=NOW)

    rows = US.load(root)
    if not rows:
        chk("F6", False, {"why": "drill 未产出记录"})
        chk("F7", False, {"why": "无记录，无法进入遗忘比对"})
        return
    rec = rows[0]
    old_at = (NOW - timedelta(days=300)).isoformat()
    p = root / "usage" / rec["file"]
    body = p.read_text(encoding="utf-8").replace(f"at: {rec['at']}", f"at: {old_at}")
    p.write_text(body, encoding="utf-8", newline="\n")

    t_old = US.aged_tendency(root, now=NOW)
    a1 = E.App(root=str(root / "nodes"), usage_now=NOW)
    r1 = a1.query({"require": REQ})
    chk("F6", t_old.get(did0, 0) < t_fresh.get(did0, 0) and did0 in t_old
        and len(US.load(root)) == 1,
        {"did": did0, "fresh": round(t_fresh.get(did0, 0), 6),
         "old": t_old.get(did0, 0), "records": len(US.load(root)),
         "usage_loaded": a1.usage_loaded})
    chk("F7", ans_of(r0) == ans_of(r1),
        {"n_answer": [r0.get("n_answer"), r1.get("n_answer")],
         "n_branches": [r0.get("n_branches"), r1.get("n_branches")],
         "answer_equal": ans_of(r0)[0] == ans_of(r1)[0]})


# ── X4R：口径复判（drill 后当场刷新）──────────────────────────────────
def run_X4R() -> None:
    root = fresh("openalex-citations", "x4r")
    a = E.App(root=str(root / "nodes"), usage_now=NOW)
    r0 = a.query({"require": REQ})
    o1 = [n["did"] for n in r0["nodes"]]
    target = o1[-1]                                   # 最末一支，drill 后应升到首位
    dr = a.drill(target, [])
    r1 = a.query({"require": REQ})
    o2 = [n["did"] for n in r1["nodes"]]
    b = E.App(root=str(root / "nodes"), usage_now=NOW)
    r2 = b.query({"require": REQ})
    o3 = [n["did"] for n in r2["nodes"]]
    ok = (dr.get("recorded") is True and o2 != o1 and o2[0] == target
          and r1.get("ordered_by") == "usage" and o3[0] == target
          and ans_of(r0) == ans_of(r1) == ans_of(r2)
          and dr.get("tendency_after") not in (None, 0.0))
    chk("X4R", ok, {"drilled": target, "recorded": dr.get("recorded"),
                    "tendency_after": dr.get("tendency_after"),
                    "o1_first3": o1[:3], "o2_first3": o2[:3], "o3_first": o3[:1],
                    "changed_in_session": o2 != o1, "persisted": o3[0] == target,
                    "ordered_by": r1.get("ordered_by"),
                    "ans_equal": ans_of(r0) == ans_of(r1) == ans_of(r2)})


# ── BR：buffer_real 等价（staging：一次性树缓冲，与 usage 缓冲是两套）──
def run_BR() -> None:
    src = REPO / "_data" / "arxiv-taxonomy"
    root = fresh("arxiv-taxonomy", "br")
    nodes_dir = root / "nodes"

    # BR-A 谓词缓冲三段
    nodes = load_nodes(nodes_dir)
    edges, _d = load_edges(nodes)
    cand, used, rej, _params = PR.derive_buffered(nodes, edges)
    u, rr = {p.谓词 for p in used}, {x["谓词"] for x in rej}
    dropped = [p for p in cand if p.谓词 not in u and p.谓词 not in rr]
    exact = (len(cand) == len(used) + len(dropped) + len(rej)) and not (u & rr)
    chk("BR-A", exact, {"语料覆盖": "1/6（arxiv-taxonomy）", "nodes": len(nodes),
                        "candidates": len(cand), "used": len(used),
                        "over_budget": len(dropped), "rejected": len(rej),
                        "reject_rate": round(len(rej) / len(cand), 4) if cand else None})

    # BR-B staging：20 个真实节点（新 id，避免与既有撞车）
    def clone_nodes(n: int, start: int, prefix: str) -> list[Path]:
        out = []
        files = sorted(nodes_dir.glob("*.md"))
        for i in range(start, start + n):
            txt = files[i % len(files)].read_text(encoding="utf-8", errors="replace")
            new_id = f"{prefix}{i:04d}"
            lines = []
            for line in txt.splitlines():
                if line.startswith("id:"):
                    line = f'id: "{new_id}"'
                lines.append(line)
            f = root.parent.parent / f"_in_{new_id}.md"
            f.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
            out.append(f)
        return out

    base_n = len(nodes)
    for f in clone_nodes(20, 0, "M0B"):
        ST.stage(root, f)
    st = ST.status(root)
    during = len(load_nodes(nodes_dir))
    res = ST.commit(root)
    after = len(load_nodes(nodes_dir))
    chk("BR-B", len(st["待提交"]) == 20 and during == base_n
        and len(res["已提交"]) == 20 and not res["拒收"] and after == base_n + 20,
        {"staged": len(st["待提交"]), "during": during, "committed": len(res["已提交"]),
         "rejected": len(res["拒收"]), "after": after, "base": base_n})

    # BR-C1 空缓冲
    empty = Path(tempfile.mkdtemp(prefix="m0r-empty-")) / "_data" / "empty"
    (empty / "nodes").mkdir(parents=True)
    r_empty = ST.commit(empty)
    chk("BR-C1", r_empty["已提交"] == [] and r_empty["拒收"] == [], dict(r_empty))

    # BR-C2 全拒收（10 个撞 id）
    dup = fresh("arxiv-taxonomy", "brdup")
    existing = sorted(n for n in load_nodes(dup / "nodes"))
    for i in range(10):
        f = dup.parent.parent / f"_dup_{i}.md"
        f.write_text(f'---\nid: "{existing[i]}"\ntype: "测试"\n---\n\n撞车\n',
                     encoding="utf-8", newline="\n")
        ST.stage(dup, f)
    r_dup = ST.commit(dup)
    reasons = len(list((dup / "_staging" / "rejected").glob("*.理由.txt")))
    chk("BR-C2", not r_dup["已提交"] and len(r_dup["拒收"]) == 10 and reasons == 10,
        {"committed": len(r_dup["已提交"]), "rejected": len(r_dup["拒收"]),
         "reason_files": reasons})

    # BR-C3 大批量 100
    big = fresh("arxiv-taxonomy", "brbig")
    big_base = len(load_nodes(big / "nodes"))
    files = sorted((big / "nodes").glob("*.md"))
    for i in range(100):
        txt = files[i % len(files)].read_text(encoding="utf-8", errors="replace")
        lines = []
        for line in txt.splitlines():
            if line.startswith("id:"):
                line = f'id: "M0C{i:04d}"'
            lines.append(line)
        f = big.parent.parent / f"_big_{i:04d}.md"
        f.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        ST.stage(big, f)
    r_big = ST.commit(big)
    big_after = len(load_nodes(big / "nodes"))
    chk("BR-C3", len(r_big["已提交"]) == 100 and big_after == big_base + 100,
        {"committed": len(r_big["已提交"]), "after": big_after, "base": big_base})

    # BR-D 索引失效
    idx_root = Path(tempfile.mkdtemp(prefix="m0r-idx-")) / "idx"
    n1, s1 = load_nodes_indexed(root / "nodes", idx_root)
    n2, s2 = load_nodes_indexed(root / "nodes", idx_root)
    more = clone_nodes(1, 20, "M0D")
    ST.stage(root, more[0])
    ST.commit(root)
    n3, s3 = load_nodes_indexed(root / "nodes", idx_root)
    chk("BR-D1", s1["重读"] == len(n1), {"reread": s1["重读"], "hit": s1["命中"], "n": len(n1)})
    chk("BR-D2", s2["重读"] == 0 and s2["命中"] == len(n1),
        {"reread": s2["重读"], "hit": s2["命中"]})
    chk("BR-D3", s3["重读"] == 1, {"reread": s3["重读"], "hit": s3["命中"], "n": len(n3)})


def main() -> int:
    groups = [g.upper() for g in sys.argv[1:]] or ["W", "F", "X", "BR"]
    print(f"被测 {REPO}｜Python {sys.version.split()[0]}｜NOW={NOW.isoformat()}", flush=True)
    if "W" in groups:
        run_W()
    if "F" in groups:
        run_F()
    if "X" in groups:
        run_X4R()
    if "BR" in groups:
        run_BR()
    fails = [c for c, ok in RESULTS if not ok]
    print(f"\n=== 汇总：{len(RESULTS)} 条，PASS {len(RESULTS) - len(fails)}，FAIL {len(fails)} ===")
    print("FAIL: " + (", ".join(fails) if fails else "无"))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
