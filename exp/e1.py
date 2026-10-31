#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E1：引导的跨任务保留（PREREG_E1 §9 V3 + DESIGN_V2 V3，判分 exp/score.py v4）。

五组（用户 2026-10-31 裁定：恢复原表 + 每agent 150k token 上下文预算）：

    R-0  每任务重置  仅任务 1 短引导   裸      引导丢失的下限（≡ 已跑的 B-0，数据沿用改名）
    R-R  每任务重置  每次重给长引导    裸      上界（≡ 已跑的 B-R，数据沿用）
    R-L  每任务重置  仅任务 1 给→进结构 ldv    能否替代"重给"（= 原 M-L）
    L-0  长会话累积  仅最初短引导      裸      真实场景：引导随压缩变远/消失
    L-L  长会话累积  仅最初长引导→结构  ldv    生态位：真实场景 + ldv

预算（两条硬规则，缺一不可）：
  Ⓐ 每 agent 上下文预算 150k token；超限 ⇒ 确定性压缩：保留最近 ~105k token +
    一行**无事实**摘要标记。压缩事件逐次落盘。
  Ⓑ **单任务（材料+任务指令）≤ 150k**——否则 R 组单任务就要压缩、L 组起点被压，
    组间不可比。守卫在 build_task 里（当前爬坡最大 88k 字 ≈ 29k token，余量 5 倍）。

    python exp/e1.py --groups R-L --runs 1 --tasks 3     # 试点
    python exp/e1.py --groups R-L,L-0,L-L --runs 1,2,3   # 全量
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
import corpus        # noqa: E402
import llm           # noqa: E402  # 统一通道：deepseek-flash / glm-5
import score         # noqa: E402

HERE = Path(__file__).resolve().parent
MCP_DIR = HERE.parent / "ldv-mcp"
RUNS = HERE / "runs"

K_CONFLICT, N_TRAP = 5, 10
RAMP_CHARS = [8000, 12000, 16000, 22000, 28000, 34000, 40000,
              46000, 52000, 58000, 64000, 70000, 76000, 82000, 88000]
N_TASKS = 15

BUDGET_TOKENS = 150_000
KEEP_TOKENS = 105_000
CHARS_PER_TOKEN = 3.0

SHORT_GUIDE = "材料是一批论文登记记录，其中可能混进了错误。请检查并报告你发现的问题。"
LONG_GUIDE = """材料是论文登记记录，每条形如〔W…〕论文登记：《标题》发表于 YYYY 年，载于 期刊。
请找出所有「同一篇论文的年份被写成两个不同值」的矛盾，输出 JSON：
{"pairs": [{"ids": ["id1","id2"], "year_a": N, "year_b": M}]}
判定规则：
- 同一论文 = 两行的标题完全一致（忽略大小写与首尾空格），id 不同
- 矛盾 = 同一论文出现两个不同年份
- 不算矛盾：同标题且同年份的重复登记（这是重复，不是矛盾，不要报告）
- 两个 id 都要给出，缺一不算命中"""
TASK_BARE = "以下是一批论文登记记录。请检查这份材料。"

SYSTEM_M = (
    "你是登记检查助手。你的记忆结构（ldv）里存着任务规则与输出约定，用工具取回。\n"
    "工具与**精确签名**（参数名必须一致，错了会被拒）：\n"
    "  orient(corpus?, session?) —— 我记得什么\n"
    "  ask(corpus?, require:[键], forbid?, plugin?, with_body?, limit?) —— 键查询；"
    "with_body=true 连正文返回\n"
    "  remember(corpus?, id, fields:{}, body) —— 写一个节点\n"
    "  drill(corpus?, did, require:[键]) / next(corpus?) / undo(corpus?, id)\n"
    "调用方式：只输出一个 JSON 对象——\n"
    '  调工具：{"tool": "工具名", "args": {...}}\n'
    '  给最终答案：{"final": "<完整回答文本>"}\n'
    "不要输出任何其他内容。")

MAX_TOKENS_OUT = 16000


