#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V11（PREREG）：视角能否由 ldv 自己输出——用**纯事实**替代权重数值。

呈现件只有事实（谓词 / 项数 / 使用次数 / 最近使用距今天数），**零分数、零排名**。
四组：F1 真/伪（真实顺序）、F2 真/伪（顺序打乱）。
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
OUT = M1 / "v_facts"
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
    """把某支的全部记录 `at` 钉到指定时刻（V11.2：drill 写的是真实墙钟，须对齐 NOW）。"""
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
    """每支的事实：记录条数、最近使用时间。**不读任何倾向值。**"""
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


def render(rows: list[dict], title: str) -> str:
    out = [title]
    for i, r in enumerate(rows, 1):
        if r["n"] == 0:
            tail = "从未使用"
        else:
            tail = f"使用 {r['n']} 次 ｜ 最近 {r['days']} 天前"
        out.append(f"{i}. {r['label']} ｜ {r['members']} 项 ｜ {tail}")
    return "\n".join(out)


def prompt(body: str) -> str:
    return f"""下面是一个材料库记录的「方向」清单。这个库的机制有两条：

- 只有被**下钻**过的方向才会留下使用记录；被摆出来看过**不算**。
- 每条记录会随年龄**衰减**，半衰期 30 天。

{body}

问题：这个库的**使用者最近更重视**哪一类方向？

先回答（两三句），然后另起一行输出 JSON：
{{"focus": "一句话说明更重视哪一类", "evidence": ["你依据的具体读数1", "读数2"]}}"""


def judge(text: str) -> dict:
    t = text or ""
    m = re.search(r'"focus"\s*:\s*"([^"]*)"', t)
    focus = m.group(1) if m else ""
    points_review = ("review" in focus) and ("article" not in focus)
    points_article = ("article" in focus) and ("review" not in focus)
    return {"focus": focus[:100], "points_review": bool(points_review),
            "points_article": bool(points_article),
            "mentions_days": bool(re.search(r"\d+\s*天", t)),
            "mentions_decay": ("衰减" in t or "半衰期" in t)}


def main() -> int:
    root0 = Path(tempfile.mkdtemp(prefix="m1v11-")) / "_data" / "oa"
    shutil.copytree(SRC, root0)
    add_era(root0)
    root = root0 / "nodes"

    a = E.App(root=str(root), usage_now=NOW)
    r = a.query({"require": []})
    top = [n["did"] for n in r["nodes"]]

    def first_with(key: str):
        for did in top:
            if key in label_of(a, did):
                return did
        return None

    B = first_with("type=review")
    A = first_with("type=article")
    if not A or not B or A == B:
        print("!! 找不到 A/B，实验作废")
        return 2
    # 另取 4 支（避开 A/B，取靠后的，制造"多个用过"的干扰）
    others = [d for d in top if d not in (A, B)][-4:]
    drilled = [B, A] + others

    for did in drilled:
        a.drill(did, [])
    aged = {did: set_at(root0, did, NOW - timedelta(days=300)) for did in [A] + others}
    set_at(root0, B, NOW)                # B 钉为「现在」（V11.2）

    a2 = E.App(root=str(root), usage_now=NOW)
    r2 = a2.query({"require": []})
    ordered = [n["did"] for n in r2["nodes"]]
    k = a2.kernels["keyset"]
    fx = facts_of(root0)

    print(f"A={A} | {label_of(a2, A)[:50]}", flush=True)
    print(f"B={B} | {label_of(a2, B)[:50]}", flush=True)
    print(f"drilled={len(drilled)} aged={aged}，有记录方向数={len(fx)}", flush=True)
    print(f"ordered_by={r2.get('ordered_by')} A pos={ordered.index(A)} B pos={ordered.index(B)}",
          flush=True)
    # 自检：只有 B 是"今天"（不打印倾向，只打印事实）
    for did in drilled:
        f = fx.get(did, {"n": 0, "last": None})
        days = (NOW - datetime.fromisoformat(f["last"])).days if f["last"] else None
        print(f"  fact {did}: n={f['n']} last_days={days}", flush=True)

    # V11.1 自检：恰 1 支 0 天 + 5 支 300 天；否则作废、不进入判读
    dz = sorted((NOW - datetime.fromisoformat(fx[d]["last"])).days
                for d in drilled if fx.get(d, {}).get("last"))
    if dz != [0, 300, 300, 300, 300, 300]:
        print(f"!! 自检失败 days={dz}，本轮作废", flush=True)
        return 3
    print(f"  自检通过：days={dz}", flush=True)

    rows = []
    for did in ordered[:10]:
        d = k.direction(did)
        f = fx.get(did, {"n": 0, "last": None})
        days = ((NOW - datetime.fromisoformat(f["last"])).days
                if f["last"] else None)
        rows.append({"did": did, "label": label_of(a2, did),
                     "members": len(k.members_of(d)), "n": f["n"], "days": days})

    title_sorted = "检索：require=[]（全部方向）｜按使用历史排序"
    title_shuffled = "检索：require=[]（全部方向）｜以下顺序无意义"

    # 伪件：把"最近使用"在 A 与 B 之间对调（其余不动）
    fake_rows = []
    for x in rows:
        y = dict(x)
        if x["did"] == A:
            y["n"], y["days"] = 1, 0
        elif x["did"] == B:
            y["n"], y["days"] = 1, 300
        fake_rows.append(y)

    rng = random.Random(20261101)
    shuf_idx = list(range(len(rows)))
    rng.shuffle(shuf_idx)
    shuf_rows = [rows[i] for i in shuf_idx]
    shuf_fake = [fake_rows[i] for i in shuf_idx]

    bodies = {
        "F1_real": render(rows, title_sorted),
        "F1_fake": render(fake_rows, title_sorted),
        "F2_real": render(shuf_rows, title_shuffled),
        "F2_fake": render(shuf_fake, title_shuffled),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    for name, body in bodies.items():
        (OUT / f"{name}.txt").write_text(body + "\n", encoding="utf-8", newline="\n")
        print(f"\n=== {name} ===\n{body}", flush=True)

    res: dict[str, list] = {}
    for arm, body in bodies.items():
        res[arm] = []
        for i in range(N_INST):
            p = prompt(body)
            rr = llm.chat([{"role": "user", "content": p}], model="deepseek-chat",
                          max_tokens=900, temperature=0, tries=4)
            text = rr.get("text") or ""
            j = judge(text)
            res[arm].append({"inst": i, **j, "answer": text,
                             "prompt_sha256": hashlib.sha256(p.encode()).hexdigest()})
            print(f"  {arm} inst{i} review={j['points_review']} article={j['points_article']} "
                  f"focus={j['focus'][:50]}", flush=True)

    def cnt(arm: str, key: str) -> int:
        return sum(1 for x in res[arm] if x[key])

    summary = {
        "P1_F1_real_review": cnt("F1_real", "points_review"),
        "P2_F1_fake_article": cnt("F1_fake", "points_article"),
        "P3_F2_real_review": cnt("F2_real", "points_review"),
        "P4_F2_fake_article": cnt("F2_fake", "points_article"),
        "of": N_INST,
        "A": A, "B": B,
        "n_drilled": len(drilled),
        "ordered_by": r2.get("ordered_by"),
    }
    (OUT / "result.json").write_text(json.dumps(
        {**summary, "bodies": bodies, "detail": res},
        ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
