#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V15.1 T4：同一套硬约束，换注意力事实清单（V11 的 F2_real）作呈现件。

用于三角对照：T1（同件→unable）/ T3（结构摘要→unable）/ T4（事实清单→应能答）。
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

1. 你的每一条依据都必须**逐字**引自下面的文本，并注明位置（第几行 / 第几个方向）。
   不许改写、不许总结、不许补全。
2. 如果找不到能支撑结论的**区分性证据**，你必须让 "answer" 等于 "unable"。
   注意：**说 unable 是正确回答**，不算失败、不扣分；编一个答案是错误的。
3. 不许使用「通常 / 一般 / 大概 / 看起来 / 似乎」这类判断，
   也不许引入下面文本之外的领域常识。"""


def prompt(body: str) -> str:
    return f"""下面是一个材料库既有的方向清单，每行给出：方向的谓词、它的项数、以及使用记录。
这个库的机制是：只有被**下钻**过的方向才留下使用记录（被摆出来看过不算）；
每条记录会随年龄**衰减**，半衰期 30 天。

问题：**这个库的使用者最近更重视哪一类方向？**

{RULES}

【清单】
{body}

只输出 JSON：
{{"answer": "一句话结论 或 unable", "evidence": ["逐字引文（含位置）"], "reason": "一句话"}}"""


def parse(text: str) -> dict:
    m = re.search(r'"answer"\s*:\s*"([^"]*)"', text or "")
    ans = m.group(1) if m else ""
    unable = ("unable" in ans.lower()) or any(
        k in ans for k in ("无法", "不能区分", "看不出", "无法判断"))
    points_review = ("review" in ans) and not re.search(r"(?:非|不是|排除)\s*.*article", ans)
    return {"answer": ans[:120], "unable": unable, "points_review": bool(points_review)}


def main() -> int:
    body = (M1 / "v_facts" / "F2_real.txt").read_text(encoding="utf-8").strip()
    print("=== 呈现件（V11 的 F2_real，顺序已打乱）===", flush=True)
    print(body, flush=True)
    rows = []
    for i in range(N):
        p = prompt(body)
        r = llm.chat([{"role": "user", "content": p}], model="deepseek-chat",
                     max_tokens=900, temperature=0, tries=4)
        text = r.get("text") or ""
        j = parse(text)
        rows.append({**j, "inst": i, "text": text,
                     "prompt_sha": hashlib.sha256(p.encode()).hexdigest()})
        print(f"  T4 inst{i} unable={j['unable']} review={j['points_review']} "
              f"answer={j['answer'][:50]!r}", flush=True)

    c4 = sum(1 for x in rows if x["points_review"])
    summary = {"C4_T4_review": c4, "T4_unable": sum(1 for x in rows if x["unable"]),
               "of": N}
    (OUT / "result_t4.json").write_text(json.dumps(
        {**summary, "detail": rows}, ensure_ascii=False, indent=1),
        encoding="utf-8", newline="\n")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
