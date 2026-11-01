#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V15（PREREG）：受限翻译层——给盲判加硬约束，看"有求必应"是否消失。

三任务 × 5 实例，全部有确定答案：
  T1 同件对照（两份逐字相同）⇒ 必须 unable
  T2 信息不足（摘要前 3 行）  ⇒ 必须 unable
  T3 完整摘要                 ⇒ 应给出具体倾向
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
import llm                                            # noqa: E402

M1 = Path(__file__).resolve().parents[1]
OUT = M1 / "v_constrained"
N = 5

RULES = """规则（违反任何一条，本次回答作废）：

1. 你的每一条依据都必须**逐字**引自下面的文本，并注明位置（第几行 / 第几个方向）。
   不许改写、不许总结、不许补全。
2. 如果找不到能支撑结论的**区分性证据**，你必须让 "answer" 等于 "unable"。
   注意：**说 unable 是正确回答**，不算失败、不扣分；编一个答案是错的。
3. 不许使用「通常 / 一般 / 大概 / 看起来 / 似乎」这类判断，
   也不许引入下面文本之外的领域常识。"""


def ask(prompt: str, inst: int) -> tuple[str, str]:
    r = llm.chat([{"role": "user", "content": prompt}], model="deepseek-chat",
                 max_tokens=900, temperature=0, tries=4)
    return (r.get("text") or ""), hashlib.sha256(prompt.encode()).hexdigest()


def parse(text: str) -> dict:
    m = re.search(r'"answer"\s*:\s*"([^"]*)"', text or "")
    ans = m.group(1) if m else ""
    me = re.search(r'"evidence"\s*:\s*\[(.*?)\]', text or "", re.DOTALL)
    ev = me.group(1) if me else ""
    unable = ("unable" in ans.lower()) or any(
        k in ans for k in ("无法", "不能区分", "看不出", "无法判断"))
    return {"answer": ans[:120], "n_evidence": ev.count('"') // 2 if ev else 0,
            "unable": unable, "raw_has_unable": "unable" in (text or "").lower()}


def main() -> int:
    full = (M1 / "summary_1page.md").read_text(encoding="utf-8").strip()
    lines = full.splitlines()
    partial = "\n".join(lines[:3])          # 标题 + 根 + 一大支
    OUT.mkdir(parents=True, exist_ok=True)

    def p_t1(left: str, right: str) -> str:
        return f"""下面有两份结构摘要，都来自同一批材料的分类结果。

问题：**哪一份更能体现「按发表时间组织」？**（回答「摘要1」「摘要2」）

{RULES}

如果是无法区分，按规则 2 输出 unable。

【摘要1】
{left}

【摘要2】
{right}

只输出 JSON：
{{"answer": "摘要1 或 摘要2 或 unable", "evidence": ["逐字引文（含位置）"], "reason": "一句话"}}"""

    def p_what(body: str) -> str:
        return f"""下面是一份结构摘要。

问题：**建这份结构的人最重视什么？**

{RULES}

如果给定文本不足以判断，按规则 2 输出 unable。

【摘要】
{body}

只输出 JSON：
{{"answer": "一句话结论 或 unable", "evidence": ["逐字引文（含位置）"], "reason": "一句话"}}"""

    rng = random.Random(20261101)
    res: dict[str, list] = {"T1": [], "T2": [], "T3": []}

    for i in range(N):
        # T1：两份逐字相同（左右顺序交替，避免位置效应）
        same = full
        a, b = (same, same) if i % 2 == 0 else (same, same)
        pr = p_t1(a, b)
        text, sha = ask(pr, i)
        j = parse(text)
        res["T1"].append({**j, "inst": i, "text": text, "prompt_sha": sha,
                          "left": "摘要1", "note": "两份逐字相同"})
        print(f"  T1 inst{i} unable={j['unable']} answer={j['answer'][:40]!r}", flush=True)

    for i in range(N):
        pr = p_what(partial)
        text, sha = ask(pr, i)
        j = parse(text)
        res["T2"].append({**j, "inst": i, "text": text, "prompt_sha": sha})
        print(f"  T2 inst{i} unable={j['unable']} answer={j['answer'][:40]!r}", flush=True)

    for i in range(N):
        pr = p_what(full)
        text, sha = ask(pr, i)
        j = parse(text)
        res["T3"].append({**j, "inst": i, "text": text, "prompt_sha": sha})
        print(f"  T3 inst{i} unable={j['unable']} answer={j['answer'][:40]!r}", flush=True)

    cnt = lambda k: sum(1 for x in res[k] if x["unable"])
    c1, c2 = cnt("T1"), cnt("T2")
    c3 = sum(1 for x in res["T3"] if not x["unable"] and x["n_evidence"] >= 1)
    summary = {"C1_T1_unable": c1, "C2_T2_unable": c2, "C3_T3_specific": c3, "of": N,
               "verdict": ("受限翻译层可靠" if (c1 >= 4 and c2 >= 4 and c3 >= 4)
                           else "需按 PREREG 判读")}
    (OUT / "result.json").write_text(json.dumps(
        {**summary, "detail": res}, ensure_ascii=False, indent=1),
        encoding="utf-8", newline="\n")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
