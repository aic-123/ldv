# -*- coding: utf-8 -*-
"""记忆缓冲区 —— `_staging/`（2026-10-17）。

## 为什么要缓冲

新节点直接写进 `nodes/` 有两个问题：

1. **没经过检查**：front matter 坏了、id 撞了，都会**静默**进结构
2. **看不出「进来过什么」**：拒收的、待处理的，事后无从追溯

⇒ 缓冲区分三步（与谓词缓冲同一个形状）：

    暂存   `<语料>/_staging/nodes/`   —— 新节点先放这里，**不参与结构**
    提交   校验通过 ⇒ 移进 `nodes/`
    拒收   校验不过 ⇒ `<语料>/_staging/rejected/` + `<名>.理由.txt`（留痕）

## 提交判据（自动筛选，可复现）

    ① front matter 能解析（解析抛异常 ⇒ 拒收）
    ② 有 `id`（没有就用文件名当 id —— 与 `loader` 的约定一致）
    ③ **id 不与已有节点重复**
    ④ **文件名不与已有节点重复**

四条都是**确定性**的（只看文件内容与现有节点集，不看历史）。

## 为什么不参与结构

`load_nodes()` 只读 `nodes/`，不碰 `_staging/` ⇒ 缓冲区里的东西**不影响任何
结构与读数**。这也是「暂存 ≠ 已提交」在结构上成立的原因。
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ..corpus.loader import parse_front_matter

#: 缓冲目录名（与 `nodes/` 平级）
STAGING = "_staging"


def _dirs(corpus_dir: Path) -> tuple[Path, Path, Path]:
    base = Path(corpus_dir) / STAGING
    return base / "nodes", base / "rejected", Path(corpus_dir) / "nodes"


def stage(corpus_dir: Path, src: Path) -> Path:
    """把一个新节点文件放进缓冲区（**不校验** —— 校验在提交时做）。"""
    inbox, _rej, _nodes = _dirs(corpus_dir)
    inbox.mkdir(parents=True, exist_ok=True)
    dst = inbox / Path(src).name
    shutil.copy2(src, dst)
    return dst


def status(corpus_dir: Path) -> dict[str, Any]:
    """缓冲区状态（待提交 / 已拒收 各多少个、分别是谁）。"""
    inbox, rej, nodes = _dirs(corpus_dir)
    pend = sorted(p.name for p in inbox.glob("*.md")) if inbox.is_dir() else []
    rejected = sorted(p.name for p in rej.glob("*.md")) if rej.is_dir() else []
    return {"待提交": pend, "已拒收": rejected,
            "正式节点数": len(list(nodes.glob("*.md"))) if nodes.is_dir() else 0}


def commit(corpus_dir: Path) -> dict[str, Any]:
    """提交缓冲区：**判据驱动**（四条），返回 `{"已提交": [...], "拒收": [...]}`。"""
    inbox, rej, nodes = _dirs(corpus_dir)
    if not inbox.is_dir():
        return {"已提交": [], "拒收": [], "说明": "缓冲区为空"}
    rej.mkdir(parents=True, exist_ok=True)

    # 现有节点的 id 与文件名（判据 ③④ 的对照集）
    existing_ids: set[str] = set()
    existing_names: set[str] = {p.name for p in nodes.glob("*.md")} if nodes.is_dir() else set()
    for p in (nodes.glob("*.md") if nodes.is_dir() else []):
        try:
            fields, _body = parse_front_matter(
                p.read_text(encoding="utf-8", errors="replace"), str(p))
            existing_ids.add(str(fields.get("id") or p.stem))
        except Exception:
            continue                        # 已存在的坏文件不阻塞提交

    committed: list[str] = []
    rejected: list[dict[str, str]] = []
    for f in sorted(inbox.glob("*.md")):
        reason = None
        nid = None
        try:
            fields, _body = parse_front_matter(
                f.read_text(encoding="utf-8", errors="replace"), str(f))
            nid = str(fields.get("id") or f.stem)
        except Exception as exc:            # 判据 ①
            reason = f"front matter 解析失败：{type(exc).__name__}: {exc}"
        if reason is None and nid in existing_ids:      # 判据 ③
            reason = f"id 与已有节点重复：{nid!r}"
        if reason is None and f.name in existing_names:  # 判据 ④
            reason = f"文件名与已有节点重复：{f.name!r}"

        if reason is None:
            shutil.move(str(f), str(nodes / f.name))
            existing_ids.add(nid)
            existing_names.add(f.name)
            committed.append(f.name)
        else:
            shutil.move(str(f), str(rej / f.name))
            (rej / f"{f.name}.理由.txt").write_text(reason + "\n", encoding="utf-8")
            rejected.append({"文件": f.name, "理由": reason})

    return {"已提交": committed, "拒收": rejected}