# ── 材料 ─────────────────────────────────────────────────────────────
def norm_title(t: str) -> str:
    return re.sub(r"\s+", "", t).lower()


def load_pool(n: int = 4037) -> list[dict]:
    """唯一标题池（按全文标题；材料行用全标题——试点教训：截断标题撞前缀）。"""
    seen, out = set(), []
    for p in corpus.load_papers(n):
        k = norm_title(p["title"])
        if k in seen:
            continue
        seen.add(k)
        out.append(p)
    return out


def build_task(pool: list[dict], task_idx: int, run: int) -> dict:
    target = RAMP_CHARS[task_idx - 1]
    offset = run * 1000 + task_idx * 60
    base = [pool[(offset + i) % len(pool)] for i in range(max(30, target // 100))]
    lines, truth, traps = [], [], []
    for i, p in enumerate(base):
        lines.append(f"〔{p['id']}〕论文登记：《{p['title']}》发表于 {p['year']} 年，"
                     f"载于 {p['venue'][:28]}。")
    rnd = __import__("random").Random(1000 * run + task_idx)
    step = max(1, len(base) // (K_CONFLICT + 1))
    for j, i in enumerate(range(0, len(base) - 1, step)[:K_CONFLICT]):
        p = base[i]
        dup = f"D{run}{task_idx:02d}{j:02d}"
        ny = p["year"] + rnd.choice([-3, -2, -1, 1, 2, 4])
        lines.append(f"〔{dup}〕论文登记：《{p['title']}》发表于 {ny} 年，"
                     f"载于 {p['venue'][:28]}。")
        truth.append({"a": p["id"], "b": dup})
    step2 = max(1, len(base) // (N_TRAP + 1))
    for j, i in enumerate(range(3, len(base) - 1, step2)[:N_TRAP]):
        p = base[i]
        if any(p["id"] == t["a"] for t in truth):
            continue
        dup = f"T{run}{task_idx:02d}{j:02d}"
        lines.append(f"〔{dup}〕论文登记：《{p['title']}》发表于 {p['year']} 年，"
                     f"载于 {p['venue'][:28]}。")
        traps.append({"a": p["id"], "b": dup})
    text = "\n".join(lines)
    # Ⓑ 硬守卫：单任务（材料+指令）必须 ≤ 预算的 85%——否则 R 组单任务就要压缩、
    #    L 组起点被压，组间不可比（用户 2026-10-31：否则没有对比意义）。
    task_tokens = est_tokens([{"content": text + "\n" + LONG_GUIDE}])
    if task_tokens > BUDGET_TOKENS * 0.85:
        raise ValueError(f"任务 {task_idx} 单体 {task_tokens} tok 超过预算 85%"
                         f"（{int(BUDGET_TOKENS * 0.85)}）——爬坡表非法")
    return {"text": text, "truth": truth, "traps": traps,
            "n_lines": len(lines), "chars": len(text),
            "task_tokens": task_tokens}


# ── 预算与压缩 ───────────────────────────────────────────────────────
def est_tokens(msgs: list[dict]) -> int:
    return int(sum(len(m.get("content") or "") for m in msgs) / CHARS_PER_TOKEN)


def compress_history(msgs: list[dict]) -> tuple[list[dict], int]:
    """确定性压缩：保 system + 最近 ~KEEP_TOKENS，其余换一行**无事实**标记。

    Ⓐ 摘要不含任务事实——压掉的就是历史；
    Ⓑ L-0 的引导（在最早的用户消息里）会因此真的消失，这正是 L-0 要测的现象；
    Ⓒ 只在**任务之间**压（工具轮内不压）：单任务 ≤85% 预算（build_task 守卫），
       任务开始时 ctx ≤~70% ⇒ 任务中也不会破线。
    """
    if not msgs:
        return msgs, 0
    total = est_tokens(msgs)
    if total <= BUDGET_TOKENS:
        return msgs, 0
    sys_head = [msgs[0]] if msgs[0].get("role") == "system" else []
    rest = msgs[len(sys_head):]
    keep: list[dict] = []
    used = est_tokens(sys_head)
    for m in reversed(rest):
        t = est_tokens([m])
        if used + t > KEEP_TOKENS:
            break
        keep.insert(0, m)
        used += t
    dropped = len(rest) - len(keep)
    marker = {"role": "user",
              "content": f"[系统提示：此前 {dropped} 轮对话已按上下文预算压缩——"
                         f"此前任务均正常处理完毕。]"}
    return sys_head + [marker] + keep, 1


# ── 单发组（R-0 / R-R）────────────────────────────────────────────────
def run_R(group: str, mat: dict, task_idx: int) -> tuple[str, dict]:
    if group == "R-0":
        user = (f"{SHORT_GUIDE}\n\n{mat['text']}\n\n请以 JSON 报告你发现的问题。"
                if task_idx == 1 else
                f"{TASK_BARE}\n\n{mat['text']}\n\n请以 JSON 报告你发现的问题。")
        guide = "short" if task_idx == 1 else "none"
    else:
        user = f"{LONG_GUIDE}\n\n{mat['text']}\n\n请按要求输出。"
        guide = "long"
    t0 = time.perf_counter()
    r = llm.chat([{"role": "user", "content": user}], max_tokens=MAX_TOKENS_OUT,
                temperature=0, tries=6)
    return r, {"guide": guide, "wall_s": round(time.perf_counter() - t0, 1)}


# ── 工具循环（R-L 重置 / L-L 累积）────────────────────────────────────
def ensure_rules(client, run: int) -> list[str]:
    """任务 1：把规则与输出约定写进结构（**幂等**：已存在则跳过——
    换模型重跑时同一 id 再 remember 会报错，而这毫无信息量）。返回错误列表。"""
    try:
        out = client.call("ask", {"corpus": "e1", "require": ["type=规则"],
                                  "session": f"e1-r{run}"})
        if '"n_answer": 0' not in out and '"n_answer":0' not in out:
            return []                                  # 已有 ⇒ 跳过
    except Exception:                                  # noqa: BLE001
        pass                                           # 查不动就按不存在处理
    errs = []
    for nid, f, body in (
            ("rule-e1", {"type": "规则", "title": "矛盾检查规则"}, LONG_GUIDE),
            ("rule-e1-schema", {"type": "输出约定", "title": "输出约定"},
             '{"pairs": [{"ids": ["id1","id2"], "year_a": N, "year_b": M}]}\n'
             "两个 id 都要给出；同标题同年份=重复，不算矛盾。")):
        try:
            client.call("remember", {"corpus": "e1", "id": nid, "fields": f,
                                     "body": body, "actor": "agent",
                                     "session": f"e1-r{run}"})
        except Exception as exc:                      # noqa: BLE001
            errs.append(f"{nid}: {str(exc)[:150]}")
    return errs


def task_user(group: str, mat: dict, task_idx: int) -> tuple[str, str]:
    if group in ("R-L", "L-L"):
        if task_idx == 1:
            return f"{LONG_GUIDE}\n\n{mat['text']}\n\n请按要求输出。完成后给 final。", "long"
        return (f"{TASK_BARE}\n\n{mat['text']}\n\n"
                "规则与输出约定不在本窗口——先用工具从你的记忆结构取回"
                "（ask require=[\"type=规则\"] 与 [\"type=输出约定\"]，with_body=true），"
                "再检查材料。完成后给 final。"), "structure"
    raise ValueError(group)


def run_toolloop(group: str, mat: dict, task_idx: int, run: int, client,
                 state: dict, t0: float) -> tuple[str | None, dict]:
    """R-L（reset）/ L-L（共享累积历史）。压缩只在任务之间发生。"""
    reset = group == "R-L"
    if reset or not state.get("msgs"):
        state["msgs"] = [{"role": "system", "content": SYSTEM_M}]
    meta = {"guide": None, "tool_rounds": 0, "tool_calls": [],
            "remember_errors": [], "ctx_tokens": 0, "compressed": False,
            "compressed_events": state.get("compressed_events", 0),
            "guide_in_window": None}
    user, guide = task_user(group, mat, task_idx)
    meta["guide"] = guide
    if task_idx == 1:
        meta["remember_errors"] = ensure_rules(client, run)

    state["msgs"], n = compress_history(state["msgs"])
    state["compressed_events"] = state.get("compressed_events", 0) + n
    meta["compressed"] = n > 0
    meta["compressed_events"] = state["compressed_events"]
    meta["ctx_tokens"] = est_tokens(state["msgs"])
    meta["guide_in_window"] = any(LONG_GUIDE[:40] in (m.get("content") or "")
                                  for m in state["msgs"])

    state["msgs"].append({"role": "user", "content": user})
    final = None
    usage = {"in": 0, "out": 0, "cached": 0}

    def _acc(r):
        u = r.get("usage") or {}
        usage["in"] += u.get("in", 0)
        usage["out"] += u.get("out", 0)
        usage["cached"] += u.get("cached", 0)

    for rnd in range(6):
        r = llm.chat(state["msgs"], max_tokens=MAX_TOKENS_OUT, temperature=0, tries=6)
        _acc(r)
        text = (r.get("text") or "").strip()
        try:
            m = re.search(r"\{.*\}", text, re.DOTALL)
            obj = json.loads(m.group(0)) if m else {}
        except Exception:
            obj = {}
        if "final" in obj:
            final = str(obj["final"])
            state["msgs"].append({"role": "assistant", "content": text[:2000]})
            break
        tool = obj.get("tool")
        if not tool:
            state["msgs"].append({"role": "assistant", "content": text[:500]})
            state["msgs"].append({"role": "user",
                                  "content": '只输出 {"tool":...} 或 {"final":...}。'
                                  if rnd < 4 else "最后一轮：请给 final。"})
            continue
        meta["tool_rounds"] += 1
        meta["tool_calls"].append({"tool": tool,
                                   "args_keys": sorted((obj.get("args") or {}).keys())})
        try:
            res = client.call(tool, {"corpus": "e1", **(obj.get("args") or {}),
                                     "session": f"e1-r{run}"})
        except Exception as exc:                      # noqa: BLE001
            res = f"工具错误：{exc}"
        state["msgs"].append({"role": "assistant", "content": text[:800]})
        state["msgs"].append({"role": "user",
                              "content": f"工具返回：\n{str(res)[:3000]}\n\n继续。"
                                         f"（还剩 {5 - rnd} 轮）"})
    if final is None:
        state["msgs"].append({"role": "user", "content": "最后一轮：立即给 final。"})
        r = llm.chat(state["msgs"], max_tokens=MAX_TOKENS_OUT, temperature=0, tries=6)
        _acc(r)
        try:
            m = re.search(r"\{.*\}", r.get("text") or "", re.DOTALL)
            final = str(json.loads(m.group(0)).get("final") or "") if m else ""
        except Exception:
            final = ""
        meta["finish"] = "tool_exhausted" if not final else "stop"
    meta["latency_s"] = round(time.perf_counter() - t0, 1)
    meta["_usage"] = usage
    return final, meta


# ── L-0：长会话累积、裸 ───────────────────────────────────────────────
def run_L0(mat: dict, task_idx: int, state: dict, t0: float) -> tuple[str, dict]:
    meta = {"guide": None, "ctx_tokens": 0, "compressed": False,
            "compressed_events": state.get("compressed_events", 0),
            "guide_in_window": None}
    if not state.get("msgs"):
        state["msgs"] = []
    if task_idx == 1:
        user = f"{SHORT_GUIDE}\n\n{mat['text']}\n\n请以 JSON 报告你发现的问题。"
        meta["guide"] = "short"
    else:
        user = f"{TASK_BARE}\n\n{mat['text']}\n\n请以 JSON 报告你发现的问题。"
        meta["guide"] = "none"
    state["msgs"], n = compress_history(state["msgs"])
    state["compressed_events"] = state.get("compressed_events", 0) + n
    meta["compressed"] = n > 0
    meta["compressed_events"] = state["compressed_events"]
    meta["ctx_tokens"] = est_tokens(state["msgs"])
    meta["guide_in_window"] = any(SHORT_GUIDE[:30] in (m.get("content") or "")
                                  for m in state["msgs"])
    state["msgs"].append({"role": "user", "content": user})
    r = llm.chat(state["msgs"], max_tokens=MAX_TOKENS_OUT, temperature=0, tries=6)
    state["msgs"].append({"role": "assistant",
                          "content": (r.get("text") or "")[:2000]})
    meta["wall_s"] = round(time.perf_counter() - t0, 1)
    meta["finish"] = r.get("finish")
    u = r.get("usage") or {}
    meta["_usage"] = {"in": u.get("in", 0), "out": u.get("out", 0),
                      "cached": u.get("cached", 0)}
    return r.get("text") or "", meta


def main() -> int:
    global BUDGET_TOKENS, KEEP_TOKENS
    ap = argparse.ArgumentParser()
    ap.add_argument("--groups", default="R-L")
    ap.add_argument("--runs", default="1")
    ap.add_argument("--tasks", type=int, default=N_TASKS)
    ap.add_argument("--model", default="deepseek-flash",
                    help="deepseek-flash | glm-5（第二模型，规范 §2.5）")
    ap.add_argument("--budget-tokens", type=int, default=BUDGET_TOKENS,
                    help="上下文预算（估算单位）。跨模型规则：min(150k, 自身窗口70%%)"
                         "——glm-4.5-flash（窗口≈128k GLM tok）用 50000，"
                         "50k 估算单位 ≈ 90k GLM tok")
    ap.add_argument("--keep-tokens", type=int, default=KEEP_TOKENS)
    a = ap.parse_args()
    groups = [g.strip() for g in a.groups.split(",") if g.strip()]

    llm.set_model(a.model)
    BUDGET_TOKENS, KEEP_TOKENS = a.budget_tokens, a.keep_tokens
    print(f"预算 {BUDGET_TOKENS}｜保留 {KEEP_TOKENS}（估算单位）", flush=True)
    pool = load_pool()
    print(f"唯一标题池：{len(pool)} 篇｜模型 {a.model}", flush=True)
    RUNS.mkdir(exist_ok=True)
    tag = datetime.now().strftime("E1-%Y%m%d-%H%M%S")
    out_path = RUNS / f"{tag}.jsonl"

    from ldv_client import LdvSession
    store_root = HERE.parent / "e1_store"
    store_root.mkdir(exist_ok=True)
    (store_root / "_data" / "e1" / "nodes").mkdir(parents=True, exist_ok=True)

    need_mcp = any(g in ("R-L", "L-L") for g in groups)
    sess = LdvSession([sys.executable, "server.py"],
                      env={"LDV_MCP_STORE": str(store_root)},
                      cwd=str(MCP_DIR), timeout=180) if need_mcp else None
    try:
        for run in [int(x) for x in a.runs.split(",") if x.strip()]:
            states: dict[str, dict] = {}
            for group in groups:
                state = states.setdefault(group, {"compressed_events": 0})
                for task_idx in range(1, a.tasks + 1):
                  try:
                    t0 = time.perf_counter()
                    mat = build_task(pool, task_idx, run)
                    rec = {"exp": "E1", "group": group, "task_idx": task_idx,
                           "run": run, "model": a.model,
                           "n_chars": mat["chars"], "task_tokens": mat["task_tokens"],
                           "truth_k": len(mat["truth"]), "n_traps": len(mat["traps"]),
                           "SCORER_VERSION": score.SCORER_VERSION,
                           "ts": datetime.now().isoformat(timespec="seconds")}
                    if group in ("R-0", "R-R"):
                        r, meta = run_R(group, mat, task_idx)
                        u = r.get("usage") or {}
                        rec.update(guide=meta["guide"], finish=r.get("finish"),
                                   latency_s=meta["wall_s"],
                                   tokens={"in": u.get("in", 0),
                                           "out": u.get("out", 0),
                                           "cached": u.get("cached", 0)})
                        raw = r.get("text") or ""
                    elif group == "R-L":
                        final, meta = run_toolloop("R-L", mat, task_idx, run,
                                                   sess, state, t0)
                        u = meta.pop("_usage", {"in": 0, "out": 0, "cached": 0})
                        rec.update(guide=meta["guide"],
                                   finish=meta.get("finish", "stop"),
                                   tool_rounds=meta["tool_rounds"],
                                   tool_calls=meta["tool_calls"],
                                   remember_errors=meta.get("remember_errors"),
                                   ctx_tokens=meta["ctx_tokens"],
                                   compressed=meta["compressed"],
                                   compressed_events=meta["compressed_events"],
                                   guide_in_window=meta["guide_in_window"],
                                   latency_s=meta["latency_s"],
                                   tokens=u)
                        raw = final or ""
                    elif group == "L-0":
                        raw, meta = run_L0(mat, task_idx, state, t0)
                        rec.update(guide=meta["guide"], finish=meta["finish"],
                                   latency_s=meta["wall_s"],
                                   ctx_tokens=meta["ctx_tokens"],
                                   compressed=meta["compressed"],
                                   compressed_events=meta["compressed_events"],
                                   guide_in_window=meta["guide_in_window"],
                                   tokens=meta.get("_usage"))
                        raw = raw
                    elif group == "L-L":
                        final, meta = run_toolloop("L-L", mat, task_idx, run,
                                                   sess, state, t0)
                        u = meta.pop("_usage", {"in": 0, "out": 0, "cached": 0})
                        rec.update(guide=meta["guide"],
                                   finish=meta.get("finish", "stop"),
                                   tool_rounds=meta["tool_rounds"],
                                   tool_calls=meta["tool_calls"],
                                   remember_errors=meta.get("remember_errors"),
                                   ctx_tokens=meta["ctx_tokens"],
                                   compressed=meta["compressed"],
                                   compressed_events=meta["compressed_events"],
                                   guide_in_window=meta["guide_in_window"],
                                   latency_s=meta["latency_s"],
                                   tokens=u)
                        raw = final or ""
                    else:
                        raise SystemExit(f"未知组：{group}")
                    pairs, format_ok = score.extract_pairs(raw)
                    sc = score.score(pairs, mat["truth"], mat["traps"])
                    rec.update({**sc, "format_ok": format_ok, "raw_output": raw})
                    with out_path.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    extra = (f" ctx={rec.get('ctx_tokens', '-')} 压缩={rec.get('compressed', '-')}"
                             f" 引导在窗={rec.get('guide_in_window', '-')}"
                             if group in ("L-0", "L-L", "R-L") else "")
                    print(f"  {group} r{run} t{task_idx:02d}: F1={sc['f1']:.3f} "
                          f"P={sc['precision']:.3f} R={sc['recall']:.3f}"
                          f"｜报 {sc['reported']} 中 {sc['hit']}/{sc['k']}"
                          f"｜陷阱 {sc['false_on_trap']}{extra}", flush=True)
                  except llm.BalanceError as exc:
                    print(f"  ⛔ {group} r{run}：余额耗尽——干净停批（{exc}）", flush=True)
                    with out_path.open("a", encoding="utf-8") as f:
                        f.write(json.dumps({"exp": "E1", "group": group, "run": run,
                                            "task_idx": task_idx, "model": a.model,
                                            "error": f"balance: {exc}",
                                            "ts": datetime.now().isoformat(timespec="seconds")},
                                           ensure_ascii=False) + "\n")
                    return 3
                  except Exception as exc:              # noqa: BLE001 - 单任务失败不炸整批
                    print(f"  ✗ {group} r{run} t{task_idx:02d}: {type(exc).__name__}: "
                          f"{str(exc)[:120]}", flush=True)
                    with out_path.open("a", encoding="utf-8") as f:
                        f.write(json.dumps({"exp": "E1", "group": group, "run": run,
                                            "task_idx": task_idx, "model": a.model,
                                            "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                                            "ts": datetime.now().isoformat(timespec="seconds")},
                                           ensure_ascii=False) + "\n")
    finally:
        if sess is not None:
            sess.close()
    print(f"记录 → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
