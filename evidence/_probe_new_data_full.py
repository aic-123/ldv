# -*- coding: utf-8 -*-
"""用**新语料**（从没进过任何实验）把新功能全跑一遍。

每份语料测 5 项：
  ① **谓词推导三段**（候选 / 采用 / 拒收）—— 拒收率与理由
  ② **组合谓词三段**（待命名 / 组合候选 / 组合拒收）—— 含同量冗余
  ③ **方向请求**（触发 / 不触发 + 本趟查了）—— B19 树链口径
  ④ **影响分析**（删 3 项，预测 vs 实测）
  ⑤ **索引**（二次装载加速 + 等价）

跑法：python outputs/_probe_new_data_full.py
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent / "repo"
sys.path.insert(0, str(REPO))
os.chdir(REPO)

from ldv.cli import _assemble                          # noqa: E402
from ldv.core import impact as IM                      # noqa: E402
from ldv.core import request as RQ                     # noqa: E402
from ldv.corpus import predicates as PR                 # noqa: E402
from ldv.corpus.index import load_nodes_indexed        # noqa: E402
from ldv.corpus.loader import load_edges, load_nodes   # noqa: E402
from ldv.checks._fixtures import coverage_of           # noqa: E402

CORPORA = ["ext-web-stanford"]


def one(name: str, idx_root: Path) -> dict:
    print(f"\n═══ {name} ═══")
    row: dict = {"语料": name}
    t0 = time.time()
    # ★ **走索引装载**（2026-10-18）：第一次读全新写入的小文件实测 6.5 ms/个
    #    （10 万个 ⇒ 656 s，杀软/冷缓存），但**只需付一次** —— 之后索引命中是秒级。
    #    所以探针第一步就建索引，后面全部测试都吃它。
    nodes, istats = load_nodes_indexed(REPO / "_data" / name / "nodes", idx_root)
    # ★ **守卫：0 节点就是红**（2026-10-18 踩到假绿：分片目录被 `glob` 读成 0 个，
    #   下游五条判据在空语料上全部「通过」）。宁可红，不可静默跳过。
    if not nodes:
        return {"语料": name, "错": "装载出 0 个节点（索引或 glob 读不到分片目录）"}
    edges, _ = load_edges(nodes)
    row["节点"] = len(nodes)
    print(f"  装载 {time.time() - t0:.1f}s（索引命中 {istats['命中']} / "
          f"重读 {istats['重读']}）｜{len(nodes)} 节点｜"
          f"{sum(len(v) for v in edges.values())} 边")

    # ① 谓词三段
    cand, used, rej, params = PR.derive_buffered(nodes, edges)
    u, r = {p.谓词 for p in used}, {x["谓词"] for x in rej}
    dropped = [p for p in cand if p.谓词 not in u and p.谓词 not in r]
    row["谓词"] = {"候选": len(cand), "采用": len(used), "超预算": len(dropped),
                   "拒收": len(rej), "拒收率": round(len(rej) / len(cand), 3) if cand else 0}
    print(f"  ① 谓词三段：候选 {len(cand)}｜采用 {len(used)}｜超预算 {len(dropped)}｜"
          f"拒收 {len(rej)}（{row['谓词']['拒收率']:.0%}）")
    for x in rej:
        print(f"      拒收 {x['谓词']:14s} 满足 {x['满足数']:>6d}/{len(nodes)} —— {x['理由']}")

    # ② 组合三段
    to_name, cands, crej = PR.combine(nodes, edges, used)
    row["组合"] = {"待命名": len(to_name), "候选": len(cands), "拒收": len(crej)}
    print(f"  ② 组合三段：待命名 {len(to_name)}｜候选 {len(cands)}｜拒收 {len(crej)}"
          f"（同量冗余 {sum(1 for x in crej if '同量冗余' in x['理由'])}）")

    # ③ 方向请求
    k, _q, *_ = _assemble("keyset", nodes, edges, 12)
    cov = coverage_of("keyset", nodes, edges)
    rr = RQ.direction_requests(k, cov, used_preds=used)
    row["请求"] = {"条数": len(rr["请求"]), "本趟查了": rr["本趟查了"]}
    print(f"  ③ 方向请求：{rr['说明'][:66]}")

    # ④ 影响分析
    ids = sorted(k.placed)[:3]
    pred = IM.removal_impact(k, ids)
    k2, _q2, *_ = _assemble("keyset", nodes, edges, 12)
    cmp = IM.compare_prediction(k2, ids)
    row["影响"] = {"删": 3, "会空": pred["会变空数"], "会缩": pred["成员减少数"],
                   "预测==实测": cmp["会空一致"] and cmp["缩一致"]}
    print(f"  ④ 影响分析：删 3 ⇒ 空 {pred['会变空数']}｜缩 {pred['成员减少数']}｜"
          f"预测==实测 {row['影响']['预测==实测']}")

    # ⑤ 索引
    t0 = time.time()
    n1, s1 = load_nodes_indexed(REPO / "_data" / name / "nodes", idx_root)
    t_first = time.time() - t0
    t0 = time.time()
    n2, s2 = load_nodes_indexed(REPO / "_data" / name / "nodes", idx_root)
    t_second = time.time() - t0
    same = (set(n1) == set(n2)
            and all(n1[x].keys == n2[x].keys for x in n1)
            and all(n1[x].fields == n2[x].fields for x in n1))
    row["索引"] = {"首次s": round(t_first, 2), "二次s": round(t_second, 3),
                   "加速": round(t_first / t_second, 1) if t_second else 0,
                   "命中": s2["命中"], "等价": same}
    print(f"  ⑤ 索引：首次 {t_first:.2f}s → 二次 {t_second:.3f}s"
          f"（{row['索引']['加速']}×）｜命中 {s2['命中']}｜等价 {same}")
    return row


def main() -> int:
    all_ok = True
    rows = []
    # ⚠️ `idx_root=None` ⇒ 用**默认缓存目录**（`~/.cache/ldv`）。
    #    第一版传了 `mkdtemp()` ⇒ 每次都是新目录 ⇒ 永远命中 0
    #    （`_build_index_web_stanford.py` 把索引建在默认目录里）。
    tmp = None
    try:
        for name in CORPORA:
            try:
                rows.append(one(name, tmp))
            except Exception as exc:
                print(f"  ✗ {name}: {type(exc).__name__}: {exc}")
                rows.append({"语料": name, "错": f"{type(exc).__name__}: {exc}"})
    finally:
        pass                      # tmp=None：不动默认缓存（索引要留着复用）

    ok_rows = [r for r in rows if "错" not in r]
    print("\n═══ 汇总 ═══")
    print(f"{'语料':18s}{'节点':>7s}{'拒收率':>8s}{'待命名':>7s}"
          f"{'请求':>5s}{'预测=实测':>9s}{'索引加速':>9s}{'等价':>5s}")
    for r in ok_rows:
        pr = r["谓词"]["拒收率"]
        print(f"{r['语料']:18s}{r['节点']:7d}{pr:7.0%} {r['组合']['待命名']:6d}"
              f"{r['请求']['条数']:5d}{'✓' if r['影响']['预测==实测'] else '✗':>9s}"
              f"{r['索引']['加速']:8.1f}×{'✓' if r['索引']['等价'] else '✗':>5s}")
    all_ok = (len(ok_rows) == len(CORPORA)
              and all(r["影响"]["预测==实测"] for r in ok_rows)
              and all(r["索引"]["等价"] for r in ok_rows))
    print(f"\n═══ {'全过' if all_ok else '有红（见上）'} ═══")
    for r in rows:
        if "错" in r:
            print(f"  ⚠ {r['语料']}：{r['错']}")
    print("  未测：ext-hepth（nodes 目录为空）· ext-slashdot（82168 节点，装载超工具时限）")
    with io.open(HERE / "_probe_new_data_full.rows.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print("留档：outputs/_probe_new_data_full.rows.json")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
