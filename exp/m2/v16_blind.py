#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V16 盲判（PREREG_V16 §2）：折叠版 vs 原版，agent 能否准确复述筛选过程。

V15 契约：依据逐字、结论可归纳、无证据必弃答。
判据：F1 字段集合 == 真实相干字段集合（机械基准）；F3 折叠版步数 == 折叠步数。
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "exp"))
import entry as E                                    # noqa: E402
from ldv.core import chain as CH                     # noqa: E402
from ldv.core import declare as DECL                 # noqa: E402

sys.path.insert(0, str(REPO / "exp" / "m2"))
from relca_probe import parse_card, build_corpus, REQS   # noqa: E402
from fold_present import fold, render, field_of         # noqa: E402

M2 = Path(__file__).resolve().parents[1]
OUT = M2 / "v16_blind"
N = 3

RULES = """规则（违反任何一条，本次回答作废）：

1. 你的每一条依据都必须**逐字**引自下面的过程记录，并注明步骤号。
2. 结论可以归纳，但只能使用记录里出现过的字段；不许引入记录外的领域常识。
3. 如果记录不足以回答，你必须让 "fields" 为空数组且 "steps" 为 null（即弃答）。
   注意：弃答是正确回答，不算失败、不扣分；编一个答案是错误的。"""


def prompt(require: list[str], body: str) -> str:
    return f"""下面是一个材料库为某批结果走过的检索过程记录。这个库按字段对材料做二叉切分。

本次的需求（用户要的约束）：{ "、".join(require) }

{RULES}

【检索过程记录】
{body}

问题：
1. 这个过程实际用了**哪几个字段**做筛选？（只算与需求相关的字段）
2. 从需求到结果，**分几步**？

只输出 JSON：
{{"fields": ["字段1", ...], "steps": 数字, "evidence": ["逐字引文（含步骤号）"], "reason": "一句话"}}"""


def parse(text: str) -> dict:
    t = text or ""
    mf = re.search(r'"fields"\s*:\s*\[(.*?)\]', t, re.DOTALL)
    fields = re.findall(r'"([^"]+)"', mf.group(1)) if mf else []
    ms = re.search(r'"steps"\s*:\s*(\d+|null)', t)
    steps = int(ms.group(1)) if ms and ms.group(1).isdigit() else None
    unable = ("unable" in t.lower()) or (not fields and steps is None)
    return {"fields": fields, "steps": steps, "unable": unable}


def main() -> int:
    rows = parse_card()
    root = Path(tempfile.mkdtemp(prefix="m2v16-")) / "_data" / "self120"
    ok, _ = build_corpus(rows, root)
    app = E.App(root=str(root / "nodes"))
    k = app.kernels["keyset"]
    declared: set[str] = set()
    OUT.mkdir(parents=True, exist_ok=True)

    agg = {"F1_fold": 0, "F2_orig": 0, "F3_fold_steps": 0, "n": 0}
    detail = []
    for name, req in REQS:
        cid = name.split()[0]
        miss = [x for x in req if x not in app.all_keys and x not in declared]
        for key in miss:
            DECL.add(root, key, why="V16 盲判", by="agent")
            declared.add(key)
        if miss:
            app = E.App(root=str(root / "nodes"))
            k = app.kernels["keyset"]
        r = app.query({"require": req})
        if not r.get("ok") or r.get("out_of_structure"):
            print(f"{name}: 跳过", flush=True)
            continue
        fields = {str(x).split("=")[0] for x in req}
        node = r["nodes"][0]
        c = CH.chain_of(k, node["did"], require=req)
        folded = fold(c["路"], fields)
        truth_fields = {x["字段"] for x in folded if x.get("相干")}
        body_orig = "\n".join(
            f"{i}. {'[根]' if i == 0 else (st.get('切') or '子类细分')}"
            f"｜{st['成员数']} 项｜{'相干' if st.get('相干') else '过路'}"
            for i, st in enumerate(c["路"]))
        body_fold = render(folded)
        true_steps = len(folded)

        for inst in range(N):
            for arm, body, expect_steps in (("orig", body_orig, None),
                                            ("fold", body_fold, true_steps)):
                p = prompt(req, body)
                rr = llm_chat(p)
                j = parse(rr)
                got = set(j["fields"])
                hit = (got == truth_fields)
                st_ok = (j["steps"] == expect_steps) if (arm == "fold" and
                                                         j["steps"] is not None) else None
                rec = {"需求": name, "arm": arm, "inst": inst,
                       "truth_fields": sorted(truth_fields), "got_fields": j["fields"],
                       "fields_hit": hit, "steps": j["steps"],
                       "expect_steps": expect_steps, "steps_ok": st_ok,
                       "unable": j["unable"], "answer": rr}
                detail.append(rec)
                if arm == "fold":
                    agg["F1_fold"] += hit
                    if st_ok:
                        agg["F3_fold_steps"] += 1
                else:
                    agg["F2_orig"] += hit
                agg["n"] += 1
                print(f"  {name[:12]:12s} {arm:4s} i{inst} fields={sorted(got)} "
                      f"steps={j['steps']} hit={hit}", flush=True)

    summary = {"F1_fold_fields": agg["F1_fold"], "F2_orig_fields": agg["F2_orig"],
               "F3_fold_steps": agg["F3_fold_steps"],
               "fold_calls": sum(1 for x in detail if x["arm"] == "fold"),
               "orig_calls": sum(1 for x in detail if x["arm"] == "orig")}
    (OUT / "result.json").write_text(json.dumps(
        {**summary, "detail": detail}, ensure_ascii=False, indent=1),
        encoding="utf-8", newline="\n")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def llm_chat(p: str) -> str:
    import llm
    r = llm.chat([{"role": "user", "content": p}], model="deepseek-chat",
                 max_tokens=900, temperature=0, tries=4)
    return r.get("text") or ""


if __name__ == "__main__":
    raise SystemExit(main())
