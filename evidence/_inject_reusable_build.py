"""`test_reusable_build` 的**注入对照** —— 证明那 6 条**能红**（不是恒真）。

三臂，每臂只改 `_fixtures.reusable_build` 一处：

    基线        真实现                       ⇒ 期望 红 0
    共享        去掉 deepcopy（发同一个对象） ⇒ 期望 `RB2` 红
    陈旧        缓存的不是 pristine（先让消费者改过再存）⇒ 期望 `RB1` 红

⚠️ 只跑这一个测试（不跑整套），所以是秒级。
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent / "repo"
sys.path.insert(0, str(REPO))

import ldv.tests.run_tests as T  # noqa: E402
from ldv.checks import _fixtures as F  # noqa: E402

REAL = F.reusable_build


def shared(tag, nodes, edges, produce):          # noqa: ANN001
    ent = F._BUILD_CACHE.get(tag)
    if ent is not None and ent[0] is nodes and ent[1] is edges:
        return ent[2]                             # ← 没有 deepcopy
    res = produce()
    F._BUILD_CACHE[tag] = (nodes, edges, res)
    return res


def stale(tag, nodes, edges, produce):           # noqa: ANN001
    ent = F._BUILD_CACHE.get(tag)
    if ent is not None and ent[0] is nodes and ent[1] is edges:
        return copy.deepcopy(ent[2])
    res = produce()
    F._BUILD_CACHE[tag] = (nodes, edges, res)     # ← 存的**不是** pristine（是原件）
    return res


def run(label: str, impl) -> None:                # noqa: ANN001
    F.clear_build_cache()
    T.PASS.clear()
    T.FAIL.clear()
    F.reusable_build = impl
    T.test_reusable_build()
    print(f"  {label:8s} 过 {len(T.PASS)}｜红 {len(T.FAIL)}")
    for f in T.FAIL:
        print(f"           · {f}")
    F.reusable_build = REAL


def main() -> int:
    print("═══ `test_reusable_build` 注入对照 ═══")
    run("基线", REAL)
    run("共享", shared)
    run("陈旧", stale)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
