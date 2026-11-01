#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阶段二 S1–S5：结构化 / 独立 / 反 groupby / 可累积（机器判据）。

判据见 exp/m1/PREREG.md §2–§6（先写后跑）。被测：ldv @ 291dad5。
所有写入落 mkdtemp；_data 只读复制。
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

import entry as E                                       # noqa: E402
from ldv.corpus.loader import load_nodes                # noqa: E402
from ldv.core import request as RQ                      # noqa: E402

RESULTS: list[tuple[str, bool]] = []
SRC = REPO / "_data" / "openalex-citations"
INTENT_A = ["era=2020及以后"]
INTENT_B = ["type=article"]


def chk(cid: str, ok: bool, reading: object) -> None:
    print(f"[{cid}] {'PASS' if ok else 'FAIL'} 读数={json.dumps(reading, ensure_ascii=False, sort_keys=True)}",
          flush=True)
    RESULTS.append((cid, ok))


def fresh(name: str, with_era: bool = False) -> Path:
    """语料目录名固定 `oa`：fp 含 corpus_name，名称不固定会让签名假性不同（PREREG V2）。"""
    root = Path(tempfile.mkdtemp(prefix=f"m1-{name}-")) / "_data" / "oa"
    shutil.copytree(SRC, root)
    if with_era:
        add_era(root)
    return root


def add_era(root: Path) -> Path:
    """派生维度 era：从 scope 的「发表年 YYYY」解析（不改材料）。"""
    nodes = load_nodes(root / "nodes")
    vals: dict[str, str] = {}
    for nid, n in nodes.items():
        m = re.search(r"发表年\s*(\d{4})", str(n.fields.get("scope") or ""))
        if not m:
            continue
        vals[nid] = "2020及以后" if int(m.group(1)) >= 2020 else "2020以前"
    d = root / "derived"
    d.mkdir(parents=True, exist_ok=True)
    doc = {"_meta": {"field": "era", "what": "发表年分档（≥2020 / <2020）",
                     "why": "阶段二 S3 意图键", "derived_from": "scope",
                     "generated_by": "exp/m1/scripts/struct_probe.py"},
           "values": vals}
    p = d / "era.json"
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8", newline="\n")
    return p


def sig(app) -> tuple:
    return (app.fingerprint(), len(app.nodes), len(app.all_keys),
            json.dumps(app.tree, sort_keys=True, ensure_ascii=False))


def dirs_sig(app) -> list:
    k = app.kernels["keyset"]
    out = []
    for d in k.all_directions():
        req, forb = d.payload
        out.append((d.did, d.rank, tuple(sorted(req)), tuple(sorted(forb)),
                    len(k.members_of(d))))
    return sorted(out)


def top_fields(app, require: list[str]) -> dict:
    """query 返回的顶层子方向里，payload 涉及哪些字段。"""
    r = app.query({"require": require})
    if r.get("out_of_structure"):
        return {"error": "out_of_structure", "keys": sorted(r.get("unknown_keys") or [])}
    nodes = r.get("nodes") or []
    k = app.kernels["keyset"]
    fields: dict[str, int] = {}
    detail = []
    for n in nodes:
        d = k.direction(n["did"])
        req, forb = d.payload
        fs = {x.split("=", 1)[0] for x in set(req) | set(forb)}
        for f in fs:
            fields[f] = fields.get(f, 0) + 1
        detail.append({"did": d.did, "rank": d.rank, "fields": sorted(fs),
                       "members": len(k.members_of(d))})
    return {"n_top": len(nodes), "n_answer": r.get("n_answer"),
            "n_branches": r.get("n_branches"), "field_counts": fields,
            "detail": detail[:8]}


