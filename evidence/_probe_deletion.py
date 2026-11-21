"""删除流程的**前提核查** —— 先量，再断言。

## 要回答的问题

设计文档 `§10.2 C` 把 ③「**向上收缩父的 payload**」写成**硬要求**，
理由是「删掉 x 后 members 变小，**若 payload 跟着收窄**，`∪覆盖(子)` 就变小
⇒ 父不跟着收窄就红」。

**而本设计里没有任何东西从 members 重算 payload** —— `payload` 由插件声明、
`Direction` 不可变（§K4）。⇒ 若这条推理成立，③ 的前提在本设计里**不成立**。

**不许靠读代码断言。** 这个脚本把话变成读数：

    删除前后，`B16`（成员 ⊆ 覆盖）与 `B18`（覆盖(父) ⊆ ∪覆盖(子)）的
    **逐方向读数**是否发生变化？

变化 ⇒ ③ 有用；不变 ⇒ ③ 空转，且 `D2`/`D3` 在**当前设计**里只能当守卫。

## 用法

    python outputs/_probe_deletion.py            # 随仓库 36 项
    LDV_CORPUS=openalex-small python outputs/_probe_deletion.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent / "repo"
sys.path.insert(0, str(REPO))

from ldv.checks._fixtures import (  # noqa: E402
    build_keyset, build_reach, build_sequence, coverage_of, load,
)
from ldv.checks.coverage import cover_leak_profile, soundness_profile  # noqa: E402


def profile_snapshot(kernel, cover) -> dict:
    """`B16` / `B18` 的**逐方向**读数 —— 要比的是内容，不是汇总数。"""
    sound = soundness_profile(kernel, cover)
    leak = cover_leak_profile(kernel, cover)
    return {
        "越界方向": dict(sound["越界方向"]),
        "越界成员数": sound["越界成员数"],
        "漏项数": leak["漏项数"],
        "漏的对数": leak["漏的对数"],
        "漏的明细": [(p["父"], tuple(p["子"]), p["漏项数"]) for p in leak["明细"]],
    }


def main() -> int:
    loaded = load()
    if loaded is None:
        print("⚠ 找不到语料 —— 报「跳过」，不报「通过」")
        return 0
    nodes, edges, _ = loaded
    print(f"语料 {len(nodes)} 项 / 边 {sum(len(v) for v in edges.values())} 条")
    print()

    builders = {"keyset": lambda: build_keyset(nodes),
                "reach": lambda: build_reach(nodes, edges),
                "sequence": lambda: build_sequence(nodes, edges)}

    for which, build in builders.items():
        cover = coverage_of(which, nodes, edges)
        kernel, _plug = build()
        before = profile_snapshot(kernel, cover)
        st_before = kernel.stats()

        # 删掉**最后 1/3 的项**（按 id 序）—— 让若干方向真的变空。
        victims = sorted(nodes)[len(nodes) * 2 // 3:]
        paths = [kernel.remove(v) for v in victims]

        after = profile_snapshot(kernel, cover)
        st_after = kernel.stats()

        print(f"═══ {which} ═══  删 {len(victims)} 项"
              f"（其中 {sum(1 for p in paths if not p)} 项不在结构里）")
        print(f"  B16 越界成员数  {before['越界成员数']} → {after['越界成员数']}"
              f"   逐方向读数{'**变了**' if before['越界方向'] != after['越界方向'] else '未变'}")
        print(f"  B18 漏项数      {before['漏项数']} → {after['漏项数']}"
              f"   逐方向读数{'**变了**' if before['漏的明细'] != after['漏的明细'] else '未变'}")
        print(f"  结构  空方向 {st_before['空方向']} → {st_after['空方向']}"
              f" ｜ 已失效方向 {st_before['已失效方向']} → {st_after['已失效方向']}"
              f" ｜ 方向数 {st_before['方向']} → {st_after['方向']}"
              f"（**不许变**：不删方向）")
        print(f"  账本  {st_before['事件']} → {st_after['事件']} 条"
              f"（+{st_after['事件'] - st_before['事件']}）")
        # D6：「已失效」与「从来没存在过」分得开
        empties = [d.did for d in kernel.all_directions()
                   if not kernel.members_of(d)]
        st = {d.did: kernel.ledger.status_of(d.did) for d in kernel.all_directions()}
        bad = [did for did in empties if not st[did].startswith("已失效")]
        print(f"  D1  空方向 {len(empties)} 个，其中**没有失效账目**的 {len(bad)} 个"
              f"  ← 必须 0")
        print(f"  D6  `status_of(不存在) = {kernel.ledger.status_of('D9999')!r}`"
              f"；空方向的 `status_of` 例："
              f"{[st[d] for d in empties[:2]]}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
