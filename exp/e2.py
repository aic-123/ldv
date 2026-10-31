#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E2：长会话污染 + 压缩对照（DESIGN_V2 §4，PREREG_E1 §0 的 F1-d/F1-e）。

    python exp/e2.py --runs 1,2,3

每 run 7 个污染事件 × 4 条件（裸/ldv × 全史/压缩）。事件流程（一个任务窗口内）：
  植入 2007 → 3 轮灌水 → 更正 2008（勘误 2026-10-20）→ 3 轮灌水 → 三问 + 陷阱 Q4
压缩（确定性，禁 LLM 摘要）：把**更正之前**的全部轮次替换为一行摘要——
裸组的历史随之丢失；ldv 组的事实（R0 原始记录 / R1 勘误 + ts 维度）在结构里，不受影响。

陷阱 Q4 判定（score.py v4）：直接给 2007 且无作废表述 = 落陷阱；指出已更正 = 不落。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import corpus                       # noqa: E402
import ds                           # noqa: E402
import score                        # noqa: E402

HERE = Path(__file__).resolve().parent
MCP_DIR = HERE.parent / "ldv-mcp"
RUNS = HERE / "runs"

ORIG, FIX = 2007, 2008
FIX_DATE, FIX_REASON = "2026-10-20", "出版社勘误"
N_EVENTS = 7
FILLERS = 3                          # 植入后 / 更正后各几轮
N_EVENTS_PER_RUN = 7

SYSTEM_M = (
    "你是登记助手，可以用 ldv 工具查询自己的记忆结构（历史登记与更正都在那里）。\n"
    "工具：orient / ask / remember / drill / derive / remember_answer / undo / next。\n"
    '只输出一个 JSON：{"tool": "工具名", "args": {...}} 或 {"final": "<回答>"}。')


def _chat(msgs, *, max_tokens, tries=6):
    time.sleep(2.0)
    return ds.chat(msgs, max_tokens=max_tokens, temperature=0, tries=tries)


def ack_check(text: str, year: int) -> bool:
    return str(year) in (text or "")


def build_event(pool: list[dict], run: int, ev: int) -> dict:
    """一个事件：背景材料 + 被植入的论文 wid。"""
    wid_p = pool[(run * 100 + ev * 17) % len(pool)]
    wid = wid_p["id"]
    filler = []
    for k in range(FILLERS * 3):
        p = pool[(run * 97 + ev * 31 + k * 7) % len(pool)]
        if p["id"] == wid:
            p = pool[(run * 97 + ev * 31 + k * 7 + 1) % len(pool)]
        filler.append(f"〔F{run}{ev:02d}{k:02d}〕论文登记：《{p['title'][:60]}》"
                      f"发表于 {p['year']} 年，载于 {p['venue'][:24]}。")
    third = len(filler) // 3
    return {"wid": wid, "title": wid_p["title"][:50],
            "filler_pre": filler[:third], "filler_post": filler[third:]}


def plant_msg(ev: dict) -> str:
    return (f"背景登记批次：\n" + "\n".join(ev["filler_pre"]) +
            f"\n\n另外有一条**单独登记**：论文 {ev['wid']}《{ev['title']}》"
            f"最初登记的年份是 {ORIG} 年（登记时间 2026-10-01，来源：初录）。"
            f"请确认你记住了这条单独登记，只回复：已登记，年份是 {ORIG}。")


def correct_msg(ev: dict) -> str:
    return (f"更正：论文 {ev['wid']} 的年份应为 {FIX} 年。原因：{FIX_REASON}"
            f"（更正时间 {FIX_DATE}，原登记 {ORIG} 有误）。"
            f"只回复：已更正，年份是 {FIX}。")


def filler_msg(ev: dict, batch: int) -> str:
    return (f"补充登记批次 {batch}：\n" +
            "\n".join(ev["filler_post"][:3] if batch == 1 else ev["filler_post"][3:]) +
            "\n（无需复述，只回复：收到。）")


def ask_question(ev: dict) -> str:
    return (
        "现在回答四个问题，只输出 JSON：\n"
        '{"now": <年份数字>, "original": <年份数字>, '
        '"changed": {"when": "<YYYY-MM-DD 或 null>", "why": "<原因或 null>"}, '
        '"q4": "<按最初的记录这篇论文是哪一年？直接给年份；若该记录已作废请说明>"}\n'
        f"1) 你现在认为论文 {ev['wid']} 的年份是什么？\n"
        f"2) 最初登记的年份是什么？\n"
        f"3) 是什么时候、因为什么改的？\n"
        f"4) 按最初的记录，{ev['wid']} 是哪一年？请直接给年份。")


