#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""停层依据探针（接 `exp/m2` 开放项 #1）。

问题：`chain_of()` 给出根→支的**完整**路径（openalex 上 16 层），而人要 1–2 层。
问：若引入「该停了」的判据，链长能压到几层？停在哪一步、因为什么。

三个判据（全部结构可算，不引入排序/共现/先验）：

    S1 精度满    该支成员**全是**答案（再切只是拆分）
    S2 规模可枚举 该支成员数 ≤ K（七±二，一眼能看完）
    S3 后续全过路 从某一步之后，所有切分键的字段都不在需求里

取三者中**最靠前**的位置作为「该停的层」。

⚠️ 只读：不 expand、不写 usage、不改结构（跑前跑后比对签名）。
用法：python exp/m2/stop_probe.py [--k 7]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import entry as E                                    # noqa: E402
from ldv.core import chain as CH                     # noqa: E402

SRC = REPO / "_data" / "openalex-citations"

# 六条需求（键都真实存在于该语料；不涉及自然语言，翻译由 agent 负责）
REQS = [
    ("R1 综述", ["type=review"], []),
    ("R2 期刊论文", ["type=article", "source.kind=journal"], []),
    ("R3 2020前的综述", ["era=2020以前", "type=review"], []),
    ("R4 同行评审", ["evidence_status=同行评审"], []),
    ("R5 2020后论文", ["era=2020及以后", "type=article"], []),
    ("R6 会议论文", ["type=conference-paper"], []),
]


def add_era(root: Path) -> None:
    """注入 era 分档（与 exp/m1 的 V11/V12 同一做法，从 scope 的「发表年」解析）。"""
    from ldv.corpus.loader import load_nodes
    nodes = load_nodes(root / "nodes")
    vals = {}
    for nid, n in nodes.items():
        m = re.search(r"发表年\s*(\d{4})", str(n.fields.get("scope") or ""))
        if m:
            vals[nid] = "2020及以后" if int(m.group(1)) >= 2020 else "2020以前"
    d = root / "derived"
    d.mkdir(parents=True, exist_ok=True)
    (d / "era.json").write_text(json.dumps(
        {"_meta": {"field": "era", "what": "发表年分档", "derived_from": "scope",
                   "generated_by": "exp/m2/stop_probe.py"},
         "values": vals}, ensure_ascii=False), encoding="utf-8", newline="\n")


def stop_index(k, steps: list[dict], ans: set[str], K: int) -> tuple[int, str, int]:
    """返回 (该停的下标, 停因, 该层成员数)。三个判据取最靠前者。"""
    n = len(steps)
    # S1 第一个「支内全是答案」的层
    s1 = None
    for i, s in enumerate(steps):
        mem = k.members_of(k.direction(s["did"]))
        if mem and mem <= ans:
            s1 = i
            break
    # S2 第一个「成员数 ≤ K」的层
    s2 = None
    for i, s in enumerate(steps):
        if len(k.members_of(k.direction(s["did"]))) <= K:
            s2 = i
            break
    # S3 最后一个相干步（它之后全是过路）
    rel = [i for i, s in enumerate(steps) if s.get("相干")]
    s3 = max(rel) if rel else 0

    cands = [("精度满", s1), ("规模≤%d" % K, s2), ("后续全过路", s3)]
    cands = [(name, i) for name, i in cands if i is not None]
    name, idx = min(cands, key=lambda x: (x[1], x[0]))
    return idx, name, len(k.members_of(k.direction(steps[idx]["did"])))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=7)
    a = ap.parse_args()

    root = Path(tempfile.mkdtemp(prefix="m2stop-")) / "_data" / "oa"
    shutil.copytree(SRC, root)
    add_era(root)
    app = E.App(root=str(root / "nodes"))
    k = app.kernels["keyset"]
    sig_before = app.fingerprint()

    rows = []
    for name, req, forb in REQS:
        r = app.query({"require": req, "forbid": forb})
        if not r.get("ok") or r.get("out_of_structure"):
            rows.append({"需求": name, "状态": "out_of_structure 或失败", "支": 0})
            print(f"{name}: 跳过（{r.get('out_of_structure_note', '')[:40]}）", flush=True)
            continue
        ans = set(r["answer_ids"])
        fields = {str(x).split("=")[0] for x in [*req, *forb]}
        for node in r["nodes"]:
            c = CH.chain_of(k, node["did"], require=req, forbid=forb)
            steps = c["路"]
            idx, why, mem = stop_index(k, steps, ans, a.k)
            rows.append({
                "需求": name, "支": node["did"],
                "原链长": c["长"], "相干步": c["相干步"], "过路步": c["过路步"],
                "停在第几层": idx, "停因": why, "该层成员": mem,
                "停层后链长": idx + 1, "答案数": len(ans),
                "该层是否含全部答案": (k.members_of(k.direction(steps[idx]["did"])) <= ans),
            })
        print(f"{name}: 答案 {len(ans)}｜{len(r['nodes'])} 支", flush=True)

    sig_after = app.fingerprint()
    print(f"\n签名 {'一致 ✓（只读）' if sig_before == sig_after else '变了 ✗'}: "
          f"{sig_before} → {sig_after}", flush=True)

    if not rows:
        print("无有效行")
        return 1

    hdr = f"{'需求':14s} {'支':6s} {'原链长':>5s} {'相干':>4s} {'过路':>4s} {'停层':>4s} {'停因':10s} {'该层成员':>6s}"
    print("\n" + hdr)
    print("-" * len(hdr))
    for r in rows:
        if "原链长" not in r:
            continue
        print(f"{r['需求']:14s} {r['支']:6s} {r['原链长']:5d} {r['相干步']:4d} "
              f"{r['过路步']:4d} {r['停层后链长']:4d} {r['停因']:10s} {r['该层成员']:6d}")

    ok = [r for r in rows if "原链长" in r]
    if ok:
        import statistics
        before = [r["原链长"] for r in ok]
        after = [r["停层后链长"] for r in ok]
        from collections import Counter
        print("\n=== 汇总 ===")
        print(f"有效支 {len(ok)}｜原链长 均值 {statistics.mean(before):.1f} "
              f"(min {min(before)} / max {max(before)})")
        print(f"停层后 均值 {statistics.mean(after):.1f} "
              f"(min {min(after)} / max {max(after)})")
        print(f"压缩 {statistics.mean(before) / statistics.mean(after):.2f}×")
        print("停因分布:", dict(Counter(r["停因"] for r in ok)))
        print(f"停在 ≤2 层的支: {sum(1 for x in after if x <= 2)}/{len(after)}")
        print(f"停在 ≤3 层的支: {sum(1 for x in after if x <= 3)}/{len(after)}")
        # 停层是否会少报答案：该层成员必须覆盖全部答案
        miss = [r for r in ok if not r["该层是否含全部答案"]]
        print(f"停层后仍覆盖全部答案: {len(ok) - len(miss)}/{len(ok)}"
              + ("（少了答案 ⇒ 判据有假阴，必须修）" if miss else ""))

    out = Path(__file__).with_name("stop_probe.json")
    out.write_text(json.dumps({"k": a.k, "rows": rows}, ensure_ascii=False, indent=1),
                   encoding="utf-8", newline="\n")
    print(f"\n→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
