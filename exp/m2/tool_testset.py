# -*- coding: utf-8 -*-
"""M2 测试集工具：校验 / 冻结 / 生成作答表 / 打分 / 打快照。

    python3 exp/m2/tool_testset.py check      # 校验 30 题在当前语料上是否可判（不写任何文件）
    python3 exp/m2/tool_testset.py freeze     # 把 draft 升为 testset.json 并写 testset.sha256
    python3 exp/m2/tool_testset.py sheet      # 生成 answer_sheet.json（人作答用）
    python3 exp/m2/tool_testset.py snapshot --out exp/m2/run/t0/partitions.json
    python3 exp/m2/tool_testset.py score --picks exp/m2/human_picks.json \
        --partitions exp/m2/run/t0/partitions.json

⚠️ **支的身份不用 did**。`did` 由 `(父, payload)` 派生，树一长就全变；拿它跨时间点
   对齐，等于每次都在跟新编号比。这里一律用**成员集**（`frozenset(条目 id)`）认支。
   理由与三档命中口径见 `TESTSET.md` §0。

⚠️ 本工具**不碰** `_data`，不写 usage，不建内核副本。只读。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from entry import App  # noqa: E402

M2 = Path(__file__).resolve().parent
DRAFT = M2 / "testset.draft.json"
FROZEN = M2 / "testset.json"
SHEET = M2 / "answer_sheet.json"

#: 语料根 —— 环境变量优先，其次两个已知落点（不猜第三个）。
_CANDIDATES = [
    Path(os.environ["LDV_STORE"]) if os.environ.get("LDV_STORE") else None,
    ROOT.parent / "ldv-use" / "store",
    Path("/workspace/ldv-use/store"),
]
STORE = next((p for p in _CANDIDATES if p and (p / "_data" / "self" / "nodes").is_dir()), None)


def _load(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _app() -> App:
    if STORE is None:
        raise SystemExit("找不到语料：设 LDV_STORE=/path/to/store（其下有 _data/self/nodes）")
    # ⚠️ 必须给 **nodes 目录**：给 store 根的话 `rglob("*.md")` 会把 usage/ 与
    #    answers/ 一起收进来（实测 120 → 144），那不是语料，是账本。
    return App(root=str(STORE / "_data" / "self" / "nodes"))


def _partition(app: App, item: dict) -> dict:
    """跑一条题，返回它的支划分（成员集口径）。"""
    resp = app.query({"require": list(item["require"]), "forbid": list(item["forbid"])})
    if not resp.get("ok"):
        return {"ok": False, "error": resp.get("error") or resp}
    kern = app.kernels[resp.get("plugin") or "keyset"]
    branches = []
    n = resp.get("n_answer") or 0
    # ⚠️ 零结果时 `entry.query` 会把**根**当成唯一一支返回（`n_members` = 全体 144，
    #    `status` = 「没有项满足这组约束」，`coverage` = 0）。根不是「支」——
    #    空答案没有支。这里按 `stopped == "empty"` 置空，否则「空」会被当成
    #    「一支覆盖全部」，后面所有重合度都会被这一支污染。
    if n and resp.get("stopped") != "empty":
        for b in resp.get("nodes") or []:
            d = kern.direction(b["did"])
            branches.append({
                "must": sorted(b.get("must") or []),
                "mustnot": sorted(b.get("mustnot") or []),
                "members": sorted(kern.members_of(d)),
            })
    return {
        "ok": True,
        "n_answer": resp.get("n_answer"),
        "answer_ids": sorted(resp.get("answer_ids") or []),
        "stopped": resp.get("stopped"),
        "branches": branches,
    }


def _sig(part: dict) -> tuple:
    """划分的成员集签名 —— 与 did、谓词写法都无关。"""
    return tuple(sorted(tuple(b["members"]) for b in part["branches"]))


def cmd_check(args) -> int:
    ts = _load(DRAFT if not args.frozen else FROZEN)
    app = _app()
    rows, bad = [], []
    for it in ts["items"]:
        p = _partition(app, it)
        if not p["ok"]:
            bad.append(f"{it['qid']}: 查询失败 {p['error']}")
            continue
        n = p["n_answer"]
        if it["expect_empty"] and n != 0:
            bad.append(f"{it['qid']}: 预期为空，实得 {n} 条")
        if not it["expect_empty"] and n == 0:
            bad.append(f"{it['qid']}: 预期非空，实得 0 条")
        rows.append((it, p))

    # 改写对：同一语义、两种键表达，划分必须逐支相同
    pairs = {}
    for it, p in rows:
        if it.get("pair"):
            pairs.setdefault(it["pair"], []).append((it, p))
    for pid, two in sorted(pairs.items()):
        if len(two) != 2:
            bad.append(f"{pid}: 改写对不完整（{len(two)} 条）")
            continue
        (a, pa), (b, pb) = two
        if _sig(pa) != _sig(pb):
            bad.append(f"{pid}: 两种说法切法不同 —— {a['qid']} 切 {len(pa['branches'])} 支，"
                       f"{b['qid']} 切 {len(pb['branches'])} 支")

    w = [0, 4, 6, 6]
    print(f"{'qid':5s}{'类':6s}{'n':>4s}{'支':>3s}  {'切法（成员数）':60s}")
    print("-" * 84)
    for it, p in rows:
        cut = " | ".join(
            f"{'+'.join(b['must']) or '∅'}={len(b['members'])}" for b in p["branches"])
        print(f"{it['qid']:5s}{it['cls']:6s}{p['n_answer']:4d}{len(p['branches']):3d}  {cut[:60]}")
    print("-" * 84)
    n_scored = sum(1 for it in ts["items"] if it["scored"])
    print(f"共 {len(ts['items'])} 题（计分 {n_scored}、不计分 {len(ts['items'])-n_scored}）")
    if bad:
        print("\n✗ 不通过：")
        for b in bad:
            print("  -", b)
        return 1
    print("✓ 全部通过：每条可判、空/非空符合预期、5 对改写切法一致")
    print(f"  预冻结哈希（draft 规范化 JSON 的 sha256）= {_hash(DRAFT)}")
    return 0


def _canon(path: Path) -> bytes:
    ts = _load(path)
    ts.pop("_schema", None)
    ts["status"] = "frozen"
    ts["frozen"] = True
    return json.dumps(ts, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _hash(path: Path) -> str:
    return hashlib.sha256(_canon(path)).hexdigest()


def cmd_freeze(args) -> int:
    if FROZEN.exists() and not args.force:
        raise SystemExit(f"{FROZEN} 已存在 —— 冻结只能发生一次。要覆盖加 --force（会在 RESULTS 里留痕）")
    ts = _load(DRAFT)
    ts["status"] = "frozen"
    ts["frozen"] = True
    ts["frozen_at"] = args.at
    body = json.dumps(ts, ensure_ascii=False, indent=2)
    FROZEN.write_text(body + "\n", encoding="utf-8")
    h = hashlib.sha256(_canon(FROZEN)).hexdigest()
    (M2 / "testset.sha256").write_text(f"{h}  testset.json\n", encoding="utf-8")
    print(f"已冻结 {FROZEN.name}，sha256={h}")
    return 0


def cmd_sheet(args) -> int:
    ts = _load(FROZEN if FROZEN.exists() else DRAFT)
    out = {
        "_how": "每题给 1–3 组；每组写 label（一句话说明这组是「哪一类」）与 items"
                "（条目 id，照 CORPUS_CARD.md 抄）。改写对只答一次，两边共用同一份选择。"
                "答完写 human_picks.json，之后不再改。",
        "_scale": "1–3 组，多了记不下；一组最少 1 条。",
        "answers": {},
    }
    seen = set()
    for it in ts["items"]:
        pair = it.get("pair")
        if pair:
            if pair in seen:
                continue
            seen.add(pair)
            members = [x["qid"] for x in ts["items"] if x.get("pair") == pair]
            out["answers"][pair] = {"covers": members, "nl": it["nl"], "groups": []}
        else:
            out["answers"][it["qid"]] = {"covers": [it["qid"]], "nl": it["nl"], "groups": []}
    SHEET.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写 {SHEET}（{len(out['answers'])} 项，其中改写对 {len(seen)} 组）")
    return 0


def cmd_snapshot(args) -> int:
    ts = _load(FROZEN if FROZEN.exists() else DRAFT)
    app = _app()
    out = {"corpus": ts.get("corpus"), "at": args.at, "items": {}}
    for it in ts["items"]:
        out["items"][it["qid"]] = _partition(app, it)
    dst = Path(args.out)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"已写 {dst}（{len(out['items'])} 题）")
    return 0


def cmd_score(args) -> int:
    picks = _load(Path(args.picks))["answers"]
    parts = _load(Path(args.partitions))["items"]
    tot = hit = 0
    detail = []
    for key, entry in picks.items():
        # 作答表里改写对是一项，覆盖两题；两题各自打分（它们本就该同分）
        for qid in entry.get("covers") or [key]:
            p = parts.get(qid) or {}
            if not p.get("ok"):
                continue
            branches = [frozenset(b["members"]) for b in p["branches"]]
            for g in entry.get("groups") or []:
                want = frozenset(g.get("items") or [])
                if not want:
                    continue
                tot += 1
                kind = "miss"
                if want in branches:
                    kind, hit = "strict", hit + 1
                elif any(want < b for b in branches):
                    kind = "coarse"            # 系统切得更粗：这一组被并进更大的支
                elif any(want & b for b in branches):
                    sub = [b for b in branches if b & want]
                    if frozenset().union(*sub) == want:
                        kind = "refined"       # 系统切得更细：并起来才等于这一组
                detail.append((qid, kind, len(want)))
    for qid, kind, n in detail:
        print(f"  {qid:5s} {kind:8s} 组内 {n:2d} 条")
    from collections import Counter
    print(f"\n严格命中 {hit}/{tot} = {hit/tot:.3f}" if tot else "没有可打的组")
    print("分布：", dict(Counter(k for _, k, _ in detail)))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check"); c.add_argument("--frozen", action="store_true")
    f = sub.add_parser("freeze"); f.add_argument("--at", default=""); f.add_argument("--force", action="store_true")
    sub.add_parser("sheet")
    s = sub.add_parser("snapshot"); s.add_argument("--out", required=True); s.add_argument("--at", default="")
    sc = sub.add_parser("score"); sc.add_argument("--picks", required=True); sc.add_argument("--partitions", required=True)
    args = ap.parse_args()
    return {"check": cmd_check, "freeze": cmd_freeze, "sheet": cmd_sheet,
            "snapshot": cmd_snapshot, "score": cmd_score}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
