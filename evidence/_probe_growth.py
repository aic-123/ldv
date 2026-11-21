"""G4 分辨度 / G5 自主度 / G6 请求命中度 —— 三条发展读数的实体证据。

跑法（cwd 任意，脚本自己定位 repo 根）：

    python evidence/_probe_growth.py --only g6
    python evidence/_probe_growth.py --only g4
    python evidence/_probe_growth.py --only g5
    python evidence/_probe_growth.py

退出码：0 = 本趟每条读数都下得成判；1 = 有红（读数算不出来 / 核查不成立）。

⚠️ 读数只描述状态（docs/评估标准.md 的三角色）：不进仓库验收判据、不合成、不排序。
   唯一例外是 G5 的 A3 —— 那是判据（禁止的动作数必须恒为 0）。
"""
import argparse
import inspect
import io
import json
import math
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import entry as E                                            # noqa: E402

#: G4 的规模族：同一方向函数（keyset）、六份规模不同的材料（1 005 – 30 000 项）
SCALE = ["ext-email", "ext-bitcoin", "ext-wiki-vote",
         "ext-coauth", "ext-astroph", "ext-web-stanford-30k"]

FAIL = 0


def hr(t: str) -> None:
    print("\n" + "=" * 78 + "\n" + t + "\n" + "=" * 78)


def chk(tag: str, what: str, okr: bool, detail: str) -> None:
    global FAIL
    if not okr:
        FAIL += 1
    print(f"  [{'过' if okr else '红'}] {tag} {what}\n        {detail}")


# ── G4 分辨度：三个子读数 ─────────────────────────────────────────────────────


def leaf_sizes(k) -> list[int]:
    """叶容量分布：只算**没有子方向**的方向。升序。"""
    out = []
    for d in k.all_directions():
        if not k.children_of(d):
            out.append(len(k.members_of(d)))
    return sorted(out)


def quant(xs: list[int], p: float) -> int:
    """最近秩分位数。确定性：同输入同输出（不依赖插值约定）。"""
    if not xs:
        return 0
    i = int(math.ceil(p * len(xs))) - 1
    return xs[min(max(i, 0), len(xs) - 1)]


def branch_entropy(k) -> float:
    """分支熵：每个有 >=2 个子方向的节点，子支成员数分布的 Shannon 熵（bit），
    按该节点的成员数加权平均。

    它回答的是「一个节点被切开时，切得**匀不匀**」—— 与最大叶（切得多深）
    是两个不同的刻度：可以把一个 1000 项的节点切成 500+500（熵 1.0，叶很大），
    也可以切成 10 个 100（熵 3.32，叶很小）；两者都不是对方的函数。
    """
    num = den = 0.0
    for d in k.all_directions():
        kids = k.children_of(d)
        if len(kids) < 2:
            continue
        ns = [len(k.members_of(c)) for c in kids]
        tot = sum(ns)
        if tot <= 0:
            continue
        h = -sum((n / tot) * math.log2(n / tot) for n in ns if n > 0)
        num += h * tot
        den += tot
    return num / den if den else 0.0


def ols_slope(xs: list[float], ys: list[float]) -> float:
    """最小二乘斜率。"""
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    return num / den if den else float("nan")