def compress(msgs: list[dict]) -> list[dict]:
    """确定性压缩：**整窗压成一行摘要**（含"其间有 1 次更正"的过程性事实，不含任何值）。

    ⚠️ 设计点：摘要若保留植入/更正原文，裸组就测不出"历史丢失"；
       若连"发生过更正"都不提，压缩就成了无差别摧毁，不真实。取中间：过程在、值不在。
    """
    n = len(msgs)
    return [{"role": "user",
             "content": f"[系统提示：此前 {n} 轮对话已压缩为摘要——材料登记批次"
                        f"处理完毕，其间包含 1 次登记更正。]"}]


def write_ldv(store: Path, run: int, ev_i: int, ev: dict, client) -> None:
    """M 组：植入与更正**在事件发生时**写进结构（R0 原始记录 / R1 勘误 + ts 维度）。

    ⚠️ `remember` 只写节点、**不创建语料**（E2 首轮 28 个 M 事件全零的根因：
    agent 诚实地查了 5 轮、如实报「结构里没有任何登记」——模型无罪，是骨架没建）。
    """
    corpus_name = f"e2-r{run}-e{ev_i}"
    (store / "_data" / corpus_name / "nodes").mkdir(parents=True, exist_ok=True)
    from ldv_client import LdvError
    calls = [
        ("remember", {"corpus": corpus_name, "id": "R0",
                      "fields": {"type": "登记", "title": ev["wid"],
                                 "evidence_status": "原始记录"},
                      "body": f"R0 单独登记：论文 {ev['wid']}《{ev['title']}》最初登记的"
                              f"年份是 {ORIG} 年（登记时间 2026-10-01，来源：初录）。",
                      "actor": "agent", "session": f"e2-r{run}"}),
        ("remember", {"corpus": corpus_name, "id": "R1",
                      "fields": {"type": "登记", "title": ev["wid"],
                                 "evidence_status": "勘误"},
                      "body": f"R1 更正：论文 {ev['wid']} 的年份应为 {FIX} 年"
                              f"（更正时间 {FIX_DATE}，原因：{FIX_REASON}，"
                              f"原登记 {ORIG} 有误）。",
                      "actor": "agent", "session": f"e2-r{run}"}),
        ("derive", {"corpus": corpus_name, "name": "ts",
                    "values": {"R0": "2026-10-01", "R1": FIX_DATE},
                    "rationale": "E2 三问里的「何时更正」要从记录读出，不靠模型记忆",
                    "derived_from": "原材料", "session": f"e2-r{run}"}),
    ]
    for tool, args in calls:
        try:
            client.call(tool, args)
        except LdvError as exc:
            print(f"    ⚠️ ldv {tool} 失败：{str(exc)[:100]}")


def ask_via_tools(ev: dict, run: int, ev_i: int, client) -> tuple[str | None, dict]:
    """M 组问答：工具循环 ≤6 轮。"""
    meta = {"tool_rounds": 0}
    corpus_name = f"e2-r{run}-e{ev_i}"
    msgs = [{"role": "user",
             "content": ask_question(ev) +
                        "（历史登记与更正都在你的记忆结构里——先查再用。）"}]
    final = None
    for rnd in range(6):
        r = _chat([{"role": "system", "content": SYSTEM_M}] + msgs, max_tokens=3000)
        text = (r.get("text") or "").strip()
        try:
            m = re.search(r"\{.*\}", text, re.DOTALL)
            obj = json.loads(m.group(0)) if m else {}
        except Exception:
            obj = {}
        if "final" in obj:
            final = str(obj["final"])
            break
        tool = obj.get("tool")
        if not tool:
            msgs.append({"role": "assistant", "content": text[:400]})
            msgs.append({"role": "user",
                         "content": '只输出 {"tool":...} 或 {"final":...}。'
                         if rnd < 4 else "最后一轮：请给 final。"})
            continue
        meta["tool_rounds"] += 1
        try:
            res = client.call(tool, {"corpus": corpus_name,
                                     **(obj.get("args") or {}),
                                     "session": f"e2-r{run}"})
        except Exception as exc:                      # noqa: BLE001
            res = f"工具错误：{exc}"
        msgs.append({"role": "assistant", "content": text[:600]})
        msgs.append({"role": "user",
                     "content": f"工具返回：\n{str(res)[:3000]}\n\n继续。"
                                f"（还剩 {5 - rnd} 轮）"})
    return final, meta


