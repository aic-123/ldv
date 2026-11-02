#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""停层判据 V1（PREREG_STOP.md）：在 m2 重建语料上，验证三判据能否把链压到人的步数附近。

对照基线 = chains.json 里人给的步数（C1=3, C2=3, C3=3, C4=4, C5=4, C6=3, C7=3, C8=3）。
"""
from __future__ import annotations

import json
import statistics
import tempfile
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import entry as E                                    # noqa: E402
from ldv.core import chain as CH                     # noqa: E402
from ldv.core import declare as DECL                 # noqa: E402

sys.path.insert(0, str(REPO / "exp" / "m2"))
from relca_probe import parse_card, build_corpus, REQS  # noqa: E402

K = 7
HUMAN_STEPS = {"C1": 3, "C2": 3, "C3": 3, "C4": 4, "C5": 4, "C6": 3, "C7": 3, "C8": 3}


def stop_index(k, steps, ans: set[str]):
    """返回 (停点下标 | None, 停因, 该层成员数, 精度)。

    None = 零相干步（全过路），按 PREREG §2 不计停层成功。
    """
    n = len(steps)
    s1 = next((i for i in range(n)
               if k.members_of(k.direction(steps[i]["did"]))
               and k.members_of(k.direction(steps[i]["did"])) <= ans), None)
    s2 = next((i for i in range(n)
               if len(k.members_of(k.direction(steps[i]["did"]))) <= K), None)
    rel = [i for i, s in enumerate(steps) if s.get("相干")]
    if not rel:
        return None, "全过路", len(k.members_of(k.direction(steps[-1]["did"]))), None
    s3 = max(rel)
    cands = [(i, w) for i, w in ((s1, "精度满"), (s2, "规模≤7"), (s3, "后续全过路"))
             if i is not None]
    idx, why = min(cands, key=lambda x: (x[0], x[1]))
    mem = k.members_of(k.direction(steps[idx]["did"]))
    prec = (len(mem & ans) / len(mem)) if mem else None
    return idx, why, len(mem), prec


def main() -> int:
    rows = parse_card()
    root = Path(tempfile.mkdtemp(prefix="m2stop1-")) / "_data" / "self120"
    ok, bad = build_corpus(rows, root)
    app = E.App(root=str(root / "nodes"))
    k = app.kernels["keyset"]
    sig0 = app.fingerprint()
    print(f"语料 {ok} 节点｜签名 {sig0}｜K={K}", flush=True)

    declared: set[str] = set()
    out = []
    for name, req in REQS:
        cid = name.split()[0]
        human = HUMAN_STEPS[cid]
        miss = [x for x in req if x not in app.all_keys and x not in declared]
        for key in miss:
            DECL.add(root, key, why="停层 V1：等值需求", by="agent")
            declared.add(key)
        if miss:
            app = E.App(root=str(root / "nodes"))
            k = app.kernels["keyset"]
        r = app.query({"require": req})
        ans = set(r["answer_ids"])
        for node in r["nodes"]:
            c = CH.chain_of(k, node["did"], require=req)
            steps = c["路"]
            idx, why, mem, prec = stop_index(k, steps, ans)
            rec = {"需求": name, "链": cid, "支": node["did"],
                   "答案": len(ans), "支的答案": len(k.members_of(k.direction(node["did"])) & ans),
                   "原链长": c["长"], "人的步数": human,
                   "停点": (idx + 1) if idx is not None else None,
                   "停因": why, "停层成员": mem, "停层精度": round(prec, 3) if prec is not None else None,
                   "压缩到人": (idx is not None and idx + 1 <= human)}
            out.append(rec)
            print(f"  {name:18s} 支{node['did'][:8]} 原链{c['长']:2d} 人{human} "
                  f"→ 停{rec['停点']!s:>4s} {why:8s} 成员{mem:4d} 精度{rec['停层精度']}", flush=True)

    # 汇总
    valid = [x for x in out if x["停点"] is not None]
    nops = [x for x in out if x["停点"] is None]
    e1 = sum(1 for x in valid if x["压缩到人"])
    e2 = sum(1 for x in valid if x["停点"] < 2)
    before = statistics.mean([x["原链长"] for x in valid]) if valid else 0
    after = statistics.mean([x["停点"] for x in valid]) if valid else 0
    from collections import Counter
    dist = Counter(x["停因"] for x in valid)
    print("\n=== 判定（PREREG_STOP §3）===")
    print(f"E1 压缩到人步数以内: {e1}/{len(valid)}  ⇒ {'≥7/10 ⇒ 判据有效' if e1 >= 7 and len(valid) == 10 else '⇒ 按 PREREG 判'}")
    print(f"E2 停在根的支: {e2}")
    print(f"E3 停层精度: {sorted({x['停层精度'] for x in valid})}")
    print(f"E4 停因分布: {dict(dist)}")
    if nops:
        print(f"全过路的支: {len(nops)}（{[x['需求'] for x in nops]}）")
    print(f"原链长均值 {before:.1f} → 压缩后 {after:.1f} ｜人的步数均值 "
          f"{statistics.mean(list(HUMAN_STEPS.values())):.1f}")
    verdict = ("判据有效" if (e1 >= 7 and len(valid) == 10 and e2 == 0)
               else ("有效但需按 E4 限定" if e1 >= 7 else "停层判据 V1 无效"))
    print("判定:", verdict)

    outp = Path(__file__).with_name("stop_v1.json")
    outp.write_text(json.dumps({"K": K, "verdict": verdict, "E1": e1,
                                "E2_root_stops": e2, "detail": out,
                                "human_steps": HUMAN_STEPS},
                               ensure_ascii=False, indent=1),
                    encoding="utf-8", newline="\n")
    print(f"→ {outp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
