# -*- coding: utf-8 -*-
"""跨领域 · **增量**测试 —— 多份材料，且**保留已注入材料对后续检索的影响**。

用户指令（2026-10-14）：
    「甲，然后做完系统内的校验以后再去做一遍跨领域的测试，像这次五个领域的一样，
      但是要**保留已注入的材料对后续检索的影响**」

## 「保留影响」落在哪

材料一变，**外生声明**（`view_spec.json`）就作废（`_probe_incremental_stale.out.txt`）：
`spec_for` 报「外生声明是在**另一份语料**上写的」⇒ 判不了 ⇒ 认识**作废**。
⇒ 「保留影响」**不能**指望「盘上那份认识」（它必须重写）。

★ 能真正保留下来的是**结构本身** —— 内核里**已经长出来的方向树**：

    阶段 A：插入前 30% 的项 ⇒ 长出 |dirs_A| 个方向
    阶段 B：**在同一个内核上**继续插入剩下 70% ⇒ 那棵树**保留**，B 只在它上面长

## 四臂（⚠️ 第一版只有前三臂，且「②≡③」是恒真的 —— 见下）

    ① 其他系统     BM25 词袋（索引文本**不含栏位值** —— 见 `compare_bm25` 模块开头）
    ② 本系统·增量  A 的树 + B 的项（**保留 A 的材料**）
    ③ 本系统·从头  同一批项**一次全插**
    ④ 丢掉 A       **只插 B 的项**（把 A 的材料从结构里拿掉）

★★ **为什么必须有第 ④ 臂**：`ids_a + ids_b == ids` 且顺序不变 ⇒
**②与③跑的是同一串 `insert`** ⇒ 「② ≡ ③」是**构造保证的**，**不是实测发现**。
把它当读数报出来 = 一条**恒真的断言**。
真正有内容的问题是：**「留着 A」与「丢掉 A」结果一样吗？**
⇒ 一样 ⇒ 「已注入材料的影响」**无从谈起**；不一样 ⇒ 影响**看得见**（差额 = A 的项）。

## ★★ 可续跑（2026-10-14 加）—— 为什么需要

`_data/ext-*` 扩到 12 份、最大 8 万节点之后，**一次跑完不现实**：

  · 前台单次调用有时限（约 10 分钟）；
  · 本机**后台跑重内存任务会被静默杀掉**（不是崩溃：输出文件 0 字节、无 traceback）。

⇒ 把每份语料的 5 行读数 + 行文本 + **语料指纹**存进 sidecar（`*.rows.jsonl`），
   下一趟**跳过已完成且指纹相符**的语料（**打印存下来的行**，不是重算）⇒
   **每趟的输出都是「截至目前」的完整留档**（表里含缓存行 + 本趟新算行，汇总按全部行重算）。

⚠️ **指纹** = `MANIFEST.json` 的字节哈希 + `nodes/*.md` 个数。
   **材料一变（改判据、换数据、重生成）⇒ 指纹变 ⇒ 该条缓存作废重算**
   —— 「材料一变，上一份认识就作废」这条纪律同样适用于缓存。
⚠️ 想**从零重跑**：删掉 `outputs/_task_ext_incremental.rows.jsonl`（或加 `--fresh`）。

跑法：
    python outputs/_task_ext_incremental.py                       # 全部（自动发现）
    python outputs/_task_ext_incremental.py --only ext-email …    # 只跑点名的那几份
    python outputs/_task_ext_incremental.py --fresh                # 忽略缓存，从零重算
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

REPO = Path(r"C:/Users/19253/WorkBuddy AI/2026-10-05-14-16-35/repo")
sys.path.insert(0, str(REPO))
os.chdir(REPO)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace",
                              line_buffering=True)   # ★ 行缓冲：重定向到文件也能**实时**看进度

from ldv import retriever  # noqa: E402
from ldv.checks._fixtures import coverage_of, items, load  # noqa: E402
from ldv.checks.abstraction import build_views  # noqa: E402
from ldv.core import view_persist  # noqa: E402
from ldv.core.kernel import Kernel, Query  # noqa: E402
from ldv.core.views import ViewSpec  # noqa: E402
from ldv.plugins.keyset import KeysetPlugin  # noqa: E402
from ldv.tools.compare_bm25 import BM25, doc_text  # noqa: E402
from ldv.tools.gen_view_spec import build_spec  # noqa: E402

HERE = Path(__file__).resolve().parent
SIDE = HERE / "_task_ext_incremental.rows.jsonl"     # ★ 续跑 sidecar


def discover_corpora() -> list[tuple[str, str]]:
    """★ **自动发现** `_data/ext-*`（有 `MANIFEST.json` 与 `nodes/` 的），
    按**节点数升序** —— 这样表里的行就是一条**规模轴**，「随规模怎么变」直接读得出来。"""
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


def fingerprint(name: str) -> str:
    """语料指纹 = `MANIFEST.json` 字节哈希 + `nodes/*.md` 个数。

    ⚠️ 必须**同时**含「声明的字节」与「实际文件数」：只看 mtime 会被「同秒重写」骗过，
       只看个数会被「改了内容但个数不变」骗过。"""
    d = REPO / "_data" / name
    man = (d / "MANIFEST.json").read_bytes()
    n = sum(1 for _ in (d / "nodes").glob("*.md"))
    h = hashlib.sha256(man + f"|{n}".encode()).hexdigest()[:16]
    return f"{h}/{n}"


CORPORA = discover_corpora()
_ONLY = [a for a in sys.argv[1:] if not a.startswith("-")]
if _ONLY:                                   # `--only`：标定时只跑点名的那几份
    CORPORA = [c for c in CORPORA if c[0] in _ONLY]
FRESH = "--fresh" in sys.argv
CUT = 0.30

# ── 续跑缓存 ─────────────────────────────────────────────────────────────────
CACHE: dict[str, dict] = {}
if SIDE.exists() and not FRESH:
    for ln in SIDE.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            e = json.loads(ln)
            CACHE[e["name"]] = e
if FRESH and SIDE.exists():
    SIDE.unlink()


def build_partial(all_nodes, subset) -> tuple[Kernel, KeysetPlugin]:
    """只**插入** `subset`（内核持有全部项，但结构里只有 subset）。"""
    plug = KeysetPlugin()
    k = Kernel(plug, items(all_nodes))
    k.build(plug.merge([]))
    for nid in sorted(subset):
        k.insert(nid)
    return k, plug


def _pay(p):
    """payload 的不稳定 repr 归一 —— 集合类一律排序后取字符串元组。"""
    if isinstance(p, (set, frozenset)):
        return tuple(sorted(map(str, p)))
    return repr(p)


def tree_fp(k: Kernel):
    """★ 真比**树**（不是比个数）：方向身份 + 秩 + 父 + 来源 + 见证 + payload + 成员集。"""
    return tuple(
        (d.did, d.rank, d.parent, d.origin,
         tuple(sorted(map(str, d.witness))), _pay(d.payload),
         tuple(sorted(k.members_of(d))))
        for d in k.all_directions()
    )


def spec_of(nodes, edges) -> ViewSpec:
    d, _ = build_spec("keyset", nodes, edges)
    return ViewSpec(universe=tuple(d["universe"]),
                    partition=tuple(frozenset(b) for b in d["partition"]),
                    relation=frozenset(tuple(e) for e in d["relation"]))


# ── 判断层三个字段的指纹（`§14.6` 口径：收益 = **判断质量** + 少扫）──────────────
def advice_fp(r):
    return tuple(sorted((tuple(sorted(b)), s) for b, s in r.advice))


def profiles_fp(r):
    return tuple(sorted((tuple(sorted(p.block)), p.rank_lo, p.rank_hi,
                         p.n_with_children, p.n_singleton, p.n_unexpanded)
                        for p in r.profiles))


def reasons_fp(r):
    return tuple(sorted(repr(x) for x in r.reasons))


def judge_fp(r):
    return (advice_fp(r), profiles_fp(r), reasons_fp(r))


# ★ **效应量**：不只问「变没变」，还问「差多大」（`§14.6` 的「判断质量」要有刻度）
def adv_set(r) -> set:
    return {(tuple(sorted(b)), s) for b, s in r.advice}


def prof_map(r) -> dict:
    return {tuple(sorted(p.block)): (p.rank_lo, p.rank_hi, p.n_with_children,
                                     p.n_singleton, p.n_unexpanded)
            for p in r.profiles}


def n_diff(m1: dict, m2: dict) -> int:
    return sum(1 for k in set(m1) | set(m2) if m1.get(k) != m2.get(k))


def pick_target(nodes) -> tuple[str, list[str]]:
    """挑一个「不太小也不太大」的枚举键当查询要求。

    ⚠️ 兜底是必要的：12 份语料里有的键分布很偏（引用网 `出度0` 极少），
       没有兜底时 `max()` 会作用在**空序列**上 ⇒ 整个探针崩在一份语料上。
    """
    g: Counter = Counter()
    for n in nodes.values():
        for kk in n.keys:
            if "=" in kk:
                g[kk] += 1
    cand = [(kk, c) for kk, c in g.items() if 2 <= c <= len(nodes) * 0.5]
    if not cand:
        cand = [(kk, c) for kk, c in g.items() if c >= 2] or list(g.items())
    key = max(cand, key=lambda kv: kv[1])[0]
    return key, sorted(nid for nid, n in nodes.items() if key in n.keys)


BAR = "═" * 118
SUB = "─" * 118
print(BAR)
print(f"跨领域 · **增量** —— {len(CORPORA)} 份材料，**保留已注入材料对后续检索的影响**")
print(BAR)
print(f"阶段 A 插入前 {int(CUT * 100)}%，阶段 B **在同一个内核上**继续插剩下的（⇒ A 长出的树**保留**）")
print(f"★ 续跑：缓存 `{SIDE.name}` 里**指纹相符**的语料直接沿用（本趟沿用 "
      f"{sum(1 for n, _ in CORPORA if n in CACHE and CACHE[n].get('fp') == fingerprint(n))} 份）")
print()
print(f"{'语料':<16}{'领域':<12}{'节点':>6}{'A项':>6}{'键集A':>7}{'键集全':>7}"
      f"{'方向A':>7}{'方向B':>7}{'候选②':>7}{'候选③':>7}{'②=③树':>8}"
      f"{'候选④':>7}{'A的贡献':>8}{'少扫':>10}")
print(SUB)

rows = []
lines5: dict[str, dict[str, str]] = {}     # name -> 5 行文本（表：arm/cmp/tim/jdg/lat）
for name, domain in CORPORA:
    fp = fingerprint(name)
    hit = CACHE.get(name)
    if hit is not None and hit.get("fp") == fp:
        rows.append(hit["row"])
        lines5[name] = hit["lines"]
        print(lines5[name]["arm"])
        continue
    os.environ["LDV_CORPUS"] = name
    nodes, edges, _ = load()
    ids = sorted(nodes)
    cut = max(1, int(len(ids) * CUT))
    ids_a, ids_b = ids[:cut], ids[cut:]
    ks_a = len({frozenset(nodes[nid].keys) for nid in ids_a})     # 前 30% 的键空间
    ks_all = len({frozenset(nodes[nid].keys) for nid in ids})     # 全部项的键空间
    target, truth = pick_target(nodes)
    q = Query(ideal=frozenset(truth), require=frozenset({target}), label=target)

    # ── ② 本系统 · 增量：A 建树 ⇒ B 在同一内核上续插（**保留 A 的材料**）────────
    # ★ 计时**分两段**：`建树`（只 insert）与 `判断`（spec/覆盖/建视图/检索）。
    #   两段是**两把尺子** —— 拿 ② 的整条流水线 ÷ ③ 的只建树，得出的是「流水线有多重」，
    #   不是「分两次插有多贵」。
    t0 = time.time()
    kA, pA = build_partial(nodes, ids_a)
    n_dirs_a = len(kA.all_directions())
    kB, pB = kA, pA                      # ★ 同一个内核 —— A 长出的树留着
    for nid in ids_b:
        kB.insert(nid)
    t_build_inc = time.time() - t0       # ★ **只含建树**：A 的 insert + B 的 insert
    n_dirs_b_inc = len(kB.all_directions())
    fp_b = tree_fp(kB)
    t0 = time.time()
    spec_b = spec_of(nodes, edges)
    cover_b = coverage_of("keyset", nodes, edges)
    rec_b = view_persist.from_dict(
        view_persist.to_dict(build_views(kB, spec_b, cover_b, pB)), kB, cover_b, pB)
    need = {"keyset": retriever.as_bar(q)}
    r_inc = retriever.retrieve(kB, pB, need, spec=spec_b, recognition=rec_b)
    t_pipe_inc = time.time() - t0        # ★ **只含判断流水线**
    cand_inc = sorted(kB.query(q).hit_items)

    # ── ③ 本系统 · 从头（**同一串 insert** ⇒ 与 ② 恒等，只用来把这件事**印出来**）──
    t0 = time.time()
    kC, pC = build_partial(nodes, ids)   # 一次全插
    n_dirs_c = len(kC.all_directions())
    t_build_all = time.time() - t0       # ★ **只含建树** —— 与 ② 的建树段同尺子
    fp_c = tree_fp(kC)
    cand_new = sorted(kC.query(q).hit_items)
    same_tree = (fp_b == fp_c)

    # ── ④ 丢掉 A：**只插 B 的项** —— 「已注入材料的影响」有没有内容就看这一臂 ────
    t0 = time.time()
    kD, pD = build_partial(nodes, ids_b)
    t_build_drop = time.time() - t0      # ★ **只含建树** —— 三把尺子对齐
    # ★ 拿掉 A 的材料后，**原声明可能直接失效**：`spec_b` 里点名的方向是 A 长出来的
    #   ⇒ `build_views(kD, spec_b, ...)` 抛 `KeyError`。**这本身就是最硬的证据。**
    #   ⚠️ 但**别顺手把声明也换掉**：那样 ②vs④ 就同时动了「材料」与「声明」两个变量。
    #   ⇒ 原声明**还成立**时**沿用**它（真正的单变量对照）；**失效**时才不得不重写（并如实标注）。
    try:
        build_views(kD, spec_b, cover_b, pD)
        spec_d, cover_d, spec_b_fails = spec_b, cover_b, False
    except KeyError:
        spec_b_fails = True
        ns_b = {nid: nodes[nid] for nid in ids_b}
        es_b = {nid: frozenset(t for t in edges.get(nid, ()) if t in ns_b) for nid in ids_b}
        spec_d = spec_of(ns_b, es_b)             # ④ 只能**重写声明**（与「材料一变认识作废」一致）
        cover_d = coverage_of("keyset", ns_b, es_b)
    rec_d = view_persist.from_dict(
        view_persist.to_dict(build_views(kD, spec_d, cover_d, pD)), kD, cover_d, pD)
    r_drop = retriever.retrieve(kD, pD, need, spec=spec_d, recognition=rec_d)
    t_pipe_drop = time.time() - t0       # ★ **只含判断流水线**
    cand_drop = sorted(kD.query(q).hit_items)
    contrib = sorted(set(cand_inc) - set(cand_drop))     # ★ A 的材料**真的**贡献了这些
    # ★ 判断层：把 ② 与 ④ 的**三个进输出的判断字段**逐个比
    jf = (advice_fp(r_inc) == advice_fp(r_drop),
          profiles_fp(r_inc) == profiles_fp(r_drop),
          reasons_fp(r_inc) == reasons_fp(r_drop))
    adv_sym = len(adv_set(r_inc) ^ adv_set(r_drop))          # ★ 效应量：建议的对称差
    prof_diff = n_diff(prof_map(r_inc), prof_map(r_drop))    # ★ 效应量：画像不同的块数
    n_prof = len(prof_map(r_inc))

    # ── ① 其他系统：BM25 ────────────────────────────────────────────────────────
    docs = {nid: doc_text(n) for nid, n in nodes.items()}
    bm = BM25(docs)
    bm_hits = len(bm.rank(target.split("=", 1)[1]))
    in_body = sum(1 for t in docs.values() if target.split("=", 1)[1] in t)

    n_all = len(r_inc.first) + len(r_inc.late)
    # ★ 少扫的成因：说否的块有多大（`forb(块)` = 块内所有方向 `forbid` 的**交**）
    late_sizes = Counter(len(b) for b in r_inc.late)
    late_sz = ",".join(f"{k}方向×{v}" for k, v in sorted(late_sizes.items())) or "—"
    late_max = max((len(b) for b in r_inc.late), default=0)
    n_first_single = sum(1 for b in r_inc.first if len(b) == 1)
    row = dict(name=name, domain=domain, nodes=len(nodes), na=len(ids_a),
               n_late=len(r_inc.late), late_sz=late_sz, late_max=late_max,
               n_first_single=n_first_single,
               ks_a=ks_a, ks_all=ks_all,
               da=n_dirs_a, db=n_dirs_b_inc, dc=n_dirs_c,
               ci=len(cand_inc), cn=len(cand_new), same_tree=same_tree,
               cd=len(cand_drop), contrib=len(contrib), spec_b_fails=spec_b_fails,
               spec_reused=(not spec_b_fails),
               adv=len(r_inc.advice), adv_d=len(r_drop.advice),
               j_adv=jf[0], j_prof=jf[1], j_rea=jf[2],
               adv_sym=adv_sym, prof_diff=prof_diff, n_prof=n_prof,
               scanned=len(r_inc.scanned), allb=n_all,
               target=target, truth=len(truth), bm=bm_hits, in_body=in_body,
               t_build_inc=t_build_inc, t_build_all=t_build_all,
               t_build_drop=t_build_drop, t_pipe_inc=t_pipe_inc,
               t_pipe_drop=t_pipe_drop,
               rec=r_inc.recognition.source)
    # 5 行文本（表：arm / cmp / tim / jdg / lat）—— 与报告逐字一致
    l_arm = (f"{name:<16}{domain:<12}{len(nodes):>6}{len(ids_a):>6}{ks_a:>7}{ks_all:>7}"
             f"{n_dirs_a:>7}{n_dirs_b_inc:>7}{len(cand_inc):>7}{len(cand_new):>7}"
             f"{str(same_tree):>8}{len(cand_drop):>7}{len(contrib):>8}"
             f"{len(r_inc.scanned):>5}/{n_all:<4}")
    l_cmp = (f"{name:<16}{target:<26}{len(truth):>5}{bm_hits:>10}{in_body:>10}"
             f"{len(cand_inc):>7}{str(len(cand_inc) == len(truth)):>7}"
             f"{len(r_inc.advice):>8}{r_inc.recognition.source:>18}")
    l_tim = (f"{name:<16}{t_build_inc:>10.3f}s{t_build_all:>10.3f}s"
             f"{t_build_drop:>10.3f}s"
             f"{t_build_inc / max(t_build_all, 1e-6):>9.2f}×"
             f"{t_pipe_inc:>10.3f}s{t_pipe_drop:>10.3f}s"
             f"{t_pipe_inc / max(t_pipe_drop, 1e-6):>9.2f}×")
    l_jdg = (f"{name:<16}{len(r_inc.advice):>7}{len(r_drop.advice):>7}{str(jf[0]):>7}"
             f"{adv_sym:>7}{prof_diff:>9}{n_prof:>9}"
             f"{str(jf[2]):>7}{('沿用' if not spec_b_fails else '重写'):>8}")
    l_lat = (f"{name:<16}{n_all:>6}{len(r_inc.late):>8} {late_sz:<40}"
             f"{n_first_single:>9}{len(r_inc.scanned):>6}/{n_all:<4}")
    rows.append(row)
    lines5[name] = dict(arm=l_arm, cmp=l_cmp, tim=l_tim, jdg=l_jdg, lat=l_lat)
    # ★ **立刻落 sidecar**（逐份追加 + flush）—— 中途被杀也不丢已完成的部分
    with SIDE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"name": name, "fp": fp, "row": row,
                             "lines": lines5[name]}, ensure_ascii=False) + "\n")
        fh.flush()
    print(l_arm)

print(SUB)
print("★ `键集A` / `键集全` = 前 30% 项 / 全部项里**不同键集**的个数（= 键空间有多大）。")
print("★ `方向A` = 阶段 A（前 30% 项）长出的方向数；`方向B` = 续插后（**A 的树保留**）。")
print("★ `②=③树` 比的是**树指纹**（身份+秩+父+来源+见证+payload+成员集），不是比个数；")
print("  ⚠️ 它**恒真**（②③是同一串 insert）—— 印出来是为了**标明**这一点，不是当读数。")
print("★ `A的贡献` = 候选② − 候选④（**拿掉 A 的材料**后少掉的那些）。")
print("⚠️ ④ 尽量做成**单变量**：原声明还成立时**沿用**它（只差材料）；")
print("   只有原声明被材料抽空（`KeyError`）时才不得不重写 —— 那种情形下差 = 材料 + 声明，**分开标注**。")
print()

print(BAR)
print("★ 三条对比（同一个范式）")
print(BAR)
print(f"{'语料':<16}{'需求':<26}{'真值':>5}{'①BM25命中':>10}{'正文含标签':>10}"
      f"{'②候选':>7}{'②=真值':>7}{'建议条数':>8}{'认识来源':>18}")
print(SUB)
for x in rows:
    print(lines5[x['name']]['cmp'])
print(SUB)

print()
print(BAR)
print("★ 耗时：**建树**与**判断**分开计 —— 两把尺子，不许混着比")
print(BAR)
print(f"{'语料':<16}{'②建树':>11}{'③建树':>11}{'④建树':>11}{'②/③建树':>10}"
      f"{'②判断':>11}{'④判断':>11}{'②/④判断':>10}")
print(SUB)
for x in rows:
    print(lines5[x['name']]['tim'])
print(SUB)
print("★ `②建树` / `③建树` 跑的是**同一串 `insert`** ⇒ 比值**应当 ≈ 1**（对「②≡③」的独立佐证）。")
print("★ `②/④判断` = A 的材料**本身**的代价（判断流水线在「有 A 的树」上比「没 A 的树」贵多少）。")
print("⚠️ 声明重写的那几份，④ 多一次**失败的** `build_views`（`KeyError`）+ 重写声明 ⇒ ④判断偏大。")

print()
print(BAR)
print("★ 判断层：② （保留 A） vs ④（丢掉 A）—— 三个进输出的判断字段逐个比")
print(BAR)
print(f"{'语料':<16}{'建议②':>7}{'建议④':>7}{'建议同':>7}{'建议差':>7}"
      f"{'画像差':>9}{'画像块数':>9}{'读法同':>7}{'声明':>8}")
print(SUB)
for x in rows:
    print(lines5[x['name']]['jdg'])
print(SUB)
print("★ `建议差` = 建议集的**对称差**（条数相同也可能内容不同 ⇒ 只看条数会漏）；")
print("  `画像差/画像块数` = 块画像**逐块**比对后不同的块数 ÷ 总块数 —— **效应量的刻度**。")
print("★ `声明=沿用` ⇒ ②vs④ **只差材料**（单变量）；`声明=重写` ⇒ 原声明被材料抽空了（**本身是结论**）。")

print()
print("★ 回答用户那一问（**保留已注入材料对后续检索的影响**）：")
print(f"   ① 「留着 A」的树 ≡「一次全插」的树：{'全部一致' if all(x['same_tree'] for x in rows) else '有差异'}"
      f"（⚠️ 恒真，构造保证）")
_r3b = [x['t_build_inc'] / max(x['t_build_all'], 1e-6) for x in rows]
print(f"   ①b **建树耗时** ②÷③ = {min(_r3b):.2f}–{max(_r3b):.2f}×（同一串 `insert`，**应当 ≈1**）"
      f" ⇒ 「②≡③」的**独立佐证**（不是构造断言）")
_stuck = [x['name'] for x in rows if x['ks_a'] != x['ks_all'] and x['da'] == x['db']]
print(f"   ①c 方向在 **30% 项**上就长满的语料："
      f"{sum(1 for x in rows if x['da'] == x['db'])}/{len(rows)} 份；"
      f"键集在 30% 项上就齐的语料：{sum(1 for x in rows if x['ks_a'] == x['ks_all'])}/{len(rows)} 份")
print(f"      ⚠️ 两者**不等价**：键集长过、方向却**一个没多**的语料 = {('、'.join(_stuck) or '无')}"
      f" ⇒ 「方向集由键空间定」**不成立**；能说的是**方向数在 30% 项后基本饱和**")
print(f"   ② 拿掉 A 的材料后**原声明直接失效**（`build_views` 抛 `KeyError`）："
      f"{sum(1 for x in rows if x['spec_b_fails'])}/{len(rows)} 份"
      f" ⇒ A 长出的方向正是原声明点名要用的那些")
print(f"   ③ **候选集**：「留着 A」与「丢掉 A」**不一样**："
      f"{sum(1 for x in rows if x['contrib'] > 0)}/{len(rows)} 份"
      f"（0 ⇒ 候选来自 `items`，与结构无关 —— 中心定理的推论）")
print(f"   ④ **判断层**（建议 / 块画像 / 读法）：三个字段全同的语料 "
      f"{sum(1 for x in rows if x['j_adv'] and x['j_prof'] and x['j_rea'])}/{len(rows)} 份；"
      f"建议条数同 {sum(1 for x in rows if x['j_adv'])}/{len(rows)}、"
      f"块画像同 {sum(1 for x in rows if x['j_prof'])}/{len(rows)}、"
      f"读法同 {sum(1 for x in rows if x['j_rea'])}/{len(rows)}"
      f" ⇒ **已注入材料的影响就落在这里**")
_sv = [x for x in rows if x['spec_reused']]
print(f"   ④b **效应量**（只算单变量的 {len(_sv)} 份）：建议对称差 "
      f"{sum(x['adv_sym'] for x in _sv)} 条；块画像不同的块 "
      f"{sum(x['prof_diff'] for x in _sv)}/{sum(x['n_prof'] for x in _sv)} 块"
      f"（⚠️ 声明重写的那几份分区整个换了 ⇒ 不计入）")
print(f"   ⑤ 增量候选 == 真值：{sum(1 for x in rows if x['ci'] == x['truth'])}/{len(rows)} 份")
print(f"   ⑥ 少扫真的发生了：{sum(1 for x in rows if x['scanned'] < x['allb'])}/{len(rows)} 份")
print(f"   ⚠️ ④ 的「单变量」成色：声明**沿用** {sum(1 for x in rows if x['spec_reused'])}/{len(rows)} 份"
      f"（只差材料）· **重写** {sum(1 for x in rows if x['spec_b_fails'])}/{len(rows)} 份（差材料+声明）")

print()
print(BAR)
print("★ 少扫的成因：**说否的块长什么样**")
print(BAR)
print("  说否 ⟺ `forb(块) ∋ 查询要求键`；而 `forb(块)` = 块内**所有方向** `forbid` 的**交**")
print("  ⚠️ 但 `forb` 沿树**累积**（`forb(子) ⊇ forb(父)`，`keyset.split` 的硬要求）")
print("     ⇒ 同一子树的方向**共享**禁止键 ⇒ 说否块**可以是多方向**的（下面用分布验）")
print(f"{'语料':<16}{'块数':>6}{'说否块':>8} {'说否块大小分布':<40}"
      f"{'单方向块':>9}{'扫过':>9}")
print(SUB)
for x in rows:
    print(lines5[x['name']]['lat'])
print(SUB)
print(f"★ 说否块**最大方向数 ≤ 1** 的语料：{sum(1 for x in rows if x['late_max'] <= 1)}/{len(rows)} 份；"
      f"说否块里出现**多方向块**的语料：{sum(1 for x in rows if x['late_max'] > 1)}/{len(rows)} 份")
print("★ ⇒ 少扫的多寡取决于「块的合并 `forb` 是否含查询要求键」——"
      "与语料 × 查询的组合有关，**不是必然**")
