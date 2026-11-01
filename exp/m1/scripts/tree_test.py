#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V8（PREREG）：Tree Testing —— 首点正确率（first-click correctness）。

两棵树（A 从 era 入口、B 从 type 入口）× 10 个任务（A 类时间 / B 类类型），
每任务一次调用：只看分支列表，选先进入哪个。省略在这里是扣分项。
另加阴性对照（谓词—成员数打乱）。
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "exp"))
import entry as E                                    # noqa: E402
import llm                                            # noqa: E402

M1 = Path(__file__).resolve().parents[1]
OUT = M1 / "v_tree"
SRC = REPO / "_data" / "openalex-citations"

TASKS_A = [
    ("2020 年及以后发表的综述（review）", ["era=2020及以后", "type=review"]),
    ("2020 年及以后发表的期刊论文（article）", ["era=2020及以后", "type=article"]),
    ("2020 年以前发表的综述", ["era=2020以前", "type=review"]),
    ("2020 年及以后、同行评审的材料", ["era=2020及以后", "evidence_status=同行评审"]),
    ("2020 年以前发表的会议论文", ["era=2020以前", "type=conference-paper"]),
]
TASKS_B = [
    ("期刊上的论文（article, source.kind=journal）", ["type=article", "source.kind=journal"]),
    ("同行评审的综述", ["type=review", "evidence_status=同行评审"]),
    ("同行评审的会议论文", ["type=conference-paper", "evidence_status=同行评审"]),
    ("预印本（preprint）", ["type=preprint"]),
    ("来自仓储（非期刊）的论文", ["type=article", "evidence_status=仓储"]),
]


def add_era(root: Path) -> None:
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
        {"_meta": {"field": "era", "what": "发表年分档", "derived_from": "scope"},
         "values": vals}, ensure_ascii=False), encoding="utf-8", newline="\n")


def branches_from(app, require: list[str]) -> list[dict]:
    """某入口下的顶层分支（谓词 + 成员集合）。"""
    k = app.kernels["keyset"]
    r = app.query({"require": require})
    out = []
    for n in (r.get("nodes") or []):
        d = k.direction(n["did"])
        req, forb = d.payload
        out.append({"did": d.did, "members": frozenset(k.members_of(d)),
                    "label": (" ∧ ".join(sorted(req)) or "（全部）")
                             + (" ∧ ¬(" + " ∨ ".join(sorted(forb)) + ")" if forb else "")})
    return out


def target_items(app, require: list[str]) -> set[str]:
    return {nid for nid, ks in app.keys_of.items() if set(require) <= ks}


def best_branch(branches: list[dict], target: set[str]) -> tuple[int, float]:
    best_i, best_r = -1, -1.0
    for i, b in enumerate(branches):
        if not target:
            continue
        r = len(b["members"] & target) / len(target)
        if r > best_r + 1e-12 or (abs(r - best_r) < 1e-12 and
                                  (best_i < 0 or len(b["members"]) < len(branches[best_i]["members"]))):
            best_i, best_r = i, r
    return best_i, best_r


def render_list(branches: list[dict], order: list[int], scramble: bool = False) -> str:
    lines = []
    labels = [b["label"] for b in branches]
    sizes = [len(b["members"]) for b in branches]
    if scramble:
        rng = random.Random(7788)
        rng.shuffle(sizes)
    for j, idx in enumerate(order):
        n = sizes[idx] if scramble else len(branches[idx]["members"])
        lines.append(f"{j + 1}. {labels[idx]} ｜ {n} 项")
    return "\n".join(lines)


def prompt(task: str, listing: str) -> str:
    return f"""下面是一个分类结构的**第一层**分支列表（同一个材料库的不同分支，编号 1..N）。

你的任务：判断该先进入哪个分支，才能找到下面这类材料。

任务：找到「{task}」

{listing}

只能选一个分支。先说明一句理由，然后输出 JSON：
{{"pick": 序号, "why": "一句话"}}"""


