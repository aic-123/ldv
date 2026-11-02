#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验三：信念变更追溯（预注册见 PREREG3.md，先于任何 API 调用）。

    python exp/exp3.py --ns 5,15 --seeds 1        # 试点
    python exp/exp3.py --ns 5,15,40 --seeds 1,2   # 全量

一条可追溯的修正：论文 W? 最初登记 2015（2026-10-01）→ 更正为 2016（2026-10-20，勘误）。
三问：现在 / 最初 / 何时何因。三问分开计分。
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
import corpus   # noqa: E402
import ds       # noqa: E402

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"

ORIG_YEAR, FIX_YEAR = 2015, 2016
ORIG_DATE, FIX_DATE = "2026-10-01", "2026-10-20"
FIX_REASON = "出版社勘误"

FINAL_Q = (
    "现在回答三个问题，只输出 JSON（不要解释）：\n"
    '{"now": <年份数字>, "original": <年份数字>, '
    '"changed": {"when": <日期，格式 YYYY-MM-DD，不知道填 null>, '
    '"why": <一句话原因，不知道填 null>}}\n'
    "1) 你现在认为这篇论文的年份是什么？\n"
    "2) 最初登记的年份是什么？\n"
    "3) 是什么时候、因为什么改的？"
)


def _fmt(papers: list[dict], lo: int, hi: int) -> str:
    """把 OpenAlex 事实排成登记行（与实验一同款句式）。"""
    lines = []
    for i, p in enumerate(papers[lo:hi]):
        lines.append(f"〔B{i + lo:04d}〕《{p['title'][:56]}》发表于 {p['year']} 年，"
                     f"载于 {p['venue']}。")
    return "\n".join(lines)


def build_conversation(papers: list[dict], *, wid_seed: int, n_fill: int,
                       batch: int = 300) -> tuple[list[dict], dict]:
    """A 组会话：登记 → 确认 → 更正 → N 轮灌水 → 三问。

    返回 (消息列表, 真值)。`wid` 从材料里挑一条真实论文，其原始年份
    **必须不是** 2015/2016（否则灌水里的记录会与剧情冲突）。
    """
    cand = [p for p in papers if p["year"] not in (ORIG_YEAR, FIX_YEAR)]
    p = cand[wid_seed % len(cand)]
    wid = p["id"]
    truth = {"now": FIX_YEAR, "original": ORIG_YEAR,
             "when": FIX_DATE, "why": FIX_REASON, "wid": wid}

    filler1 = _fmt(papers, 0, min(batch, len(papers) - 40))
    msgs: list[dict] = [
        {"role": "system",
         "content": "你是登记助手。用户会分批给你文献登记记录，如实记住即可。"},
        {"role": "user",
         "content": f"下面是一批文献登记记录：\n\n{filler1}\n\n"
                    f"另外有一条**单独登记**：论文 {wid}《{p['title'][:40]}》"
                    f"最初登记的年份是 {ORIG_YEAR} 年（登记号 R0，"
                    f"登记时间 {ORIG_DATE}，来源：初录）。"
                    "请确认你记住了这条单独登记，只回复：已登记，年份是 "
                    f"{ORIG_YEAR}。"},
    ]
    acks = [(0, ORIG_YEAR)]
    return msgs, truth, acks, wid, p


def _chat(msgs, *, max_tokens: int) -> dict:
    """带节奏的调用：隔 2s、重试 6 次（上次 429 是**频率限制**，连轰会再撞）。"""
    time.sleep(2.0)
    return ds.chat(msgs, max_tokens=max_tokens, temperature=0, tries=6)


