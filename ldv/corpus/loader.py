"""语料装载 —— 36 个 scaffold 节点。

**零第三方依赖**：自己写一个只覆盖语料**实际形状**的 front-matter 解析器。
不引 PyYAML 的理由与 dce 一致（§十七 零第三方 import）。

只认四种形状：

    标量          `type: 概念`  /  `title: "..."`
    行内列表      `aliases: ["a", "b"]`
    块列表        `cues:` 换行 + `  - "..."`
    一层嵌套映射  `source:` 换行 + `  ref: ...` + `  kind: ...`

**形状之外一律报错，不猜。** 猜错的后果是静默产出一批错语料 ——
那是最难查的一类故障（dce 的 `LESSONS.md` 里记过同类）。

---

## 节点的「键」

方向 A 要的键 = 字段名 ∪ 枚举字段的**取值**：

    {id, type, title, aliases, cues, scope, source, source.kind,
     evidence_status, relations, filled_by, notes,
     "type=概念", "evidence_status=未验证", "source.kind=教材"}

把取值也做成键，是为了让「按类型分方向」这种最自然的意图**表达得出来** ——
否则方向 A 只能按「有没有 aliases 字段」这种没意义的轴切。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 语料里出现过的枚举字段 —— 它们的**取值**也进键集
ENUM_FIELDS = ("type", "evidence_status")

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


class CorpusError(RuntimeError):
    """语料形状不认识 —— 报错，不猜。"""


@dataclass
class Node:
    id: str
    fields: dict[str, Any]
    body: str = ""
    keys: frozenset[str] = field(default_factory=frozenset)

    def relations(self) -> list[str]:
        rel = self.fields.get("relations") or []
        return [str(r) for r in rel] if isinstance(rel, list) else []

    def as_item(self) -> dict[str, Any]:
        return {"id": self.id, "keys": self.keys, "node": self}


def parse_front_matter(text: str, where: str = "<text>") -> tuple[dict[str, Any], str]:
    m = _FM_RE.match(text)
    if not m:
        raise CorpusError(f"{where}: 没有 front-matter（`---` 包裹的块）")
    return _parse_block(m.group(1), where), m.group(2)


def _parse_block(block: str, where: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    lines = block.split("\n")
    i = 0
    while i < len(lines):
        raw = lines[i]
        if not raw.strip() or raw.lstrip().startswith("#"):
            i += 1
            continue
        if raw.startswith(" ") or "\t" in raw[:2]:
            raise CorpusError(f"{where}: 第 {i+1} 行意外缩进 —— 只支持一层嵌套")
        key, sep, rest = raw.partition(":")
        if not sep:
            raise CorpusError(f"{where}: 第 {i+1} 行不是 `键: 值` 形状：{raw!r}")
        key, rest = key.strip(), rest.strip()

        if rest:
            out[key] = _parse_scalar_or_inline_list(rest, where, i + 1)[0]
            i += 1
            continue

        # `key:` 后面没东西 ⇒ 块列表 或 一层嵌套映射
        i += 1
        children: list[str] = []
        while i < len(lines) and (lines[i].startswith("  ") and lines[i].strip()):
            children.append(lines[i][2:])
            i += 1
        if not children:
            out[key] = None
            continue
        if all(c.lstrip().startswith("- ") for c in children):
            out[key] = [_parse_scalar_or_inline_list(c.lstrip()[2:].strip(), where, i)[0]
                        for c in children]
        elif any(c.lstrip().startswith("- ") for c in children):
            raise CorpusError(f"{where}: `{key}` 的块列表混了非列表行")
        else:
            nested: dict[str, Any] = {}
            for c in children:
                k2, s2, v2 = c.partition(":")
                if not s2:
                    raise CorpusError(f"{where}: `{key}` 的嵌套行不是 `键: 值`：{c!r}")
                nested[k2.strip()] = _parse_scalar_or_inline_list(v2.strip(), where, i)[0]
            out[key] = nested
    return out


def _parse_scalar_or_inline_list(raw: str, where: str, lineno: int) -> tuple[Any, bool]:
    if raw.startswith("["):
        if not raw.endswith("]"):
            raise CorpusError(f"{where}: 第 {lineno} 行行内列表没闭合：{raw!r}")
        inner = raw[1:-1].strip()
        if not inner:
            return [], True
        return [_unquote(p.strip()) for p in inner.split(",")], True
    return _unquote(raw), False


def _unquote(raw: str) -> Any:
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    return raw


def keys_of(fields: dict[str, Any]) -> frozenset[str]:
    """字段名 ∪ 枚举字段的取值。"""
    keys: set[str] = set(fields)
    for f in ENUM_FIELDS:
        val = fields.get(f)
        if isinstance(val, str) and val:
            keys.add(f"{f}={val}")
    src = fields.get("source")
    if isinstance(src, dict) and src.get("kind"):
        keys.add(f"source.kind={src['kind']}")
    return frozenset(keys)


def load_nodes(directory: str | Path) -> dict[str, Node]:
    d = Path(directory)
    if not d.is_dir():
        raise CorpusError(f"语料目录不存在：{d}")
    out: dict[str, Node] = {}
    for path in sorted(d.glob("*.md")):
        fields, body = parse_front_matter(
            path.read_text(encoding="utf-8", errors="replace"), str(path)
        )
        nid = str(fields.get("id") or path.stem)
        if nid in out:
            raise CorpusError(f"重复的 id：{nid}")
        out[nid] = Node(id=nid, fields=fields, body=body, keys=keys_of(fields))
    if not out:
        raise CorpusError(f"{d} 里没有 .md 节点")
    return out


def load_edges(nodes: dict[str, Node]) -> tuple[dict[str, frozenset[str]], frozenset[str]]:
    """`relations` 边。返回 `(边表, 悬挂边集合)`。

    指向语料外的 id **不静默丢掉** —— 悬挂边是真实存在的现象，
    藏起来会让「图」和事实不符。这里显式返回，由调用方决定怎么报。
    """
    known = set(nodes)
    edges: dict[str, frozenset[str]] = {nid: frozenset() for nid in nodes}
    dangling: set[str] = set()
    for nid, node in nodes.items():
        edges[nid] = frozenset(r for r in node.relations() if r in known)
        dangling |= {r for r in node.relations() if r not in known}
    return edges, frozenset(dangling)
