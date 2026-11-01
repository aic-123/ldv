#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V11.3 离线重判：用已存原话重新判分（不重新调用模型）。

修正点：识别 `article` 的否定形式（「非 article」「不是 article」「排除 article」
「¬article」），避免把「review（非 article）」这种**正确回答**判成指向 article。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

M1 = Path(__file__).resolve().parents[1]
P = M1 / "v_facts" / "result.json"

NEG = re.compile(r"(?:非|不是|排除|不含|没有|¬|而非|并非)\s*(?:type\s*=\s*)?article",
                 re.IGNORECASE)
OUTSIDE = re.compile(r"article[^，。；]{0,6}(?:之外|以外)", re.IGNORECASE)


def judge(text: str) -> dict:
    m = re.search(r'"focus"\s*:\s*"([^"]*)"', text or "")
    focus = m.group(1) if m else ""
    has_r = "review" in focus
    has_a = "article" in focus
    a_neg = bool(NEG.search(focus) or OUTSIDE.search(focus))
    # article 只以否定形式出现 ⇒ 不算指向 article
    a_pos = has_a and not a_neg
    return {"focus": focus[:100],
            "points_review": bool(has_r and not a_pos),
            "points_article": bool(a_pos and not has_r),
            "a_negated": a_neg}


def main() -> int:
    d = json.loads(P.read_text(encoding="utf-8"))
    out = {}
    for arm, rows in d["detail"].items():
        new = []
        for x in rows:
            j = judge(x["answer"])
            new.append({**x, **j})
        out[arm] = new
        n_r = sum(1 for x in new if x["points_review"])
        n_a = sum(1 for x in new if x["points_article"])
        others = [x for x in new if not x["points_review"] and not x["points_article"]]
        print(f"{arm}: review={n_r} article={n_a} 其他={len(others)} / {len(new)}")
        for x in others:
            print(f"   其他 focus={x['focus'][:60]}")

    summary = {
        "P1_F1_real_review": sum(1 for x in out["F1_real"] if x["points_review"]),
        "P2_F1_fake_article": sum(1 for x in out["F1_fake"] if x["points_article"]),
        "P3_F2_real_review": sum(1 for x in out["F2_real"] if x["points_review"]),
        "P4_F2_fake_article": sum(1 for x in out["F2_fake"] if x["points_article"]),
        "of": len(out["F1_real"]),
        "note": "V11.3 离线重判（同一批原话，未重新调用模型）",
    }
    (M1 / "v_facts" / "result_rejudge.json").write_text(
        json.dumps({**summary, "detail": out}, ensure_ascii=False, indent=1),
        encoding="utf-8", newline="\n")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