def group_A(papers: list[dict], *, n_fill: int, seed: int,
            max_tokens: int = 2000, bury: str = "early", per: int = 60) -> dict:
    """跑一次 A 组（一条会话）。返回三问判分与用量。

    `bury`：更正放哪。`early` = 预注册协议（更正 ≈ 上下文 3% 深度，首因区）；
    `middle` = 事后探索（⚠️ 非预注册项）：一半灌水在更正前，更正落在 ≈50% 深度，
    直接检验「滑出窗口 / lost-in-the-middle」。结果单独标注，不与预注册混报。
    """
    t0 = time.perf_counter()
    msgs, truth, acks, wid, p = build_conversation(papers, wid_seed=seed,
                                                   n_fill=n_fill)

    # ── 第 1 步：登记确认（模型答错 ⇒ 该 seed 作废，预注册 §3）────────
    r = _chat(msgs, max_tokens=64)
    if str(ORIG_YEAR) not in r["text"]:
        return {"invalid": True, "why": f"登记确认没答 {ORIG_YEAR}: {r['text'][:60]!r}"}
    msgs.append({"role": "assistant", "content": r["text"]})

    # ── 更正 + 灌水：`bury=middle` 时一半灌水在更正**前**（≈50% 深度）──
    # ⚠️ bury=middle 是**事后探索**（预注册协议是 early = ≈3% 深度首因区）。
    # per 由 CLI 传入（60 ⇒ N=40 ≈ 73k token；300 ⇒ ≈ 400k token）
    n_half = n_fill // 2 if bury == "middle" else 0

    def _batch(k: int) -> dict | None:
        lo, hi = 300 + k * per, min(300 + (k + 1) * per, len(papers))
        if lo >= hi:
            return None
        return {"role": "user",
                "content": f"补充登记批次 {k + 1}：\n\n" + _fmt(papers, lo, hi) +
                           "\n\n（无需复述，只回复：收到。）"}

    for k in range(n_half):
        b = _batch(k)
        if b is None:
            break
        msgs.append(b)
        r3 = _chat(msgs, max_tokens=32)
        msgs.append({"role": "assistant", "content": r3["text"]})

    msgs.append({"role": "user",
                 "content": f"更正：论文 {wid} 的年份应为 {FIX_YEAR} 年。"
                            f"原因：{FIX_REASON}（更正时间 {FIX_DATE}，"
                            f"原登记 {ORIG_YEAR} 有误）。"
                            f"只回复：已更正，年份是 {FIX_YEAR}。"})
    r2 = _chat(msgs, max_tokens=64)
    if str(FIX_YEAR) not in r2["text"]:
        return {"invalid": True,
                "why": f"更正确认没答 {FIX_YEAR}: {r2['text'][:60]!r}"}
    msgs.append({"role": "assistant", "content": r2["text"]})

    for k in range(n_half, n_fill):
        b = _batch(k)
        if b is None:
            break
        msgs.append(b)
        r3 = _chat(msgs, max_tokens=32)
        msgs.append({"role": "assistant", "content": r3["text"]})

    # ── 第 4 步：三问（一次调用，重放全部会话）───────────────────────
    n_msgs = len(msgs)
    msgs.append({"role": "user", "content": FINAL_Q})
    rf = _chat(msgs, max_tokens=max_tokens)
    text = rf["text"]
    try:
        obj = json.loads(re.search(r"\{.*\}", text, re.DOTALL).group(0))
    except Exception:
        obj = {}

    def _yr(v):
        m = re.search(r"(20\d\d)", str(v))
        return int(m.group(1)) if m else None

    got = {"now": _yr(obj.get("now")), "original": _yr(obj.get("original")),
           "when": str(obj.get("changed", {}).get("when") or ""),
           "why": str(obj.get("changed", {}).get("why") or "")}
    score = {
        "q1_now": got["now"] == FIX_YEAR,
        "q2_original": got["original"] == ORIG_YEAR,
        "q3_when": FIX_DATE in got["when"],
        "q3_why": FIX_REASON in got["why"],
    }
    return {"invalid": False, "score": score, "got": got, "truth": truth,
            "n_msgs": n_msgs, "finish": rf["finish"], "ms": rf["ms"],
            "usage": rf["usage"], "wall_s": round(time.perf_counter() - t0, 1),
            "final_input_tokens": rf["usage"].get("prompt_tokens")}