def parse_answer(text: str) -> dict:
    try:
        m = re.search(r"\{.*\}", text or "", re.DOTALL)
        return json.loads(m.group(0)) if m else {}
    except Exception:
        return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="1,2,3")
    ap.add_argument("--conditions", default="B-full,B-comp,M-full,M-comp")
    ap.add_argument("--done", default="",
                    help="已有有效记录的 jsonl——其中 (cond,run,ev) 跳过（断点续跑）")
    a = ap.parse_args()
    conds = [c for c in a.conditions.split(",") if c.strip()]
    done_set = set()
    if a.done and Path(a.done).is_file():
        for l in Path(a.done).read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(l)
                if "q1_now" in r and not r.get("invalid") and not r.get("error"):
                    done_set.add((r["cond"], r["run"], r["ev"]))
            except Exception:                          # noqa: BLE001
                continue
        print(f"断点续跑：跳过 {len(done_set)} 个已完成事件")

    pool = corpus.load_papers(4037)
    # 池子标题唯一化（与 E1 同理由）
    seen, uniq = set(), []
    for p in pool:
        k = re.sub(r"\s+", "", p["title"]).lower()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(p)
    pool = uniq

    RUNS.mkdir(exist_ok=True)
    tag = datetime.now().strftime("E2-%Y%m%d-%H%M%S")
    out_path = RUNS / f"{tag}.jsonl"
    store_root = HERE.parent / "e1_store"          # 与 E1 共用一个 MCP store
    store_root.mkdir(exist_ok=True)

    from ldv_client import LdvSession
    sess = None
    if any(c.startswith("M") for c in conds):
        sess = LdvSession([sys.executable, "server.py"],
                          env={"LDV_MCP_STORE": str(store_root)},
                          cwd=str(MCP_DIR), timeout=180)
    try:
        for run in [int(x) for x in a.runs.split(",") if x.strip()]:
            for cond in conds:
                backend = "M" if cond.startswith("M") else "B"
                comp = cond.endswith("comp")
                for ev_i in range(1, N_EVENTS_PER_RUN + 1):
                    if (cond, run, ev_i) in done_set:
                        continue
                    ev = build_event(pool, run, ev_i)
                    rec = {"exp": "E2", "cond": cond, "run": run, "ev": ev_i,
                           "wid": ev["wid"], "model": "deepseek-flash",
                           "SCORER_VERSION": score.SCORER_VERSION,
                           "ts": datetime.now().isoformat(timespec="seconds")}
                    # ── 事件窗 ──
                    msgs = [{"role": "user", "content": plant_msg(ev)}]
                    invalid = None
                    r = _chat(msgs, max_tokens=64)
                    if not ack_check(r.get("text"), ORIG):
                        invalid = f"植入确认没答 {ORIG}: {str(r.get('text'))[:50]!r}"
                    msgs.append({"role": "assistant", "content": r.get("text") or ""})
                    if invalid is None:
                        for b in (1, 2):
                            msgs.append({"role": "user", "content": filler_msg(ev, b)})
                            r = _chat(msgs, max_tokens=32)
                            msgs.append({"role": "assistant",
                                         "content": r.get("text") or ""})
                        msgs.append({"role": "user", "content": correct_msg(ev)})
                        r = _chat(msgs, max_tokens=64)
                        if not ack_check(r.get("text"), FIX):
                            invalid = f"更正确认没答 {FIX}: {str(r.get('text'))[:50]!r}"
                        msgs.append({"role": "assistant",
                                     "content": r.get("text") or ""})
                        for b in (3, 4):
                            msgs.append({"role": "user", "content": filler_msg(ev, b)})
                            r = _chat(msgs, max_tokens=32)
                            msgs.append({"role": "assistant",
                                         "content": r.get("text") or ""})
                    if invalid:
                        rec.update({"invalid": True, "why": invalid})
                        with out_path.open("a", encoding="utf-8") as f:
                            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        print(f"  {cond} r{run} e{ev_i}: 作废——{invalid}")
                        continue
                    # ── 压缩（在更正确认之后、提问之前）──
                    if comp:
                        msgs = compress(msgs)
                    # ── M 组：事实在结构里（植入/更正时已写）──
                    if backend == "M":
                        write_ldv(store_root, run, ev_i, ev, sess)
                        final, meta = ask_via_tools(ev, run, ev_i, sess)
                        rec["tool_rounds"] = meta["tool_rounds"]
                        raw = final or ""
                    else:
                        msgs.append({"role": "user", "content": ask_question(ev)})
                        r = _chat(msgs, max_tokens=3000)
                        raw = r.get("text") or ""
                        rec["finish"] = r.get("finish")
                    ans = parse_answer(raw)
                    sc = score.score_belief(ans, now=FIX, orig=ORIG,
                                            fix_date=FIX_DATE, fix_reason=FIX_REASON)
                    rec.update({**sc, "raw_output": raw, "compressed": comp,
                                "backend": backend})
                    with out_path.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    print(f"  {cond} r{run} e{ev_i}: Q1={sc['q1_now']} "
                          f"Q2={sc['q2_original']} Q3w={sc['q3_when']} "
                          f"Q3y={sc['q3_why']} 陷阱Q4={sc['q4_trap']}")
    finally:
        if sess is not None:
            sess.close()
    print(f"记录 → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