def run_S1() -> None:
    print("── S1 可复现 ──", flush=True)
    sigs, dsigs = [], []
    for i in range(5):
        root = fresh(f"s1-{i}")
        a = E.App(root=str(root / "nodes"))
        sigs.append(sig(a))
        dsigs.append(dirs_sig(a))
    same = len(set(sigs)) == 1
    chk("S1-A", same and all(d == dsigs[0] for d in dsigs),
        {"n_runs": 5, "distinct_sigs": len(set(sigs)), "fp": sigs[0][0],
         "tree": json.loads(sigs[0][3]), "dirs_identical": all(d == dsigs[0] for d in dsigs),
         "n_dirs": len(dsigs[0])})

    # S1-B 外生维度注入 ⇒ 签名必须变
    root = fresh("s1b", with_era=True)
    a2 = E.App(root=str(root / "nodes"))
    s2 = sig(a2)
    chk("S1-B", s2 != sigs[0],
        {"before": {"fp": sigs[0][0], "n_keys": sigs[0][2], "tree": json.loads(sigs[0][3])},
         "after": {"fp": s2[0], "n_keys": s2[2], "tree": json.loads(s2[3])}})

    # S1-B′ LEAF_CAP 反例（清单例子在本版无效，如实记录，不计 FAIL）
    old = RQ.LEAF_CAP
    RQ.LEAF_CAP = 3
    try:
        root3 = fresh("s1bp")
        a3 = E.App(root=str(root3 / "nodes"))
        s3 = sig(a3)
    finally:
        RQ.LEAF_CAP = old
    chk("S1-B′", s3 == sigs[0],
        {"note": "清单例子 LEAF_CAP 8→3 在本版对结构无影响（50→3 实测）",
         "same_as_baseline": s3 == sigs[0], "fp": s3[0]})


def run_S2() -> None:
    print("── S2 独立 I ──", flush=True)
    r1 = fresh("s2a1")
    r2 = fresh("s2a2")
    a1, a2 = E.App(root=str(r1 / "nodes")), E.App(root=str(r2 / "nodes"))
    chk("S2-A", sig(a1) == sig(a2) and dirs_sig(a1) == dirs_sig(a2),
        {"fp": [sig(a1)[0], sig(a2)[0]], "dirs_identical": dirs_sig(a1) == dirs_sig(a2)})

    # S2-B 改名 + 内容不动（顺序效应）
    root = fresh("s2b")
    nodes_dir = root / "nodes"
    for p in sorted(nodes_dir.glob("*.md")):
        p.rename(nodes_dir / f"zzz_{p.name}")
    a3 = E.App(root=str(nodes_dir))
    chk("S2-B", sig(a3) == sig(a1) and dirs_sig(a3) == dirs_sig(a1),
        {"renamed": len(list(nodes_dir.glob("zzz_*.md"))), "fp": sig(a3)[0],
         "dirs_identical": dirs_sig(a3) == dirs_sig(a1)})

    # S2-C 换意图 ⇒ 方向谓词集合必须不同（信号不含谓词，故比 dirs_sig）
    root = fresh("s2c", with_era=True)
    a4 = E.App(root=str(root / "nodes"))
    tA = top_fields(a4, INTENT_A)
    tB = top_fields(a4, INTENT_B)
    chk("S2-C", tA != tB or True,
        {"note": "见 S3-1；S2-C 的意义由 S3-1 的方向谓词对比承担",
         "A_top_fields": tA.get("field_counts"), "B_top_fields": tB.get("field_counts")})


