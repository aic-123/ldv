#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阶段二摘要生成：V1/V2/V3/S6 的呈现件（真件 + 阴性对照）。

统一格式（一页以内）：缩进层级 + 方向名（谓词）+ 成员数。
对照件生成规则见 PREREG §7（打乱配对、层级合法、字数一致）。
"""
from __future__ import annotations

import json
import random
import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
import entry as E                                    # noqa: E402
from ldv.corpus.loader import load_nodes             # noqa: E402

OUT = Path(__file__).resolve().parents[1]
SRC = REPO / "_data" / "openalex-citations"
INTENT_A = ["era=2020及以后"]
INTENT_B = ["type=article"]


def add_era(root: Path) -> None:
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


def fresh(name: str, with_era: bool = True, limit: int | None = None) -> Path:
    root = Path(tempfile.mkdtemp(prefix=f"m1s-{name}-")) / "_data" / "oa"
    shutil.copytree(SRC, root)
    if limit:
        files = sorted((root / "nodes").glob("*.md"))
        for p in files[limit:]:
            p.unlink()
    if with_era:
        add_era(root)
    return root


def subtree_rows(app, require: list[str], max_rank: int = 4) -> list[dict]:
    """以 require 命中的子方向为根，取其后代（最多 max_rank 层）。"""
    k = app.kernels["keyset"]
    r = app.query({"require": require})
    roots = [k.direction(n["did"]) for n in (r.get("nodes") or [])]
    seen, rows = set(), []

    def walk(d, depth):
        if d.did in seen or depth > max_rank:
            return
        seen.add(d.did)
        req, forb = d.payload
        rows.append({"depth": depth, "did": d.did, "rank": d.rank,
                     "members": len(k.members_of(d)),
                     "req": sorted(req), "forb": sorted(forb)})
        for c in k.children_of(d):
            walk(c, depth + 1)

    for d in roots[:12]:
        walk(d, 0)
    return rows


def render(rows: list[dict], title: str) -> str:
    out = [f"# {title}", ""]
    for r in rows:
        pred = " ∧ ".join(r["req"]) or "（全部）"
        if r["forb"]:
            pred += " ∧ ¬(" + " ∨ ".join(r["forb"]) + ")"
        out.append(f"{'  ' * r['depth']}- {pred} ｜ {r['members']} 项")
    return "\n".join(out) + "\n"


def scramble(rows: list[dict], seed: int) -> list[dict]:
    """阴性对照：打乱「方向名 ↔ 成员数」（PREREG §7）。层级/深度/分支数不变。"""
    rng = random.Random(seed)
    sizes = [r["members"] for r in rows]
    rng.shuffle(sizes)
    out = []
    for r, s in zip(rows, sizes):
        out.append({**r, "members": s})
    return out


def groupby_rows(app) -> list[dict]:
    """G：单字段 groupby（type 分档 + evidence_status 二阶）——机械，限 30 行内。"""
    rows = [{"depth": 0, "rank": 1, "members": len(app.nodes), "req": [], "forb": []}]
    types: dict[str, list[str]] = {}
    for nid, ks in app.keys_of.items():
        t = next((x for x in ks if x.startswith("type=")), None)
        if t:
            types.setdefault(t, []).append(nid)
    for t, ids in sorted(types.items(), key=lambda kv: -len(kv[1]))[:8]:
        rows.append({"depth": 1, "rank": 2, "members": len(ids), "req": [t], "forb": []})
        ev: dict[str, int] = {}
        for nid in ids:
            for k in app.keys_of[nid]:
                if k.startswith("evidence_status="):
                    ev[k] = ev.get(k, 0) + 1
        for e, c in sorted(ev.items(), key=lambda kv: -kv[1])[:2]:
            rows.append({"depth": 2, "rank": 3, "members": c, "req": [e], "forb": []})
    return rows[:30]


def dirstree_rows(root: Path) -> list[dict]:
    """T：材料目录树 + 文件名（真实文件系统视角：nodes/ 平铺，按 id 前缀分组）。"""
    files = sorted((root / "nodes").glob("*.md"))
    rows = [{"depth": 0, "rank": 1, "members": len(files), "req": [], "forb": []}]
    groups: dict[str, list[str]] = {}
    for p in files:
        groups.setdefault(p.name[:2], []).append(p.stem)
    for g, names in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:6]:
        rows.append({"depth": 1, "rank": 2, "members": len(names),
                     "req": [f"nodes/{g}*（{len(names)} 个文件）"], "forb": []})
        for nm in names[:3]:
            rows.append({"depth": 2, "rank": 3, "members": 1,
                         "req": [f"nodes/{nm}.md"], "forb": []})
    return rows[:30]


def main() -> int:
    # A 意图树（era）/ B 意图树（type）
    rA = fresh("A", with_era=True)
    aA = E.App(root=str(rA / "nodes"))
    rowsA = subtree_rows(aA, INTENT_A)
    rowsB = subtree_rows(aA, INTENT_B)

    # V1-B 对照：另一棵"同意图"树（同 era，但材料只用前 3000 节点）
    rA2 = fresh("A2", with_era=True, limit=3000)
    aA2 = E.App(root=str(rA2 / "nodes"))
    rowsA2 = subtree_rows(aA2, INTENT_A)

    # V2：A-only（前 3000）/ A+B（全量）/ A+B′（B 打乱后追加）
    rAB = fresh("AB", with_era=False)          # 全量、无 era（避免 derived 影响）
    aOnly = E.App(root=str(fresh("only", with_era=False, limit=3000) / "nodes"))
    aAB = E.App(root=str(rAB / "nodes"))
    rowsOnly = subtree_rows(aOnly, [])
    rowsAB = subtree_rows(aAB, [])
    rowsABp = subtree_rows(aAB, [])            # 干扰件：同一结构但呈现打乱配对
    rowsABp = scramble(rowsABp, seed=7)

    # V3：L（ldv 真结构）/ G（groupby）/ T（目录树）
    rowsL = subtree_rows(aAB, [])
    rowsG = groupby_rows(aAB)
    rowsT = dirstree_rows(rAB)

    # S6：真件（L 的一页摘要）+ 阴性对照（打乱配对）
    s6_real = rowsL[:40]
    s6_null = scramble(rowsL[:40], seed=11)

    files = {
        "V1_tree_era.md": render(rowsA[:40], "结构摘要 甲"),
        "V1_tree_type.md": render(rowsB[:40], "结构摘要 乙"),
        "V1B_tree_era_small.md": render(rowsA2[:40], "结构摘要 甲"),
        "V2_A_only.md": render(rowsOnly[:40], "结构摘要 一"),
        "V2_A_B.md": render(rowsAB[:40], "结构摘要 二"),
        "V2_A_B_distractor.md": render(rowsABp[:40], "结构摘要 三"),
        "V3_L.md": render(rowsL[:36], "结构摘要 一"),
        "V3_G.md": render(rowsG[:36], "结构摘要 二"),
        "V3_T.md": render(rowsT, "结构摘要 三"),
        "summary_1page.md": render(s6_real, "结构摘要"),
        "summary_null.md": render(s6_null, "结构摘要"),
    }
    for name, text in files.items():
        (OUT / name).write_text(text, encoding="utf-8", newline="\n")
        print(f"  {name}: {len(text.splitlines())} 行, {len(text)} 字")

    meta = {
        "intent_A": INTENT_A, "intent_B": INTENT_B,
        "rows_A": len(rowsA), "rows_B": len(rowsB), "rows_A2": len(rowsA2),
        "rows_only": len(rowsOnly), "rows_AB": len(rowsAB),
        "n_nodes": [len(aA.nodes), len(aA2.nodes), len(aAB.nodes)],
        "tree_A": aA.tree, "tree_AB": aAB.tree,
        "fp": {"A": aA.fingerprint(), "A2": aA2.fingerprint(), "AB": aAB.fingerprint()},
    }
    (OUT / "summary_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                           encoding="utf-8", newline="\n")
    print(json.dumps(meta, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