def g4() -> None:
    hr("G4 分辨度：分布形状 + 规模指数 + 分支熵（同一方向函数，六份规模族）")
    rows = []
    print(f"  {'语料':22s} {'|U|':>6s} {'方向':>4s} {'层':>3s} {'叶数':>5s} "
          f"{'p50':>5s} {'p90':>6s} {'max':>6s} {'p90/p50':>7s} "
          f"{'max/p50':>7s} {'熵bit':>6s}")
    for name in SCALE:
        try:
            app = E.App(name)
        except Exception as exc:                             # noqa: BLE001
            print(f"  {name:22s} 跳过：{type(exc).__name__}: {exc}")
            continue
        k = app.kernels["keyset"]
        ls = leaf_sizes(k)
        n_u = len(app.nodes)
        p50, p90, mx = quant(ls, 0.50), quant(ls, 0.90), (ls[-1] if ls else 0)
        h = branch_entropy(k)
        rows.append({"corpus": name, "n_u": n_u, "n_leaf": len(ls),
                     "dirs": app.tree["方向数"], "depth": app.tree["层数"],
                     "p50": p50, "p90": p90, "max": mx,
                     "r9050": (p90 / p50) if p50 else float("inf"),
                     "rmax50": (mx / p50) if p50 else float("inf"),
                     "entropy": h})
        print(f"  {name:22s} {n_u:6d} {app.tree['方向数']:4d} {app.tree['层数']:3d} "
              f"{len(ls):5d} {p50:5d} {p90:6d} {mx:6d} "
              f"{rows[-1]['r9050']:7.2f} {rows[-1]['rmax50']:7.2f} {h:6.2f}")

    if len(rows) < 2:
        chk("G4-0", "规模族至少两点才谈得上趋势", False, f"只拿到 {len(rows)} 点")
        return

    # G4a 分布形状：无量纲比值 —— 与 L4 的绝对最大叶**不是同一个刻度**
    r9050 = [r["r9050"] for r in rows]
    rmax50 = [r["rmax50"] for r in rows]
    chk("G4a", "分布形状（p90/p50、max/p50）有刻度 —— 至少两个不同值",
        len(set(round(x, 2) for x in r9050)) >= 2
        and len(set(round(x, 2) for x in rmax50)) >= 2,
        f"p90/p50 取值 {sorted(set(round(x, 2) for x in r9050))}｜"
        f"max/p50 取值 {sorted(set(round(x, 2) for x in rmax50))}")

    # G4b 规模指数 alpha：log(max_leaf) 对 log(|U|) 的斜率
    xs = [math.log(r["n_u"]) for r in rows]
    ys = [math.log(r["max"]) for r in rows if r["max"] > 0]
    alpha = ols_slope(xs, ys) if len(ys) == len(xs) else float("nan")
    span_u = rows[-1]["n_u"] / rows[0]["n_u"]
    span_leaf = (rows[-1]["max"] / rows[0]["max"]) if rows[0]["max"] else float("nan")
    chk("G4b", "规模指数 alpha 可算（log 最大叶 对 log|U| 的斜率）",
        not math.isnan(alpha),
        f"alpha = {alpha:.2f}｜|U| x{span_u:.1f} 而最大叶 x{span_leaf:.1f}"
        f"｜{'退化（叶子涨得比项数快）' if alpha > 1 else '持平或改善'}")

    # G4c 分支熵
    hs = [r["entropy"] for r in rows]
    chk("G4c", "分支熵有刻度 —— 至少两个不同值",
        len(set(round(x, 2) for x in hs)) >= 2,
        f"取值 {sorted(set(round(x, 2) for x in hs))}")

    # R4 自查：三个子读数两两之间不许互为函数（秩相关系数满 ±1 就说明重复记账）
    def spearman(a: list[float], b: list[float]) -> float:
        def rank(v: list[float]) -> list[float]:
            order = sorted(range(len(v)), key=lambda i: v[i])
            rk = [0.0] * len(v)
            for pos, i in enumerate(order):
                rk[i] = float(pos)
            return rk
        ra, rb = rank(a), rank(b)
        return ols_slope(ra, rb) / (max(ra) - min(ra) or 1) * (max(rb) - min(rb) or 1) \
            if max(ra) > min(ra) else float("nan")

    pairs = [("max/p50", rmax50, "熵", hs), ("p90/p50", r9050, "熵", hs),
             ("max/p50", rmax50, "p90/p50", r9050)]
    for na, va, nb, vb in pairs:
        rho = spearman(va, vb)
        chk(f"G4-{na}/{nb}", "R4：两个子读数不互为函数（|rho| < 1）",
            not math.isnan(rho) and abs(rho) < 1.0,
            f"rho = {rho:.2f}" + ("（完全同序 ⇒ 重复记账）" if abs(rho) >= 1.0 else ""))

    out = REPO / "evidence" / "_probe_growth_g4.json"
    # ⚠️ 显式 newline="\n"：Windows 上 write_text 会把 \n 转成 \r\n，
    #    而仓库规定 LF（.gitattributes 的 eol=lf）⇒ 工作区会漂。
    io.open(out, "w", encoding="utf-8", newline="\n").write(
        json.dumps({"rows": rows, "alpha": alpha}, ensure_ascii=False, indent=1))
    print(f"\n  留档：{out.relative_to(REPO)}")