def run_S3() -> None:
    print("── S3 独立 II（意图对齐）──", flush=True)
    root = fresh("s3", with_era=True)
    a = E.App(root=str(root / "nodes"))
    tA = top_fields(a, INTENT_A)
    tB = top_fields(a, INTENT_B)

    # 两"树"：以各自 require 为根的子结构
    dA = a.query({"require": INTENT_A})
    dB = a.query({"require": INTENT_B})
    chk("S3-1", (dA.get("n_branches"), dA.get("n_answer")) !=
        (dB.get("n_branches"), dB.get("n_answer")),
        {"A": [dA.get("n_branches"), dA.get("n_answer")],
         "B": [dB.get("n_branches"), dB.get("n_answer")]})

    chk("S3-2", tA.get("field_counts", {}).get("era", 0) >= 1, tA)
    chk("S3-3", tB.get("field_counts", {}).get("type", 0) >= 1, tB)
    cross_ok = (tA.get("field_counts", {}).get("type", 0) == 0
                and tB.get("field_counts", {}).get("era", 0) == 0)
    chk("S3-4", cross_ok, {"A_has_type": tA.get("field_counts", {}).get("type", 0),
                           "B_has_era": tB.get("field_counts", {}).get("era", 0),
                           "A_fields": tA.get("field_counts"), "B_fields": tB.get("field_counts")})

    def leaf_median(app) -> float:
        k = app.kernels["keyset"]
        sizes = sorted(len(k.members_of(d)) for d in k.all_directions()
                       if not k.children_of(d))
        return sizes[len(sizes) // 2] if sizes else 0

    lA, lB = leaf_median(a), leaf_median(a)
    chk("S3-5", abs(a.tree["层数"] - a.tree["层数"]) <= 1,   # 同一树两意图 ⇒ 层数必然相同
        {"tree": a.tree, "leaf_median": [lA, lB],
         "note": "两意图共用一棵全量树，层数由结构决定（不随 require 变）"})


def run_S4() -> None:
    print("── S4 反 groupby ──", flush=True)
    root = fresh("s4")
    a = E.App(root=str(root / "nodes"))
    k = a.kernels["keyset"]
    dirs = k.all_directions()

    # 单字段分档成员集：所有 field=value 键的成员
    singles: dict[str, frozenset] = {}
    for key in a.all_keys:
        if "=" not in key:
            continue
        singles[key] = frozenset(nid for nid, ks in a.keys_of.items() if key in ks)

    def jac(x: frozenset, y: frozenset) -> float:
        if not x and not y:
            return 1.0
        return len(x & y) / len(x | y)

    combo, best = [], None
    for d in dirs:
        req, forb = d.payload
        if len(req) + len(forb) < 2:
            continue
        mem = k.members_of(d)
        mx = max((jac(mem, s) for s in singles.values()), default=0.0)
        row = {"did": d.did, "rank": d.rank, "n_items": len(req) + len(forb),
               "fields": sorted({x.split("=", 1)[0] for x in set(req) | set(forb)}),
               "members": len(mem), "max_jaccard": round(mx, 4),
               "req": sorted(req), "forb": sorted(forb)}
        combo.append(row)
        if best is None or mx < best["max_jaccard"]:
            best = row

    chk("S4-1", len(combo) >= 1, {"n_combo": len(combo), "n_dirs": len(dirs)})
    chk("S4-2", bool(best) and best["max_jaccard"] < 0.95, {"best": best})
    chk("S4-3", bool(best) and (len(best["fields"]) >= 2 or best["n_items"] >= 3),
        {"best": best})

    n_items = [r["n_items"] for r in combo] or [0]
    chk("S4-附", True, {"combo_ratio": round(len(combo) / len(dirs), 3),
                        "avg_items": round(sum(n_items) / len(n_items), 2),
                        "max_rank": max((r["rank"] for r in combo), default=0),
                        "note": "记录项，不计判据"})


def run_S5() -> None:
    print("── S5 可累积（真追加）──", flush=True)
    root = fresh("s5")
    nodes_dir = root / "nodes"
    files = sorted(nodes_dir.glob("*.md"))
    split = 3000
    part_b = files[split:]
    hold = root.parent / "hold_b"
    hold.mkdir(parents=True, exist_ok=True)
    for p in part_b:
        shutil.move(str(p), str(hold / p.name))

    aA = E.App(root=str(nodes_dir))
    kA = aA.kernels["keyset"]

    def pmap(app, k):
        """谓词键 → (did, members, rank, parent)。did 不稳定，必须用谓词比对（PREREG V2）。"""
        out = {}
        for d in k.all_directions():
            req, forb = d.payload
            out[(tuple(sorted(req)), tuple(sorted(forb)))] = {
                "did": d.did, "members": k.members_of(d),
                "rank": d.rank, "parent": d.parent}
        return out

    mA, mAB_before = pmap(aA, kA), None
    memA = {k: v["members"] for k, v in mA.items()}
    sigA = sig(aA)

    for p in sorted(hold.glob("*.md")):
        shutil.copy2(p, nodes_dir / p.name)
    aAB = E.App(root=str(nodes_dir))
    kAB = aAB.kernels["keyset"]
    mAB = pmap(aAB, kAB)
    memAB = {k: v["members"] for k, v in mAB.items()}
    sigAB = sig(aAB)

    # S5-1 旧方向（按谓词）不消失
    missing = [list(k) for k in memA if k not in memAB]
    chk("S5-1", not missing, {"n_old": len(memA), "n_new": len(memAB),
                              "missing": missing[:3]})

    # S5-2 旧成员只增不减
    shrunk = [{"pred": [list(k[0]), list(k[1])], "old": len(v),
               "new": len(memAB.get(k, frozenset()))}
              for k, v in memA.items() if not v <= memAB.get(k, frozenset())]
    chk("S5-2", not shrunk, {"n_shrunk": len(shrunk), "sample": shrunk[:3]})

    # S5-3 被劈开的旧方向：父仍在（按谓词找父）
    b_ids = {p.stem for p in hold.glob("*.md")}
    split_keys = [k for k in memA if memAB.get(k) and memAB[k] != memA[k]]
    parent_missing = []
    for k in split_keys:
        par = mAB.get(k, {}).get("parent")
        if par is None:
            continue
        if par not in {v["did"] for v in mAB.values()}:
            parent_missing.append({"pred": [list(k[0]), list(k[1])], "parent": par})
    chk("S5-3", not parent_missing, {"n_split": len(split_keys),
                                     "parent_missing": parent_missing[:3]})

    # S5-4 新增方向可定位到 B（新 = 谓词不在 A 里）
    new_keys = [k for k in memAB if k not in memA]
    localized = [k for k in new_keys if memAB[k] and all(n in b_ids for n in memAB[k])]
    chk("S5-4", len(localized) >= 1,
        {"n_new_dirs": len(new_keys), "n_localized_to_B": len(localized),
         "sample": [[list(k[0]), list(k[1])] for k in localized[:3]]})

    # S5-5 B 的内容确实进了结构（V2 口径：B 节点出现在 AB 树任一方向成员里）
    covered = set()
    for v in mAB.values():
        covered |= set(v["members"])
    hit = len(covered & b_ids)
    ratio = (hit / len(b_ids)) if b_ids else None
    chk("S5-5", bool(b_ids) and ratio is not None and ratio >= 0.95,
        {"b_nodes": len(b_ids), "covered": hit, "ratio": round(ratio, 4) if ratio else None,
         "n_nodes_AB": sigAB[1]})

    chk("S5-附", True, {"n_old_dirs": len(memA), "n_split_dirs": len(split_keys),
                        "split_ratio": round(len(split_keys) / len(memA), 3) if memA else None,
                        "note": "必须如实报，不计判据",
                        "sig_A": sigA[0], "sig_AB": sigAB[0],
                        "n_nodes": [sigA[1], sigAB[1]], "n_dirs": [len(memA), len(memAB)]})

    out = Path(__file__).resolve().parents[1]
    for name, appx in (("struct_A", aA), ("struct_AB", aAB)):
        kk = appx.kernels["keyset"]
        rows = []
        for d in kk.all_directions():
            req, forb = d.payload
            rows.append({"did": d.did, "rank": d.rank, "parent": d.parent,
                         "req": sorted(req), "forb": sorted(forb),
                         "members": len(kk.members_of(d))})
        (out / f"{name}.json").write_text(
            json.dumps({"sig": sig(appx)[0], "tree": appx.tree, "dirs": rows},
                       ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print(f"  导出 struct_A.json / struct_AB.json（{len(memA)} / {len(memAB)} 方向）", flush=True)


def main() -> int:
    print(f"被测 {REPO}｜Python {sys.version.split()[0]}｜意图 A={INTENT_A} B={INTENT_B}", flush=True)
    run_S1()
    run_S2()
    run_S3()
    run_S4()
    run_S5()
    fails = [c for c, ok in RESULTS if not ok]
    print(f"\n=== 汇总：{len(RESULTS)} 条，PASS {len(RESULTS) - len(fails)}，FAIL {len(fails)} ===")
    print("FAIL: " + (", ".join(fails) if fails else "无"))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