def run(tree_name: str, branches: list[dict], tasks: list[tuple], tag: str) -> dict:
    rows = []
    for task_desc, req in tasks:
        target = target_items_raw(req)
        best_i, best_r = best_branch(branches, target)
        valid = best_i >= 0 and best_r >= 0.5
        row = {"task": task_desc, "require": req, "n_target": len(target),
               "best_ratio": round(best_r, 3), "valid": valid}
        if valid:
            hits_real = hits_null = 0
            for inst in range(5):
                order = list(range(len(branches)))
                random.Random(900 + inst).shuffle(order)
                listing = render_list(branches, order)
                picked = ask_pick(prompt(task_desc, listing))
                # 真实结构下的正确序号 = order.index(best_i) + 1
                correct_no = order.index(best_i) + 1
                hit = picked == correct_no
                hits_real += int(hit)
                # 阴性：谓词-成员数打乱
                listing2 = render_list(branches, order, scramble=True)
                picked2 = ask_pick(prompt(task_desc, listing2))
                hits_null += int(picked2 == correct_no)
                row.setdefault("instances", []).append(
                    {"inst": inst, "pick": picked, "correct_no": correct_no, "hit": hit})
            row["real_rate"] = hits_real / 5
            row["null_rate"] = hits_null / 5
        rows.append(row)
        print(f"  [{tag}] {task_desc[:24]:24s} valid={valid} best={best_r:.2f} "
              f"real={row.get('real_rate')} null={row.get('null_rate')}", flush=True)
    return {"tree": tree_name, "rows": rows}


_TARGET_CACHE: dict = {}


def target_items_raw(req: list[str]) -> set[str]:
    return _TARGET_CACHE[tuple(sorted(req))]


def ask_pick(p: str) -> int:
    r = llm.chat([{"role": "user", "content": p}], model="deepseek-chat",
                 max_tokens=800, temperature=0, tries=4)
    text = r.get("text") or ""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    js = json.loads(m.group(0)) if m else {}
    try:
        return int(js.get("pick"))
    except Exception:                                  # noqa: BLE001
        nums = re.findall(r"\b(\d+)\b", text)
        return int(nums[0]) if nums else -1


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="m1tree-")) / "_data" / "oa"
    shutil.copytree(SRC, root)
    add_era(root)
    a = E.App(root=str(root / "nodes"))

    for _, req in TASKS_A + TASKS_B:
        _TARGET_CACHE[tuple(sorted(req))] = target_items(a, req)

    treeA = branches_from(a, ["era=2020及以后"])
    treeB = branches_from(a, ["type=article"])
    print(f"A 树分支 {len(treeA)}｜B 树分支 {len(treeB)}", flush=True)

    rA = run("A", treeA, TASKS_A, "A树×A类") | {"task_class": "A"}
    rA2 = run("A", treeA, TASKS_B, "A树×B类") | {"task_class": "B"}
    rB = run("B", treeB, TASKS_B, "B树×B类") | {"task_class": "B"}
    rB2 = run("B", treeB, TASKS_A, "B树×A类") | {"task_class": "A"}

    def agg(res: dict) -> dict:
        valid = [r for r in res["rows"] if r["valid"]]
        real = sum(r["real_rate"] for r in valid) / len(valid) if valid else None
        null = sum(r["null_rate"] for r in valid) / len(valid) if valid else None
        return {"n_tasks": len(res["rows"]), "n_valid": len(valid),
                "real_rate": round(real, 3) if real is not None else None,
                "null_rate": round(null, 3) if null is not None else None}

    summary = {
        "A_tree_A_tasks": agg(rA), "A_tree_B_tasks": agg(rA2),
        "B_tree_B_tasks": agg(rB), "B_tree_A_tasks": agg(rB2),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "result.json").write_text(json.dumps(
        {"summary": summary, "detail": [rA, rA2, rB, rB2]},
        ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
