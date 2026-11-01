#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V13（PREREG）：防御性验证——伪造注意力的路是否被堵住。

A 组：对外接口（App 公开方法）必须全部守住。
B 组：工具层口子，如实记录（不判实现 FAIL）。
"""
from __future__ import annotations

import hashlib
import inspect
import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "exp"))
import entry as E                                    # noqa: E402
from ldv.tools import usage_store as US              # noqa: E402

M1 = Path(__file__).resolve().parents[1]
OUT = M1 / "v_forge"
SRC = REPO / "_data" / "openalex-citations"
NOW = datetime(2026, 11, 1, 12, 0, 0)
results: list[dict] = []


def chk(cid: str, ok: bool, read: dict, expect: str = "") -> None:
    results.append({"id": cid, "pass": bool(ok), "read": read, "expect": expect})
    flag = "PASS" if ok else "RED "
    print(f"[{cid}] {flag} {json.dumps(read, ensure_ascii=False)}", flush=True)


def label_of(app, did: str) -> str:
    d = app.kernels["keyset"].direction(did)
    req, forb = d.payload
    s = " ∧ ".join(sorted(req)) or "（全部）"
    if forb:
        s += " ∧ ¬(" + " ∨ ".join(sorted(forb)) + ")"
    return s


def md5s(root: Path) -> dict[str, str]:
    d = root / US.DIRNAME
    if not d.exists():
        return {}
    return {p.name: hashlib.md5(p.read_bytes()).hexdigest() for p in sorted(d.glob("*.md"))}


def main() -> int:
    root0 = Path(tempfile.mkdtemp(prefix="m1v13-")) / "_data" / "oa"
    shutil.copytree(SRC, root0)
    root = root0 / "nodes"
    a = E.App(root=str(root), usage_now=NOW)
    k = a.kernels["keyset"]

    # ① A5：只用公开只读方法（query 会 stage，但 stage 不算有用）
    r0 = a.query({"require": []})
    tend0 = US.aged_tendency(root0, now=NOW)
    chk("A5", (len(tend0) == 0 and r0.get("ordered_by") == "default"),
        {"n_tendency": len(tend0), "ordered_by": r0.get("ordered_by"),
         "n_staged_batches": len(US.stage_load(root0))},
        "曝光不进倾向")

    # ② A6：篡改返回值不影响后续
    r1 = a.query({"require": []})
    clean = {kk: vv for kk, vv in r1.items() if kk != "ms"}
    r1["ordered_by"] = "FAKE"
    r1["nodes"] = list(reversed(r1["nodes"]))
    r2 = a.query({"require": []})
    clean2 = {kk: vv for kk, vv in r2.items() if kk != "ms"}
    same = json.dumps(clean, sort_keys=True, ensure_ascii=False) == \
        json.dumps(clean2, sort_keys=True, ensure_ascii=False)
    chk("A6", same, {"identical": same}, "篡改返回值不影响内部状态")

    # ③ A1：drill 一个不在本次 shown 里的方向
    r = a.query({"require": ["type=review"]})
    shown = {n["did"] for n in r["nodes"]}
    outsider = None
    for d in k.all_directions():
        if d.did not in shown and "type=article" in label_of(a, d.did):
            outsider = d.did
            break
    if outsider is None:
        chk("A1", False, {"error": "找不到不在 shown 的 article 方向"}, "")
    else:
        out = a.drill(outsider, ["type=review"])
        chk("A1", out.get("recorded") is False,
            {"did": outsider, "recorded": out.get("recorded"),
             "note": str(out.get("record_note") or out.get("error"))[:70]},
            "不在 shown 的不许 promote")

    # ④ A2：同一 (qfp,did) 重复写
    rr = a.query({"require": []})
    first = rr["nodes"][0]["did"]
    o1 = a.drill(first, [])
    rr2 = a.query({"require": []})          # 同 require ⇒ 同 qfp
    o2 = a.drill(first, [])
    chk("A2", (o1.get("recorded") is True and o2.get("recorded") is False),
        {"did": first, "first": o1.get("recorded"), "second": o2.get("recorded"),
         "note2": str(o2.get("record_note") or o2.get("error"))[:70]},
        "同 (qfp,did) 不叠加")

    # ⑤ A3：机械检查签名
    sig = inspect.signature(E.App.drill)
    params = list(sig.parameters)
    bad = [p for p in params if p in ("at", "time", "when", "ts", "outcome",
                                      "half_life_days", "propensity")]
    chk("A3", not bad, {"params": params, "forbidden_hits": bad},
        "drill 不接受时间/强度参数")

    # ⑥ A4：agent 请求判错
    o = a.drill(first, [], verdict="wrong", actor="agent")
    chk("A4", o.get("ok") is False,
        {"ok": o.get("ok"), "error": str(o.get("error"))[:60]}, "agent 不能判错")

    # ── B 组：工具层口子，如实记录 ──
    before = US.aged_tendency(root0, now=NOW)
    b1 = US.append(root0, did="FORGED", outcome=1.0, propensity=0.5,
                   qfp="forge-test", at="2000-01-01T00:00:00")
    after = US.aged_tendency(root0, now=NOW)
    chk("B1", True,
        {"accepted": after.get("FORGED") is not None,
         "value": round(after.get("FORGED", 0.0), 6),
         "normal_same_age_would_be": round(1.0 / 0.5, 6),
         "note": "工具层接受外部 at ⇒ 可伪造；serve*.py 未暴露 append"},
        "记录（不判 FAIL）")

    b2_default = US.aged_tendency(root0, now=NOW).get("FORGED", 0.0)
    b2_long = US.aged_tendency(root0, now=NOW, half_life_days=1e5).get("FORGED", 0.0)
    chk("B2", True,
        {"default_hl_30": round(b2_default, 6), "hl_1e5": round(b2_long, 6),
         "ratio": round(b2_long / b2_default, 3) if b2_default else None,
         "note": "ageed_tendency 接受 half_life_days ⇒ 可外部改遗忘速度；entry 不传它"},
        "记录（不判 FAIL）")

    m1 = md5s(root0)
    a2 = E.App(root=str(root), usage_now=NOW + timedelta(days=300))
    _ = a2.query({"require": []})
    m2 = md5s(root0)
    chk("B3", m1 == m2,
        {"files": len(m1), "identical": m1 == m2,
         "note": "usage_now 只改读侧；账本逐字不变"},
        "读侧时间不改账本")

    OUT.mkdir(parents=True, exist_ok=True)
    a_ok = sum(1 for x in results if x["id"].startswith("A") and x["pass"])
    a_n = sum(1 for x in results if x["id"].startswith("A"))
    summary = {"A_pass": a_ok, "A_total": a_n,
               "verdict": "对外接口守住" if a_ok == a_n else "对外可伪造（attention 不得显性）"}
    (OUT / "result.json").write_text(json.dumps(
        {**summary, "detail": results}, ensure_ascii=False, indent=1),
        encoding="utf-8", newline="\n")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if a_ok == a_n else 1


if __name__ == "__main__":
    raise SystemExit(main())
