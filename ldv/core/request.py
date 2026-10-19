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


# ── ★ 闭环：把「已批准」的维度**真的用上**（2026-10-19）───────────────────
#
# 为什么需要这一段：在它之前，`directions.json` 的 `已批准` **只被用来防重复请求**
# （`already_handled`）—— 人批了，系统**不会真去做**。于是「报告 → 人拍板」是开环：
# 下一趟还是同一批项分不开，人以为自己批准过就好了，实际什么都没变。
#
# ⚠️ **红线不变**：系统**不自己**加维度、不猜、不提优先级。
#    这里只执行**人已经写进 `已批准`** 的那一条 —— 人在环里，系统照办。
#
# ⚠️ **默认不生效**：由调用方显式开启（`cli --apply-approved`）。
#    否则「批准一个字段」会**静默改变所有既有读数**（键集变大 ⇒ 方向数变多）。

def approved_dimensions(corpus_dir: Path) -> list[dict[str, Any]]:
    """读 `已批准` 段里**声明了新维度**的那些条目。

    条目格式（人写）：

        {"信号": "B19：…", "维度": "scope", "取值": null, "涉及的项": [...]}

    - `维度` 是 front matter 的**字段名**（`source.kind` 这种点路径也认）
    - `取值` 可选：`null` = 整个字段当维度；给了值 = 只把「等于这个值」的项标出来
    - 没写 `维度` 的条目**不算**（可能只是「同意这个诊断」，没指定用什么维度）
    """
    path = Path(corpus_dir) / "directions.json"
    if not path.is_file():
        return []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for row in doc.get("已批准", []) or []:
        if not isinstance(row, dict):
            continue
        dim = str(row.get("维度") or "").strip()
        if not dim:
            continue
        want = row.get("取值")
        out.append({"维度": dim,
                    "取值": None if want in (None, "") else str(want),
                    "信号": str(row.get("信号") or "")})
    return out


def approved_predicates(corpus_dir: Path) -> list[dict[str, Any]]:
    """读 `已批准` 里声明了**谓词定义**的那些条目。

    条目格式（人写，与 `predicates.json` 的 `基础谓词` **同一种形状**）：

        {"信号": "矛盾：边打架", "谓词": {"名": "……", "定义": ["出度档>2", "入度档>5"]}}

    ⚠️ 只认**形状完整**的（有名 + 有定义）—— 半条定义不猜。
    """
    path = Path(corpus_dir) / "directions.json"
    if not path.is_file():
        return []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for row in doc.get("已批准", []) or []:
        if not isinstance(row, dict):
            continue
        p = row.get("谓词")
        if isinstance(p, dict) and str(p.get("名") or "").strip() \
                and isinstance(p.get("定义"), list) and p.get("定义"):
            out.append({"名": str(p["名"]).strip(),
                        "定义": [str(x) for x in p["定义"]]})
    return out


