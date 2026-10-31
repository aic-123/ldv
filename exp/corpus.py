# -*- coding: utf-8 -*-
"""材料生成器：**事实来自公开语料**（OpenAlex 切片，CC0），**矛盾与假阳性由脚本注入**。

为什么要这样分工：
- 只用自造句子 ⇒ 会被说「语料偏向你的方法」；只用真实语料 ⇒ 没有 ground truth。
- 所以：**每条事实是真的**（标题/年份/期刊都来自 OpenAlex 节点），
  只在「同一条事实被第二次陈述」时改掉年份 —— 改动位置与真假都由注入表决定。

三种重复实体（这是本实验能算 precision 的前提）：

    conflict   同一篇论文，年份被写成两个不同的值   ← **ground truth = 矛盾**
    trap       同一篇论文，年份被重复成同一个值     ← 假阳性陷阱：只按「标题重复」判就会误报

⚠️ 第二处陈述**换句式**（不是复制第一句）：`于 2015 年发表` vs `的发表年份记为 2015`。
   不换句式的话，模型只要做字符串去重就能全对 —— 那测的就不是「读」，是「找重复行」。
"""
from __future__ import annotations

import random
import re
import sys
from pathlib import Path

#: `exp/` 在工作区根，仓库在 `../repo` —— 实验脚本不进仓库（仓库有自己的证据纪律）
REPO = Path(__file__).resolve().parents[1] / "repo"
sys.path.insert(0, str(REPO))

CORPUS = REPO / "_data" / "openalex-citations"

#: 三种陈述句式（第一句用 0/1，第二处用 2/3 —— 刻意不同）
T1 = "〔{sid}〕论文《{title}》于 {year} 年发表在 {venue}。"
T2 = "〔{sid}〕{year} 年，{venue} 刊出了《{title}》。"
T3 = "〔{sid}〕《{title}》的发表年份记为 {year}，出处是 {venue}。"
T4 = "〔{sid}〕关于《{title}》，{venue} 给出的年份是 {year}。"


def load_papers(n: int, *, min_year: int = 1990) -> list[dict]:
    """从 OpenAlex 切片读 n 篇论文（标题/年份/期刊）。**真实数据，不编。**"""
    from ldv.corpus.loader import load_nodes

    nodes = load_nodes(CORPUS / "nodes")
    out: list[dict] = []
    for nid in sorted(nodes):
        n_ = nodes[nid]
        title = str(n_.fields.get("title") or "").strip()
        scope = str(n_.fields.get("scope") or "")
        m = re.search(r"发表年\s*(\d{4})", scope)
        if not title or not m:
            continue
        year = int(m.group(1))
        if year < min_year:
            continue
        venue = str(n_.fields.get("notes") or "某刊").strip() or "某刊"
        # 标题截短：句子长度要接近，否则「长句更容易被注意到」会混进结果
        title = title[:56].rstrip(" :;,.") + ("…" if len(title) > 56 else "")
        out.append({"id": nid, "title": title, "year": year, "venue": venue[:28]})
    # ★ **按年份分层轮转**：OpenAlex 切片是按 id 排的，年份成簇（连着一百条都是 2015）
    #   ⇒ 直接取前 n 条会让「年份几乎全是 2015」，于是"找出不一样的那个"变成找众数，
    #   模型不用读也能猜。分层之后年份铺开，才真的是「逐条比对」。
    by_year: dict[int, list[dict]] = {}
    for p_ in out:
        by_year.setdefault(p_["year"], []).append(p_)
    picked: list[dict] = []
    years = sorted(by_year)
    i = 0
    while len(picked) < n and any(by_year[y] for y in years):
        y = years[i % len(years)]
        if by_year[y]:
            picked.append(by_year[y].pop(0))
        i += 1
    return picked[:n]


def make(papers: list[dict], k: int, *, traps: int = 20, seed: int = 7,
         min_gap: int | None = None, attempts: int = 25) -> dict:
    """`build` 的**重试版**：注入位置的间距不满足就换 seed 重来（最多 `attempts` 次）。

    为什么要间距：B 组按 1200 字符切块，两处陈述**落在同一块**的话，
    它一次就能同时看到、矛盾变得毫无难度 —— 那测的就不是检索窗口了。
    """
    last = None
    for i in range(attempts):
        try:
            return build(papers, k, traps=traps, seed=seed + i, min_gap=min_gap)
        except RuntimeError as exc:            # 只有「间距过小」会被重试
            last = exc
    raise RuntimeError(f"换了 {attempts} 个 seed 都没摆开：{last}")


