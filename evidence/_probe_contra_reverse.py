# -*- coding: utf-8 -*-
"""反向对照：`contradictions()` 那三条检查**到底会不会红**（2026-10-19）。

为什么需要：9 份语料上「矛盾数」**全是 0**。而「0」有两种可能 ——

    ① 真的没有矛盾
    ② 这三条检查**永远不可能红**（写死了 / 判据本身是恒真的）

分不开就是「跳过 ≠ 通过」的同一类问题。这里用**合成内核**（stub）把三类矛盾
各造一个，证明检查本身能红。

⚠️ 这是**判据层的合成对照**，不是拿真源码回退 —— 那三类矛盾是「内核建出来的结构」，
   正确内核**不可能**产生，只能造。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

sys.path.insert(0, ".")

from ldv.core.reflect import contradictions, contradiction_requests  # noqa: E402


@dataclass(frozen=True)
class D:
    did: str
    parent: str | None = None


class Stub:
    """最小内核：只提供 `contradictions` 用到的那几个方法。"""

    def __init__(self, dirs, kids, members):
        self._dirs = {d.did: d for d in dirs}
        self._kids = kids
        self._members = members

    def all_directions(self):
        return list(self._dirs.values())

    def direction(self, did):
        return self._dirs[did]

    def children_of(self, d):
        return [self._dirs[k] for k in self._kids.get(d.did, ())]

    def members_of(self, d):
        return set(self._members.get(d.did, set()))


def case(name, stub, want):
    c = contradictions(stub)
    kinds = [r["类型"] for r in c["矛盾"]]
    ok = kinds == want
    print(f"{'✓' if ok else '✗'} {name}: 矛盾数 {c['矛盾数']}，类型 {kinds}"
          f"（期望 {want}）")
    if not ok:
        print("   明细:", c["矛盾"])
    # 转成请求也要能出来
    rq = contradiction_requests(stub, c)
    print(f"   → 请求 {len(rq['请求'])} 条"
          + (f"，首条请求内容：{rq['请求'][0]['请求'][:34]}…" if rq["请求"] else ""))
    return ok


def main() -> int:
    allok = True

    # ① 边打架：d 声明父 p，但 p 的子列表里没有 d
    allok &= case(
        "边打架（声明了父，但不在子列表）",
        Stub([D("D0"), D("D1", "D0"), D("D2", "D0")],
             {"D0": ("D1",)},                       # D2 被漏在子列表外
             {"D0": {1, 2}, "D1": {1}, "D2": {2}}),
        ["边打架"])

    # ② 子覆盖越出父：子的成员并集 ⊄ 父成员
    allok &= case(
        "子覆盖越出父（D2 的成员 3 不在 D1 里）",
        Stub([D("D0"), D("D1", "D0"), D("D2", "D0")],
             {"D0": ("D1", "D2"), "D1": (), "D2": ()},
             {"D0": {1, 2}, "D1": {1}, "D2": {3}}),
        ["子覆盖越出父"])

    # ③ 孤立方向：没父、也不是根
    allok &= case(
        "孤立方向（D9 既没父也不是 D0）",
        Stub([D("D0"), D("D9", None)],
             {"D0": (), "D9": ()},
             {"D0": {1}, "D9": {2}}),
        ["孤立方向"])

    # ④ 干净内核：必须是 0（对照的另一头）
    ok4 = case(
        "干净内核（不该报）",
        Stub([D("D0"), D("D1", "D0")],
             {"D0": ("D1",), "D1": ()},
             {"D0": {1, 2}, "D1": {1}}),
        [])
    allok &= ok4

    print(f"\n⇒ {'三条检查都能红、干净内核不报' if allok else '✗ 有检查红不了 —— 那 9 份语料上的 0 不可信'}")
    return 0 if allok else 2


if __name__ == "__main__":
    raise SystemExit(main())
