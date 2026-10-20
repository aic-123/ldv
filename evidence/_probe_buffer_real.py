# -*- coding: utf-8 -*-
"""用**真实数据**测试缓冲区（谓词三段 + 记忆 `_staging/`）。

四组：

  A **谓词缓冲 × 6 份真实语料** —— 三段不重不漏、拒收率分布、拒收理由
  B **记忆缓冲 × 真实节点**（副本上做，不碰原件）—— 跨语料搬 20 个真实节点
    → 缓冲期不影响结构 → 提交 → 结构增长 → **索引检测到新增**
  C **边界**：空缓冲区 / 全拒收（全撞 id）/ 大批量（100 个）
  D **交叉验证**：提交后索引必须失效（新增文件 ⇒ 重读），且提交前后结构签名可解释

跑法：python outputs/_probe_buffer_real.py
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent / "repo"
sys.path.insert(0, str(REPO))
os.chdir(REPO)

from ldv.corpus import predicates as PR              # noqa: E402
from ldv.corpus.index import load_nodes_indexed      # noqa: E402
from ldv.corpus.loader import load_edges, load_nodes  # noqa: E402
from ldv.tools import staging as ST                  # noqa: E402

PRED_CORPORA = [
    ("pypi-deps", "软件依赖"),
    ("ext-coauth", "学术合作"),
    ("snap-email-eu-mini", "邮件切片"),
    ("arxiv-taxonomy", "学术分类"),
    ("gh-langs", "开源生态"),
    ("crossref-journals", "学术出版"),
]


def ok(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  [{'过' if cond else '红'}] {label}" + (f"  —— {detail}" if detail else ""))
    return cond


def main() -> int:
    all_ok = True
    rows = []

    # ══ A. 谓词缓冲 × 6 份真实语料 ══════════════════════════════════
    print("═══ A. 谓词缓冲 × 6 份真实语料 ═══")
    print(f"{'语料':22s}{'节点':>7s}{'候选':>5s}{'采用':>5s}{'超预算':>7s}{'拒收':>5s}"
          f"{'拒收率':>7s}{'三段合':>7s}")
    for name, desc in PRED_CORPORA:
        nodes = load_nodes(REPO / "_data" / name / "nodes")
        edges, _d = load_edges(nodes)
        cand, used, rej, params = PR.derive_buffered(nodes, edges)
        u, r = {p.谓词 for p in used}, {x["谓词"] for x in rej}
        dropped = [p for p in cand if p.谓词 not in u and p.谓词 not in r]
        exact = (len(cand) == len(used) + len(dropped) + len(rej)) and not (u & r)
        rate = len(rej) / len(cand) if cand else 0
        print(f"{name:22s}{len(nodes):7d}{len(cand):5d}{len(used):5d}"
              f"{len(dropped):7d}{len(rej):5d}{rate:6.0%}{'✓' if exact else '✗':>7s}")
        all_ok &= exact
        rows.append({"语料": name, "说明": desc, "节点": len(nodes),
                     "候选": len(cand), "采用": len(used),
                     "超预算": len(dropped), "拒收": len(rej),
                     "三段不重不漏": exact,
                     "拒收明细": [{"谓词": x["谓词"], "满足数": x["满足数"],
                                   "理由": x["理由"]} for x in rej]})
    all_ok &= ok("A 六份语料全部：候选 = 采用 ∪ 超预算 ∪ 拒收（两两不交）",
                 all(r["三段不重不漏"] for r in rows),
                 "｜".join(f"{r['语料']}:{r['拒收']}拒收" for r in rows))

    # ══ B. 记忆缓冲 × 真实节点（副本上做）════════════════════════════
    print("\n═══ B. 记忆缓冲 × 真实节点（副本，不碰原件）═══")
    tmp = Path(tempfile.mkdtemp(prefix="ldv-real-buf-"))
    idx_root = tmp / "idx"
    try:
        # 拿 pypi-deps 的副本当「已有语料」
        corpus = tmp / "pypi"
        shutil.copytree(REPO / "_data" / "pypi-deps", corpus)
        base_n = len(load_nodes(corpus / "nodes"))
        print(f"  副本：pypi-deps（{base_n} 节点）")

        # 真实节点：从 ext-coauth 搬 20 个（id 格式完全不同 ⇒ 应该全提交）
        src = REPO / "_data" / "ext-coauth" / "nodes"
        picked = sorted(src.glob("*.md"))[:20]
        for f in picked:
            ST.stage(corpus, f)
        st = ST.status(corpus)
        print(f"  暂存 {len(st['待提交'])} 个真实节点（来自 ext-coauth）")

        # 缓冲期不影响结构
        during = load_nodes(corpus / "nodes")
        all_ok &= ok("B1 缓冲期：结构不变（暂存的东西不参与）",
                     len(during) == base_n, f"{len(during)} vs {base_n}")

        # 提交
        res = ST.commit(corpus)
        after = load_nodes(corpus / "nodes")
        print(f"  提交：{len(res['已提交'])} 个｜拒收 {len(res['拒收'])} 个")
        all_ok &= ok("B2 真实节点全部提交（id 格式不同 ⇒ 无冲突）",
                     len(res["已提交"]) == 20 and not res["拒收"],
                     f"提交 {len(res['已提交'])}｜拒收 {[x['文件'] for x in res['拒收']][:3]}")
        all_ok &= ok("B3 提交后结构增长 20",
                     len(after) == base_n + 20, f"{base_n} → {len(after)}")

        # ══ D. 交叉验证：提交后索引必须失效 ═══════════════════════════
        print("\n═══ D. 交叉验证：提交后索引必须检测到新增 ═══")
        n1, s1 = load_nodes_indexed(corpus / "nodes", idx_root)
        n2, s2 = load_nodes_indexed(corpus / "nodes", idx_root)
        all_ok &= ok("D1 首次建索引：全读（重读 = 节点数）",
                     s1["重读"] == len(n1), f"重读 {s1['重读']} / 命中 {s1['命中']}")
        all_ok &= ok("D2 二次：全命中", s2["重读"] == 0 and s2["命中"] == len(n1),
                     f"重读 {s2['重读']} / 命中 {s2['命中']}")
        # 再提交 1 个 ⇒ 索引应只重读 1 个
        one_more = sorted(src.glob("*.md"))[20]
        ST.stage(corpus, one_more)
        ST.commit(corpus)
        n3, s3 = load_nodes_indexed(corpus / "nodes", idx_root)
        all_ok &= ok("D3 提交 1 个后：索引**只重读 1 个**（其余仍命中）",
                     s3["重读"] == 1, f"重读 {s3['重读']} / 命中 {s3['命中']} / 共 {len(n3)}")

        # ══ C. 边界 ═══════════════════════════════════════════════════
        print("\n═══ C. 边界（空 / 全拒收 / 大批量）═══")
        # C1 空缓冲区
        empty = tmp / "empty"
        (empty / "nodes").mkdir(parents=True)
        r_empty = ST.commit(empty)
        all_ok &= ok("C1 空缓冲区：commit 不报错、返回空",
                     r_empty["已提交"] == [] and r_empty["拒收"] == [],
                     f"{r_empty}")

        # C2 全拒收：10 个文件 id 全部与已有重复
        dup = tmp / "dup"
        shutil.copytree(REPO / "_data" / "pypi-deps", dup)
        existing = sorted(load_nodes(dup / "nodes"))
        for i in range(10):
            f = tmp / f"_dup_{i}.md"
            f.write_text(f'---\nid: "{existing[i]}"\ntype: "测试"\n---\n\n撞车\n',
                         encoding="utf-8")
            ST.stage(dup, f)
        r_dup = ST.commit(dup)
        all_ok &= ok("C2 全拒收：10 个全撞 id ⇒ 0 提交、10 拒收、每条都有理由文件",
                     not r_dup["已提交"] and len(r_dup["拒收"]) == 10
                     and len(list((dup / "_staging" / "rejected").glob("*.理由.txt"))) == 10,
                     f"提交 {len(r_dup['已提交'])}｜拒收 {len(r_dup['拒收'])}"
                     f"｜理由文件 {len(list((dup / '_staging' / 'rejected').glob('*.理由.txt')))}")

        # C3 大批量：100 个真实节点
        big = tmp / "big"
        shutil.copytree(REPO / "_data" / "pypi-deps", big)
        big_base = len(load_nodes(big / "nodes"))
        for f in sorted(src.glob("*.md"))[100:200]:
            ST.stage(big, f)
        r_big = ST.commit(big)
        big_after = len(load_nodes(big / "nodes"))
        all_ok &= ok("C3 大批量：100 个真实节点全提交、结构 +100",
                     len(r_big["已提交"]) == 100 and big_after == big_base + 100,
                     f"{big_base} → {big_after}｜提交 {len(r_big['已提交'])}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n═══ 汇总：{'全过' if all_ok else '有红'} ═══")
    with io.open(HERE / "_probe_buffer_real.rows.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print("留档：outputs/_probe_buffer_real.rows.json")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