def _field_of(fields: dict[str, Any], name: str) -> Any:
    """按点路径取值（`source.kind`）。取不到 ⇒ `None`。"""
    cur: Any = fields
    for part in name.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def apply_approved(nodes: dict[str, Any], corpus_dir: Path,
                   edges: dict[str, frozenset[str]] | None = None,
                   preds: Any = ()) -> tuple[dict[str, Any], dict[str, Any]]:
    """把 `已批准` 的维度**注入成键**（`维度=值`），返回 `(新 nodes, 账本)`。

    **纯函数**：不改入参，返回新的 `Node` 对象。

    账本三段，都**如实报**：

        生效     维度 / 取值 / 项数      —— 材料里真有这个字段
        未生效   维度 / 原因            —— **批准了但材料里没有**（不假装成功）
        生效项数 有多少项被加上了新键

    ⚠️ 「未生效」不是错误 —— 它可能是：批准的是**还没摄入**的字段（得先补材料），
       或者人写错了字段名。两种情况都要人看见，所以**必须报**，不能静默。

    ## 第二条路：批准一个**谓词**（2026-10-19）

    人在 `已批准` 里写 `{"谓词": {"名": …, "定义": ["…", "…"]}}` ⇒ 走
    `predicates.apply_basic`，给满足合取的节点加键 `名`。

    ⚠️ 需要 `edges` 与 `preds`（推导出的单谓词）—— 没有它们**不生效**，
       并记一条「未生效：没推导谓词」而不是假装成功。
    ⚠️ `apply_basic` **就地改** `Node.keys` ⇒ 这里先造一份**可安全改的副本**，
       保证函数仍是纯函数（判据 ㊘b）。
    """
    from dataclasses import replace

    dims = approved_dimensions(corpus_dir)
    pred_rows = approved_predicates(corpus_dir)
    ledger: dict[str, Any] = {"生效": [], "未生效": [], "生效项数": 0,
                              "已批准条目": len(dims), "批准的谓词": len(pred_rows)}
    # ⚠️ 两处都要查：只批准**谓词**（没有维度）时**不许**在这里返回 ——
    #    第一版就是 `if not dims: return` ⇒ 谓词路径永远走不到（判据 ㊠/㊡ 当场红）。
    if not dims and not pred_rows:
        return nodes, ledger

    # ① 先判「材料里到底有没有」—— 这一趟只看，不改
    for d in dims:
        hit = 0
        for n in nodes.values():
            v = _field_of(n.fields, d["维度"])
            if v is None or v == "":
                continue
            if d["取值"] is not None and str(v) != d["取值"]:
                continue
            hit += 1
        if hit == 0:
            ledger["未生效"].append({
                "维度": d["维度"], "取值": d["取值"],
                "原因": ("材料里没有这个字段（或没有取该值的项）"
                         "—— 批准了但**没生效**，不假装成功"),
            })
        else:
            ledger["生效"].append({"维度": d["维度"], "取值": d["取值"], "项数": hit})

    # ⚠️ **只在「既没有生效的维度、也没有批准的谓词」时才提前返回** ——
    #    第一版在这把谓词路径一起挡掉了（维度没生效就 return ⇒
    #    批准了谓词也永远不生效，判据 ㊠/㊡ 当场红）。
    if not ledger["生效"] and not approved_predicates(corpus_dir):
        return nodes, ledger

    # ② 真的加键（`维度=值`，与 `keys_of` 的枚举字段同一种形状）
    new: dict[str, Any] = {}
    for nid, n in nodes.items():
        extra: set[str] = set()
        for d in ledger["生效"]:
            v = _field_of(n.fields, d["维度"])
            if v is None or v == "":
                continue
            if d["取值"] is not None and str(v) != d["取值"]:
                continue
            extra.add(f"{d['维度']}={v}")
        if extra:
            new[nid] = replace(n, keys=n.keys | frozenset(extra))
            ledger["生效项数"] += 1
        else:
            new[nid] = n

    # ③ 第二条路：批准**谓词**（`predicates.apply_basic`）
    if pred_rows:
        if edges is None or not list(preds):
            ledger["未生效"].append({
                "维度": "（谓词）", "取值": [r["名"] for r in pred_rows],
                "原因": ("批准了谓词，但**没有 `edges` / 推导出的单谓词**"
                         "—— 先跑 `--predicates`（或让调用方把它们传进来）"),
            })
        else:
            from dataclasses import replace as _replace
            from ldv.corpus.predicates import apply_basic
            work = {nid: _replace(n) for nid, n in new.items()}   # 可安全就地改的副本
            touched = apply_basic(work, edges, pred_rows, list(preds))
            if touched == 0:
                ledger["未生效"].append({
                    "维度": "（谓词）", "取值": [r["名"] for r in pred_rows],
                    "原因": "**没有任何节点满足**这些谓词的合取 —— 定义对了但材料不对",
                })
            else:
                ledger["生效"].append({
                    "维度": "（谓词）", "取值": [r["名"] for r in pred_rows],
                    "项数": touched,
                })
                new = work
    return new, ledger


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