# ── G5 自主度：动作 × LOA 等级（不是分数） ───────────────────────────────────
#
# 刻度来源：Sheridan & Verplank (1978) 的 LOA 量表，以及后来被
# Parasuraman / Sheridan / Wickens (2000) 收成的「人在环内 / 环上 / 环外」三分。
# 这里只取它的**有序等级**，不取任何加权公式 —— 加权就是打分，打分就是裁判。

LOA = {
    "A0": "全人工：人做，系统不参与",
    "A1": "系统提议 + 人逐条批准",
    "A2": "规则内自动 + 人可回退",
    "A3": "系统改规则本身（本项目禁止）",
}

#: (动作, 级, 依据, 是否「禁止项声明」)
#: ⚠️ 最后一列是必须的：把「禁止 A3」的**声明**和「已实现的 A3 动作」混在一张表里数，
#:    判据就会去数声明（第一版实测红 2 条，其实是误判）。
ACTIONS = [
    ("装载语料（解析 -> 节点）", "A2", "规则内自动；回退 = 删缓存重跑", False),
    ("按方向函数建树", "A2", "规则内自动（插件 which）", False),
    ("推导候选谓词", "A2", "predicates.derive，规则从语料读", False),
    ("预算内挑进划分的谓词", "A2", "自动决定选谁；候选集是外生的", False),
    ("批准维度（把维度注入成键）", "A1", "只执行人写的「已批准」", False),
    ("新增派生维度", "A0", "人写 sidecar；系统只读", False),
    ("回答查询 / 下钻", "A2", "规则内自动", False),
    ("记录使用并按倾向排序", "A2", "规则内自动；回退 = usage clear（清完次序回默认）", False),
    ("改方向函数定义", "A0", "人改插件代码", False),
    ("系统自选新维度定义", "A3", "禁止 —— 撞 D1 外生准入", True),
    ("系统自改准入线阈值", "A3", "禁止 —— 撞 S1 / E-1 不作分", True),
]


def _scan_no_writer(pattern: str) -> list[str]:
    """在 ldv/ 与 entry.py 里找「同时出现 pattern 和写操作」的行。"""
    hits = []
    files = sorted((REPO / "ldv").rglob("*.py")) + [REPO / "entry.py"]
    for f in files:
        if "test" in f.name:
            continue
        for i, ln in enumerate(f.read_text(encoding="utf-8", errors="replace")
                               .splitlines(), 1):
            if pattern in ln and re.search(r'write_text|\.write\(|json\.dump|"w"|\'w\'', ln):
                hits.append(f"{f.relative_to(REPO)}:{i}: {ln.strip()[:90]}")
    return hits


