#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在 120 条语料上量「链长」的两种口径（接 STOP_PROBE.md §4 建议）。

语料**由 `exp/m2/CORPUS_CARD.md` 重建**（卡片是语料本身的人读版，字段名照 `T0.md` 的措辞）。
⚠️ 这不是原始 `nodes/`：`did` 会不同，但**链长/LCA 只依赖结构形状**，与 did 无关。
读数用于判定「链长 5–10 是缺陷还是度量口径」，**不与 `chains.json` 的 26 步逐字比**。

两种口径：
    单支到叶 = 根 → 该支（`chain_of` 的长度）
    LCA     = 根 → 所有答案支开始分叉的那一层（讲给人听只需要讲到这里）
"""
from __future__ import annotations

import json
import re
import shutil
import statistics
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import entry as E                                    # noqa: E402
from ldv.core import chain as CH                     # noqa: E402

CARD = REPO / "exp" / "m2" / "CORPUS_CARD.md"

# ── 每类的解析器：正文 → 字段 ────────────────────────────────────────────────
PARSERS = {
    "读书": (r"^(.+?)，(.+?) 著，(\d+) 页，主题 (.+?)。$",
             ["title", "作者", "页数", "主题"]),
    "财务": (r"^(.+?)：(\d+) 元，类别 (.+?)，(\d+) 月。$",
             ["项目", "金额", "类别", "月份"]),
    "会议": (r"^(.+?)：(.+?)。日期 (\d{8})，参会 (\d+) 人。$",
             ["title", "议题", "日期", "参会"]),
    "报修": (r"^(.+?)：(.+?)，报修于 (\d{8})，当前状态 (.+?)。$",
             ["title", "位置", "报修日", "状态"]),
    "健身": (r"^(.+?)：(\d+) 组，(\d+) kg，练 (.+?)。$",
             ["title", "组数", "重量", "部位"]),
    "食谱": (r"^(.+?)：食材 (.+?)，耗时 (\d+) 分钟，口味 (.+?)。$",
             ["title", "食材", "耗时", "口味"]),
    "旅行": (r"^(.+?)：(\d+) 天，预算 (\d+) 元，(.+?)，路线 (.+?)。$",
             ["title", "天数", "预算", "季节", "路线"]),
    "论文": (r"^(.+?)。(\d{4}) 年发表在 (.+?)，方法是 (.+?)，关注 (.+?)。$",
             ["title", "年份", "发表", "方法", "关注"]),
}

CATS = ["读书", "财务", "会议", "报修", "健身", "食谱", "旅行", "论文"]

# 参考 chains.json 的语义，改成等值谓词（数值比较内核要 derived，这里不引入）
REQS = [
    ("C1 读书+重要", ["type=读书", "标记=重要"]),
    ("C2 健身+背", ["type=健身", "部位=背"]),
    ("C3 食谱+清淡", ["type=食谱", "口味=清淡"]),
    ("C4 旅行+夏季", ["type=旅行", "季节=夏季"]),
    ("C5 财务+固定支出", ["type=财务", "类别=固定支出"]),
    ("C6 报修+待修", ["type=报修", "状态=待修"]),
    ("C7 会议+6人", ["type=会议", "参会=6"]),
    ("C8 论文+NeurIPS", ["type=论文", "发表=NeurIPS"]),
]


def yaml_scalar(v: str) -> str:
    s = str(v)
    return f'"{s}"' if any(c in s for c in ':#"') or s.strip() != s else s


def parse_card() -> list[dict]:
    rows = []
    cur = None
    for line in CARD.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^##\s+(.+?)（(\d+)\s*条）", line.strip())
        if m:
            cur = m.group(1).strip()
            continue
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or cells[0].startswith("---") or cells[0] == "id":
            continue
        nid = cells[0].strip("`").strip()
        mark = cells[1].strip()
        text = cells[2].strip()
        if cur not in PARSERS:
            continue
        pattern, names = PARSERS[cur]
        mm = re.match(pattern, text)
        if not mm:
            rows.append({"id": nid, "cat": cur, "mark": mark, "text": text,
                         "fields": None})
            continue
        rows.append({"id": nid, "cat": cur, "mark": mark, "text": text,
                     "fields": dict(zip(names, mm.groups()))})
    return rows


def build_corpus(rows: list[dict], root: Path) -> tuple[int, int]:
    nodes = root / "nodes"
    nodes.mkdir(parents=True, exist_ok=True)
    ok = bad = 0
    for r in rows:
        if r["fields"] is None:
            bad += 1
            print(f"  !! 解析失败: {r['id']} {r['text'][:40]}", flush=True)
            continue
        ok += 1
        lines = ["---", f'id: {yaml_scalar(r["id"])}', f'type: {yaml_scalar(r["cat"])}']
        for k, v in r["fields"].items():
            lines.append(f"{k}: {yaml_scalar(v)}")
        if r["mark"]:
            lines.append(f'标记: "{ "重要" if r["mark"] == "★" else "存疑" }"')
        lines += ['filled_by: "语料卡重建（2026-11-02）"', "relations: []", "---",
                  r["text"], ""]
        (nodes / (r["id"].replace("/", "_") + ".md")).write_text(
            "\n".join(lines), encoding="utf-8", newline="\n")
    return ok, bad


def main() -> int:
    rows = parse_card()
    print(f"解析语料卡：{len(rows)} 条行", flush=True)
    root = Path(tempfile.mkdtemp(prefix="m2relca-")) / "_data" / "self120"
    ok, bad = build_corpus(rows, root)
    print(f"写入节点：成功 {ok} / 失败 {bad}", flush=True)
    if ok < 100:
        print("!! 解析不足，停止")
        return 2

    app = E.App(root=str(root / "nodes"))
    k = app.kernels["keyset"]

    # ── 值谓词声明（T0.md：字段在语料里、只是没进 ENUM_FIELDS ⇒ 走 declare 闭环）──
    from ldv.core import declare as DECL
    declared: set[str] = set()

    def ensure(req: list[str]) -> None:
        """缺哪个键就声明哪个（写 <语料根>/predicates.json），然后重建 App。"""
        nonlocal app, k
        miss = [x for x in req if x not in app.all_keys and x not in declared]
        if not miss:
            return
        for key in miss:
            try:
                DECL.add(root, key, why="relca 探针：等值需求（语料卡字段）",
                         by="agent")
                declared.add(key)
                print(f"  declare {key} ✓", flush=True)
            except Exception as exc:                       # noqa: BLE001
                print(f"  declare {key} 失败: {str(exc)[:70]}", flush=True)
        app = E.App(root=str(root / "nodes"))
        k = app.kernels["keyset"]

    print(f"结构：{len(app.nodes)} 节点｜{len(k.all_directions())} 方向｜"
          f"签名 {app.fingerprint()}｜树 {app.tree}", flush=True)

    print(f"\n{'需求':18s} {'答案':>4s} {'支':>3s} {'LCA':>4s} {'单支均值':>7s} {'最短支':>6s}")
    print("-" * 52)
    agg = []
    for name, req in REQS:
        ensure(req)
        r = app.query({"require": req})
        if not r.get("ok") or r.get("out_of_structure"):
            print(f"{name:18s} 跳过（{str(r.get('out_of_structure_note'))[:24]}）")
            continue
        ans = r["answer_ids"]
        dids = [n["did"] for n in r["nodes"]]
        paths = []
        for d in dids:
            c = CH.chain_of(k, d, require=req)
            paths.append([s["did"] for s in c["路"]])
        L = 0
        for i in range(min(len(p) for p in paths)):
            if len({p[i] for p in paths}) == 1:
                L = i + 1
            else:
                break
        lens = [len(p) for p in paths]
        agg.append({"需求": name, "答案": len(ans), "支": len(dids), "LCA": L,
                    "单支均值": round(statistics.mean(lens), 1), "最短支": min(lens)})
        print(f"{name:18s} {len(ans):4d} {len(dids):3d} {L:4d} "
              f"{statistics.mean(lens):7.1f} {min(lens):6d}", flush=True)

    if agg:
        print("\n=== 汇总（120 条重建语料）===")
        print(f"有效需求 {len(agg)}｜LCA 均值 {statistics.mean([x['LCA'] for x in agg]):.1f} "
              f"(min {min(x['LCA'] for x in agg)} / max {max(x['LCA'] for x in agg)})")
        print(f"单支到叶 均值 {statistics.mean([x['单支均值'] for x in agg]):.1f}")
        print(f"LCA ≤2 的需求: {sum(1 for x in agg if x['LCA'] <= 2)}/{len(agg)}")
        print(f"LCA ≤3 的需求: {sum(1 for x in agg if x['LCA'] <= 3)}/{len(agg)}")

    out = Path(__file__).with_name("relca_probe.json")
    out.write_text(json.dumps({"nodes": ok, "rows": agg}, ensure_ascii=False, indent=1),
                   encoding="utf-8", newline="\n")
    print(f"\n→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
