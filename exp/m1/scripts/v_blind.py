#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阶段二 V1/V2/V3 + S6 盲判（deepseek-chat，每任务 5 个独立实例）。

规则（PREREG §7）：不告知预期、不出现暗示词、给"看不出来"出口、
真件与伪件混排、随机呈现顺序（seed 固定可复现）、原话必存。

输出：exp/m1/v/<task>_<arm>_<i>.json
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "exp"))
import llm                                              # noqa: E402

M1 = Path(__file__).resolve().parents[1]
V = M1 / "v"
MODEL = "deepseek-chat"
N = 5


def read(name: str) -> str:
    return (M1 / name).read_text(encoding="utf-8")


def ask(prompt: str, seed: int) -> tuple[str, str]:
    r = llm.chat([{"role": "user", "content": prompt}], model=MODEL,
                 max_tokens=1200, temperature=0, tries=4)
    return r.get("text") or "", hashlib.sha256(prompt.encode()).hexdigest()


def parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:                                    # noqa: BLE001
        return {}


def save(rec: dict) -> None:
    V.mkdir(parents=True, exist_ok=True)
    role = f"_{rec['role']}" if rec.get("role") else ""
    p = V / f"{rec['task']}{role}_{rec['arm']}_{rec['instance']}.json"
    p.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")


def prompt_V1(d1: str, d2: str) -> str:
    return f"""下面有两份结构摘要，每份是一组层级式的分类结果（来自同一批材料）。

请判断：哪一份是按「发表时间」组织的，哪一份是按「文献类型（article/review 等）」组织的。

如果看不出来，直接说看不出来。

【摘要 1】
{d1}

【摘要 2】
{d2}

先用两句话说明依据，然后另起一行输出 JSON：
{{"time": "摘要1 或 摘要2", "type": "摘要1 或 摘要2", "basis": "一句话依据"}}"""


def run_V1() -> None:
    a, b = read("V1_tree_era.md"), read("V1_tree_type.md")
    for i in range(N):
        order = [("era", a), ("type", b)]
        random.Random(100 + i).shuffle(order)
        p = prompt_V1(order[0][1], order[1][1])
        text, sha = ask(p, i)
        js = parse_json(text)
        truth = {1: order[0][0], 2: order[1][0]}
        pick_time = js.get("time", "")
        correct = (pick_time == "摘要1" and truth[1] == "era") or \
                  (pick_time == "摘要2" and truth[2] == "era")
        save({"task": "V1", "instance": i, "arm": "real", "order": [o[0] for o in order],
              "prompt_sha256": sha, "answer": text, "json": js, "correct": correct})


def run_V1B() -> None:
    a, b = read("V1_tree_era.md"), read("V1B_tree_era_small.md")
    for i in range(N):
        order = [("era_full", a), ("era_small", b)]
        random.Random(200 + i).shuffle(order)
        p = prompt_V1(order[0][1], order[1][1])
        text, sha = ask(p, i)
        js = parse_json(text)
        save({"task": "V1B", "instance": i, "arm": "null",
              "order": [o[0] for o in order], "prompt_sha256": sha,
              "answer": text, "json": js,
              "note": "两份同意图（都是 era）⇒ 正确率应≈50%"})
        _ = js


def prompt_V2(d1: str, d2: str, d3: str) -> str:
    return f"""下面有三份结构摘要（同一批材料的不同版本或不同整理结果）。

请判断：
1. 哪两份是同一材料的前后两个版本（A 与 B）；
2. 这两份里，哪一份是后来的版本；
3. 后来的版本相比前一份，增量落在哪几个方向上（举 1-3 个方向即可）。

如果看不出来，直接说看不出来。

【摘要 一】
{d1}

【摘要 二】
{d2}

【摘要 三】
{d3}

先用两句话说明依据，然后另起一行输出 JSON：
{{"pair": ["一", "二"], "later": "一 或 二", "incremental": ["方向1", "方向2"]}}"""


def run_V2() -> None:
    only, ab, dis = read("V2_A_only.md"), read("V2_A_B.md"), read("V2_A_B_distractor.md")
    for i in range(N):
        items = [("only", only), ("ab", ab), ("distractor", dis)]
        random.Random(300 + i).shuffle(items)
        label = {items[0][0]: "一", items[1][0]: "二", items[2][0]: "三"}
        p = prompt_V2(items[0][1], items[1][1], items[2][1])
        text, sha = ask(p, i)
        js = parse_json(text)
        pair = [str(x) for x in (js.get("pair") or [])]
        later = str(js.get("later") or "")
        want = {label["only"], label["ab"]}
        ok_pair = set(pair) == want
        ok_later = later == label["ab"]
        ok_distractor = later != label["distractor"]
        inc = js.get("incremental") or []
        save({"task": "V2", "instance": i, "arm": "real",
              "order": [o[0] for o in items], "labels": label,
              "prompt_sha256": sha, "answer": text, "json": js,
              "pair_correct": ok_pair, "later_correct": ok_later,
              "distractor_not_later": ok_distractor,
              "n_incremental": len(inc)})


