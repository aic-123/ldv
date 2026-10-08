"""视图集合的落盘 / 读回 —— `§A6`（`docs/分层方向视图-抽象层.md` §9 / `§4 E7`）。

    python -m ldv.run_checks            # 判据在 `(视图)` 那一组里

纪律**照抄** `core/persist.py`（`§10.2 D` 那一节），一个字都不新发明：

    落盘只认**一条权威边**；派生边**读回现算**
    ⇒ 派生边漂移**不可能发生**（它根本不在盘上）
    ⇒ 而「不可能发生」这件事要**验**，不能靠说 —— 见 `a6_roundtrip` 与
      `_to_dict_storing_derived` / `_from_dict_trusting_derived` 这一对**对照实现**

---

## 视图的权威边 / 派生边

    权威   `spec`（`U` / `P` / `E`）+ `q` + 每张视图的 `vid` / `block`
    派生   `payload`（= `插件.合并(block)`）· `concretization`（= `覆盖(payload)`）· `covered`

⚠️ **`payload` 也是派生的**，这一点和内核那边不同（内核的 `payload` 由插件声明、
   `§K4` 说方向不可变）。视图的 `payload` 是**从块现算**出来的，所以它**不该进盘**：
   进盘就意味着「盘上的合并结果」与「按 `§I2` 现算的结果」是**两个可以不一致的东西**，
   而它们本可以只有一个。⇒ 盘上只留 `block`，`payload` 读回时重算。

⚠️ **不落 `payload` 还有一个实际理由**：它是**插件定义的不透明内容**，
   内核从不解释它（`Direction` 的 docstring）。视图层没有资格替插件定它的序列化格式。

---

## 为什么 `q` 要单独落

`q` 由 `spec` 决定（`coarsest_stable_refinement`，**唯一**），所以理论上可以读回时重算。
但它同时是 `§A3` 的被判对象 —— 若读回时重算，`§A6` 就**验不到**「盘上的 `q` 是不是那个解」。
⇒ 落盘存 `q`，读回时**先比**（比不出来才红），再拿它去跑 `§A1`。
「存一份再比」在这里是**判据的一部分**，不是冗余。
"""

from __future__ import annotations

import json
from typing import Any, Callable

from .views import ViewSpec, partition_of

__all__ = [
    "FORMAT",
    "ViewPersistenceError",
    "to_dict",
    "from_dict",
]

#: 格式串 —— 与 `core/persist.py::FORMAT` 同一个用途：换了格式要能一眼看出来。
FORMAT = "ldv-views/1"


class ViewPersistenceError(RuntimeError):
    """存档不合法（缺字段 / 格式串不对 / `P` 不是划分）。"""


def to_dict(vs: Any) -> dict:
    """视图集合 → 可 JSON 的字典。**只存权威边**（见模块开头）。

    ⚠️ 这里**没有** `payload` / `concretization` / `covered` 三栏 —— 那是**故意**的。
       要造「存了派生边」的形态，用 `_to_dict_storing_derived`。
    """
    return {
        "格式": FORMAT,
        "spec": vs.spec.as_dict(),
        "q": [sorted(b) for b in sorted(vs.q, key=min)],
        "视图": [{"vid": v.vid, "block": sorted(v.block)} for v in vs.views],
    }


def from_dict(
    data: dict,
    kernel: Any,
    cover: Callable[[Any], frozenset[str]],
    plugin: Any,
) -> Any:
    """字典 → 视图集合。**派生边一律现算**（`payload` / `concretization` / `covered`）。

    ⚠️ `spec` 由 `ViewSpec(...)` 重建 ⇒ `__post_init__` 的三条校验**照跑**：
       盘上写坏的 `P` 会**抛**，不会静默变成一个别的划分。
    """
    from ..checks.abstraction import View, ViewSet  # 局部 import：避免 core→checks 的常态依赖

    if data.get("格式") != FORMAT:
        raise ViewPersistenceError(
            f"格式串是 {data.get('格式')!r}，期望 {FORMAT!r}")
    s = data.get("spec") or {}
    spec = ViewSpec(
        universe=tuple(s.get("universe") or ()),
        partition=tuple(frozenset(b) for b in (s.get("partition") or ())),
        relation=frozenset(tuple(e) for e in (s.get("relation") or ())),
    )
    q = partition_of([frozenset(b) for b in (data.get("q") or ())])
    views: list[Any] = []
    for row in data.get("视图") or ():
        block = frozenset(row["block"])
        dirs = [kernel.direction(d) for d in sorted(block)]
        covered: set[str] = set()
        for d in dirs:
            covered |= set(kernel.members_of(d))
        try:
            payload = plugin.merge(dirs)
            conc = frozenset(cover(payload))
            bad = ""
        except Exception as exc:  # noqa: BLE001 - payload 由插件产出
            payload, conc = None, frozenset()
            bad = f"合并/覆盖算不出来：{type(exc).__name__}: {exc}"
        v = View(vid=row["vid"], block=block, payload=payload,
                 concretization=conc, covered=frozenset(covered))
        if bad:
            from dataclasses import replace

            v = replace(v, category=bad)
        views.append(v)
    return ViewSet(spec=spec, q=q, views=tuple(views))


def dumps(vs: Any) -> str:
    """落盘用的文本。`ensure_ascii=False` + `sort_keys` ⇒ 同一份视图只有一个字节形态。"""
    return json.dumps(to_dict(vs), ensure_ascii=False, sort_keys=True, indent=2)


# ═══ 两个「错一处」的对照实现 ═════════════════════════════════════════════════
#
# ⚠️ 它们是**对照**，不是备选方案。作用：让 `§A6` 的每条判据都能被证明「会红」——
#    空转与通过长得一模一样，不配对照就分不开。
#
# 与 `core/persist.py` 那一对**同形**：一个把派生边也存进盘、一个读回时信任盘上的派生边。
# 只有**两个都上**，漂移才可能发生 —— 所以注入必须**同时**用这两个，
# 只用其中一个的注入是**造不出漂移的**（那会得到一条永远绿的判据）。


def _to_dict_storing_derived(vs: Any) -> dict:
    """对照①：落盘时**也存一份派生边** —— `§10.2 D` 说的「各存一份」。

    只多这一件事。它使**漂移成为可能**（不是必然 —— 一份没漂移的双份存档
    读回来照样对，所以「读回来对」这一条**单独不足以**证明只存了一份）。
    """
    data = to_dict(vs)
    by_vid = {v.vid: v for v in vs.views}
    for row in data["视图"]:
        v = by_vid[row["vid"]]
        row["concretization"] = sorted(v.concretization)
        row["covered"] = sorted(v.covered)
    return data


def _from_dict_trusting_derived(
    data: dict,
    kernel: Any,
    cover: Callable[[Any], frozenset[str]],
    plugin: Any,
) -> Any:
    """对照②：读回时**信任存档里的派生边**，不从 `block` 重算。

    只多这一件事。存档里没有那两栏时退回重算 ⇒ 与真实现**同输出**
    （所以它只在**带派生边**的存档上才分得开 —— 这正是注入实验要造的形态）。
    """
    from dataclasses import replace

    vs = from_dict(data, kernel, cover, plugin)
    by_vid = {v.vid: v for v in vs.views}
    out = []
    for row in data["视图"]:
        v = by_vid[row["vid"]]
        if "concretization" in row or "covered" in row:
            v = replace(v,
                        concretization=frozenset(row.get("concretization") or ()),
                        covered=frozenset(row.get("covered") or ()))
        out.append(v)
    return replace(vs, views=tuple(out))
