#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""折叠实验（PREREG V4）：呈现层按倾向折叠 vs 不折叠 vs 随机折叠。

内核零改动——只调 App.query / drill / all_directions 读结构，自己渲染文本。
判据：D1 主（folded vs full）、D2 阴性（folded vs shuffled）。
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
OUT = M1 / "v_fold"
SRC = REPO / "_data" / "openalex-citations"
K = 8                                                 # 保留的方向数（PREREG V4 定死）


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


def rows_of(app) -> list[dict]:
    k = app.kernels["keyset"]
    rows, seen = [], set()

    def walk(d, depth):
        if d.did in seen:
            return
        seen.add(d.did)
        req, forb = d.payload
        rows.append({"did": d.did, "depth": depth, "rank": d.rank,
                     "members": len(k.members_of(d)),
                     "req": sorted(req), "forb": sorted(forb)})
        for c in k.children_of(d):
            walk(c, depth + 1)

    for d in k.all_directions():
        if d.rank == 1:
            walk(d, 0)
    return rows


def render(rows: list[dict], keep: set[str], title: str) -> str:
    """保 keep 里的方向；其余方向折叠成一行（同一父下的折叠汇总）。"""
    out = [f"# {title}", ""]
    folded_at_depth: dict[int, int] = {}
    for r in rows:
        pred = " ∧ ".join(r["req"]) or "（全部）"
        if r["forb"]:
            pred += " ∧ ¬(" + " ∨ ".join(r["forb"]) + ")"
        indent = "  " * min(r["depth"], 8)
        if r["did"] in keep:
            out.append(f"{indent}- {pred} ｜ {r['members']} 项")
        else:
            folded_at_depth[r["depth"]] = folded_at_depth.get(r["depth"], 0) + 1
    for depth in sorted(folded_at_depth):
        n = folded_at_depth[depth]
        out.append(f"{'  ' * min(depth, 8)}- …（另有 {n} 个方向，低倾向，可展开）")
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


def run_group(tag: str, a_text: str, b_text: str, a_is_folded: bool) -> dict:
    wins = 0
    for i in range(5):
        if i % 2 == 0:
            left, right = ("folded", a_text), ("other", b_text)
        else:
            left, right = ("other", b_text), ("folded", a_text)
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
        rec = {"task": tag, "instance": i, "left": left[0], "right": right[0],
               "pick": pick, "folded_wins": win,
               "prompt_sha256": hashlib.sha256(p.encode()).hexdigest(),
               "answer": text, "json": js}
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"{tag}_{i}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1),
                                             encoding="utf-8", newline="\n")
        print(f"  {tag} inst{i} left={left[0]} pick={pick} folded_win={win}", flush=True)
    _ = a_is_folded
    return {"wins": wins, "of": 5}


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="m1fold-")) / "_data" / "oa"
    shutil.copytree(SRC, root)
    add_era(root)
    a = E.App(root=str(root / "nodes"))

    # 真实倾向：drill 位次靠后的三支（propensity 小 ⇒ 倾向大，阶段一 X1/X4R 验过）
    r = a.query({"require": []})
    order = [n["did"] for n in r["nodes"]]
    for did in (order[-1], order[-2], order[-3]):
        a.drill(did, [])
    tend = a._tendency
    ranked = sorted(tend.items(), key=lambda kv: -kv[1])
    print(f"倾向 {len(ranked)} 个方向，top: {ranked[:4]}", flush=True)

    rows = rows_of(a)
    keep_tend = {did for did, _ in ranked[:K]}
    # 随机折叠对照：同样 K 个，但随机选（不看倾向）
    rng = random.Random(20261101)
    keep_rand = set(rng.sample([x["did"] for x in rows], K))

    text_full = render(rows, {x["did"] for x in rows}, "结构摘要")
    text_fold = render(rows, keep_tend, "结构摘要")
    text_shuf = render(rows, keep_rand, "结构摘要")
    print(f"方向 {len(rows)}；full {len(text_full)} 字｜folded {len(text_fold)} 字"
          f"｜shuffled {len(text_shuf)} 字", flush=True)
    print(f"折叠保留（倾向）：{sorted(keep_tend)}", flush=True)
    print(f"折叠保留（随机）：{sorted(keep_rand)}", flush=True)
    print(f"两组重合 {len(keep_tend & keep_rand)}/{K}", flush=True)

    d1 = run_group("D1", text_fold, text_full, True)
    d2 = run_group("D2", text_fold, text_shuf, True)
    print(json.dumps({"D1_folded_wins": d1, "D2_folded_wins": d2,
                      "K": K, "n_dirs": len(rows),
                      "overlap_keep": len(keep_tend & keep_rand)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
