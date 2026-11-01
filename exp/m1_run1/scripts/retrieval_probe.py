#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V10（PREREG）：受限检索能否成为"视角素材"。

给 agent 一次真实检索的**形状**（排序 + 倾向值），问"使用者最近更重视什么"。
真件 vs 伪件（倾向值与方向打乱配对）。
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
OUT = M1 / "v_retrieval"
SRC = REPO / "_data" / "openalex-citations"
NOW = datetime(2026, 11, 1, 12, 0, 0)


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


def age_records(root: Path, did: str, days: int) -> int:
    n = 0
    for p in sorted((root / US.DIRNAME).glob("*.md")):
        t = p.read_text(encoding="utf-8")
        if f"did: {did}\n" not in t:
            continue
        lines = []
        for line in t.splitlines():
            if line.startswith("at: "):
                line = f"at: {(NOW - timedelta(days=days)).isoformat()}"
                n += 1
            lines.append(line)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return n


def render(rows: list[dict], title: str) -> str:
    out = [title]
    for i, r in enumerate(rows, 1):
        out.append(f"{i}. {r['label']} ｜ {r['members']} 项 ｜ 倾向 {r['tend']:.4f}")
    return "\n".join(out)


def prompt(body: str) -> str:
    return f"""下面是一个材料库**最近一次检索**的结果。倾向值是「该方向被下钻累积的证据强度」，
会随记录变老而衰减。

{body}

问题：这个库的**使用者最近更重视**哪一类方向？你怎么看出来的？

先回答（两三句），然后另起一行输出 JSON：
{{"focus": "一句话说明更重视哪一类", "evidence": ["你依据的具体读数1", "读数2"]}}"""


def judge(text: str) -> dict:
    t = text or ""
    has_review = "review" in t
    m = re.search(r'"focus"\s*:\s*"([^"]*)"', t)
    focus = m.group(1) if m else ""
    points_review = ("review" in focus) or (has_review and "article" not in focus)
    cites = any(k in t for k in ("倾向", "排序", "权重", "靠前", "第一"))
    points_article = ("article" in focus) and ("review" not in focus)
    return {"mentions_review": has_review, "focus": focus[:80],
            "points_review": bool(points_review),
            "points_article": bool(points_article), "cites_order": cites}


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="m1v10-")) / "_data" / "oa"
    shutil.copytree(SRC, root)
    add_era(root)
    a = E.App(root=str(root / "nodes"), usage_now=NOW)

    r = a.query({"require": []})
    top = [n["did"] for n in r["nodes"]]
    # A = 含 type=article 的顶层方向；B = 含 type=review 的顶层方向
    def first_with(key: str):
        for did in top:
            if key in label_of(a, did):
                return did
        return None

    A, B = first_with("type=article"), first_with("type=review")
    print(f"A={A} {label_of(a, A)[:60]}", flush=True)
    print(f"B={B} {label_of(a, B)[:60]}", flush=True)
    if not A or not B or A == B:
        print("!! 找不到 A/B，实验作废")
        return 2

    # drill A 后改老，drill B 保持新
    a.drill(A, [])
    a.drill(B, [])
    n_aged = age_records(root, A, 300)
    a2 = E.App(root=str(root / "nodes"), usage_now=NOW)
    r2 = a2.query({"require": []})
    ordered = [n["did"] for n in r2["nodes"]]
    t = a2._tendency
    print(f"aged {n_aged} 条｜tendency: A={t.get(A):.6f} B={t.get(B):.6f}", flush=True)
    print(f"ordered_by={r2.get('ordered_by')}｜A pos={ordered.index(A)} B pos={ordered.index(B)}",
          flush=True)

    k = a2.kernels["keyset"]
    rows = []
    for did in ordered[:10]:
        d = k.direction(did)
        rows.append({"did": did, "label": label_of(a2, did),
                     "members": len(k.members_of(d)), "tend": float(t.get(did, 0.0))})
    real_body = render(rows, "检索：require=[]（全部方向）｜按使用历史排序（ordered_by=usage）")

    # 伪件（V10.1 定向反证）：把唯一的大倾向值强制给 A（article 方向），其余随机
    rng = random.Random(20261101)
    tends = [x["tend"] for x in rows]
    big = max(tends)
    rest = [v for v in tends if v != big]
    rng.shuffle(rest)
    fake = []
    rest_iter = iter(rest)
    for x in rows:
        tv = big if x["did"] == A else (next(rest_iter, 0.0))
        fake.append({**x, "tend": tv})
    fake_sorted = sorted(fake, key=lambda x: -x["tend"])
    fake_body = render(fake_sorted, "检索：require=[]（全部方向）｜按使用历史排序（ordered_by=usage）")

    print("\n=== 真件 ===", flush=True)
    print(real_body, flush=True)
    print("\n=== 伪件 ===", flush=True)
    print(fake_body, flush=True)

    res = {"real": [], "fake": []}
    for arm, body in (("real", real_body), ("fake", fake_body)):
        for i in range(5):
            p = prompt(body)
            rr = llm.chat([{"role": "user", "content": p}], model="deepseek-chat",
                          max_tokens=900, temperature=0, tries=4)
            text = rr.get("text") or ""
            j = judge(text)
            res[arm].append({"inst": i, **j, "answer": text,
                             "prompt_sha256": hashlib.sha256(p.encode()).hexdigest()})
            print(f"  {arm} inst{i} points_review={j['points_review']} cites={j['cites_order']} "
                  f"focus={j['focus'][:40]}", flush=True)

    real_ok = sum(1 for x in res["real"] if x["points_review"])
    fake_follows = sum(1 for x in res["fake"] if x["points_article"])
    fake_ok = sum(1 for x in res["fake"] if x["points_review"])
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "result.json").write_text(json.dumps(
        {"A": A, "B": B, "tend": {A: t.get(A), B: t.get(B)},
         "ordered_by": r2.get("ordered_by"),
         "pos": {A: ordered.index(A), B: ordered.index(B)},
         "real_body": real_body, "fake_body": fake_body,
         "W1_real_points_review": real_ok, "W2_fake_points_review": fake_ok,
         "W3_fake_follows_weight": fake_follows,
         "detail": res}, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print(json.dumps({"W1_real_points_review": real_ok,
                      "W2_fake_points_review": fake_ok,
                      "W3_fake_follows_weight(article)": fake_follows, "of": 5},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
