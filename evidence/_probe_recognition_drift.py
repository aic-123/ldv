"""探针：**认识的持续维护会不会退化** —— 逐项插入时「只传播」的 `|Q|` 曲线。

    ⚠️ 这是「抽象层持续影响检索层」是否有意义的**唯一证据**。

## 为什么必须量

`基线§7.3` 自己写着：`E′` 传播**只细化、不重新合并**，实测「拿掉一个方向 ⇒ 11 > 8」、
「拿掉全部 13 个叶方向 ⇒ 25 > 8（退化成一张视图一个方向）」。

⇒ 若「持续」= 每次结构变动都**只传播**，那么 `|Q|` 只增不减 ⇒
  **「模糊掌握」会被磨光**，检索层最终拿到的「认识」与**离散划分**没有区别。
  而那时 `T1`（候选集 ≡ 不用视图）**照样绿** —— 因为候选集本来就与 Q 无关。
  ⇒ 这是本项目那个中心形状：**退化与「正常」在只看判据时长得一模一样。**

## 量什么

对 k = 6, 8, …, 36（真语料，keyset 方向）：

    ① 只传播      `Q_k = propagate(Q_{k-1}, spec_k)`          ← 今天没有任何代码会这么连做
    ② 重算        `Q_k = csr(spec_k)`                          ← 「恢复粗粒度」的上界
    ③ 阈值政策    `Q_k = maintain(Q_{k-1}, spec_k, 0.5)`        ← 本轮要落地的做法

三个轨迹的 `|Q|` 与 `shrink = |Q|/|U|`，以及 ③ 里「传播 vs 重建」各触发几次。
"""
from __future__ import annotations

import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1] / "repo"
sys.path.insert(0, str(REPO))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from ldv.checks._fixtures import load, make_builder, items as items_of  # noqa: E402
from ldv.core.kernel import _did_order  # noqa: E402
from ldv.core.views import (  # noqa: E402
    MIN_SHRINK_RATIO,
    ViewSpec,
    coarsest_stable_refinement,
    propagate,
)
from ldv.tools.gen_view_spec import build_spec  # noqa: E402

WHICH = "keyset"
KS = [6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32, 34, 36]


def spec_of(kernel) -> ViewSpec:
    """从内核现算 `spec` —— 与 `gen_view_spec.build_spec` **同一套三行**。

    ⚠️ 与 `build_spec` 的一致性由 `main()` 里那条断言守（k = 36 时两边必须逐字相同）——
       「两处各写一份会漂移」是本仓库记过的形状。
    """
    dirs = sorted(kernel.all_directions(), key=lambda d: _did_order(d.did))
    u = tuple(d.did for d in dirs)
    rel = frozenset((d.parent, d.did) for d in dirs if d.parent)
    return ViewSpec(universe=u, partition=(frozenset(u),), relation=rel)


def shrink(q, spec) -> float:
    return len(q) / len(spec.universe) if spec.universe else 0.0


def maintain(old_q, spec: ViewSpec, *, shrink_max: float = MIN_SHRINK_RATIO):
    """**候选政策**（尚未落地）：传播；若传播后收缩比超阈值 ⇒ 重建。"""
    q_prop = propagate(old_q, spec)
    if shrink(q_prop, spec) <= shrink_max:
        return q_prop, "传播"
    return coarsest_stable_refinement(spec), "重建"


def main() -> int:
    loaded = load()
    if loaded is None:
        print("找不到语料 —— 探针什么都没量（这不是通过）")
        return 1
    nodes, edges, _ = loaded
    all_items = items_of(nodes)
    ids = sorted(nodes)
    build = make_builder(WHICH, nodes, edges)

    # 一致性自检：`spec_of(k=全集)` 必须与 `build_spec` 逐字相同
    k_full, _ = build(ids)
    s_here = spec_of(k_full)
    d_full, _notes = build_spec(WHICH, nodes, edges)
    assert (s_here.universe == tuple(d_full["universe"])
            and s_here.relation == frozenset(tuple(e) for e in d_full["relation"])), \
        "spec_of 与 gen_view_spec.build_spec 分岔了 —— 两处各写一份就会这样"
    print("自检：`spec_of(全集)` 与 `gen_view_spec.build_spec` **逐字相同** ✓")
    print()

    print(f"═══ 认识的持续维护：逐项插入（{WHICH}｜{len(ids)} 项｜阈值 {MIN_SHRINK_RATIO}）═══")
    print(f"{'k':>3} {'|U|':>4} {'|Q|只传播':>9} {'|Q|重算':>8} {'|Q|政策':>8} "
          f"{'shrink传播':>10} {'shrink政策':>10}  动作")
    q_prop = q_reb = q_pol = None
    prop_actions = rebuild_actions = 0
    for k in KS:
        kernel, _plug = build(ids[:k])
        spec = spec_of(kernel)
        q_reb = coarsest_stable_refinement(spec)
        # ① 只传播
        q_prop = coarsest_stable_refinement(spec) if q_prop is None else propagate(q_prop, spec)
        # ③ 阈值政策
        if q_pol is None:
            q_pol, act = coarsest_stable_refinement(spec), "重建（首轮）"
        else:
            q_pol, act = maintain(q_pol, spec)
        if act == "传播":
            prop_actions += 1
        else:
            rebuild_actions += 1
        print(f"{k:>3} {len(spec.universe):>4} {len(q_prop):>9} {len(q_reb):>8} {len(q_pol):>8} "
              f"{shrink(q_prop, spec):>10.3f} {shrink(q_pol, spec):>10.3f}  {act}")

    print()
    print("★ 判读：")
    print(f"    只传播末态 |Q| = {len(q_prop)}（|U| = {len(spec.universe)}）"
          f" ⇒ shrink {shrink(q_prop, spec):.3f}"
          f" ⇒ {'**退化成离散划分，粗粒度被磨光**' if len(q_prop) == len(spec.universe) else '仍有粗粒度'}")
    print(f"    重算末态 |Q| = {len(q_reb)} ⇒ shrink {shrink(q_reb, spec):.3f}")
    print(f"    阈值政策末态 |Q| = {len(q_pol)} ⇒ shrink {shrink(q_pol, spec):.3f}"
          f"｜动作 传播 {prop_actions} 次 / 重建 {rebuild_actions} 次")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
