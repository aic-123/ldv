#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M0 阶段一：缓冲区（B）与遗忘（D）机制的存在性验证 + 耦合（X）端到端。

判据全部来自 exp/m0/PREREG.md（先写后跑，跑完不改）。
被测对象：内核 ldv @ 291dad5（v0.1.52）。**不导入 ldv-mcp**。
所有写入落 mkdtemp；不碰 _data/ 原始语料（只读复制）。

用法：
    python exp/m0/probe_mechanisms.py            # 全部
    python exp/m0/probe_mechanisms.py B D        # 只跑某组

退出码：有任一 FAIL ⇒ 1，否则 0。
"""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from ldv.tools import usage_store as US            # noqa: E402
from entry import App                               # noqa: E402

NOW = datetime(2026, 11, 1, 12, 0, 0)
RESULTS: list[tuple[str, bool, str]] = []


def record(cid: str, ok: bool, reading: object) -> None:
    """PASS 才打印 PASS；读数永远打印（量具说话）。"""
    verdict = "PASS" if ok else "FAIL"
    print(f"[{cid}] {verdict} 读数={json.dumps(reading, ensure_ascii=False, sort_keys=True)}",
          flush=True)
    RESULTS.append((cid, ok, json.dumps(reading, ensure_ascii=False, sort_keys=True)))


def mkcorpus(name: str) -> Path:
    """<tmp>/_data/<name>/ 形态（usage 启用的唯一合法形态），返回语料根。"""
    root = Path(tempfile.mkdtemp(prefix=f"m0-{name}-")) / "_data" / name
    (root / "nodes").mkdir(parents=True, exist_ok=True)
    return root


def copy_corpus(name: str, src: str = "openalex-citations") -> Path:
    root = Path(tempfile.mkdtemp(prefix=f"m0-{name}-")) / "_data" / name
    shutil.copytree(REPO / "_data" / src, root)
    return root


# ── B 组：缓冲区 ─────────────────────────────────────────────────────
def run_B() -> None:
    # B1 隔离性
    c = mkcorpus("b1")
    out = US.stage(c, qfp="q1", shown=["D1", "D2"])
    rec, aged = US.load(c), US.aged_tendency(c)
    record("B1", rec == [] and aged == {},
           {"load": len(rec), "aged": aged, "stage_ok": out.get("ok")})

    # B2 覆盖语义（同 qfp 重跑）
    c = mkcorpus("b2")
    US.stage(c, qfp="q1", shown=["D1", "D2"])
    out2 = US.stage(c, qfp="q1", shown=["D3"])
    rows = US.stage_load(c)
    ok2 = (len(rows) == 1 and rows[0]["shown"] == ["D3"] and out2.get("overwrote") is True)
    record("B2", ok2, {"n_batches": len(rows), "shown": rows[0]["shown"] if rows else None,
                       "overwrote": out2.get("overwrote")})

    # B3 容量边界
    cap = US.STAGE_CAP
    res3 = {}
    for tag, n in (("cap-1", cap - 1), ("cap", cap), ("cap+1", cap + 1)):
        c = mkcorpus(f"b3-{tag}")
        last = None
        for i in range(n):
            last = US.stage(c, qfp=f"q{i:04d}", shown=["D1"])
        res3[tag] = {"n": len(US.stage_load(c)),
                     "evicted": len((last or {}).get("evicted") or [])}
    ok3 = (res3["cap-1"]["n"] == cap - 1 and res3["cap"]["n"] == cap
           and res3["cap+1"]["n"] == cap and res3["cap+1"]["evicted"] == 1)
    record("B3", ok3, {"cap": cap, **res3})

    # B4 淘汰的是最旧的一批
    c = mkcorpus("b4")
    for i in range(cap + 5):
        US.stage(c, qfp=f"q{i:04d}", shown=["D1"])
        time.sleep(0.002)                       # 保证 at 严格单调（微秒精度）
    remain = {r["qfp"] for r in US.stage_load(c)}
    want = {f"q{i:04d}" for i in range(5, cap + 5)}
    ok4 = remain == want
    record("B4", ok4, {"remain_n": len(remain), "want_n": len(want),
                       "missing": sorted(want - remain)[:5],
                       "extra": sorted(remain - want)[:5]})

    # B5 淘汰确定性（同 at 三批，跑两次比较删除集合）
    def build_tie(c: Path) -> None:
        d = c / US.DIRNAME / US.STAGE
        d.mkdir(parents=True, exist_ok=True)
        for q in ("qa", "qb", "qc"):
            (d / f"{US._qfp8(q)}.md").write_text(
                "---\n" f"id: {US._qfp8(q)}\nqfp: {q}\nat: 2026-11-01T12:00:00.000000\n"
                "shown: D1\nn_shown: 1\nnote: \n---\n", encoding="utf-8", newline="\n")

    c = mkcorpus("b5")
    build_tie(c)
    drop1 = sorted(US._evict(c, cap=2))
    build_tie(c)
    drop2 = sorted(US._evict(c, cap=2))
    record("B5", drop1 == drop2 and len(drop1) == 1,
           {"drop1": drop1, "drop2": drop2})

    # B6 promote 递减与归零
    c = mkcorpus("b6")
    US.stage(c, qfp="q9", shown=["D1", "D2", "D3"])
    rem = [US.promote(c, qfp="q9", did=d, propensity=0.5).get("remaining")
           for d in ("D1", "D2", "D3")]
    left = len(US.stage_load(c))
    recs = US.load(c)
    ok6 = (rem == [2, 1, 0] and left == 0 and len(recs) == 3
           and all(r["outcome"] == 1.0 for r in recs))
    record("B6", ok6, {"remaining_seq": rem, "stage_left": left,
                       "records": len(recs),
                       "outcomes": [r["outcome"] for r in recs]})


# ── D 组：遗忘 ───────────────────────────────────────────────────────
def run_D() -> None:
    # D2 单条记录的可观测衰减（核心）
    c = mkcorpus("d2")
    US.append(c, did="D1", outcome=1.0, propensity=0.5, qfp="a", at=NOW.isoformat())
    US.append(c, did="D2", outcome=1.0, propensity=0.5, qfp="b",
              at=(NOW - timedelta(days=300)).isoformat())
    t = US.aged_tendency(c, now=NOW)
    ratio = (t["D1"] / t["D2"]) if t.get("D2") else float("inf")
    ok2 = abs(t["D1"] - 2.0) < 1e-6 and t["D2"] < 0.01 and ratio > 1000
    record("D2", ok2, {"D1": t["D1"], "D2": t["D2"], "ratio": ratio,
                       "expect_D2": 2.0 * 0.5 ** 10})

    # D3 缺 at ⇒ 不衰减，且如实计数
    c = mkcorpus("d3")
    d = c / US.DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    (d / "deadbeef-nodate.md").write_text(
        "---\nid: deadbeef-nodate\ndid: D9\noutcome: 1.0\npropensity: 0.5\n"
        "qfp: manual\nnote: 无时间戳\n---\n手写记录（故意不写 at）。\n",
        encoding="utf-8", newline="\n")
    w = US.decay_weight("", now=NOW)
    st = US.status(c)
    no_at = st[US.LONG_TERM]["无时间戳（不衰减）"]
    record("D3", w == 1.0 and no_at == 1, {"decay_weight_empty": w, "no_at_count": no_at})

    # D4 时间倒退 / 非法格式
    future = (NOW + timedelta(days=5)).isoformat()
    w1 = US.decay_weight(future, now=NOW)
    try:
        w2 = US.decay_weight("这不是时间", now=NOW)
        raised = None
    except Exception as exc:                       # noqa: BLE001
        w2, raised = None, f"{type(exc).__name__}: {exc}"
    record("D4", w1 == 1.0 and w2 == 1.0 and raised is None,
           {"future": w1, "illegal": w2, "raised": raised})

    # D5 精确数值表
    cases = [("now", NOW.isoformat(), US.HALF_LIFE_DAYS, 1.0),
             ("-30d hl=30", (NOW - timedelta(days=30)).isoformat(), 30.0, 0.5),
             ("-60d", (NOW - timedelta(days=60)).isoformat(), US.HALF_LIFE_DAYS, 0.25),
             ("-90d", (NOW - timedelta(days=90)).isoformat(), US.HALF_LIFE_DAYS, 0.125),
             ("-90d hl=0", (NOW - timedelta(days=90)).isoformat(), 0.0, 1.0)]
    rows, ok5 = {}, True
    for name, at, hl, want in cases:
        got = US.decay_weight(at, now=NOW, half_life_days=hl)
        rows[name] = {"got": got, "want": want, "err": abs(got - want)}
        ok5 = ok5 and abs(got - want) < 1e-9
    record("D5", ok5, rows)

    # D6 降权不删（字节级 md5）
    c = mkcorpus("d6")
    US.append(c, did="D1", outcome=1.0, propensity=0.5, qfp="a",
              at=(NOW - timedelta(days=40)).isoformat())
    US.append(c, did="D2", outcome=-1.0, propensity=0.25, qfp="b",
              at=(NOW - timedelta(days=5)).isoformat())

    def snap() -> dict[str, str]:
        return {p.name: hashlib.md5(p.read_bytes()).hexdigest()
                for p in sorted((c / US.DIRNAME).glob("*.md"))}

    before = snap()
    for _ in range(3):
        US.aged_tendency(c, now=NOW)
    after = snap()
    record("D6", before == after, {"n_files": len(before), "identical": before == after,
                                   "files": sorted(before)})

    # D7 负事件同样衰减
    c = mkcorpus("d7")
    US.append(c, did="D1", outcome=-1.0, propensity=0.5, qfp="n",
              at=(NOW - timedelta(days=300)).isoformat())
    v = US.aged_tendency(c, now=NOW)["D1"]
    record("D7", v < 0 and abs(v) < 0.01, {"value": v, "expect": -2.0 * 0.5 ** 10})


# ── X 组：耦合 ───────────────────────────────────────────────────────
def ans_of(r: dict) -> tuple[list[str], int, int]:
    return (sorted(r.get("answer_ids") or []), r.get("n_answer"), r.get("n_branches"))


def usage_files(root: Path) -> list[Path]:
    return sorted((root / US.DIRNAME).glob("*.md"))


def age_records(root: Path, did: str, days: int, now: datetime = NOW) -> int:
    """把某方向记录的 at 改成 now-days（在 tmp 语料里改，不碰 _data）。"""
    n = 0
    for p in usage_files(root):
        t = p.read_text(encoding="utf-8")
        if f"did: {did}\n" not in t:
            continue
        lines = []
        for line in t.splitlines():
            if line.startswith("at: "):
                line = f"at: {(now - timedelta(days=days)).isoformat()}"
                n += 1
            lines.append(line)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return n


def run_X() -> None:
    # X1 ★★ 遗忘改变呈现次序
    root = copy_corpus("x1")
    a = App(root=str(root / "nodes"), usage_now=NOW)
    r = a.query({"require": []})
    ordered = [n["did"] for n in r["nodes"]]
    A, B = ordered[0], ordered[1]
    dA = a.drill(A, [])
    dB = a.drill(B, [])
    nA = age_records(root, A, 300)
    a2 = App(root=str(root / "nodes"), usage_now=NOW)
    r2 = a2.query({"require": []})
    ordered2 = [n["did"] for n in r2["nodes"]]
    t = a2._tendency
    tA, tB = t.get(A, 0.0), t.get(B, 0.0)
    ok1 = (dA.get("recorded") and dB.get("recorded")
           and nA >= 1 and tA < tB
           and ordered2.index(B) < ordered2.index(A)
           and ans_of(r) == ans_of(r2)
           and r["ordered_by"] != "usage" and r2["ordered_by"] == "usage")
    record("X1", ok1, {"A": A, "B": B, "recorded": [dA.get("recorded"), dB.get("recorded")],
                       "aged_records": nA, "tA": tA, "tB": tB,
                       "posA": ordered2.index(A), "posB": ordered2.index(B),
                       "ordered_by": [r["ordered_by"], r2["ordered_by"]],
                       "ans_equal": ans_of(r) == ans_of(r2), "root": str(root)})

    # X2 ★★ 衰减到 ~0 仍在候选集（构造见 PREREG V2）
    root = copy_corpus("x2")
    a = App(root=str(root / "nodes"), usage_now=NOW)
    r = a.query({"require": []})
    ordered = [n["did"] for n in r["nodes"]]
    D, E = ordered[0], ordered[1]
    a.drill(D, [])
    a.drill(E, [])
    nD = age_records(root, D, 3000)
    a2 = App(root=str(root / "nodes"), usage_now=NOW)
    r2 = a2.query({"require": []})
    ordered2 = [n["did"] for n in r2["nodes"]]
    t = a2._tendency
    ok2 = (D in ordered2 and r2["n_branches"] == r["n_branches"] == 39
           and nD >= 1 and t.get(D, 0.0) < t.get(E, 0.0)
           and ordered2.index(D) > ordered2.index(E))
    record("X2", ok2, {"D": D, "E": E, "aged_records": nD,
                       "D_in_nodes": D in ordered2, "n_branches": [r["n_branches"], r2["n_branches"]],
                       "tD": t.get(D, 0.0), "tE": t.get(E, 0.0),
                       "posD": ordered2.index(D) if D in ordered2 else None,
                       "posE": ordered2.index(E), "root": str(root)})

    # X3 反复曝光不涨分
    root = copy_corpus("x3")
    a = App(root=str(root / "nodes"), usage_now=NOW)
    for _ in range(100):
        a.query({"require": []})
    recs0 = US.load(root)
    aged0 = US.aged_tendency(root, now=NOW)
    batches0 = len(US.stage_load(root))
    r = a.query({"require": []})
    first = r["nodes"][0]["did"]
    d1 = a.drill(first, [])
    recs1 = US.load(root)
    ok3 = (len(recs0) == 0 and aged0 == {} and len(recs1) == 1
           and recs1[0]["outcome"] == 1.0 and d1.get("recorded"))
    record("X3", ok3, {"records_after_100": len(recs0), "aged": aged0,
                       "batches": batches0, "records_after_promote": len(recs1),
                       "outcomes": [x["outcome"] for x in recs1], "root": str(root)})

    # X4 会话内不刷新（预期 FAIL，见 PREREG）
    root = copy_corpus("x4")
    a = App(root=str(root / "nodes"), usage_now=NOW)
    o1 = [n["did"] for n in a.query({"require": []})["nodes"]]
    a.drill(o1[-1], [])                  # 挑最后一支下钻（最容易看出次序变化）
    o2 = [n["did"] for n in a.query({"require": []})["nodes"]]
    b = App(root=str(root / "nodes"), usage_now=NOW)
    o3 = [n["did"] for n in b.query({"require": []})["nodes"]]
    same_in_session = (o1 == o2)
    diff_across = (o2 != o3)
    record("X4", same_in_session and diff_across,
           {"same_in_session": same_in_session, "diff_across_session": diff_across,
            "first_before": o1[:3], "first_after_drill": o2[:3], "after_rebuild": o3[:3],
            "root": str(root)})


def main() -> int:
    groups = [g.upper() for g in sys.argv[1:]] or ["B", "D", "X"]
    print(f"被测：{REPO}｜git HEAD 由外层记录｜Python {sys.version.split()[0]}", flush=True)
    print(f"usage_store sha256 由外层记录；NOW={NOW.isoformat()}", flush=True)
    t0 = time.time()
    if "B" in groups:
        run_B()
    if "D" in groups:
        run_D()
    if "X" in groups:
        run_X()
    fails = [cid for cid, ok, _ in RESULTS if not ok]
    print(f"\n=== 汇总：{len(RESULTS)} 条，PASS {len(RESULTS) - len(fails)}，FAIL {len(fails)} ===",
          flush=True)
    print("FAIL: " + (", ".join(fails) if fails else "无"), flush=True)
    print(f"耗时 {time.time() - t0:.1f}s", flush=True)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
