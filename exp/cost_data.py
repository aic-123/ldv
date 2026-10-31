#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""COST.md 的数据准备：双模型全部落盘调用的用量与成本三栏。"""
import collections
import glob
import json

by = collections.defaultdict(lambda: {"calls": 0, "in": 0, "out": 0,
                                      "cached": 0, "miss": 0, "h": 0.0})
for p in glob.glob("exp/runs/E1-*.jsonl") + glob.glob("exp/runs/E2-*.jsonl"):
    for l in open(p, encoding="utf-8"):
        r = json.loads(l)
        m = r.get("model", "deepseek-flash")
        t = r.get("tokens") or {}
        if not t:
            continue
        k = by[m]
        k["calls"] += 1
        k["in"] += t.get("in", 0)
        k["out"] += t.get("out", 0)
        k["cached"] += t.get("cached", 0)
        k["miss"] += t.get("in", 0) - t.get("cached", 0)
        k["h"] += r.get("latency_s", 0) or 0

lines = []
for m, k in by.items():
    if m.startswith("deepseek"):
        cold = k["in"] * 1.0 / 1e6 + k["out"] * 4.0 / 1e6
        hot = (k["miss"] * 1.0 / 1e6 + k["cached"] * 0.02 / 1e6
               + k["out"] * 4.0 / 1e6)
        cost = f"冷 ¥{cold:.2f}｜热 ¥{hot:.2f}"
    else:
        cost = "¥0（免费档 glm-4.5-flash）"
    lines.append(f"{m:16s} 调用 {k['calls']:4d}｜输入 {k['in']/1e6:.2f}M"
                 f"（缓存命中 {k['cached']/1e6:.2f}M）｜输出 {k['out']/1e6:.3f}M"
                 f"｜{cost}｜落盘延迟合计 {k['h']/3600:.1f}h")
print("\n".join(lines))
