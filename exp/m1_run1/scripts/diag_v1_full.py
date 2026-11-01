#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断（PREREG V3）：用**全量**结构（不截断）重跑 V1，判断 0/5 的归因。

判据不变，只决定"V1-A 红"是结构问题还是呈现件问题。
输出：raw/diag_v1_full.txt + v_diag/*.json
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "exp"))
import entry as E                                    # noqa: E402
import llm                                            # noqa: E402

M1 = Path(__file__).resolve().parents[1]
OUT = M1 / "v_diag"
SRC = REPO / "_data" / "openalex-citations"
INTENT_A = ["era=2020及以后"]
INTENT_B = ["type=article"]


def add_era(root: Path) -> None:
    from ldv.corpus.loader import load_nodes
    nodes = load_nodes(root / "nodes")
    vals = {}
    for nid, n in nodes.items():
        m = re.search(r"发表年\s*(\d{4})", str(n.fields.get("scope") or ""))
        if m:
            vals[nid] = "2020及以后" if int(m.group(1)) >= 2020 else "2020以前"
    d = root / "derived"
    d.mkdir(parents=True, exist_ok=True)
    (d / "era.json").write_text(json.dumps(
        {"_meta": {"field": "era", "what": "发表年分档", "derived_from": "scope"},
         "values": vals}, ensure_ascii=False), encoding="utf-8", newline="\n")


def fresh(name: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix=f"m1d-{name}-")) / "_data" / "oa"
    shutil.copytree(SRC, root)
    add_era(root)
    return root


def full_rows(app, require: list[str]) -> list[dict]:
    """全量子树：不限深度，返回 require 命中的子树的全部后代（不截断）。"""
    k = app.kernels["keyset"]
    r = app.query({"require": require})
    roots = [k.direction(n["did"]) for n in (r.get("nodes") or [])]
    seen, rows = set(), []

    def walk(d, depth):
        if d.did in seen:
            return
        seen.add(d.did)
        req, forb = d.payload
        rows.append({"depth": depth, "did": d.did, "rank": d.rank,
                     "members": len(k.members_of(d)),
                     "req": sorted(req), "forb": sorted(forb)})
        for c in k.children_of(d):
            walk(c, depth + 1)

    for d in roots:
        walk(d, 0)
    return rows


def render(rows: list[dict], title: str) -> str:
    out = [f"# {title}", ""]
    for r in rows:
        pred = " ∧ ".join(r["req"]) or "（全部）"
        if r["forb"]:
            pred += " ∧ ¬(" + " ∨ ".join(r["forb"]) + ")"
        out.append(f"{'  ' * min(r['depth'], 8)}- {pred} ｜ {r['members']} 项")
    return "\n".join(out) + "\n"


def prompt(d1: str, d2: str) -> str:
    return f"""下面有两份结构摘要，每份是一组层级式的分类结果（来自同一批材料）。

请判断：哪一份是按「发表时间」组织的，哪一份是按「文献类型（article/review 等）」组织的。

如果看不出来，直接说看不出来。

【摘要 1】
{d1}

【摘要 2】
{d2}

先用两句话说明依据，然后另起一行输出 JSON：
{{"time": "摘要1 或 摘要2", "type": "摘要1 或 摘要2", "basis": "一句话依据"}}"""


def main() -> int:
    root = fresh("v1full")
    a = E.App(root=str(root / "nodes"))
    rows_era = full_rows(a, INTENT_A)
    rows_type = full_rows(a, INTENT_B)
    text_era = render(rows_era, "结构摘要 甲")
    text_type = render(rows_type, "结构摘要 乙")
    print(f"全量：era 树 {len(rows_era)} 方向 / {len(text_era)} 字；"
          f"type 树 {len(rows_type)} 方向 / {len(text_type)} 字", flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    n_correct, pos1 = 0, 0
    for i in range(5):
        # 顺序均衡：3 次 [type, era]，2 次 [era, type]（抵消主轮 4/5 同序偏差）
        if i < 3:
            order = [("type", text_type), ("era", text_era)]
        else:
            order = [("era", text_era), ("type", text_type)]
        p = prompt(order[0][1], order[1][1])
        r = llm.chat([{"role": "user", "content": p}], model="deepseek-chat",
                     max_tokens=1200, temperature=0, tries=4)
        text = r.get("text") or ""
        m = re.search(r"\{.*\}", text, re.DOTALL)
        js = json.loads(m.group(0)) if m else {}
        pick = str(js.get("time") or "")
        truth = {1: order[0][0], 2: order[1][0]}
        correct = (pick == "摘要1" and truth[1] == "era") or \
                  (pick == "摘要2" and truth[2] == "era")
        n_correct += int(correct)
        pos1 += int(pick == "摘要1")
        print(f"  inst{i} order={[o[0] for o in order]} time={pick} correct={correct} "
              f"basis={str(js.get('basis'))[:60]}", flush=True)
        (OUT / f"V1full_{i}.json").write_text(json.dumps(
            {"task": "V1-full", "instance": i, "order": [o[0] for o in order],
             "prompt_sha256": hashlib.sha256(p.encode()).hexdigest(),
             "answer": text, "json": js, "correct": correct},
            ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")

    print(json.dumps({"n_correct": n_correct, "of": 5, "picked_first": pos1,
                      "rows_era": len(rows_era), "rows_type": len(rows_type),
                      "note": "全量摘要诊断（PREREG V3）"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