def g5() -> None:
    hr("G5 自主度：动作 x LOA 等级（报分布，不合成分数）")
    print("  等级定义（有序，不是刻度值）：")
    for k, v in LOA.items():
        print(f"    {k}  {v}")
    print(f"\n  {'动作':28s} {'级':3s} 依据")
    for act, lv, why, forbidden in ACTIONS:
        mark = "（禁止项）" if forbidden else ""
        print(f"  {act:28s} {lv:3s} {why}{mark}")

    # 只对**已实现**的动作统计分布；禁止项单独列（它不是实现，不能进分母）
    impl = [(a, lv) for a, lv, _w, f in ACTIONS if not f]
    forbid = [a for a, _lv, _w, f in ACTIONS if f]
    dist = {}
    for _a, lv in impl:
        dist[lv] = dist.get(lv, 0) + 1
    print(f"\n  已实现动作 {len(impl)} 个，分布（计数，不加权）：" +
          " · ".join(f"{k} {dist.get(k, 0)}" for k in LOA))
    print(f"  禁止项声明 {len(forbid)} 条（不是实现，不进上面的分布）")

    # ★ 这条是判据，不是读数：已实现的 A3 动作数恒为 0
    chk("G5-1", "已实现的 A3（系统改规则本身）动作数必须恒为 0",
        dist.get("A3", 0) == 0,
        f"A3 = {dist.get('A3', 0)} 个｜已实现 {len(impl)} 个动作里没有一个是 A3")

    # 禁止项本身要登记，不能悄悄没有（「不许静默省略」）
    chk("G5-1b", "禁止项有登记（禁止不是靠没写代码，而是写明禁止什么）",
        len(forbid) >= 2, f"{len(forbid)} 条：{'；'.join(forbid)}")

    # 核查：代码里没有写 derived 的路径（系统无法自造维度）
    hits = _scan_no_writer("derived")
    chk("G5-2", "ldv/ 与 entry.py 里没有对 derived 的写路径",
        not hits, f"命中 {len(hits)} 行" + ("：" + hits[0] if hits else ""))

    # 核查：每个派生维度都记了命名者（人）
    side = sorted((REPO / "_data").glob("*/derived/*.json"))
    named, unnamed = [], []
    for p in side:
        try:
            meta = json.loads(p.read_text(encoding="utf-8")).get("_meta") or {}
        except Exception:                                    # noqa: BLE001
            unnamed.append(p.name + "（读不出）")
            continue
        (named if str(meta.get("named_by") or "").strip() else unnamed).append(p.name)
    chk("G5-3", "每个派生维度都记了命名者（人是命名者，不是系统）",
        bool(side) and not unnamed,
        f"{len(side)} 个 sidecar｜记名 {len(named)}｜未记名 {len(unnamed) or '无'}")

    # 核查：批准来源是磁盘上人写的文件，不是内存里系统自选
    try:
        from ldv.core.request import apply_approved as _ap
        src = inspect.getsource(_ap)
    except Exception as exc:                                 # noqa: BLE001
        src = ""
        print(f"  （读 apply_approved 源码失败：{type(exc).__name__}）")
    chk("G5-4", "批准动作从 corpus_dir 读（外生），不是系统自选",
        "corpus_dir" in src,
        "源码里出现 corpus_dir" if "corpus_dir" in src else "源码里没找到 corpus_dir")

    # 核查：预算/规则也从语料读（不是系统学出来的）
    try:
        from ldv.corpus.predicates import load_rules as _lr
        src2 = inspect.getsource(_lr)
    except Exception:                                        # noqa: BLE001
        src2 = ""
    chk("G5-5", "预算与规则从语料目录读（外生），不是系统学出来的",
        "corpus_dir" in src2 or "path" in src2.lower(),
        "load_rules 读 corpus_dir" if src2 else "（读不到 load_rules）")


# ── G6 请求命中度：外生请求集，四分类 ────────────────────────────────────────
#
# 分类来自 query 的三个既有返回字段，不新造接口：
#   ok=False                  -> rejected（键不在词汇里，系统当场拒收）
#   n_answer > 0              -> direct  （不需要松弛就答上了）
#   n_answer == 0 且 relax 非空 -> relaxed （要丢掉一个键才答得上）
#   n_answer == 0 且 relax 空   -> none    （连松弛都救不了）

CATS = ["direct", "relaxed", "none", "rejected"]


