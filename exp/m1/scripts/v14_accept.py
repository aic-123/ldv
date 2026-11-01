#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V14：attention 字段的验收（提案 ATTENTION_READOUT.md §6 的 A-1..A-5）。

A-1 = I1..I5 五条不变式；A-2 无记录态；A-3 协议信封；A-4 确定性；A-5 沿用 V13-A。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "exp"))
import entry as E                                    # noqa: E402
from ldv import protocol as PROTO                    # noqa: E402

M1 = Path(__file__).resolve().parents[1]
OUT = M1 / "v_accept"
SRC = REPO / "_data" / "openalex-citations"
NOW = datetime(2026, 11, 1, 12, 0, 0)
res: list[dict] = []


def chk(cid: str, ok: bool, read: dict) -> None:
    res.append({"id": cid, "pass": bool(ok), "read": read})
    print(f"[{cid}] {'PASS' if ok else 'RED '} {json.dumps(read, ensure_ascii=False)}",
          flush=True)


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="m1v14-")) / "_data" / "oa"
    shutil.copytree(SRC, root)
    nodes = root / "nodes"

    # ── 无记录态（A-2 / I4）──
    a = E.App(root=str(nodes), usage_now=NOW)
    q0 = a.query({"require": []})
    att0 = q0["attention"]
    chk("A-2", (att0["basis"] == "default" and att0["rows"] == []
                and q0["ordered_by"] == "default"),
        {"basis": att0["basis"], "rows": len(att0["rows"]),
         "ordered_by": q0["ordered_by"], "hl": att0["half_life_days"]})
    chk("I4", (att0["basis"] == "default") == (att0["rows"] == []),
        {"basis": att0["basis"], "n": len(att0["rows"])})

    # ── 造两条真实记录（drill 走公开接口）──
    d1 = q0["nodes"][0]["did"]
    a.drill(d1, [])
    q1 = a.query({"require": []})
    d2 = next(n["did"] for n in q1["nodes"] if n["did"] != d1)
    a.drill(d2, [])

    a2 = E.App(root=str(nodes), usage_now=NOW)       # 重建 ⇒ 从盘上读回
    q = a2.query({"require": []})
    att = q["attention"]
    pos = {n["did"]: i for i, n in enumerate(q["nodes"])}
    print("nodes 前3:", [n["did"] for n in q["nodes"][:3]], flush=True)
    print("attention:", json.dumps(att, ensure_ascii=False), flush=True)

    # I1 同序
    idx = [pos[x["did"]] for x in att["rows"]]
    chk("I1", idx == sorted(idx) and len(idx) == len(att["rows"]),
        {"row_positions": idx})
    # I2 子集
    chk("I2", set(x["did"] for x in att["rows"]) <= set(pos),
        {"n_rows": len(att["rows"]), "n_nodes": len(pos)})
    # I3 无截断时相等
    chk("I3", att["n_with_records"] == len(att["rows"]),
        {"n_with_records": att["n_with_records"], "len_rows": len(att["rows"]),
         "branches_hidden": q.get("branches_hidden")})
    # A-4 确定性
    q_again = a2.query({"require": []})
    chk("A-4", json.dumps(att, sort_keys=True) ==
        json.dumps(q_again["attention"], sort_keys=True), {"identical": True})

    # I5 可移除性（动态 + 静态）
    def strip(r: dict) -> str:
        return json.dumps({k: v for k, v in r.items() if k not in ("attention", "ms")},
                          sort_keys=True, ensure_ascii=False)
    dynamic_ok = strip(q) == strip(a2.query({"require": []}))
    src = (REPO / "entry.py").read_text(encoding="utf-8")
    # attention 只应出现在：方法定义、注释、返回处 —— 没有任何**读取**它的代码
    reads = [ln for ln in src.splitlines()
             if re.search(r'(spec|opts|r|q|self\.\w+)\s*(\.get\(|\[)\s*["\']attention',
                          ln)]
    chk("I5", dynamic_ok and not reads,
        {"dynamic_identical": dynamic_ok, "read_sites": reads[:3]})

    # A-3 协议信封（禁词检查）
    env = PROTO.envelope(PROTO.MSG_RESULT, q, frm="ldv", fp=a2.fingerprint())
    ok, why = PROTO.check_envelope(env)
    chk("A-3", ok, {"why": why or "(收)", "banned_checked": True})

    # A-5 沿用 V13-A
    p = subprocess.run([sys.executable, str(M1 / "scripts" / "v13_forge.py")],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env={**__import__("os").environ,
                                              "PYTHONIOENCODING": "utf-8"})
    tail = [ln for ln in (p.stdout or "").splitlines() if ln.startswith("{")]
    chk("A-5", p.returncode == 0, {"exit": p.returncode,
                                   "summary": (tail[-1][:120] if tail else "")})

    OUT.mkdir(parents=True, exist_ok=True)
    n_ok = sum(1 for x in res if x["pass"])
    summary = {"pass": n_ok, "total": len(res),
               "verdict": "attention 验收通过" if n_ok == len(res) else "有红项"}
    (OUT / "result.json").write_text(json.dumps(
        {**summary, "detail": res}, ensure_ascii=False, indent=1),
        encoding="utf-8", newline="\n")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if n_ok == len(res) else 1


if __name__ == "__main__":
    raise SystemExit(main())
