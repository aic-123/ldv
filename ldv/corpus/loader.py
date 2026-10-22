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
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

# 语料里出现过的枚举字段 —— 它们的**取值**也进键集
#: ★ 2026-10-22 加后四个：`我` 是自我维度（`docs/认知原型.md` §3），
#:   其余三个是**自我语料**的字段（`ldv/tools/make_self_corpus.py` 生成）。
#:   它们只在该字段**存在**时才产键 ⇒ 对外部语料零影响。
ENUM_FIELDS = ("type", "evidence_status", "我", "种类", "级", "含定理")

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


#: 单个文件解析结果的缓存 —— 键是 `(路径, mtime_ns, 字节数)`。
#:
#: ⚠️ **为什么需要它**：语料规模一上去，「同一份语料被解析很多遍」就成了主导开销。
#: 实测 3907 项的公开语料：**单次装载 23 秒**，而 `run_tests` 会 `load()` 十几次
#: ⇒ 光解析就四分钟。那不是「测试慢」，是**同一个纯函数被算了十几遍**。
#:
#: 键里带 `mtime_ns` 与字节数，所以**改了文件就会重新解析** ——
#: 缓存不能把「语料变了」这件事吃掉（那正是「跳过 ≠ 通过」的同族错误）。
#:
#: ★★ 2026-10-21 修（`_probe_d7_rollback.py` 实测抓到）：缓存命中**必须返回副本**。
#:    曾经直接返回缓存里的那个 `Node` 对象，而 `Node` 是**可变**的
#:    （`entry.py` 的派生维度叠加、`predicates.apply_*` 都**就地改** `node.keys`）
#:    ⇒ 调用方的改动静默写进缓存 ⇒ 同进程内下一次 `load_nodes` 拿到**被改过的**节点。
#:    实测后果：把 `derived/influence.json` 移走再建一次入口，派生键**还在**、
#:    树一模一样 —— 「删掉文件即完全回到原状」（sidecar 自己的 `reversible` 声明）
#:    **在进程内不成立**；且下面那句「调用方改动它不会串味」的注释当时是**假的**。
#:    ⇒ 缓存只省「解析」，**绝不共享可变对象**：命中即 `replace(hit)`。
_NODE_CACHE: dict[tuple[str, int, int], Node] = {}


def _node_of(path: Path) -> Node:
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    hit = _NODE_CACHE.get(key)
    if hit is None:
        fields, body = parse_front_matter(
            path.read_text(encoding="utf-8", errors="replace"), str(path)
        )
        nid = str(fields.get("id") or path.stem)
        hit = Node(id=nid, fields=fields, body=body, keys=keys_of(fields))
        _NODE_CACHE[key] = hit
    # ★ 每次都返回**副本**（见上）：浅拷贝足以隔离 `keys` 的改写
    #   （`fields` / `body` 无人就地改 —— 2026-10-21 全仓核过）。
    return replace(hit)


def load_nodes(directory: str | Path) -> dict[str, Node]:
    """读语料目录下的全部 `.md` 节点。

    ⚠️ **`rglob` 而不是 `glob`**（2026-10-18）：单层目录在 **28 万文件**量级会退化
       （实测 `ls -S` 列目录 300 s 超时；装载 >103 分钟仍未完成）。
       允许摄入侧把节点**分片**到子目录（`nodes/<id 后两位>/xxx.md`），
       把单个目录的项数降到几千 —— 目录枚举从「一个巨大目录」变成「上百个小目录」。

       - 向后兼容：扁平目录 `rglob("*.md")` 与 `glob("*.md")` 结果**逐字相同**
         （判据 `test_loader_sharded` 钉这条）
       - 不读 `_staging/`：缓冲目录在语料目录下，与 `nodes/` 平级 ⇒ 不受影响
    """
    d = Path(directory)
    if not d.is_dir():
        raise CorpusError(f"语料目录不存在：{d}")
    out: dict[str, Node] = {}
    for path in sorted(d.rglob("*.md")):
        node = _node_of(path)
        if node.id in out:
            raise CorpusError(f"重复的 id：{node.id}")
        out[node.id] = node
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