def prompt_V3(d1: str, d2: str, d3: str) -> str:
    return f"""下面有三份结构摘要，来自同一批材料的三种整理方式。

请判断：
1. 哪一份最像人（有判断、有取舍地）组织出来的；
2. 哪一份最像机械（按固定规则批量）生成的；
3. 给出你的理由。

如果看不出来，直接说看不出来。

【摘要 一】
{d1}

【摘要 二】
{d2}

【摘要 三】
{d3}

先说明理由（两到三句），然后另起一行输出 JSON：
{{"human": "一 或 二 或 三", "machine": "一 或 二 或 三", "reason": "你的理由"}}"""


def run_V3() -> None:
    L, G, T = read("V3_L.md"), read("V3_G.md"), read("V3_T.md")
    for i in range(N):
        items = [("L", L), ("G", G), ("T", T)]
        random.Random(400 + i).shuffle(items)
        label = {items[0][0]: "一", items[1][0]: "二", items[2][0]: "三"}
        p = prompt_V3(items[0][1], items[1][1], items[2][1])
        text, sha = ask(p, i)
        js = parse_json(text)
        human = str(js.get("human") or "")
        machine = str(js.get("machine") or "")
        reason = str(js.get("reason") or text)
        struct_terms = ["跨字段", "组合", "中间层", "分支", "层级", "不平衡", "谓词", "合取", "深度"]
        save({"task": "V3", "instance": i, "arm": "real",
              "order": [o[0] for o in items], "labels": label,
              "prompt_sha256": sha, "answer": text, "json": js,
              "human_is_L": human == label["L"],
              "machine_is_T": machine == label["T"],
              "L_grouped_with_G": human == label["G"] or machine == label["L"],
              "reason_struct_terms": [t for t in struct_terms if t in reason]})


def material_sample(n: int = 20) -> str:
    files = sorted((REPO / "_data" / "openalex-citations" / "nodes").glob("*.md"))[:n]
    out = []
    for p in files:
        t = p.read_text(encoding="utf-8", errors="replace")
        title = re.search(r'title: "(.*?)"', t)
        scope = re.search(r'scope: "(.*?)"', t)
        out.append(f"- {title.group(1) if title else p.stem}｜{scope.group(1) if scope else ''}")
    return "\n".join(out)


def prompt_S6(summary: str, material: str | None) -> str:
    head = ("下面是一份结构摘要（一组层级式的分类结果）。"
            + ("\n\n作为背景，这是被整理的部分材料条目：\n" + material if material else ""))
    return f"""{head}

【结构摘要】
{summary}

问题：只看这份结构，你认为建它的人觉得什么最重要？

如果你认为看不出任何倾向，直接说看不出倾向。

先用两三句话回答，然后另起一行输出 JSON：
{{"most_important": "一句话", "evidence": ["结构里的具体迹象1", "迹象2"], "verdict": "有倾向 或 看不出倾向"}}"""


def run_S6() -> None:
    real, null = read("summary_1page.md"), read("summary_null.md")
    mat = material_sample()
    for role, mat_text in (("R2", mat), ("R3", None)):
        for i in range(N * 2):                       # 每角色 10 实例（真伪各 5，交错）
            arm = "real" if i % 2 == 0 else "null"
            summary = real if arm == "real" else null
            p = prompt_S6(summary, mat_text)
            text, sha = ask(p, i)
            js = parse_json(text)
            verdict = str(js.get("verdict") or "")
            save({"task": "S6", "role": role, "arm": arm, "instance": i,
                  "prompt_sha256": sha, "answer": text, "json": js,
                  "no_tendency": ("看不出" in verdict) or ("看不出" in text[:200]),
                  "has_evidence": bool(js.get("evidence"))})


def summarize() -> None:
    rows = {}
    for p in sorted(V.glob("*.json")):
        r = json.loads(p.read_text(encoding="utf-8"))
        k = (r.get("task"), r.get("role") or "", r.get("arm"))
        rows.setdefault(k, []).append(r)
    print(json.dumps({str(k): len(v) for k, v in rows.items()}, ensure_ascii=False))


def main() -> int:
    only = sys.argv[1].upper() if len(sys.argv) > 1 else "ALL"
    if only in ("ALL", "V1"):
        run_V1()
        run_V1B()
    if only in ("ALL", "V2"):
        run_V2()
    if only in ("ALL", "V3"):
        run_V3()
    if only in ("ALL", "S6"):
        run_S6()
    summarize()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
