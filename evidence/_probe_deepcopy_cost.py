"""量：`copy.deepcopy(内核)` 要多久 —— 决定「建一次 + 各发一份」可不可行。

## 为什么问这个

`run_checks` 里同一个 `which` 的**批建内核被建 4 次**：
`run_one` 一次，`(视图)` / `(L0)` / `(检索)` 各一次（`run_checks.py` l.271 / 521 / 631 / 762）。
它们是**同一份装配**，所以这 3 次是**纯重复**。

⚠️ 但**不能直接把同一个对象发给四个消费者**：判据跑查询会经 `kernel.query`
   → `expand` **就地改内核**，而 `retriever.py:713` 明写「唯一写动作」。
   共享 = 让 `(视图)` 拿到一个「已经被 `run_one` 展开过」的树 —— 那正是本仓库
   反复记的中心反模式（退化与正常长得一样）。

⇒ 若「建一次 + 每份 `deepcopy`」的**拷贝代价 ≪ 建造代价**，那就是一条安全的路。
   本脚本只量**代价比**；忠实性由 A/B 逐字 diff 另测（`_probe_share_batch_kernel.py`）。

跑法：
    LDV_CORPUS=openalex-n200 python outputs/_probe_deepcopy_cost.py reach
"""
from __future__ import annotations

import copy
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent / "repo"
sys.path.insert(0, str(REPO))

from ldv.checks._fixtures import load  # noqa: E402
from ldv.run_checks import batch_kernel  # noqa: E402


def main(argv: list[str]) -> int:
    which = argv[1] if len(argv) > 1 else "reach"
    loaded = load()
    if loaded is None:
        print("⚠ 找不到语料 —— 设 `LDV_CORPUS`")
        return 0
    nodes, edges, _ = loaded
    print(f"语料 {len(nodes)} 项 / {sum(len(v) for v in edges.values())} 边｜方向 {which}")

    t = time.perf_counter()
    kernel, plugin, _q, _mk = batch_kernel(which, nodes, edges)
    t_build = time.perf_counter() - t
    n_dirs = len(kernel.all_directions())
    print(f"  建造          {t_build:8.3f} s   （{n_dirs} 个方向）")

    copies = []
    for i in range(3):
        t = time.perf_counter()
        k2 = copy.deepcopy(kernel)
        copies.append(time.perf_counter() - t)
        print(f"  deepcopy #{i + 1}   {copies[-1]:8.3f} s   （{len(k2.all_directions())} 个方向）")

    best = min(copies)
    print()
    print(f"  ⇒ 拷贝 / 建造 = {best / t_build:.4f}   "
          f"（{'拷贝便宜，可行' if best < t_build / 4 else '拷贝不便宜，不划算'}）")
    print(f"  ⇒ 4 个消费者：现状 = 4 次建 = {4 * t_build:.2f} s；"
          f"改成 1 建 + 4 拷 = {t_build + 4 * best:.2f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
