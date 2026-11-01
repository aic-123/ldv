#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V15.2：规则歧义修正后重测三组（同件 / 结构摘要 / 事实清单）。

唯一改动：把「依据必须逐字」与「结论可以归纳」分开写。
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "exp"))
import llm                                            # noqa: E402

M1 = Path(__file__).resolve().parents[1]
OUT = M1 / "v_constrained"
N = 5

RULES = """规则（违反任何一条，本次回答作废）：

1. 你的每一条**依据**都必须逐字引自下面的文本，并注明位置（第几行 / 第几个方向）。
   依据不许改写、不许补全。
   **但结论可以是对这些依据的归纳**（例如把若干个方向归为一类），
   归纳只能使用清单里出现过的字段（如 type= / evidence_status= / era= / 项数 / 使用记录），
   不许引入清单外的概念。
2. 如果找不到能支撑结论的**区分性证据**，你必须让 "answer" 等于 "unable"。
   注意：**说 unable 是正确回答**，不算失败、不扣分；编一个答案是错误的。
3. 不许使用「通常 / 一般 / 大概 / 看起来 / 似乎」这类判断。"""


def ask(p: str) -> tuple[str, str]:
    r = llm.chat([{"role": "user", "content": p}], model="deepseek-chat",
                 max_tokens=900, temperature=0, tries=4)
    return (r.get("text") or ""), hashlib.sha256(p.encode()).hexdigest()


NEG = re.compile(r"(?:非|不是|排除|而非|并非)\s*(?:type\s*=\s*)?article")


def parse(text: str) -> dict:
    t = text or ""
    m = re.search(r'"answer"\s*:\s*"([^"]*)"', t)
    ans = m.group(1) if m else ""
    unable = ("unable" in ans.lower()) or any(
        k in ans for k in ("无法", "不能区分", "看不出", "无法判断"))
    has_a = "article" in ans
    a_neg = bool(NEG.search(ans))
    return {"answer": ans[:140], "unable": unable,
            "points_review": ("review" in ans) and (not has_a or a_neg),
            "points_article": has_a and not a_neg and "review" not in ans}


def main() -> int:
    full = (M1 / "summary_1page.md").read_text(encoding="utf-8").strip()
    facts = (M1 / "v_facts" / "F2_real.txt").read_text(encoding="utf-8").strip()

    def p_pair(body: str) -> str:
        return f"""下面有两份结构摘要，都来自同一批材料的分类结果。

问题：**哪一份更能体现「按发表时间组织」？**（回答「摘要1」或「摘要2」）

{RULES}

【摘要1】
{body}

【摘要2】
{body}

只输出 JSON：
{{"answer": "摘要1 / 摘要2 / unable", "evidence": ["逐字引文（含位置）"], "reason": "一句话"}}"""

    def p_what(kind: str, body: str) -> str:
        head = ("下面是一个材料库既有的方向清单，每行给出：谓词、项数、使用记录。"
                "机制：只有被下钻过的方向才留使用记录（被摆出来看过不算）；"
                "记录随年龄衰减，半衰期 30 天。"
                if kind == "facts" else "下面是一份结构摘要。")
        return f"""{head}

问题：**这个库的使用者最近更重视哪一类方向？**（若为结构摘要：建这份结构的人最重视什么？）

{RULES}

【材料】
{body}

只输出 JSON：
{{"answer": "一句话结论 或 unable", "evidence": ["逐字引文（含位置）"], "reason": "一句话"}}"""

    groups = {"T1_same": p_pair(full),
              "T2_struct": p_what("struct", full),
              "T4_facts": p_what("facts", facts)}
    res: dict[str, list] = {}
    for name, pr in groups.items():
        res[name] = []
        for i in range(N):
            text, sha = ask(pr)
            j = parse(text)
            res[name].append({**j, "inst": i, "text": text, "prompt_sha": sha})
            print(f"  {name} inst{i} unable={j['unable']} review={j['points_review']} "
                  f"answer={j['answer'][:60]!r}", flush=True)

    c1 = sum(1 for x in res["T1_same"] if x["unable"])
    c2 = sum(1 for x in res["T2_struct"] if x["unable"])
    c4 = sum(1 for x in res["T4_facts"] if x["points_review"])
    summary = {"C1_T1_unable": c1, "C2_T2_unable": c2, "C4_T4_review": c4, "of": N,
               "verdict": ("三角成立：约束有效 / 结构无证据 / 事实可归纳"
                           if (c1 >= 4 and c2 >= 4 and c4 >= 4) else "未成立，见 PREREG V15.2")}
    (OUT / "result_v152.json").write_text(json.dumps(
        {**summary, "detail": res}, ensure_ascii=False, indent=1),
        encoding="utf-8", newline="\n")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
