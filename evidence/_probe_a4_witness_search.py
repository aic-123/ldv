"""探针：`§A4` 的 `holistic` 见证**能不能在语料上搜出来**（而不是写死）。

## 为什么要量

`view_spec.json` 的 `读数[].见证` 是**人挑的一组方向**（A / B）。实测（2026-10-10）：
`gen_view_spec --write` 在换语料时把 `读数` **逐字照抄**（生成器的既定行为），
于是那份**为 36 项挑的**见证被搬到全量语料上 —— 而它的 `摘要` 不再相同 ⇒ `§A4` 红。

⇒ 两个可能的解法：
    ① **重审声称**（`中位数` / `覆盖计数` 也许其实是 `algebraic`）
    ② **改见证构造**（不写死 A/B，而是**在语料上搜**一对「同摘要、异读数」）

本探针回答：**② 在两份语料上都搜得出来吗？** 搜不出来，② 就不成立。

## 判据（与 `§A4` 的 `holistic` 分支逐字同款）

    摘要相同（`计数与和`：`(|S|, Σ|members(d)|)`）  且  读数不同
"""
from __future__ import annotations

import itertools
import json
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1] / "repo"
sys.path.insert(0, str(REPO))


def load_ctx(nodes, edges, which: str):
    from ldv.checks.abstraction import load_spec_file, reading_ctx, spec_for
    from ldv.checks.coverage import corpus_fingerprint
    from ldv.run_checks import batch_kernel

    from ldv.checks._fixtures import find_corpus

    doc = load_spec_file(find_corpus(), corpus_fingerprint(nodes, edges))
    spec, _why = spec_for(doc, which, corpus_fingerprint(nodes, edges))
    if spec is None:
        return None, None, None
    kernel, _p, _q, _mk = batch_kernel(which, nodes, edges)
    return spec, kernel, reading_ctx(kernel, spec)


def search(ctx, spec, k: int, reading: str) -> tuple[tuple, tuple] | None:
    """在 `k` 元方向子集里搜一对：摘要相同、读数不同。"""
    from ldv.checks.abstraction import _median

    def digest(S):
        return (len(S), sum(ctx.value_of[d] for d in S))

    def value(S):
        if reading == "中位数":
            return _median([ctx.value_of[d] for d in S])
        return len(set().union(*(ctx.cover_of[d] for d in S)))

    buckets: dict[tuple, list[tuple]] = {}
    for combo in itertools.combinations(sorted(spec.universe), k):
        buckets.setdefault(digest(combo), []).append(combo)
    for dg, combos in buckets.items():
        vals = {(round(value(c), 9), c) for c in combos}
        if len(vals) >= 2:
            s = sorted(vals)
            return s[0][1], s[1][1]
    return None


def main() -> int:
    from ldv.checks._fixtures import find_corpus, load

    which = "keyset"
    print("═══ `§A4` 的 `holistic` 见证：能不能在语料上搜出来 ═══")
    full = REPO / "_data" / "openalex-citations" / "nodes"
    for tag, env in (("36 项（仓内）", None), ("全量（3907）", full)):
        if env:
            os.environ["LDV_CORPUS"] = str(env)
        else:
            os.environ.pop("LDV_CORPUS", None)
        loaded = load()
        if loaded is None:
            print(f"  {tag}: 找不到语料")
            continue
        nodes, edges, _ = loaded
        from ldv.checks.coverage import corpus_fingerprint
        corpus_fp = corpus_fingerprint(nodes, edges)
        spec, kernel, ctx = load_ctx(nodes, edges, which)
        if spec is None:
            print(f"  {tag}: 声明读不到（跳过）")
            continue
        print(f"\n── {tag}｜|U| = {len(spec.universe)}｜语料指纹 "
              f"{corpus_fp}")
        for reading, ks in (("中位数", (3,)), ("覆盖计数", (2, 3, 4))):
            found = None
            for k in ks:
                found = search(ctx, spec, k, reading)
                if found:
                    a, b = found
                    print(f"   ★ {reading}：**搜到**（k={k}）A={sorted(a)} B={sorted(b)}")
                    break
            if not found:
                print(f"   ✗ {reading}：k ∈ {ks} 里**搜不到** ⇒ ② 不成立")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
