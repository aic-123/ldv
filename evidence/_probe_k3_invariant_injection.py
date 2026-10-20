"""`§K3` 锥不变量的**注入验证**：那两条新断言真的能红吗？

    run_tests 里新增的两条断言（`test_kernel` / `test_flows` 各一条）
    查的是同一个不变量：`set(Cone(x))` == 「当前真的持有 x 的方向集合」。

    ⚠️ **一条查不出来的断言与一条恒真的断言长得一模一样** ——
       所以这里把缺陷**装回去**（`_expand_inner` 的追加撤销，与
       `outputs/_probe_ghost_before_after.py` 同一个单变量），
       再跑那两条断言，看它们是否报红。

判据（**基线绿 + 注入红**）：
    修复后（现行）  两条都绿
    修复前（重建）  两条都红

用法（**cwd = repo**）：
    python ../outputs/_probe_k3_invariant_injection.py
"""
import sys

sys.path.insert(0, ".")

from ldv.core.kernel import Kernel
from ldv.tests import run_tests as RT

_orig = Kernel._expand_inner


def old_expand_inner(self, d):
    before = dict(self._path)
    kids = _orig(self, d)
    self._path = before
    return kids


def probe(tag: str, patch: bool) -> None:
    if patch:
        Kernel._expand_inner = old_expand_inner
    else:
        Kernel._expand_inner = _orig
    RT.PASS.clear()
    RT.FAIL.clear()
    try:
        RT.test_kernel()
        RT.test_flows()
    finally:
        Kernel._expand_inner = _orig
    hits = [f for f in RT.FAIL if "支撑锥" in f or "锥 == 持有" in f]
    print(f"  {tag:<14} 红 {len(RT.FAIL):>2} 条，其中锥不变量 {len(hits)} 条")
    for h in hits:
        print(f"      [红] {h[:150]}")


print("注入验证：`§K3` 锥不变量（`test_kernel` + `test_flows` 两条断言）")
print("─" * 72)
probe("修复后（现行）", patch=False)
probe("修复前（重建）", patch=True)
print("─" * 72)