def build(papers: list[dict], k: int, *, traps: int = 20, seed: int = 7,
          min_gap: int | None = None) -> dict:
    """生成材料。返回 `{text, facts, truth, traps, stats}`。

    `truth` 每条 = `{"entity", "a", "b", "year_a", "year_b"}`（a/b 是句 id）。

    放置规则（**先占位、再插句**）：`a` 只从「后面还留得下 `min_gap`」的位置选，
    `b` 从 `[a+min_gap, n)` 里选没被占的。这样不再依赖 seed 撞运气。
    """
    rnd = random.Random(seed)
    n = len(papers)
    m = k + traps
    if min_gap is None:
        min_gap = max(20, n // 6)
    if n - min_gap < m:
        raise ValueError(f"论文太少（{n}）：留不出 {m} 处、间距 {min_gap}")

    # ① 纯事实（第一处陈述）
    facts = []
    for i, p in enumerate(papers):
        t = T1 if i % 2 == 0 else T2
        facts.append({"sid": f"S{i:04d}", "entity": p["id"], "attr": "year",
                      "value": p["year"], "text": t.format(sid=f"S{i:04d}", **p),
                      "role": "plain"})

    # ② 占位：a 在前段、b 在后段且离 a 至少 min_gap
    a_pool = list(range(n - min_gap))
    rnd.shuffle(a_pool)
    taken: set[int] = set()
    slots: list[tuple[int, int, str, dict]] = []      # (a_pos, b_pos, role, paper)
    role_of = ["conflict"] * k + ["trap"] * traps
    rnd.shuffle(role_of)
    for role in role_of:
        placed = False
        for a_pos in a_pool:
            if a_pos in taken:
                continue
            cand = [x for x in range(a_pos + min_gap, n) if x not in taken]
            if not cand:
                continue
            b_pos = rnd.choice(cand)
            taken.add(a_pos)
            taken.add(b_pos)
            slots.append((a_pos, b_pos, role, papers[a_pos]))
            placed = True
            break
        if not placed:
            raise RuntimeError(f"摆不下第 {len(slots) + 1} 处（间距 {min_gap}）")

    # ③ 生成第二处陈述，并插到 b_pos 之后
    extra_at: dict[int, dict] = {}
    truth: list[dict] = []
    trap_list: list[dict] = []
    for j, (a_pos, b_pos, role, p) in enumerate(slots):
        sid = f"S{n + j:04d}"
        if role == "conflict":
            new_year = p["year"] + rnd.choice([-3, -2, -1, 1, 2, 4])
            p2 = dict(p, year=new_year)
            text = T3.format(sid=sid, **p2)
            truth.append({"entity": p["id"], "a": f"S{a_pos:04d}", "b": sid,
                          "year_a": p["year"], "year_b": new_year})
            val = new_year
        else:
            text = T4.format(sid=sid, **p)
            trap_list.append({"entity": p["id"], "a": f"S{a_pos:04d}", "b": sid,
                              "year": p["year"]})
            val = p["year"]
        extra_at[b_pos] = {"sid": sid, "entity": p["id"], "attr": "year",
                           "value": val, "text": text, "role": role}

    ordered: list[dict] = []
    for i, f in enumerate(facts):
        ordered.append(f)
        if i in extra_at:
            ordered.append(extra_at[i])

    pos_of = {f["sid"]: i for i, f in enumerate(ordered)}
    gaps = [abs(pos_of[t["b"]] - pos_of[t["a"]]) for t in truth]
    text = "\n".join(f["text"] for f in ordered)
    return {"text": text, "facts": ordered, "truth": truth, "traps": trap_list,
            "stats": {"n_facts": len(ordered), "k": k, "traps": len(trap_list),
                      "chars": len(text), "min_gap": min(gaps) if gaps else 0,
                      "max_gap": max(gaps) if gaps else 0}}


def chunks(text: str, size: int = 1200, overlap: int = 120) -> list[dict]:
    """B 组用：滑窗切块（按字符，按句子边界尽量对齐）。"""
    facts = [ln for ln in text.splitlines() if ln.strip()]
    out: list[dict] = []
    buf: list[str] = []
    n = 0
    for ln in facts:
        buf.append(ln)
        n += len(ln)
        if n >= size:
            out.append({"id": f"C{len(out):04d}", "text": "\n".join(buf)})
            buf = buf[-2:] if overlap else []
            n = sum(len(x) for x in buf)
    if buf:
        out.append({"id": f"C{len(out):04d}", "text": "\n".join(buf)})
    return out
