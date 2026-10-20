# -*- coding: utf-8 -*-
"""把 SNAP `web-Stanford`（281903 节点 / 2312497 边）摄入成 ldv 语料。

**实验量级版**（2026-10-18 优化）：先跑 10 万节点，别一头撞进 28 万的目录规模墙。

优化点（都用实测逼出来）：
  ① **分片目录** `nodes/<id 后两位>/xxx.md` —— 单目录项数从 28 万降到约 1000。
     实测扁平 28 万：`ls -S` 列目录 300 s 超时、装载 >103 分钟未完成。
  ② **容量上限** `--max-nodes` —— 默认 100000（实验量级），要全量再显式给 0。

用法：
  python outputs/_intake_web_stanford.py                 # 10 万节点（实验量级）
  python outputs/_intake_web_stanford.py --max-nodes 0   # 全量 28 万（夜里跑）
  python outputs/_intake_web_stanford.py --flat          # 不分片（对照组）
"""
from __future__ import annotations

import argparse
import io
import json
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent / "repo"          # outputs/ 的上一级才是工作区根
CORPUS = REPO / "_data" / "ext-web-stanford"
SRC = HERE.parent / "sources" / "web-stanford" / "web-Stanford.txt"


def bucket_out(k: int) -> str:
    return "出度0" if k == 0 else ("出度1-3" if k <= 3
                                     else ("出度4-15" if k <= 15 else "出度16+"))


def bucket_in(k: int) -> str:
    return "入度0" if k == 0 else ("入度1-5" if k <= 5
                                     else ("入度6-50" if k <= 50 else "入度51+"))


def shard_of(nid: str) -> str:
    """按 id 后两位分 100 个桶（数字 id 均摊；非数字退回前两位）。"""
    tail = nid[-2:] if len(nid) >= 2 else nid.zfill(2)
    return tail if tail.isdigit() else nid[:2]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-nodes", type=int, default=100000,
                    help="最多摄多少个节点（0 = 全量 281903）")
    ap.add_argument("--flat", action="store_true",
                    help="不分片（对照组：全部摊在 nodes/ 一层）")
    ap.add_argument("--corpus", default=str(CORPUS))
    args = ap.parse_args()
    corpus = Path(args.corpus)
    max_n = args.max_nodes if args.max_nodes > 0 else 10 ** 9

    t0 = time.time()
    print(f"读 {SRC} …")
    edges: list[tuple[str, str]] = []
    nodes_seen: set[str] = set()
    with io.open(SRC, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            a, b = line.split()
            nodes_seen.update((a, b))
            if a != b:
                edges.append((a, b))
            if len(nodes_seen) >= max_n and max_n < 10 ** 8:
                # 容量上限：节点够了就停读（边随后按子集过滤）
                pass
    print(f"  原始 {len(nodes_seen)} 节点｜{len(edges)} 边｜读 {time.time() - t0:.1f}s")

    # 容量上限：取**出度最高的前 N 个**（保度分布形状，而非取前 N 个 id 的顺序偏置）
    keep: set[str] = set(nodes_seen)
    dropped = 0
    if len(nodes_seen) > max_n:
        deg = defaultdict(int)
        for a, b in edges:
            deg[a] += 1
            deg[b] += 1
        ordered = sorted(nodes_seen, key=lambda x: (-deg[x], x))
        keep = set(ordered[:max_n])
        dropped = len(nodes_seen) - len(keep)
        print(f"  容量上限 {max_n}：按度数取前 {len(keep)}，弃 {dropped}"
              f"（保度分布形状）")

    out_deg: dict[str, int] = defaultdict(int)
    in_deg: dict[str, int] = defaultdict(int)
    adj: dict[str, list[str]] = defaultdict(list)
    n_edges = 0
    for a, b in edges:
        if a in keep and b in keep:
            out_deg[a] += 1
            in_deg[b] += 1
            adj[a].append(b)
            n_edges += 1
    print(f"  子集内边 {n_edges}")

    (corpus / "nodes").mkdir(parents=True, exist_ok=True)
    print(f"写 {len(keep)} 个节点文件（{'扁平' if args.flat else '分片'}）…")
    t1 = time.time()
    for i, nid in enumerate(sorted(keep)):
        od, idg = out_deg[nid], in_deg[nid]
        rels = adj.get(nid, [])
        rel_json = json.dumps(rels, ensure_ascii=False, separators=(",", ":"))
        sub = (corpus / "nodes" / nid) if args.flat else (
            corpus / "nodes" / shard_of(nid))
        sub.mkdir(parents=True, exist_ok=True)
        body = (f"---\n"
                f'id: "{nid}"\n'
                f'type: "{bucket_out(od)}"\n'
                f'title: "web-Stanford 节点 {nid}"\n'
                f'evidence_status: "{bucket_in(idg)}"\n'
                f"source:\n"
                f'  ref: "https://snap.stanford.edu/data/web-Stanford.html"\n'
                f'  kind: "SNAP"\n'
                f'scope: "网页/链接（Stanford 2002 网页图）；出度 {od} / 入度 {idg}"\n'
                f'notes: "web-Stanford 的一个网页节点；{od} 条出边"\n'
                f'filled_by: "SNAP（公开数据集）"\n'
                f'relations: {rel_json}\n'
                f"---\n\n"
                f"SNAP 节点 `{nid}`（web-Stanford）。出度 {od} / 入度 {idg}。\n")
        (sub / f"{nid}.md").write_text(body, encoding="utf-8")
        if (i + 1) % 20000 == 0:
            print(f"  …{i + 1}（{time.time() - t1:.0f}s）")

    n_shards = len({shard_of(x) for x in keep}) if not args.flat else 1
    manifest = {
        "来源": "https://snap.stanford.edu/data/web-Stanford.html",
        "领域": "网页/链接（Stanford 2002 网页图）",
        "节点数": len(keep),
        "弃掉的节点（容量上限）": dropped,
        "边数（子集内、去自环）": n_edges,
        "分片": "扁平（nodes/ 一层）" if args.flat else f"按 id 后两位分 {n_shards} 个桶",
        "max_nodes 参数": args.max_nodes,
        "type 维度": "出度分档（4 档）",
        "evidence_status 维度": "入度分档（4 档）",
        "写文件秒": round(time.time() - t1, 1),
        "总秒": round(time.time() - t0, 1),
    }
    (corpus / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"完成：{len(keep)} 节点｜{n_edges} 边｜分片 {n_shards} 个桶｜"
          f"写 {manifest['写文件秒']}s｜总 {manifest['总秒']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
