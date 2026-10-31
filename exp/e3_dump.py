#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E3 臂 B 的 CSV 导出：把 e1_store/_data/e1/nodes/*.md 机械转成一张 CSV。

规则（PREREG_E3 §2）：无人工编辑、逐节点顺序转换、产物 sha256 冻结。
CSV 列：node_id,type,title,content（content 内换行转 \n，字段引号转义按 RFC 4180）。
"""
from __future__ import annotations

import csv
import hashlib
import io
import sys
from pathlib import Path

STORE = Path(__file__).resolve().parent.parent / "e1_store" / "_data" / "e1" / "nodes"
OUT = Path(__file__).resolve().parent / "e3_rule_table.csv"


def parse_node(p: Path) -> dict:
    text = p.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {"node_id": p.stem, "type": "", "title": p.stem, "content": text}
    head, body = text[3:].split("---", 1)
    meta = {}
    for line in head.strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip('"')
    return {"node_id": meta.get("id", p.stem),
            "type": meta.get("type", ""),
            "title": meta.get("title", p.stem),
            "content": body.strip()}


def main() -> int:
    nodes = sorted(STORE.glob("*.md"))
    if not nodes:
        print("no nodes", file=sys.stderr)
        return 1
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["node_id", "type", "title", "content"])
    for p in nodes:
        n = parse_node(p)
        w.writerow([n["node_id"], n["type"], n["title"], n["content"]])
    OUT.write_text(buf.getvalue(), encoding="utf-8")
    data = OUT.read_bytes()
    print(f"nodes={len(nodes)} bytes={len(data)} sha256={hashlib.sha256(data).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
