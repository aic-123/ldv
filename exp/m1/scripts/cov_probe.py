#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V7（PREREG）：活动 limit = 覆盖率截断。同一棵树，四份呈现件。

A 全量 / B 覆盖95% / C 覆盖90% / B' 随机同数量（阴性）。
三组二选一各 5 实例：D4(B vs A)、D5(C vs A)、D6(B vs B')。
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
OUT = M1 / "v_cov"
SRC = REPO / "_data" / "openalex-citations"


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
        rows.append({"did": d.did, "depth": depth, "parent": parent,
                     "members": frozenset(k.members_of(d)),
                     "seg": (" ∧ ".join(sorted(req)) or "（全部）")
                            + (" ∧ ¬(" + " ∨ ".join(sorted(forb)) + ")" if forb else "")})
        for c in k.children_of(d):
            walk(c, depth + 1, d.did)

    for d in k.all_directions():
        if d.rank == 1:
            walk(d, 0, None)
    return rows


def ancestors(rows: list[dict], keep: set[str]) -> set[str]:
    par = {r["did"]: r["parent"] for r in rows}
    out = set(keep)
    for did in list(keep):
        p = par.get(did)
        while p:
            out.add(p)
            p = par.get(p)
    return out


def pick_by_cover(rows: list[dict], target: float, total: int) -> tuple[set[str], float, int]:
    # 排除根方向：根成员 = 全部项，会一步吃掉 100% 覆盖（V7.1 勘误）
    order = sorted([r for r in rows if r["parent"] is not None],
                   key=lambda r: (-len(r["members"]), r["did"]))
    covered: set[str] = set()
    keep: set[str] = set()
    for r in order:
        if r["did"] in keep:
            continue
        keep.add(r["did"])
        covered |= r["members"]
        if total and len(covered) / total >= target:
            break
    keep = ancestors(rows, keep)
    cov = len(covered) / total if total else 0.0
    return keep, cov, len(covered)


def render(rows: list[dict], keep: set[str], title: str) -> str:
    out = [f"# {title}", ""]
    dropped = [r for r in rows if r["did"] not in keep]
    folded_by_parent: dict[str, int] = {}
    for r in dropped:
        folded_by_parent[r["parent"] or "ROOT"] = folded_by_parent.get(r["parent"] or "ROOT", 0) + 1
    seen_parents: set[str] = set()
    for r in rows:
        indent = "  " * min(r["depth"], 8)
        if r["did"] in keep:
            out.append(f"{indent}- {r['seg']} ｜ {len(r['members'])} 项")
            if folded_by_parent.get(r["did"]):
                out.append(f"{indent}  （其余 {folded_by_parent[r['did']]} 个方向未展开）")
            seen_parents.add(r["did"])
    if folded_by_parent.get("ROOT"):
        out.append(f"- （其余 {folded_by_parent['ROOT']} 个方向未展开）")
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


def run_group(tag: str, test_text: str, base_text: str) -> int:
    wins = 0
    for i in range(5):
        if i % 2 == 0:
            left, right = ("test", test_text), ("base", base_text)
        else:
            left, right = ("base", base_text), ("test", test_text)
        p = prompt(left[1], right[1])
        r = llm.chat([{"role": "user", "content": p}], model="deepseek-chat",
                     max_tokens=1200, temperature=0, tries=4)
        text = r.get("text") or ""
        m = re.search(r"\{.*\}", text, re.DOTALL)
        js = json.loads(m.group(0)) if m else {}
        pick = str(js.get("human") or "")
        test_side = "摘要1" if left[0] == "test" else "摘要2"
        win = pick == test_side
        wins += int(win)
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"{tag}_{i}.json").write_text(json.dumps(
            {"task": tag, "instance": i, "left": left[0], "pick": pick, "test_wins": win,
             "prompt_sha256": hashlib.sha256(p.encode()).hexdigest(),
             "answer": text, "json": js}, ensure_ascii=False, indent=1),
            encoding="utf-8", newline="\n")
        print(f"  {tag} inst{i} left={left[0]} pick={pick} test_win={win}", flush=True)
    return wins


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="m1cov-")) / "_data" / "oa"
    shutil.copytree(SRC, root)
    add_era(root)
    a = E.App(root=str(root / "nodes"))
    rows = collect(a)
    total = len(a.nodes)
    print(f"方向 {len(rows)}｜总项 {total}", flush=True)

    kB, covB, nB = pick_by_cover(rows, 0.95, total)
    kC, covC, nC = pick_by_cover(rows, 0.90, total)
    rng = random.Random(20261101)
    kR = set(rng.sample([r["did"] for r in rows], min(len(kB), len(rows))))
    kR = ancestors(rows, kR)

    A = render(rows, {r["did"] for r in rows}, "结构摘要")
    B = render(rows, kB, "结构摘要")
    C = render(rows, kC, "结构摘要")
    R = render(rows, kR, "结构摘要")
    print(f"B(95%): keep {len(kB)} 方向 / 覆盖 {covB:.3f} / {len(B)} 字", flush=True)
    print(f"C(90%): keep {len(kC)} 方向 / 覆盖 {covC:.3f} / {len(C)} 字", flush=True)
    print(f"B'(随机): keep {len(kR)} 方向 / {len(R)} 字｜A {len(A)} 字", flush=True)
    depthB = max([r["depth"] for r in rows if r["did"] in kB], default=0)
    print(f"B 最大缩进深度 {min(depthB, 8)}（全量 {min(max(r['depth'] for r in rows), 8)}）", flush=True)

    # V7.2 等字数阴性对照：随机 5 个方向、不做祖先闭包
    kR2 = set(rng.sample([r["did"] for r in rows if r["parent"] is not None], len(kB)))
    R2 = render(rows, kR2, "结构摘要")
    print(f"B'': keep {len(kR2)} 方向 / {len(R2)} 字（B {len(B)} 字）", flush=True)

    d4 = run_group("D4", B, A)
    d5 = run_group("D5", C, A)
    d6 = run_group("D6", B, R)
    d7 = run_group("D7", B, R2)
    print(json.dumps({"D4_B_vs_A": d4, "D5_C_vs_A": d5, "D6_B_vs_Bprime": d6,
                      "D7_B_vs_Bdoubleprime": d7, "chars_R2": len(R2),
                      "keep": {"B": len(kB), "C": len(kC), "R": len(kR)},
                      "cover": {"B": round(covB, 4), "C": round(covC, 4)},
                      "chars": {"A": len(A), "B": len(B), "C": len(C), "R": len(R)}},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
