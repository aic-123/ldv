#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V12（PREREG）：注意力事实能否改变**决策**（先看哪一个）。

同一批候选、同一顺序、同一个问题，唯一变量是"使用记录"字段。
四组：G1 真形状 / G2 反向着色 / G3 无事实 / G4 极小方向。
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "exp"))
import entry as E                                    # noqa: E402
import llm                                            # noqa: E402
from ldv.tools import usage_store as US              # noqa: E402

M1 = Path(__file__).resolve().parents[1]
OUT = M1 / "v_decide"
SRC = REPO / "_data" / "openalex-citations"
NOW = datetime(2026, 11, 1, 12, 0, 0)
N_INST = 5


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


def label_of(app, did: str) -> str:
    d = app.kernels["keyset"].direction(did)
    req, forb = d.payload
    s = " ∧ ".join(sorted(req)) or "（全部）"
    if forb:
        s += " ∧ ¬(" + " ∨ ".join(sorted(forb)) + ")"
    return s


def set_at(root: Path, did: str, when) -> int:
    n = 0
    for p in sorted((root / US.DIRNAME).glob("*.md")):
        t = p.read_text(encoding="utf-8")
        if f"did: {did}\n" not in t:
            continue
        lines = []
        for line in t.splitlines():
            if line.startswith("at: "):
                line = f"at: {when.isoformat()}"
                n += 1
            lines.append(line)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return n