# ── B 组：RAG（两个变体）─────────────────────────────────────────────
def _chunks(text: str, size: int = 1200):
    return [text[i:i + size] for i in range(0, len(text), size)]


def _retrieve(chunks: list[str], q: str, k: int = 8) -> list[str]:
    """字面检索（与实验一 B 组同法：字符 bigram 重叠）。"""
    def bg(s):
        s = re.sub(r"\s", "", s)
        return {s[i:i + 2] for i in range(len(s) - 1)}
    qb = bg(q)
    scored = sorted(range(len(chunks)),
                    key=lambda i: -len(qb & bg(chunks[i])))
    return [chunks[i] for i in scored[:k]]


def group_B(papers: list[dict], *, seed: int, mode: str) -> dict:
    """B 组：overwrite = 就地改写原块；append = 新旧并存（对 B 最有利）。"""
    p = [q for q in papers if q["year"] not in (ORIG_YEAR, FIX_YEAR)][seed % len(papers)]
    wid = p["id"]
    base = _fmt(papers, 0, min(3000, len(papers)))
    orig_line = (f"单独登记：论文 {wid}《{p['title'][:40]}》最初登记的年份是 "
                 f"{ORIG_YEAR} 年（登记号 R0，登记时间 {ORIG_DATE}，来源：初录）。")
    fix_line = (f"更正：论文 {wid} 的年份应为 {FIX_YEAR} 年（更正时间 {FIX_DATE}，"
                f"原因：{FIX_REASON}，原登记 {ORIG_YEAR} 有误）。")
    if mode == "overwrite":
        # 行业常规：文档就地更新 ⇒ 旧年份那行被替换掉
        base2 = base.replace(
            f"《{p['title'][:56]}》发表于 {p['year']} 年",
            f"《{p['title'][:56]}》发表于 {p['year']} 年")  # 灌水里没有 wid 的行
        body = base2 + "\n" + orig_line.replace(str(ORIG_YEAR), str(FIX_YEAR)) \
                                       .replace("最初登记的年份是", "登记的年份是") \
                                       .replace("初录", "初录（后经勘误）")
        body = body + "\n" + fix_line
    else:
        body = base + "\n" + orig_line + "\n" + fix_line
    chunks = _chunks(body)
    out = {"mode": mode, "wid": wid}
    qs = {"q1_now": f"论文 {wid} 的年份现在应该是多少？只答 JSON：{{\"now\": <年份>}}",
          "q2_original": f"论文 {wid} 最初登记的年份是多少？只答 JSON：{{\"original\": <年份>}}",
          "q3": (f"论文 {wid} 的年份是什么时候、因为什么改的？"
                 "只答 JSON：{\"when\": <日期或 null>, \"why\": <原因或 null>}")}
    score = {}
    for name, q in qs.items():
        ctx = "\n".join(_retrieve(chunks, q))
        r = ds.chat([{"role": "user",
                      "content": f"资料：\n{ctx}\n\n问题：{q}"}],
                    max_tokens=300, temperature=0)
        try:
            o = json.loads(re.search(r"\{.*\}", r["text"], re.DOTALL).group(0))
        except Exception:
            o = {}
        if name == "q1_now":
            m = re.search(r"(20\d\d)", str(o.get("now")))
            score[name] = (m and int(m.group(1)) == FIX_YEAR)
        elif name == "q2_original":
            m = re.search(r"(20\d\d)", str(o.get("original")))
            score[name] = (m and int(m.group(1)) == ORIG_YEAR)
        else:
            score["q3_when"] = FIX_DATE in str(o.get("when") or "")
            score["q3_why"] = FIX_REASON in str(o.get("why") or "")
        out.setdefault("usage", []).append(r["usage"])
    out["score"] = score
    return out


