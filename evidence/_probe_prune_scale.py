# -*- coding: utf-8 -*-
"""块级剪枝的收益随**语料规模**怎么变？—— `§13.0` 的裁定只在**默认 36 项语料**上做过。

## 为什么要重测

`§13.0`（2026-10-09）把「给流程 A 加接口，支持从给定方向集出发」判成「实测不值」，三条理由：

    ① 块横跨树层（5 成员的块跨 3 个 rank，单层前沿最大 3 个方向）⇒ 剪枝**无处安放**
    ② **即使装得下，收益 ≈ 0**：19 个「合并说否」的块**各只有 1 个成员**
       ⇒ 省下的 `hit` 调用 = 0（`Σ(|块|−1) = 19−19`）
    ③ 「顺序」对流程 A 不适用（逐层走整条前沿，没有「先看哪个」这一步）

用户口径（2026-10-14）：「**视图层的材料是逐渐丰盈的才能看出差距**」
+ 拍板「甲」（**承认「少扫」是一种影响**）

⇒ 本探针做**单变量对照：语料规模**（默认 36 项 vs `_data/ext-*` 里**自动发现**的跨领域材料），重测 ②。
⚠️ 2026-10-14 起语料表**自动发现**（按节点数升序）—— 手写列表会漏掉后接入的语料；
   `mutual` 判据修正后键空间变了 ⇒ **旧读数全部作废**，必须重跑。

## 收益口径（必须写清，否则读数会冒充收益）

定理（`§I2` 保证，本探针逐条验 0 反例）：

    合并说「否」 ⇒ 覆盖(合并) 里没有能匹配 q 的项 ⇒ **每个成员的覆盖里也没有** ⇒ 每个成员都说「否」

⇒ 块级 `hit` 说否 ⇒ **该块所有成员都不必逐个判 hit** ⇒ 净省 `Σ_{说否块}(|块| − 1)` 次 `hit` 调用。
⚠️ 这**不省遍历**：流程 A 里 `v is Tri.NO` 本来就不 `expand` ⇒ 否方向的子树**本来就不跑**。
   ⇒ 「跳过否块」的**全部**增量收益就是这 `Σ(|块|−1)` 次调用。

⚠️ 本探针为了**验定理**，对说否块**额外**逐成员判了一次 hit —— 那是探针的成本，不是系统的成本。

跑法：python outputs/_probe_prune_scale.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(r"C:/Users/19253/WorkBuddy AI/2026-10-05-14-16-35/repo")
sys.path.insert(0, str(REPO))
os.chdir(REPO)
sys.stdout = __import__("io").TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                            errors="replace", line_buffering=True)

from ldv import retriever as R  # noqa: E402
from ldv.checks._fixtures import build_keyset, coverage_of, keyset_queries, load  # noqa: E402
from ldv.checks.abstraction import build_views  # noqa: E402
from ldv.core.tri import Tri  # noqa: E402
from ldv.core.views import ViewSpec  # noqa: E402
from ldv.tools.gen_view_spec import build_spec  # noqa: E402

#: (语料名 or None=内置, 领域) —— ★ **自动发现** `_data/ext-*`（有 MANIFEST 的），
#: 按**节点数升序**；2026-10-14 扩：原先是手写 5 份，新增的语料不会自动进来。
def discover_corpora() -> list[tuple[str, str]]:
    import json
    found: list[tuple[str, str, int]] = []
    for d in sorted((REPO / "_data").glob("ext-*")):
        mf = d / "MANIFEST.json"
        if not (mf.exists() and (d / "nodes").is_dir()):
            continue
        man = json.loads(mf.read_text(encoding="utf-8"))
        dom = str(man.get("领域", "")).split("（")[0].strip() or "—"
        found.append((d.name, dom, int(man.get("节点数", 0))))
    found.sort(key=lambda t: (t[2], t[0]))
    return [(n, d) for n, d, _ in found]


CORPORA = [(None, "内置 36 项")] + discover_corpora()

# ── ★★ 可续跑（2026-10-14 加，与 `_task_ext_incremental.py` 同一套机制）────────
#    `_data/ext-*` 到 3 万节点后，一次跑完全部会超过单次调用时限 ⇒
#    每份的读数 + **语料指纹**存 sidecar，下一趟跳过**指纹相符**的，只算新的。
#    ⚠️ 材料一变（改判据 / 换数据）⇒ 指纹变 ⇒ 该条作废重算。
_HERE = Path(__file__).resolve().parent
SIDE = _HERE / "_probe_prune_scale.rows.jsonl"
_ONLY = [a for a in sys.argv[1:] if not a.startswith("-")]
if _ONLY:
    CORPORA = [c for c in CORPORA if c[0] is None or c[0] in _ONLY]
FRESH = "--fresh" in sys.argv


def fingerprint(name: str | None) -> str:
    if name is None:                       # 内置语料：跟着仓库走，指纹就是节点数
        return "builtin"
    import hashlib
    d = REPO / "_data" / name
    man = (d / "MANIFEST.json").read_bytes()
    n = sum(1 for _ in (d / "nodes").glob("*.md"))
    return hashlib.sha256(man + f"|{n}".encode()).hexdigest()[:16] + f"/{n}"


CACHE: dict[str, dict] = {}
if SIDE.exists() and not FRESH:
    for ln in SIDE.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            e = json.loads(ln)
            CACHE[e["name"]] = e
if FRESH and SIDE.exists():
    SIDE.unlink()

MAXQ = 40

BAR = "═" * 116
SUB = "─" * 116

print(BAR)
print("块级剪枝的收益随**语料规模**怎么变 —— `§13.0` 的裁定只在默认 36 项语料上做过")
print(BAR)
print(f"★ 续跑：缓存 `{SIDE.name}` 里**指纹相符**的语料直接沿用")
print(f"{'语料':<14}{'领域':<10}{'节点':>6}{'|U|':>5}{'|Q|':>5}{'块最小':>7}{'块中位':>7}"
      f"{'块最大':>7}{'说否块':>8}{'均块大小':>9}{'saved':>7}{'查询':>6}{'节省%':>8}")
print(SUB)

rows = []
tot_viol = 0
for corpus, domain in CORPORA:
    key = corpus or "(内置)"
    fp = fingerprint(corpus)
    hit = CACHE.get(key)
    if hit is not None and hit.get("fp") == fp:
        rows.append(hit["row"])
        tot_viol += hit["row"]["viol"]
        continue
    if corpus is None:
        os.environ.pop("LDV_CORPUS", None)
    else:
        os.environ["LDV_CORPUS"] = corpus
    loaded = load()
    nodes, edges, _ = loaded
    kernel, plugin = build_keyset(nodes)
    specd, _ = build_spec("keyset", nodes, edges)
    spec = ViewSpec(universe=tuple(specd["universe"]),
                    partition=tuple(frozenset(b) for b in specd["partition"]),
                    relation=frozenset(tuple(e) for e in specd["relation"]))
    cover = coverage_of("keyset", nodes, edges)
    vs = build_views(kernel, spec, cover, plugin)
    q = vs.q
    qs = keyset_queries(nodes)
    use = qs[:MAXQ]

    sizes = sorted(len(b) for b in q)
    med = sizes[len(sizes) // 2] if sizes else 0
    n_no = members_no = saved = viol = 0
    for query in use:
        for block in q:
            ids = sorted(block, key=R._did_order)
            dirs = [kernel.direction(d) for d in ids]
            blk = plugin.hit(R._carrier(kernel, block, plugin), query)
            if blk is Tri.NO:
                n_no += 1
                members_no += len(dirs)
                saved += len(dirs) - 1
                per = [plugin.hit(d, query) for d in dirs]   # ← 只为本探针验定理
                if any(v is not Tri.NO for v in per):
                    viol += 1

    hits = len(use) * len(spec.universe)
    pct = (saved / hits * 100) if hits else 0.0
    avg = (members_no / n_no) if n_no else 0.0
    tot_viol += viol
    row = dict(name=key, domain=domain, nodes=len(nodes),
               U=len(spec.universe), Q=len(q), saved=saved, hits=hits,
               n_no=n_no, avg=avg, pct=pct, nq=len(use), nq_all=len(qs),
               viol=viol, med=med, mn=sizes[0] if sizes else 0,
               mx=sizes[-1] if sizes else 0)
    rows.append(row)
    # ★ 立刻落 sidecar（逐份追加 + flush）—— 中途被杀也不丢已完成的部分
    with SIDE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"name": key, "fp": fp, "row": row},
                            ensure_ascii=False) + "\n")
        fh.flush()

for r in rows:
    print(f"{r['name']:<14}{r['domain']:<10}{r['nodes']:>6}{r['U']:>5}{r['Q']:>5}"
          f"{r['mn']:>7}{r['med']:>7}{r['mx']:>7}{r['n_no']:>8}{r['avg']:>9.2f}"
          f"{r['saved']:>7}{r['nq']:>6}{r['pct']:>7.2f}%")

print(SUB)
print(f"★ 定理反例（合并说否 而某成员 ≠ 否）：**{tot_viol}**"
      f" ⇒ 推论{'成立' if tot_viol == 0 else '不成立'}")
print(f"★ `saved` = 跳过说否块能省的 `hit` 调用数 = `Σ(|块| − 1)`；"
      f"`节省%` = saved / (查询数 × |U|)")
print(f"★ 查询取每份语料的前 {MAXQ} 条（如实印出：见 `查询` 列 / 该语料实际总查询数见下行）")
print(f"   实际总查询数：" + " · ".join(f"{r['name']}={r['nq_all']}" for r in rows))
print()

# ── 关键对照：块大小 vs 说否块大小 ────────────────────────────────────────────
print(BAR)
print("★ 关键对照 —— 「说否的块」到底有多大（`§13.0` ② 说它「各只有 1 个成员」）")
print(BAR)
print(f"{'语料':<14}{'|U|':>5}{'块均大小':>10}{'说否块均大小':>14}{'比值':>8}{'saved':>8}{'节省%':>9}")
print(SUB)
for r in rows:
    blk_avg = (r["U"] / r["Q"]) if r["Q"] else 0.0
    ratio = (r["avg"] / blk_avg) if blk_avg else 0.0
    print(f"{r['name']:<14}{r['U']:>5}{blk_avg:>10.2f}{r['avg']:>14.2f}{ratio:>8.2f}"
          f"{r['saved']:>8}{r['pct']:>8.2f}%")
print(SUB)
print("★ 若「说否块均大小」≈ 1.00 ⇒ `§13.0` ② 在**所有规模**上都成立 ⇒ 「少扫」的收益恒为 0")
print("★ 若它随规模**上升** ⇒ 前提变了（材料丰盈才看得出）⇒ 「甲」有落点")
