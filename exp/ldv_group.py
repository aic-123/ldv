# -*- coding: utf-8 -*-
"""C 组：ldv。**不调任何模型**。

做法（这三步就是全部）：
1. 每条事实建成一个节点（`id=S0123`），并把 `entity` / `year` **声明成派生维度**：
   写 `<租户>/derived/entity.json` 与 `derived/year.json`（`{"_meta":…, "values":{id: 值}}`）
   ⇒ 每个节点拿到键 `entity=W123` / `year=2015`。
   ⚠️ 一开始我把 `entity` 写成节点的**字段**，然后查 `entity=W123` —— **查不到**：
      `keys_of()` 只把**字段名**做成键，值级键要经派生维度或枚举字段。这条路是仓库自带的，
      不是我发明的（`entry.App.__init__` 读 `derived/*.json` 时就在做这件事）。
2. 对**每个实体**做一次结构查询 `require=entity=W123` —— 拿回的是「这个实体的全部事实」，
   不是「最像的一段」。这一步把候选集从 O(材料) 压到 O(该实体的事实数)。
3. 比较取值：用节点**自己的键** `year=…`（不是我另存一张表）——两个不同值 ⇒ 一处矛盾，
   节点 id 就是定位。

⚠️ **C 组的成本要一起报**（预注册 §4）：
   · LLM token = 0；
   · 但要做 `O(实体数)` 次结构查询 —— 次数与墙钟都落盘；
   · **谓词（entity/attr/value）是脚本按数据集 schema 命名的**，这一步算人工成本，不许隐去。
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1] / "repo"
sys.path.insert(0, str(REPO))

import serve_tenants as T          # noqa: E402
import entry                        # noqa: E402

NODE = """---
id: "{sid}"
type: "事实"
title: "{sid}"
evidence_status: "派生子"
relations: []
---

{sid} 的原始陈述：{text}
"""

#: 派生维度 sidecar —— 这是「把某字段升成键」的**唯一**正规入口
SIDECAR_META = {
    "what": "实验一：把每条事实的实体/年份升成键（键 = `{field}=值`）",
    "why": "要按实体取「这个实体的全部事实」—— 靠字段名只能取到「所有有该字段的节点」",
    "derived_from": "材料的〔Sxxxx〕行（OpenAlex 标题/年份）",
    "generated_by": "exp/ldv_group.py（一次性，人声明维度）",
}


def build_tenant(mat: dict, root: Path, tenant: str = "exp1") -> tuple[Path, float]:
    t0 = time.perf_counter()
    if (root / tenant).exists():
        shutil.rmtree(root / tenant)
    T.create(root, tenant)
    nd = root / tenant / "nodes"
    for f in mat["facts"]:
        (nd / f"{f['sid']}.md").write_text(NODE.format(**f), encoding="utf-8")
    dd = root / tenant / "derived"
    dd.mkdir(exist_ok=True)
    for field, key in (("entity", "entity"), ("year", "year")):
        vals = {f["sid"]: str(f["entity"] if field == "entity" else f["value"])
                for f in mat["facts"]}
        (dd / f"{key}.json").write_text(json.dumps({
            "_meta": dict(SIDECAR_META, field=field,
                          **{"derived_at": "2026-10-29"}),
            "values": vals}, ensure_ascii=False), encoding="utf-8")
    return root / tenant, time.perf_counter() - t0


def group_C(mat: dict, root: Path, *, tenant: str = "exp1") -> dict:
    t_build0 = time.perf_counter()
    base, t_nodes = build_tenant(mat, root, tenant)
    app = entry.App(root=base / "nodes")
    t_load = time.perf_counter() - t_build0

    val_of = {f["sid"]: str(f["value"]) for f in mat["facts"]}   # 校验用（判分只认 id）
    eids = sorted({f["entity"] for f in mat["facts"]})
    pairs: list[tuple[str, str]] = []
    times: list[float] = []
    for eid in eids:
        t0 = time.perf_counter()
        q = app.query({"require": [f"entity={eid}"]})
        times.append(time.perf_counter() - t0)
        ids = list(q.get("answer_ids") or ())
        if len(ids) < 2:
            continue
        groups: dict[str, list[str]] = {}
        for nid in ids:
            # ★ 用节点**自己的键**取年份（`year=2015`），不是我另存的表
            yr = next((k.split("=", 1)[1] for k in (app.keys_of.get(nid) or ())
                       if k.startswith("year=")), "?")
            groups.setdefault(yr, []).append(nid)
        if len(groups) > 1:                     # 同实体 ≥2 个不同取值 ⇒ 矛盾
            a, b = sorted(ids)[:2]
            pairs.append((a, b))
    times.sort()
    return {"pairs": pairs, "queries": len(eids),
            "q_p50_ms": round(times[len(times) // 2] * 1000, 2) if times else 0.0,
            "q_total_s": round(sum(times), 2),
            "build_s": round(t_nodes, 2), "load_s": round(t_load - t_nodes, 2),
            "tokens": 0}
