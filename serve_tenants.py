# -*- coding: utf-8 -*-
"""**租户生命周期**（P1，2026-10-29）—— 建 / 列 / 删。

## 它治的是什么

约束②（`docs/服务化设计.md` §0）说「一租户一目录，备份=拷目录」。但**建和删没有入口**：
原来是手工 `mkdir` + 从别处拷一份 `view_spec.json`。手工步骤的代价不是麻烦，是
**每次做的形状都可能不一样** —— 而「形状不一样」在数据目录上等于静默的格式漂移。

## 三个决定

1. **建出来的库是空的，而且空库能跑**。内核 2026-10-25 已经定过「空语料是合法的」
   （`corpus/loader.py::load_nodes` 的注释），但壳里 `_load_tenant` 还要求
   `nodes/` 下至少有一个 `.md` ⇒ **壳与内核在「空库」上口径不一致**。
   本文件与 `serve.py` 一起把这条对齐：建完就能 `GET /<t>/run`，输出「装进 0 项」。
   ⚠️ 不往新库里塞占位文件 —— 那就是「为了让它看起来能用而编一条数据」。

2. **删 = 移到 `<root>/.trash/<名字>.<时间戳>`**，不是 `rm`。
   误删一个租户 = 误删一整份语料，而这份仓库的整个立场是「不可逆的操作要少」。
   回收站放在租户根**里面**（而不是旁边），这样「备份 = 拷目录」这条不变 ——
   拷走根目录时，被删的东西也一起在。

3. **名字先过白名单正则**。`POST /tenants/../../etc` 这种请求，如果在拼接路径之后
   才检查，就已经晚了（`Path` 会把它规约掉）。所以检查发生在**任何**路径拼接之前，
   且保留字（`tasks` / `tenants` / `healthz` / `readyz` / `metrics`）不许当租户名 ——
   否则一个叫 `healthz` 的租户会让端点**互相遮蔽**，而遮蔽的方向取决于路由顺序。
"""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

#: 一个租户要「完整」必须有什么（与 `serve.REQUIRED` 同一份约定）
REQUIRED = ("nodes", "view_spec.json")

#: 回收站目录名（在租户根**里面**；以 `.` 开头 ⇒ 不会出现在租户列表里）
TRASH = ".trash"

#: 租户名：字母数字（或 `_` `-`）开头，允许 `.`，最长 64。
#: ⚠️ **不许 `.` 开头** —— 回收站与其它点文件都是点开头的，一个叫 `.trash` 的租户
#:    会与回收站**同名**（删两次就互相覆盖）。
#: ⚠️ 允许 `_` 开头是为了 `_rt-ops-*` 这类**临时挂载**（`test_service_shell` 用它做
#:    「备份副本当租户跑」的隔离验证）—— 那条判据真的会用这个名字跑。
NAME_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]{0,63}$")

#: 保留字 —— 与端点路径冲突的名字会让「哪个先匹配」变成行为
RESERVED = frozenset({"tasks", "tenants", "healthz", "readyz", "metrics"})

#: 新库的 `view_spec.json`：**空声明**。人声明的东西不许系统替它填。
MINIMAL_SPEC: dict = {
    "_note": [
        "由 ldv 服务壳建租户时生成（P1）：这是一个**空库**（0 项），所以声明也是空的。",
        "",
        "`universe` / `partition` / `relation` / `读数` 留空**不是占位**，是因为还没有方向可声明。",
        "往里写进节点之后，照 `_data/snap-email-eu/view_spec.json` 的形状补：",
        "  `universe` 参与视图的方向 id（方向 id 每个内核各自从 0 开始）",
        "  `partition` 必须是 `universe` 的**划分**（写成 `[universe]` = 「无先验意见」）",
        "  `relation` 的**两个端点都必须在 `universe` 里**，方向是「父→子」",
        "  `读数` 每条要写类别（distributive / algebraic / holistic），类别是人**声称**的，",
        "        不是从实现里猜的 —— `§A4` 会实测这个声称对不对。",
        "",
        "⚠️ 声明了却写坏（JSON 错 / P 不是划分 / 端点不在 universe 里）⇒ 那六条判据**报错**；",
        "   **没声明**（还是这份空文件）⇒ 它们**报跳过**。两者必须分得开。",
    ],
    "语料": {"项数": 0, "边数": 0},
    "方向": "keyset",
    "universe": [],
    "partition": [],
    "relation": [],
    "读数": [],
}


class TenantError(Exception):
    """租户操作失败。`kind` 给壳映射到状态码：`bad-name` / `exists` / `missing`。"""

    def __init__(self, kind: str, msg: str):
        super().__init__(msg)
        self.kind = kind


def name_ok(name: str) -> bool:
    return bool(NAME_RE.match(name or "")) and name not in RESERVED and ".." not in name


def check_name(name: str) -> None:
    if not name_ok(name):
        raise TenantError("bad-name",
                          f"租户名不合法：{name!r}（要 {NAME_RE.pattern}，且不能是 "
                          f"{sorted(RESERVED)} 之一）")


def list_tenants(root: str | Path) -> list[str]:
    """列出租户：**有 `view_spec.json` 的目录**（回收站是点目录，自然不出现）。"""
    base = Path(root)
    if not base.is_dir():
        return []
    return sorted(d.name for d in base.iterdir()
                  if d.is_dir() and not d.name.startswith(".")
                  and (d / "view_spec.json").exists())


def complete(root: str | Path, name: str) -> bool:
    """「完整」= 有 `nodes/` 且 `nodes/` 是目录、有 `view_spec.json`。

    ⚠️ **不要求 `nodes/` 非空**：空库是合法的（见文件头第 1 条）。
       这一条与 `serve._load_tenant` 必须一致，否则「建完的租户用不了」。
    """
    if not name_ok(name):
        return False
    base = Path(root) / name
    if not base.is_dir():
        return False
    for req in REQUIRED:
        if not (base / req).exists():
            return False
    return (base / "nodes").is_dir()


def create(root: str | Path, name: str) -> Path:
    """建一个**空**租户。已存在 ⇒ 抛 `exists`（**不覆盖** —— 覆盖等于静默删数据）。"""
    check_name(name)
    base = Path(root) / name
    if base.exists():
        raise TenantError("exists", f"租户 {name!r} 已存在（不覆盖）")
    (base / "nodes").mkdir(parents=True)
    (base / "view_spec.json").write_text(
        json.dumps(MINIMAL_SPEC, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return base


def delete(root: str | Path, name: str) -> Path:
    """删 = **移到回收站**。返回回收站里的那个路径（调用方可以据此恢复）。"""
    check_name(name)
    base = Path(root) / name
    if not base.is_dir():
        raise TenantError("missing", f"租户 {name!r} 不存在")
    trash = Path(root) / TRASH
    trash.mkdir(exist_ok=True)
    # 时间戳到秒 + 冲突时加后缀：同一秒删两次同名（改回来再删）不许互相覆盖
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = trash / f"{name}.{stamp}"
    n = 1
    while dest.exists():
        dest = trash / f"{name}.{stamp}.{n}"
        n += 1
    shutil.move(str(base), str(dest))
    return dest


def trash_entries(root: str | Path, name: str | None = None) -> list[Path]:
    """回收站里有什么（按名字倒序 = 最近删的在前）。"""
    trash = Path(root) / TRASH
    if not trash.is_dir():
        return []
    items = [p for p in trash.iterdir() if p.is_dir()]
    if name is not None:
        items = [p for p in items if p.name == name or p.name.startswith(name + ".")]
    return sorted(items, key=lambda p: p.name, reverse=True)
