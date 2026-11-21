# -*- coding: utf-8 -*-
"""探针：`B17`@`reach` 的**单变量对照** —— `snap-hepth`（留环）vs `snap-hepth-dag`（去环）。

## 要证的一件事

`B17`@`reach` 在随仓库那份 **36 项 DAG** 语料上**跳过**（每个等价类都是单点 ⇒ 两条断言恒真）。
「跳过」有两种可能：**判据坏了** 或 **这份语料上没内容**。本探针把后者钉死 ——
仓库自带一对**同一张图、只差有没有环**的语料，正好是**设计好的单变量对照**：

    snap-hepth       1200 项，**留环**
    snap-hepth-dag   1200 项，**去环**（其余同一张图）

若「留环那份判、去环那份跳」，则「跳过」是**语料性质**，不是判据的毛病。

⚠️ 代价（实测）：`reach` 在 1200 项上**建核 25.5 s / 14.6 s** ⇒ 只在本探针里跑，
   **不进** `run_tests` 常规套件（那边用 60 项的 `pypi-deps`，亚秒）。
⚠️ 它**不动内核、不动语料**（只读），也不写任何文件。
"""
from __future__ import annotations

import os
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "repo"))

from ldv.checks._fixtures import equiv_classes  # noqa: E402
from ldv.checks._framework import Report  # noqa: E402
from ldv.checks.equivalence import b17_leaf_is_equivalence_class  # noqa: E402
from ldv.run_checks import batch_kernel, load  # noqa: E402

#: 一对**同一张图**的语料：只差有没有环。
PAIR = [("snap-hepth", "留环"), ("snap-hepth-dag", "去环")]


def one(name: str, note: str) -> None:
    os.environ["LDV_CORPUS"] = name
    loaded = load()
    if loaded is None:
        print(f"{name:16s} 载入失败")
        return
    nodes, edges, _ = loaded
    n_edges = sum(len(v) for v in edges.values())
    cls = equiv_classes("reach", nodes, edges)
    biggest = max((len(c) for c in set(cls.values())), default=0)
    t0 = time.time()
    kernel, _plugin, _q, _mk = batch_kernel("reach", nodes, edges)
    t_build = time.time() - t0
    t1 = time.time()
    rep = Report(plugin="(探针)")
    b17_leaf_is_equivalence_class(kernel, cls, rep, "reach")
    t_judge = time.time() - t1
    rows = [a for a in rep.assertions if a.code == "B17"]
    got = str(rows[-1].result) if rows else "(没注册)"
    print(f"{name:16s}（{note}）项 {len(nodes):5d}｜边 {n_edges:5d}｜"
          f"reach 最大等价类 {biggest:4d}｜建核 {t_build:6.2f}s｜判 {t_judge:6.3f}s｜"
          f"B17 = {got}")
    print(f"{'':16s}    detail: {rows[-1].detail if rows else '(无)'}")


def main() -> int:
    print("★ 单变量对照：同一张图，只差**有没有环** ⇒ 看 `B17` 判还是跳\n")
    for name, note in PAIR:
        try:
            one(name, note)
        except Exception as exc:  # noqa: BLE001
            print(f"{name:16s} 崩了：{exc!r}")
    os.environ.pop("LDV_CORPUS", None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
