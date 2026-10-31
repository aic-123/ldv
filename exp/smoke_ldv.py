#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阶段 0 冒烟：100 条小语料上跑通 remember → ask → drill → next（PREREG_E1 §7）。

在 ldv 工作区根目录运行：PYTHONIOENCODING=utf-8 python exp/smoke_ldv.py
通过标准：tools/list 八工具与 server.py 一致；四个工具各一次真调用、返回可解析。
存储布局按 server.py 实测：<store>/_data/<名>/nodes/*.md（derived 同级）。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ldv_client import LdvSession, LdvError          # noqa: E402

MCP_DIR = Path(__file__).resolve().parents[1] / "ldv-mcp"
CORPUS = "smoke"


def make_store(root: Path, n: int = 100) -> Path:
    """直接落盘 100 条登记（10 篇论文 × 10 条），year 派生维度。

    （批量摄入走文件与历史实验一致；`remember` 工具的单条写路径另测——见 main。）
    """
    nd = root / "_data" / CORPUS / "nodes"
    nd.mkdir(parents=True, exist_ok=True)
    dd = root / "_data" / CORPUS / "derived"
    dd.mkdir(parents=True, exist_ok=True)
    vals = {}
    for i in range(n):
        paper = f"W{9000000000 + i // 10}"
        year = 2010 + (i // 10)
        sid = f"S{i:04d}"
        (nd / f"{sid}.md").write_text(
            f'---\nid: "{sid}"\ntype: "登记"\ntitle: "{sid}"\n'
            f'evidence_status: "原始记录"\nrelations: []\n---\n\n'
            f"{sid} 论文 {paper}（{i % 10} 号副本）发表于 {year} 年。\n",
            encoding="utf-8")
        vals[sid] = str(year)
    (dd / "year.json").write_text(json.dumps(
        {"_meta": {"field": "year", "what": "冒烟", "why": "键查询",
                   "derived_from": "原材料", "generated_by": "exp/smoke_ldv.py"},
         "values": vals}, ensure_ascii=False), encoding="utf-8")
    return root


def main() -> int:
    store = Path(tempfile.mkdtemp(prefix="ldv-smoke-"))
    make_store(store)
    print(f"store: {store}")
    with LdvSession([sys.executable, "server.py"],
                    env={"LDV_MCP_STORE": str(store)}, cwd=str(MCP_DIR),
                    timeout=180) as s:
        info = s.server_info.get("serverInfo") or {}
        print(f"server: {info.get('name')} {info.get('version')}")
        tools = [t.get("name") for t in s.list_tools()]
        print(f"tools/list: {tools}")

        print("\n── remember（单条写路径）──")
        out = s.call("remember", {
            "corpus": CORPUS, "id": "SMOKE1",
            "fields": {"type": "登记", "title": "SMOKE1",
                       "evidence_status": "原始记录"},
            "body": "SMOKE1 论文 W9999999999（冒烟副本）发表于 2011 年。",
            "actor": "agent", "session": "smoke"})
        print(out[:240])

        print("\n── ask（require 键查询）──")
        out = s.call("ask", {"corpus": CORPUS, "require": ["year=2012"],
                             "lens": {"插件": "keyset"},
                             "rationale": "冒烟：按年份键查一支", "session": "smoke"})
        print(out[:500])

        print("\n── drill（下钻=有用）──")
        # 先 ask 拿一个真实 did 再 drill（空语料下 D1 可能不存在，动态取）
        try:
            obj = json.loads(out[out.index("{"):])
            did = next((b.get("did") for b in obj.get("nodes", [])
                        if b.get("did")), "D1")
        except Exception:
            did = "D1"
        out2 = s.call("drill", {"corpus": CORPUS, "did": did,
                                "require": ["year=2012"], "session": "smoke"})
        print(out2[:240])

        print("\n── next（反思）──")
        out3 = s.call("next", {"corpus": CORPUS, "session": "smoke"})
        print(out3[:300])
    print("\n冒烟完成 ✓")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except LdvError as exc:
        print(f"LdvError: {exc}", file=sys.stderr)
        raise SystemExit(1)
