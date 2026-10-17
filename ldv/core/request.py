# -*- coding: utf-8 -*-
"""方向请求（Direction Request）—— 系统**只提问、不回答**（2026-10-17）。

## 它在边界内的哪一侧

    §5：什么算一个方向 → **外生，必须人声明，不能自我优化**

本模块做的事：**发现「缺维度」并把证据递给人**。
它不定义方向、不加键、不改结构 —— 加不加、加什么，由人写进
`<语料>/directions.json` 的 `已批准` / `已拒绝` 段。

    ✗ 不请求「新方向函数」—— 那要人写代码，门槛高且撞外生线
    ✗ 不写「优先级 / 严重度 / 置信度」—— 那是打分（`B12` 会扫源码）
    ✓ 请求的是「一个新维度」（一个 front matter 字段，或一个新度量量）

## 信号 ①：`B19` 连续 N 次展开没变细

复用 `checks/coverage.py` 的 `progress_profile` —— 与 `B19` 判据**同一口径**，
不另造一套「连续」算法（那会出现「请求说触发、判据说没触发」的分歧）。

## 为什么「连续」只在会话内

「连续 10 次」如果跨运行累积，请求就**依赖你跑过多少次** ⇒ 破坏可复现。
⇒ 有意收紧：宁可少触发，不可让请求依赖历史。

## 判据（`run_tests.test_direction_request`）

    ㊀ 只读 · ㊁ 可复现 · ㊂ 每条带证据 · ㊃ 不重复请求
    ㊄ 人写段不被覆盖 · ㊅ 不触发时也要说
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from ..checks.coverage import progress_profile

#: 触发阈值取自 `progress_profile` 的 `N`（外生，照 SP-GiST 前例 `longValuesOK`）
DEFAULT_N = 10


def _longest_stuck(kernel: Any, cover: Callable[[Any], frozenset[str]]
                   ) -> tuple[int, list[str]]:
    """自算「没变细」的最长链，返回 `(长度, 链上方向 did)`。

    ⚠️ 为什么要自己算链：`progress_profile` 只给**长度**与**哪些方向**没变细，
       证据包需要**链**（到底是哪条路一直卡住）。判据 ㊁ 会拿这里的长度与
       `progress_profile` 的读数**对照**，保证两边不分歧。
    """
    def stuck(d) -> bool:
        kids = kernel.children_of(d)
        if not kids:
            return False                     # 叶会打断链（B19 的规矩）
        parent = cover(d.payload)          # ⚠️ cover 吃的是 **payload**，不是 Direction
        return not any(cover(k.payload) < parent for k in kids)

    best = (0, [])
    for d in kernel.all_directions():
        if d.parent:                         # 只看链首（无父的）
            continue
        chain, cur = [], d
        # ⚠️ 链只含**没变细**的方向（**不含叶**）—— 与 `progress_profile` 的
        #    `chain(flat)` 同一口径。第一版把叶也 append 进去，导致我算 12、
        #    B19 算 11，两边对不上。
        while stuck(cur):
            chain.append(cur.did)
            cur = kernel.children_of(cur)[0]
        if len(chain) > best[0]:
            best = (len(chain), chain)
    return best


def direction_requests(kernel: Any, cover: Callable[[Any], frozenset[str]],
                       used_preds: Sequence[Any] = (),
                       n: int = DEFAULT_N) -> dict[str, Any]:
    """生成方向请求（**只读**，不改结构）。

    返回 `{"请求": [...], "本趟查了": {...}, "说明": "..."}`。
    - 未触发 ⇒ `请求` 为空，但 `本趟查了` 仍要说清查了多少（判据 ㊅）
    """
    prof = progress_profile(kernel, cover)
    N = int(prof.get("N", n))
    streak_prof = int(prof.get("最长没变细连续段", 0))
    length, chain = _longest_stuck(kernel, cover)
    checked = {"展开方向数": prof.get("已展开方向数"),
               "没变细次数": prof.get("没变细展开"),
               "最长连续段": streak_prof, "阈值N": N}

    if streak_prof < N or not chain:
        return {"请求": [], "本趟查了": checked,
                "说明": f"未触发（最长连续段 {streak_prof} < N={N}）—— "
                        f"这不是「没查」，是「查了没发现」"}

    # ── 证据包 ────────────────────────────────────────────────────────
    stuck = [d for d in kernel.all_directions() if d.did in chain]
    items = sorted({i for d in stuck for i in kernel.members_of(d)})
    keys_now: set[str] = set()
    for d in stuck:
        for i in kernel.members_of(d):
            keys_now |= set(kernel.item_keys(i) if hasattr(kernel, "item_keys") else [])
    return {
        "请求": [{
            "信号": f"B19：连续 {streak_prof} 次展开没变细",
            "方向": chain[-1],
            "链": chain,
            "阈值": N,
            "涉及的项": items,
            "涉及的项数": len(items),
            # ★ `已经试过`：含**谓词推导**的结果（2026-10-17 用户拍板「要」）
            #   —— 没有它，人会以为「你不是已经有谓词推导了吗」
            "已经试过": sorted({str(p.谓词) for p in used_preds}),
            "当前状态": (f"这 {len(items)} 个项在当前键集下**无法二分**："
                        f"沿 {chain[0]}→{chain[-1]} 的任何一次展开，"
                        f"都没有让覆盖严格变小"),
            "请求": ("需要一个能切开这批项的**新维度**"
                     "（一个 front matter 字段，或一个新度量量）"),
            "判据编号": "B19",
        }],
        "本趟查了": checked,
        "说明": f"已触发（最长连续段 {streak_prof} ≥ N={N}）—— "
                f"人拍板后写进 `directions.json` 的 `已批准` / `已拒绝`",
    }


# ── 登记文件（`directions.json`：人写的段，程序重写不覆盖）───────────
def write_requests(corpus_dir: Path, req: dict[str, Any],
                   keep: Sequence[str] = ("已批准", "已拒绝")) -> Path:
    """把请求写进 `<语料>/directions.json`，**保留人写的段**。"""
    path = Path(corpus_dir) / "directions.json"
    old: dict[str, Any] = {}
    if path.is_file():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            old = {}
    doc = {
        "_note": [
            "方向请求：系统因需要或矛盾**向人请求**新维度（`docs/谓词推导与反思层.md` §12）。",
            "",
            "三段：`请求`（系统生成）· `已批准` / `已拒绝`（**人写**）。",
            "⚠️ 系统只产证据，**不加方向、不改结构、不提优先级**。",
            "⚠️ 拒绝也必须留痕 —— 否则同一请求每次都冒，人会把系统关掉。",
            "⚠️ 本文件由 `ldv/core/request.py` 生成；人写的两段会被原样保留。",
        ],
        "请求": req.get("请求", []),
        "本趟查了": req.get("本趟查了", {}),
        "说明": req.get("说明", ""),
    }
    for k in keep:
        if k in old:
            doc[k] = old[k]
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def already_handled(corpus_dir: Path, items: Sequence[str]) -> bool:
    """这批项是不是**已经被处置过**（批准或拒绝过）—— 用来防重复请求。"""
    path = Path(corpus_dir) / "directions.json"
    if not path.is_file():
        return False
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    done: set[str] = set()
    for seg in ("已批准", "已拒绝"):
        for row in doc.get(seg, []) or []:
            done |= {str(x) for x in (row.get("涉及的项") or [])}
    return bool(set(items) & done)