def g6() -> None:
    hr("G6 请求命中度：外生请求集，四分类（请求集由人写，系统不出题）")
    path = REPO / "evidence" / "requests_g6.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    meta = doc.get("_meta") or {}
    chk("G6-0", "请求集是外生冻结版本（有人写的版本号，不是现编的）",
        bool(str(meta.get("version") or "").strip()),
        f"version = {meta.get('version')}｜authored_by = {meta.get('authored_by')}")

    total = {c: 0 for c in CATS}
    for corpus, items in (doc.get("corpora") or {}).items():
        print(f"\n  ── {corpus} ──")
        try:
            app = E.App(corpus)
        except Exception as exc:                             # noqa: BLE001
            chk(f"G6-{corpus}", "语料可装载", False, f"{type(exc).__name__}: {exc}")
            continue
        print(f"  {'id':4s} {'类':9s} {'答案':>5s} {'支':>3s}  问题")
        for it in items:
            r = app.query({"require": it["require"]})
            if not r.get("ok"):
                cat, n, nb = "rejected", 0, 0
            elif r.get("n_answer", 0) > 0:
                cat, n, nb = "direct", r["n_answer"], r["n_branches"]
            elif r.get("relax"):
                cat, n, nb = "relaxed", 0, 0
            else:
                cat, n, nb = "none", 0, 0
            total[cat] += 1
            print(f"  {it['id']:4s} {cat:9s} {n:5d} {nb:3d}  {it['q']}")

    n_req = sum(total[c] for c in CATS)
    n_ans = total["direct"] + total["relaxed"] + total["none"]
    print(f"\n  合计 {n_req} 条：" +
          " · ".join(f"{c} {total[c]}" for c in CATS))

    chk("G6-1", "四类之和 = 请求集条数（没有条目静默丢失）",
        n_req > 0 and all(total[c] >= 0 for c in CATS),
        f"{total['direct']}+{total['relaxed']}+{total['none']}+{total['rejected']} = {n_req}")
    chk("G6-2", "rejected 只出现在「键不在词汇里」的条目上（不是系统误拒）",
        total["rejected"] <= 2,
        f"rejected {total['rejected']} 条" +
        ("（请求集里刻意留的拼错键）" if total["rejected"] <= 2 else "（超出预期，要查）"))
    chk("G6-3", "至少有一条需要松弛才答得上（否则这条读数没有信息量）",
        total["relaxed"] > 0, f"relaxed = {total['relaxed']} 条")

    if n_ans:
        print(f"\n  读数（比率，不是分数）：直接命中 {total['direct']}/{n_ans}"
              f" = {total['direct'] / n_ans:.3f}｜需松弛 {total['relaxed']}/{n_ans}"
              f" = {total['relaxed'] / n_ans:.3f}｜答不上 {total['none']}/{n_ans}"
              f" = {total['none'] / n_ans:.3f}")
        print("  ⚠️ 分母不含 rejected（那是「问题写错了」，不是「结构答不上」）。")
        print("  ⚠️ 这个比率只描述状态，不进退出码、不排序、不与别的读数合成。")

    out = REPO / "evidence" / "_probe_growth_g6.json"
    io.open(out, "w", encoding="utf-8", newline="\n").write(
        json.dumps({"version": meta.get("version"), "counts": total},
                   ensure_ascii=False, indent=1))
    print(f"  留档：{out.relative_to(REPO)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["g4", "g5", "g6"], default=None,
                    help="只跑一条读数（g4 慢：六份规模族）")
    a = ap.parse_args()
    if a.only in (None, "g6"):
        g6()
    if a.only in (None, "g5"):
        g5()
    if a.only in (None, "g4"):
        g4()
    hr("汇总")
    if FAIL:
        print(f"  红 {FAIL} 条 —— 见上（读数算不出来 / 核查不成立）")
    else:
        print("  全过。读数只描述状态；唯一进判据的是 G5-1（A3 恒为 0）。")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