# ── C 组：ldv（机制作答，0 token）────────────────────────────────────
#: ⚠️ 不复用 `ldv_group.NODE`（它的 evidence_status 写死「派生子」）——
#: 实验三的角色本身就是答案（原始记录 / 勘误），必须逐条可写。
NODE = """---
id: "{sid}"
type: "事实"
title: "{sid}"
evidence_status: "{role}"
relations: []
---

{sid} 的登记原文：{text}
"""

SIDECAR_META = {
    "what": "实验三：把实体/年份/时间升成键（键 = `{field}=值`）",
    "why": "三问都要能从**记录**读出，而不是靠模型记忆 —— 这是「能力有无」的本体",
    "derived_from": "实验三剧情（R0 初录 / R1 勘误）",
    "generated_by": "exp/exp3.py",
}
def group_C(papers: list[dict], root: Path, *, seed: int) -> dict:
    p = [q for q in papers if q["year"] not in (ORIG_YEAR, FIX_YEAR)][seed % len(papers)]
    tenant = f"exp3-s{seed}"
    t0 = time.perf_counter()
    nd = root / tenant / "nodes"
    nd.mkdir(parents=True, exist_ok=True)
    dd = root / tenant / "derived"
    dd.mkdir(parents=True, exist_ok=True)
    facts = [
        {"sid": "R0", "entity": p["id"], "value": ORIG_YEAR, "role": "原始记录",
         "text": (f"R0 单独登记：论文 {p['id']}《{p['title'][:40]}》最初登记的年份是 "
                  f"{ORIG_YEAR} 年（登记时间 {ORIG_DATE}，来源：初录）。"),
         "ts": ORIG_DATE},
        {"sid": "R1", "entity": p["id"], "value": FIX_YEAR, "role": "勘误",
         "text": (f"R1 更正：论文 {p['id']} 的年份应为 {FIX_YEAR} 年"
                  f"（更正时间 {FIX_DATE}，原因：{FIX_REASON}，原登记 {ORIG_YEAR} 有误）。"),
         "ts": FIX_DATE},
    ]
    for f in facts:
        (nd / f"{f['sid']}.md").write_text(NODE.format(
            sid=f["sid"], role=f["role"], text=f["text"]), encoding="utf-8")
    vals_e = {f["sid"]: str(f["entity"]) for f in facts}
    vals_y = {f["sid"]: str(f["value"]) for f in facts}
    vals_t = {f["sid"]: str(f["ts"]) for f in facts}
    for key, vals in (("entity", vals_e), ("year", vals_y), ("ts", vals_t)):
        (dd / f"{key}.json").write_text(json.dumps(
            {"_meta": dict(SIDECAR_META, field=key), "values": vals},
            ensure_ascii=False), encoding="utf-8")
    import entry
    app = entry.App(root=nd)
    out = app.query({"plugin": "keyset", "require": [f"entity={p['id']}"],
                     "forbid": []})
    # ⚠️ `nodes` 里是**分支**（did=D1… 是分支号，must 是分支的键条件）；
    #    真正的节点 id 在顶层 `answer_ids`（R0/R1）—— 第一版拿分支号去查
    #    `keys_of`，查出来全是 None，四问全 False。这个形状差异记下来。
    ids = sorted(out.get("answer_ids") or [])
    role_of, ts_of = {}, {}
    for nid in ids:
        ks = app.keys_of.get(nid) or ()
        for k in ks:
            if k.startswith("evidence_status="):
                role_of[nid] = k.split("=", 1)[1]
            if k.startswith("ts="):
                ts_of[nid] = k.split("=", 1)[1]
    fix_ids = [n for n in ids if role_of.get(n) == "勘误"]
    orig_ids = [n for n in ids if role_of.get(n) == "原始记录"]
    score = {"q1_now": bool(fix_ids), "q2_original": bool(orig_ids),
             "q3_when": bool(fix_ids and ts_of.get(fix_ids[0]) == FIX_DATE),
             "q3_why": bool(fix_ids and FIX_REASON in
                            (nd / f"{fix_ids[0]}.md").read_text(encoding="utf-8"))}
    return {"score": score, "ids": ids, "queries": len(ids),
            "wall_s": round(time.perf_counter() - t0, 1), "tokens": 0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--papers", type=int, default=4000)
    ap.add_argument("--ns", default="5,15,40")
    ap.add_argument("--seeds", default="1")
    ap.add_argument("--groups", default="AC")
    ap.add_argument("--max-tokens", type=int, default=2000)
    ap.add_argument("--extend-to", type=int, default=0,
                    help="记录不足时用合成干草堆补齐到此数（事后扩展，单独标注）")
    ap.add_argument("--per", type=int, default=60,
                    help="每轮灌水的记录条数（60 ⇒ N=40 时约 73k token；300 ⇒ 约 400k）")
    ap.add_argument("--bury", default="early", choices=("early", "middle"),
                    help="更正位置：early=预注册（约3%深度）；middle=事后探索（约50%）")
    a = ap.parse_args()

    papers = corpus.load_papers(a.papers)
    # ⚠️ `--extend-to`：真实切片不够长时，用**合成干草堆**补齐（循环真实标题 +
    #    唯一 id + 轮转年份）。被追踪的剧情（R0/R1）仍是真实论文 —— 与 RULER 的
    #    needle/haystack 同一做法；**事后扩展，结果单独标注**，不与预注册混报。
    if a.extend_to and len(papers) < a.extend_to:
        base = list(papers)
        i = 0
        while len(papers) < a.extend_to:
            q = dict(base[i % len(base)])
            q["id"] = f"T{i:07d}"
            q["year"] = 1980 + (i * 7) % 47
            papers.append(q)
            i += 1
        print(f"（合成干草堆：补到 {len(papers)} 条记录）")
    RUNS.mkdir(exist_ok=True)
    tag = datetime.now().strftime("run3-%Y%m%d-%H%M%S")
    out_path = RUNS / f"{tag}.jsonl"
    root = HERE.parent / "exp3_tenants"
    root.mkdir(exist_ok=True)

    for n in [int(x) for x in a.ns.split(",") if x.strip()]:
        for seed in [int(x) for x in a.seeds.split(",") if x.strip()]:
            rec = {"exp": 3, "n_fill": n, "seed": seed, "bury": a.bury,
                   "per": a.per, "at": tag}
            if "A" in a.groups:
                r = group_A(papers, n_fill=n, seed=seed, max_tokens=a.max_tokens,
                            bury=a.bury, per=a.per)
                rec["A"] = r
                if r.get("invalid"):
                    print(f"  A n={n} s={seed}: **作废** {r['why']}")
                else:
                    s = r["score"]
                    print(f"  A n={n} s={seed} bury={a.bury}: Q1={s['q1_now']} Q2={s['q2_original']} "
                          f"Q3w={s['q3_when']} Q3y={s['q3_why']}"
                          f"｜{r['n_msgs']} 消息｜末次输入 {r['final_input_tokens']} tok"
                          f"｜{r['wall_s']}s")
            if "B" in a.groups:
                for mode in ("overwrite", "append"):
                    r = group_B(papers, seed=seed, mode=mode)
                    rec[f"B_{mode}"] = r
                    s = r["score"]
                    print(f"  B({mode}) s={seed}: {s}")
            if "C" in a.groups:
                r = group_C(papers, root, seed=seed)
                rec["C"] = r
                s = r["score"]
                print(f"  C s={seed}: Q1={s['q1_now']} Q2={s['q2_original']} "
                      f"Q3w={s['q3_when']} Q3y={s['q3_why']}｜{r['queries']} 节点"
                      f"｜{r['wall_s']}s｜0 token")
            with out_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"记录 → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
