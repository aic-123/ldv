#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""折叠 v2（PREREG V5）：修 V4 的三条实现缺陷后重测。

修的是：①drill 8 支 ②祖先链一律保留 ③折叠行用结构语言、按父分组。
内核仍零改动：只调 App.query / drill / all_directions 读结构，自己渲染。
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
OUT = M1 / "v_fold2"
SRC = REPO / "_data" / "openalex-citations"
K = 8


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


def collect(app) -> list[dict]:
    k = app.kernels["keyset"]
    rows, seen = [], set()

    def walk(d, depth, parent):
        if d.did in seen:
            return
        seen.add(d.did)
        req, forb = d.payload
        rows.append({"did": d.did, "depth": depth, "rank": d.rank, "parent": parent,
                     "members": len(k.members_of(d)),
                     "req": sorted(req), "forb": sorted(forb),
                     "has_children": bool(k.children_of(d))})
        for c in k.children_of(d):
            walk(c, depth + 1, d.did)

    for d in k.all_directions():
        if d.rank == 1:
            walk(d, 0, None)
    return rows


def ancestors(rows: list[dict], dids: set[str]) -> set[str]:
    by_did = {r["did"]: r for r in rows}
    out: set[str] = set()
    for did in dids:
        p = by_did.get(did, {}).get("parent")
        while p:
            out.add(p)
            p = by_did.get(p, {}).get("parent")
    return out


def render(rows: list[dict], keep: set[str], title: str) -> str:
    """保留 keep（含祖先）；同父下的未保留兄弟合成一行『（其余分支未展开）』。"""
    out = [f"# {title}", ""]
    folded_under: dict[str, int] = {}
    for r in rows:
        indent = "  " * min(r["depth"], 8)
        if r["did"] in keep:
            pred = " ∧ ".join(r["req"]) or "（全部）"
            if r["forb"]:
                pred += " ∧ ¬(" + " ∨ ".join(r["forb"]) + ")"
            out.append(f"{indent}- {pred} ｜ {r['members']} 项")
            if r["has_children"] and folded_under.get(r["did"]):
                out.append(f"{indent}  （其余分支未展开）")
        else:
            par = r["parent"] or "ROOT"
            folded_under[par] = folded_under.get(par, 0) + 1
    # 顶层若有被折的，补一行
    if folded_under.get("ROOT"):
        out.append(f"- （其余分支未展开）")
    return "\n".join(out) + "\n"


def prompt(a: str, b: str) -> str:
    return f"""下面有两份结构摘要，都是从同一批材料整理出来的分类结果。

问题：哪一份更像**人**（有取舍地、按自己的判断）组织出来的？哪一份更像机械地逐个列出？

如果看不出来，直接说看不出来。

【摘要 1】
{a}

【摘要 2】
{b}

先说明理由（两到三句），然后另起一行输出 JSON：
{{"human": "摘要1 或 摘要2", "reason": "你的理由"}}"""


def run_group(tag: str, folded: str, other: str) -> int:
    wins = 0
    for i in range(5):
        if i % 2 == 0:
            left, right = ("folded", folded), ("other", other)
        else:
            left, right = ("other", other), ("folded", folded)
        p = prompt(left[1], right[1])
        r = llm.chat([{"role": "user", "content": p}], model="deepseek-chat",
                     max_tokens=1200, temperature=0, tries=4)
        text = r.get("text") or ""
        m = re.search(r"\{.*\}", text, re.DOTALL)
        js = json.loads(m.group(0)) if m else {}
        pick = str(js.get("human") or "")
        folded_side = "摘要1" if left[0] == "folded" else "摘要2"
        win = pick == folded_side
        wins += int(win)
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"{tag}_{i}.json").write_text(json.dumps(
            {"task": tag, "instance": i, "left": left[0], "right": right[0],
             "pick": pick, "folded_wins": win,
             "prompt_sha256": hashlib.sha256(p.encode()).hexdigest(),
             "answer": text, "json": js}, ensure_ascii=False, indent=1),
            encoding="utf-8", newline="\n")
        print(f"  {tag} inst{i} left={left[0]} pick={pick} win={win}", flush=True)
    return wins


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="m1f2-")) / "_data" / "oa"
    shutil.copytree(SRC, root)
    add_era(root)
    a = E.App(root=str(root / "nodes"))
    r = a.query({"require": []})
    order = [n["did"] for n in r["nodes"]]
    for did in order[-K:]:                     # V5-①：drill 8 支
        a.drill(did, [])
    tend = a._tendency
    ranked = sorted(tend.items(), key=lambda kv: -kv[1])
    rows = collect(a)
    by_did = {x["did"]: x for x in rows}

    leaves = [x["did"] for x in rows if not x["has_children"]]
    keep_t = {did for did, _ in ranked[:K]} & set(x["did"] for x in rows)
    keep_t |= ancestors(rows, keep_t)           # V5-②：祖先链一律保留
    rng = random.Random(20261101)
    keep_s = set(rng.sample(leaves, min(K, len(leaves))))
    keep_s |= ancestors(rows, keep_s)

    text_full = render(rows, set(by_did), "结构摘要")
    text_t = render(rows, keep_t, "结构摘要")
    text_s = render(rows, keep_s, "结构摘要")
    print(f"方向 {len(rows)}｜倾向 {len(ranked)} 个｜keep_t {len(keep_t)}｜keep_s {len(keep_s)}",
          flush=True)
    print(f"字数 full {len(text_full)}｜folded {len(text_t)}｜shuffled {len(text_s)}", flush=True)
    print(f"倾向排序 top: {[d for d, _ in ranked[:K]]}", flush=True)

    d1 = run_group("D1v2", text_t, text_full)
    d2 = run_group("D2v2", text_t, text_s)
    print(json.dumps({"D1_folded_wins": d1, "D2_folded_wins": d2, "K": K,
                      "n_dirs": len(rows), "keep_t": len(keep_t), "keep_s": len(keep_s),
                      "chars": [len(text_full), len(text_t), len(text_s)]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
