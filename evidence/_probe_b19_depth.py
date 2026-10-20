"""探针：`B19` 在真语料上到底会不会红？—— 设计文档 §7.7 的一条声称要实测。

设计文档 §7.7（line 1094）写着：

    ⚠️ **真语料上 `B19` 不会红**：树只有 7 层，够不到 N=10。

全量 3907 上 `B19` **红了**（最长连续段 **10** ≥ N=10）。
而 `progress_profile` 的 `walk` 是**沿根→叶路径**数连续段的 ⇒
「最长连续段 = 10」**蕴含**「树深度 ≥ 10」。

⇒ 那条声称给的**理由**（「树只有 7 层」）本身就是**尺度相关**的。
   本探针要量的是：**B19 能红，究竟是被什么决定的？**

    ① 树的最大深度（根→叶边数）—— 各语料
    ② 深度分布
    ③ 最长无进步连续段的那条**路径**，逐跳打出 `细化量` 与两个子的覆盖大小
    ④ **把「无进步」拆成两类**（这是关键）：
         **真空转**   所有子 k 的 |覆盖(k)| 都 == |覆盖(父)|
                      ⇒ 这次展开**一个项都没分开**，插件在**空转**
         **不均衡切** 至少有一个子严格更小，只是**最大的那个**子没变小
                      ⇒ 它**分开了东西**，只是分得不均匀
       `细化量 = |覆盖(父)| − max_k |覆盖(子)|` ⇒ 两类都 ≤ 0 ⇒ **同一个记号**。
       若全量上那 1515 次「无进步」绝大多数是**不均衡切**，
       那 `B19` 红的就不是「空转」，而是「切得不匀」—— 两件不同的事。

跑法（两个语料各一遍）：

    cd repo
    LDV_CORPUS=openalex-small      python ../outputs/_probe_b19_depth.py
    LDV_CORPUS=openalex-citations  python ../outputs/_probe_b19_depth.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "repo"))

from ldv.checks._fixtures import coverage_of, load, make_builder  # noqa: E402


def depths(kernel) -> dict[str, int]:
    out: dict[str, int] = {}

    def walk(d, dep: int) -> None:
        out[d.did] = dep
        for c in kernel.children_of(d):
            walk(c, dep + 1)

    walk(kernel.root, 0)
    return out


def hops(kernel, cover) -> dict[str, dict]:
    """每个**有子**的方向一行：父覆盖 / 最大子覆盖 / 最小子覆盖 / 细化量 / 子数。"""
    rows: dict[str, dict] = {}
    for d in kernel.all_directions():
        kids = kernel.children_of(d)
        if not kids:
            continue
        cp = len(cover(d.payload))
        cs = [len(cover(k.payload)) for k in kids]
        rows[d.did] = {
            "父": cp,
            "max子": max(cs),
            "min子": min(cs),
            "细化量": cp - max(cs),
            "子数": len(cs),
        }
    return rows


def classify(row: dict) -> str:
    """把「无进步」拆成两类 —— 这才是本探针要分开的东西。"""
    if row["细化量"] > 0:
        return "进步"
    if row["min子"] == row["父"]:
        return "真空转"          # 每个子都还是全集 ⇒ 一个项都没分开
    return "不均衡切"            # 有子严格更小 ⇒ 分开了东西，只是最大的没变小


def longest_chain(kernel, rows: dict[str, dict], pred) -> tuple[int, list[str], list[str]]:
    """沿根→叶找最长的「命中 pred」连续段，返回 (长度, 路径, 逐跳分类)。

    ⚠️ 连续段的计数必须**按 `pred`**，不能按 `classify` 的标签 ——
       两个谓词下同一个节点可能一个算命中、一个不算（这正是本探针要分开的东西）。
    """
    best = {"n": 0, "path": [], "kinds": []}

    def walk(d, path, kinds, run) -> None:
        r = rows.get(d.did)
        if r is None:                       # 叶：链到此为止
            return
        if pred(r):
            path, kinds, run = path + [d.did], kinds + [classify(r)], run + 1
            if run > best["n"]:
                best.update(n=run, path=list(path), kinds=list(kinds))
        else:
            path, kinds, run = [d.did], [classify(r)], 0
        for c in kernel.children_of(d):
            walk(c, path, kinds, run)

    walk(kernel.root, [], [], 0)
    return best["n"], best["path"], best["kinds"]


#: 两种「无进步」谓词 —— 本探针要分开的**就是这两个**
def PRED_NOW(r: dict) -> bool:
    """现定义：`细化量 = 父 − max子` ≤ 0。"""
    return r["细化量"] <= 0


def PRED_VACUOUS(r: dict) -> bool:
    """拟改定义：**所有**子覆盖都 == 父覆盖（`父 − min子` ≤ 0）⇒ 真·一个项都没分开。"""
    return r["min子"] == r["父"]


def main() -> int:
    name = os.environ.get("LDV_CORPUS", "(仓库内 36 项)")
    got = load()
    if got is None:
        print(f"语料不在：{name}")
        return 1
    nodes, edges, _universe = got

    from ldv.checks.coverage import PROGRESS_N, progress_profile

    print(f"=== 语料 {name}：{len(nodes)} 项 ===")

    build = make_builder("reach", nodes, edges)
    k, _plug = build(sorted(nodes))
    cover = coverage_of("reach", nodes, edges)

    dep = depths(k)
    mx = max(dep.values())
    hist: dict[int, int] = {}
    for v in dep.values():
        hist[v] = hist.get(v, 0) + 1

    print(f"  ① 最大深度（根→叶边数） = {mx}   ⇒ 层数 = {mx + 1}")
    print("  ② 深度分布 = " + "｜".join(f"{d}:{hist[d]}" for d in sorted(hist)))

    prof = progress_profile(k, cover)
    print(f"  ③ 已展开 {prof['已展开方向数']} 次｜无进步 {prof['无进步展开']} 次"
          f"｜最长连续段 {prof['最长无进步连续段']}（N={PROGRESS_N}）"
          f" ⇒ B19 = {'红' if prof['最长无进步连续段'] >= PROGRESS_N else '绿'}")

    rows = hops(k, cover)
    kinds: dict[str, int] = {}
    for r in rows.values():
        kinds[classify(r)] = kinds.get(classify(r), 0) + 1
    tot = sum(kinds.values())
    print(f"  ④ 把「无进步」拆开（{tot} 个内部方向）：")
    for kk in ("进步", "不均衡切", "真空转"):
        n = kinds.get(kk, 0)
        pct = (100.0 * n / tot) if tot else 0.0
        print(f"       {kk:6s} {n:5d}  ({pct:5.1f}%)")
    noadv = kinds.get("不均衡切", 0) + kinds.get("真空转", 0)
    if noadv:
        print(f"       ⇒ 无进步里「不均衡切」占 "
              f"{100.0 * kinds.get('不均衡切', 0) / noadv:5.1f}%")

    n, path, ks = longest_chain(k, rows, PRED_NOW)
    print(f"  ⑤ 最长无进步链（**现定义**：细化量 ≤ 0）= {n} 跳（路径 {len(path)} 个方向）")
    for i, (did, kk) in enumerate(zip(path, ks)):
        r = rows.get(did, {})
        tail = "  ← 根" if i == 0 else ""
        print(f"       [{i:2d}] {did:5s} {kk:5s} "
              f"父 {r.get('父', 0):4d} ｜ max子 {r.get('max子', 0):4d} "
              f"｜ min子 {r.get('min子', 0):4d} ｜ 子数 {r.get('子数', 0)}{tail}")

    m, path2, ks2 = longest_chain(k, rows, PRED_VACUOUS)
    print(f"  ⑥ 最长「真空转」链（**拟改定义**：所有子 == 父）= {m} 跳"
          f"（路径 {len(path2)} 个方向）")
    for i, (did, kk) in enumerate(zip(path2, ks2)):
        r = rows.get(did, {})
        tail = "  ← 根" if i == 0 else ""
        print(f"       [{i:2d}] {did:5s} {kk:5s} "
              f"父 {r.get('父', 0):4d} ｜ max子 {r.get('max子', 0):4d} "
              f"｜ min子 {r.get('min子', 0):4d} ｜ 子数 {r.get('子数', 0)}{tail}")

    print(f"  ⇒ B19 判决：现定义 {n} {'≥' if n >= PROGRESS_N else '<'} N={PROGRESS_N}"
          f" ⇒ **{'红' if n >= PROGRESS_N else '绿'}**"
          f" ｜ 拟改定义 {m} {'≥' if m >= PROGRESS_N else '<'} N={PROGRESS_N}"
          f" ⇒ **{'红' if m >= PROGRESS_N else '绿'}**")
    print(f"  ⇒ 深度 {mx} vs 最长无进步段 {n}："
          f"{'段 ≤ 深度（自洽）' if n <= mx else '★ 段 > 深度 ⇒ 我对连续段的理解错了'}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
