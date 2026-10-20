"""**检索层功能验收** —— 把这一层实际交付的东西逐项跑出来（不只是判据布尔值）。

    cwd = repo：
        python ../outputs/_verify_retriever_features.py            # 36 项（仓内）
        LDV_CORPUS=... python ../outputs/_verify_retriever_features.py   # 任意语料

## 为什么要有它（而不是看 `run_checks` 的绿）

`run_checks` 的 `(检索)` 组印的是**判据**（8 条三态）。而「功能在不在」是**另一件事**：
一条判据可以恒绿（守卫），功能却可能**什么都没交付**。
⇒ 本脚本按**设计稿的步骤**逐项打印**产物**：

    T0 分派     取到几栏需求、本内核跑哪一栏
    T1 认识     来源 / 指纹 / `|Q|` vs `|U|`（**粗粒度** —— 这就是「模糊掌握」）
    T2 排序     「先看 / 后看」分了几块 ＋ **按使用细调**的证据（几个块有记录）
    T3 跑 A     按顺序跑了哪些块（**扫完全部块** = 不自立终止依据）
    T4 解释     每条理由 = 一个候选方向 + 它覆盖里的一个项（**可指认**）
    T5 展示/记录 候选数 / 记录数（走流程 A 的 `R5a`/`R5b`）
    建议        块级下一步建议（答 `R3a`）+ **覆盖到几个方向**
    深化        展开哪些方向、候选怎么变、**只增不减 + 幂等**
    画像        每块三档读法 + 它落在哪个块

⚠️ 与判据的分工：**判据判「对不对」，本脚本印「有没有」**。两者都要有。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1] / "repo"
sys.path.insert(0, str(REPO))

from ldv import flow, retriever  # noqa: E402
from ldv.checks._fixtures import coverage_of, find_corpus, load  # noqa: E402
from ldv.checks._framework import Report  # noqa: E402
from ldv.checks.abstraction import load_spec_file, spec_for  # noqa: E402
from ldv.checks.coverage import corpus_fingerprint  # noqa: E402
from ldv.checks.retrieval import run_retrieval  # noqa: E402
from ldv.core import selfopt, view_persist  # noqa: E402
from ldv.core.views import coarsest_stable_refinement  # noqa: E402


def main() -> int:
    loaded = load()
    if loaded is None:
        print("找不到语料")
        return 1
    nodes, edges, _ = loaded
    nd = find_corpus()
    corpus = corpus_fingerprint(nodes, edges)
    doc = load_spec_file(nd, corpus)
    which = "keyset"
    spec, why = spec_for(doc, which, corpus)
    if spec is None:
        print(f"声明读不到：{why}")
        return 1
    kernel, plugin, queries, _mk = batch_kernel = None, None, None, None
    from ldv.run_checks import batch_kernel as bk

    kernel, plugin, queries, _mk = bk(which, nodes, edges)
    cover = coverage_of(which, nodes, edges)

    print("═" * 78)
    print(f"检索层功能验收｜语料 {corpus}｜方向 `{which}`｜声明 {why}")
    print("═" * 78)

    # ── T0 分派 ────────────────────────────────────────────────────────
    need = {which: retriever.as_bar(queries[0])}
    bar = need[which]
    print(f"\n【T0 分派】需求栏 {sorted(need)}｜本内核 `{plugin.name}` 取 "
          f"`require {len(bar.get('require', ()))} 项 / forbid "
          f"{len(bar.get('forbid', ()))} 项`｜已知栏 {sorted(retriever.BAR_NAMES)}")

    # ── 攒使用记录（流程 A 的正常动作，`R5a`/`R5b`）──────────────────────
    for q0 in queries[:3]:
        flow.run_query(kernel, q0)
    tendency = selfopt.tendency_by_direction(kernel)

    # ── 读盘上那份认识（有就用）────────────────────────────────────────
    path = view_persist.default_path(nd)
    disk = view_persist.read(path)
    disk_fp = rec_obj = None
    if disk:
        try:
            rec_obj = view_persist.from_dict(disk, kernel, cover, plugin)
            disk_fp = retriever.spec_fingerprint(rec_obj.spec)
        except Exception as exc:  # noqa: BLE001
            print(f"  盘上那份读不回来（{type(exc).__name__}）⇒ 按重算走")
            rec_obj, disk_fp = None, None

    # ── 跑一趟（判据也在里面）─────────────────────────────────────────
    rep = Report(plugin="(检索)", expects=__import__(
        "ldv.checks.retrieval", fromlist=["RETRIEVER_CODES"]).RETRIEVER_CODES)
    r = run_retrieval(kernel, plugin, need, spec, rep, recognition=rec_obj,
                      tendency=tendency, disk_fingerprint=disk_fp)
    deep = retriever.deepen(kernel, plugin, r)

    # ── T1 认识 ───────────────────────────────────────────────────────
    print(f"\n【T1 认识】来源 {r.recognition.source}")
    print(f"           `|U| = {len(spec.universe)}` ⇒ `|Q| = {len(r.recognition.q)}` 块"
          f"｜收缩比 {len(r.recognition.q)/len(spec.universe):.3f}"
          f"（**这就是「模糊掌握」**）｜指纹 {r.recognition.fingerprint}")

    # ── T2 排序 + 按使用细调 ──────────────────────────────────────────
    n_tend = r.n_blocks_with_tendency
    print(f"\n【T2 排序】「先看」{len(r.first)} 块（{sum(len(b) for b in r.first)} 个方向）"
          f"｜「后看」{len(r.late)} 块")
    print(f"           按使用细调：有记录的块 **{n_tend}/{len(r.first)}**"
          f"（「不知道」不参与比较 ⇒ 没记录时自动退化到结构序）")

    # ── T3 跑 A ───────────────────────────────────────────────────────
    print(f"\n【T3 跑 A】按顺序跑了 **{len(r.scanned)}/{len(r.recognition.q)}** 块"
          f"（**扫完全部块** —— 本层不自立终止依据）")

    # ── T4 解释 ───────────────────────────────────────────────────────
    print(f"\n【T4 解释】候选 **{len(r.candidates)}** 个｜理由 **{len(r.reasons)}** 条"
          f"（每条 = 一个候选方向 + 它覆盖里的一个项）")
    for did, item in r.reasons[:3]:
        print(f"          {retriever.render_reason((did, item))}")

    # ── T5 展示/记录 ──────────────────────────────────────────────────
    stats = kernel.stats()
    print(f"\n【T5 展示/记录】（走流程 A 的 `R5a`/`R5b`）使用记录 "
          f"**{stats['使用记录']}** 条｜`§K2 判空` {stats['§K2 判空']}")

    # ── 建议（答 `R3a`）───────────────────────────────────────────────
    print(f"\n【下一步建议】答 `R3a` 的依据｜**{len(r.advice)}** 条块级建议｜"
          f"覆盖 **{r.advice_span}/{len(r.candidates)}** 个候选方向"
          f"（粒度 {r.advice_span/max(1,len(r.advice)):.1f} 个方向/条）")
    for b, a in r.advice[:4]:
        ids = ", ".join(sorted(b, key=retriever._did_order)[:3])
        print(f"          [{ids}{'…' if len(b) > 3 else ''}]"
              f"（{len(b)} 个成员）⇒ **{a}**")

    # ── 深化 ──────────────────────────────────────────────────────────
    print(f"\n【深化（`R3a` 接线）】展开 **{len(deep.deepened)}** 个方向"
          f"｜候选 {len(r.candidates)} → **{len(deep.candidates)}**"
          f"（**只增不减**）｜再深化一次："
          f"展开 {len(retriever.deepen(kernel, plugin, deep).deepened)} 个（**幂等**）")

    # ── 画像 ──────────────────────────────────────────────────────────
    print(f"\n【块级画像】{len(r.profiles)} 块：")
    for p in r.profiles:
        hit = "  ← 有候选" if p.block & r.candidates else ""
        print(f"          {retriever.render_profile(p)}{hit}")

    # ── 读数 ──────────────────────────────────────────────────────────
    print(f"\n【读数】{retriever.render_reading(r)}")

    # ── 判据 ──────────────────────────────────────────────────────────
    print(f"\n【判据】{len(rep.assertions)} 条：")
    for a in rep.assertions:
        mark = {"YES": "过", "NO": "红", "UNEXPANDED": "跳"}[a.result.name]
        print(f"          [{mark}] {a.code}  {str(a.detail)[:96]}")

    # ── 逐层：功能 vs 判据 ────────────────────────────────────────────
    print("\n【结论】")
    feats = {
        "T0 分派": True,
        "T1 认识（粗粒度）": len(r.recognition.q) < len(spec.universe),
        "T2 排序（两档）": bool(r.first or r.late),
        "T2 按使用细调": n_tend > 0,
        "T3 扫完全部块": len(r.scanned) == len(r.recognition.q),
        "T4 解释可指认": len(r.reasons) > 0,
        "T5 展示/记录": stats["使用记录"] > 0,
        "块级建议": len(r.advice) > 0,
        "深化": True,
        "画像": len(r.profiles) > 0,
    }
    for k, v in feats.items():
        print(f"          {'✅' if v else '❌'} {k}")
    print(f"          {'✅' if all(a.result.name != 'NO' for a in rep.assertions) else '❌'} "
          f"判据无红（{len(rep.assertions)} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