def facts_of(root: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for rec in US.load(root):
        did = rec.get("did")
        if not did:
            continue
        cur = out.setdefault(did, {"n": 0, "last": None})
        cur["n"] += 1
        at = rec.get("at") or ""
        if at and (cur["last"] is None or at > cur["last"]):
            cur["last"] = at
    return out


def render(rows: list[dict], days: dict[str, int | None]) -> str:
    out = ["检索：require=[]（全部方向）｜以下是这个库的 10 个方向"]
    for i, r in enumerate(rows, 1):
        d = days.get(r["did"])
        if d is None:
            tail = "从未使用"
        else:
            tail = f"使用 1 次 ｜ 最近 {d} 天前"
        out.append(f"{i}. {r['label']} ｜ {r['members']} 项 ｜ {tail}")
    return "\n".join(out)


def prompt(body: str) -> str:
    return f"""下面是一个材料库的方向清单。每行一个方向，给出它的谓词、规模（项数）与使用记录。
这个库的机制有两条：只有被**下钻**过的方向才会留下使用记录（被摆出来看过不算）；
每条记录会随年龄**衰减**，半衰期 30 天。

{body}

用户说：「我没时间全看，帮我挑**一个**最值得先深入的方向。」

你会先给哪一个？只选一个。

先回答两三句，然后另起一行输出 JSON：
{{"pick_index": <清单里的序号>, "pick_type": "review|article|other", "reason": "一句话理由"}}"""


def parse(text: str) -> dict:
    t = text or ""
    mi = re.search(r'"pick_index"\s*:\s*(\d+)', t)
    mt = re.search(r'"pick_type"\s*:\s*"([^"]*)"', t)
    return {"pick_index": int(mi.group(1)) if mi else None,
            "pick_type": mt.group(1) if mt else "",
            "mentions_days": bool(re.search(r"\d+\s*天", t)),
            "mentions_decay": ("衰减" in t or "半衰期" in t)}


def main() -> int:
    root0 = Path(tempfile.mkdtemp(prefix="m1v12-")) / "_data" / "oa"
    shutil.copytree(SRC, root0)
    add_era(root0)
    root = root0 / "nodes"

    a = E.App(root=str(root), usage_now=NOW)
    r = a.query({"require": []})
    top = [n["did"] for n in r["nodes"]]
    k = a.kernels["keyset"]

    def first_with(key: str):
        for did in top:
            if key in label_of(a, did):
                return did
        return None

    B = first_with("type=review")
    A = first_with("type=article")
    if not A or not B or A == B:
        print("!! 找不到 A/B")
        return 2
    # 其余 4 支（避开 A/B）：保证清单里有"2 项"的极小方向
    rest = [d for d in top if d not in (A, B)]
    tiny = None
    for did in rest:
        if len(k.members_of(k.direction(did))) == 2:
            tiny = did
            break
    if tiny is None:
        print("!! 找不到 2 项方向")
        return 2
    others = [d for d in rest if d != tiny][:3]
    drilled = [B, A, tiny] + others

    for did in drilled:
        a.drill(did, [])
    for did in [A, B, tiny] + others:
        set_at(root0, did, NOW - timedelta(days=300))

    # 清单：固定 10 行、固定顺序（与使用无关），四组共用
    rows = []
    for did in top[:10]:
        d = k.direction(did)
        rows.append({"did": did, "label": label_of(a, did),
                     "members": len(k.members_of(d))})
    rng = random.Random(20261101)
    order = list(range(len(rows)))
    rng.shuffle(order)
    rows = [rows[i] for i in order]

    idx = {r["did"]: i + 1 for i, r in enumerate(rows)}
    print(f"A(article)={A} 第{idx.get(A)}行｜B(review)={B} 第{idx.get(B)}行｜"
          f"tiny={tiny}({rows[idx[tiny]-1]['members']}项) 第{idx.get(tiny)}行", flush=True)
    need = [A, B, tiny]
    if any(d not in idx for d in need):
        print("!! A/B/tiny 不在前 10 行")
        return 2

    ALL_NOT_USED = {r["did"]: None for r in rows}

    def days_for(star: str | None) -> dict:
        if star is None:
            return dict(ALL_NOT_USED)
        return {r["did"]: (0 if r["did"] == star else 300) for r in rows}

    bodies = {
        "G1_real": render(rows, days_for(B)),
        "G2_flip": render(rows, days_for(A)),
        "G3_none": render(rows, days_for(None)),
        "G4_tiny": render(rows, days_for(tiny)),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    for name, body in bodies.items():
        (OUT / f"{name}.txt").write_text(body + "\n", encoding="utf-8", newline="\n")
        print(f"\n=== {name} ===\n{body}", flush=True)

    def which(did: str | None) -> str:
        if did == B:
            return "review"
        if did == A:
            return "article"
        if did == tiny:
            return "tiny"
        return "other"

    res: dict[str, list] = {}
    for arm, body in bodies.items():
        res[arm] = []
        for i in range(N_INST):
            p = prompt(body)
            rr = llm.chat([{"role": "user", "content": p}], model="deepseek-chat",
                          max_tokens=900, temperature=0, tries=4)
            text = rr.get("text") or ""
            j = parse(text)
            pi = j["pick_index"]
            did = rows[pi - 1]["did"] if pi and 1 <= pi <= len(rows) else None
            res[arm].append({"inst": i, **j, "did": did,
                             "chosen": which(did), "answer": text,
                             "prompt_sha256": hashlib.sha256(p.encode()).hexdigest()})
            print(f"  {arm} inst{i} idx={pi} chosen={which(did)} "
                  f"type={j['pick_type']} reason={text[:60]!r}", flush=True)

    def cnt(arm: str, label: str) -> int:
        return sum(1 for x in res[arm] if x["chosen"] == label)

    summary = {
        "Q1_G1_review": cnt("G1_real", "review"),
        "Q2_G2_article": cnt("G2_flip", "article"),
        "Q3_G3_review(应<=2)": cnt("G3_none", "review"),
        "Q4_G4_tiny(应>=3)": cnt("G4_tiny", "tiny"),
        "G3_article(默认偏好)": cnt("G3_none", "article"),
        "of": N_INST,
        "pos": {"review": idx[B], "article": idx[A], "tiny": idx[tiny]},
        "members": {"review": rows[idx[B] - 1]["members"],
                    "article": rows[idx[A] - 1]["members"],
                    "tiny": rows[idx[tiny] - 1]["members"]},
        "rows": [{"i": i + 1, "did": r["did"], "members": r["members"]}
                 for i, r in enumerate(rows)],
    }
    (OUT / "result.json").write_text(json.dumps(
        {**summary, "bodies": bodies, "detail": res},
        ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
