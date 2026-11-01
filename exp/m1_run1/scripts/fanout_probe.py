#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扇出重排实验（PREREG V6）：同一棵树、信息逐项相同，只改排版。

chain：逐行缩进链（现状渲染）
fanout：路径串（`A › B › C ｜ N 项`，不缩进）
先做信息等价性检查（不等价则实验作废），再 5 实例二选一。
"""
from __future__ import annotations

import hashlib
import json
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
OUT = M1 / "v_fanout"
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

    def walk(d, depth, path):
        if d.did in seen:
            return
        seen.add(d.did)
        req, forb = d.payload
        seg = " ∧ ".join(sorted(req)) or "（全部）"
        if forb:
            seg += " ∧ ¬(" + " ∨ ".join(sorted(forb)) + ")"
        rows.append({"did": d.did, "depth": depth, "members": len(k.members_of(d)),
                     "seg": seg, "path": path + [seg]})
        for c in k.children_of(d):
            walk(c, depth + 1, path + [seg])

    for d in k.all_directions():
        if d.rank == 1:
            walk(d, 0, [])
    return rows


def render_chain(rows: list[dict], title: str) -> str:
    out = [f"# {title}", ""]
    for r in rows:
        out.append(f"{'  ' * min(r['depth'], 8)}- {r['seg']} ｜ {r['members']} 项")
    return "\n".join(out) + "\n"


def render_fanout(rows: list[dict], title: str) -> str:
    out = [f"# {title}", ""]
    for r in rows:
        out.append(f"- {' › '.join(r['path'])} ｜ {r['members']} 项")
    return "\n".join(out) + "\n"


def info_set(text: str) -> tuple:
    """解析出 (方向数, 成员数多重集, 谓词片段集合长度) —— 用于信息等价性检查。"""
    pairs = re.findall(r"^(?:-|\s+-)\s*(.+?)\s*｜\s*(\d+)\s*项$", text, re.M)
    return (len(pairs), tuple(sorted(int(p[1]) for p in pairs)))


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


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="m1fo-")) / "_data" / "oa"
    shutil.copytree(SRC, root)
    add_era(root)
    a = E.App(root=str(root / "nodes"))
    rows = collect(a)
    chain = render_chain(rows, "结构摘要")
    fan = render_fanout(rows, "结构摘要")

    ic, ifn = info_set(chain), info_set(fan)
    print(f"方向 {len(rows)}｜chain {len(chain)} 字｜fanout {len(fan)} 字", flush=True)
    print(f"信息等价性：chain={ic[0]} 行/成员集一致={ic[1] == ifn[1]}｜"
          f"fanout={ifn[0]} 行/成员集一致={ic[1] == ifn[1]}", flush=True)
    if ic[0] != ifn[0] or ic[1] != ifn[1]:
        print("!! 信息不一致 ⇒ 按 PREREG V6 本实验作废", flush=True)
        return 2

    wins = 0
    for i in range(5):
        if i % 2 == 0:
            left, right = ("fanout", fan), ("chain", chain)
        else:
            left, right = ("chain", chain), ("fanout", fan)
        p = prompt(left[1], right[1])
        r = llm.chat([{"role": "user", "content": p}], model="deepseek-chat",
                     max_tokens=1200, temperature=0, tries=4)
        text = r.get("text") or ""
        m = re.search(r"\{.*\}", text, re.DOTALL)
        js = json.loads(m.group(0)) if m else {}
        pick = str(js.get("human") or "")
        fan_side = "摘要1" if left[0] == "fanout" else "摘要2"
        win = pick == fan_side
        wins += int(win)
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"D3_{i}.json").write_text(json.dumps(
            {"task": "D3", "instance": i, "left": left[0], "pick": pick,
             "fanout_wins": win, "prompt_sha256": hashlib.sha256(p.encode()).hexdigest(),
             "answer": text, "json": js}, ensure_ascii=False, indent=1),
            encoding="utf-8", newline="\n")
        print(f"  D3 inst{i} left={left[0]} pick={pick} fanout_win={win}", flush=True)

    print(json.dumps({"D3_fanout_wins": wins, "of": 5, "chars": [len(chain), len(fan)],
                      "info_equal": ic[0] == ifn[0] and ic[1] == ifn[1]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
