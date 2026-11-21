"""探针：`B4` 在**删除路径**上报「子方向多出父没有的项」的机制。

假设：`kernel.remove(x)` 只从 `Cone(x)`（= **插入时**记下的 `_path[x]`）里的方向
去掉 x。但 `expand()` 会**重试**（叶不是终态），重试时 x 可能被分进一个
**不在原路径上**的新子方向 ⇒ 那个子方向的成员里有 x，而父方向被摘掉了 x
⇒ `∪members(子) − members(父) ∋ x` ⇒ `extra` 非空。

用法（cwd = repo）：python ../outputs/_probe_extra_defect.py <语料> <方向> [holdout] [remove]
"""
import sys

sys.path.insert(0, ".")

from ldv.checks._fixtures import load
from ldv.cli import _assemble
from ldv.flow import insert_items

corpus = sys.argv[1] if len(sys.argv) > 1 else "openalex-n50"
which = sys.argv[2] if len(sys.argv) > 2 else "keyset"
holdout = int(sys.argv[3]) if len(sys.argv) > 3 else 12
remove = int(sys.argv[4]) if len(sys.argv) > 4 else 12

nodes, edges, _ = load()
kernel, queries, new, removable, cover = _assemble(which, nodes, edges, holdout, remove)
insert_items(kernel, new)

print(f"语料 {corpus} / 方向 {which}：{len(kernel.items)} 项，"
      f"{len(kernel.all_directions())} 方向；待删 {len(removable)} 项")

# ── 先找出「父子不一致」的那些对（删之前就该是 0） ──────────────────────────
def extras(kernel):
    out = []
    for d in kernel.all_directions():
        kids = kernel.children_of(d)
        if len(kids) < 2:
            continue
        pm = set(kernel.members_of(d))
        um = set()
        for k in kids:
            um |= set(kernel.members_of(k))
        ex = sorted(um - pm)
        if ex:
            out.append((d, ex))
    return out

print(f"\n删之前的 extra 对：{len(extras(kernel))}（应当为 0）")
for d, ex in extras(kernel):
    print(f"   {d.did}: {ex[:3]}")

# ── 逐个删，每次删之前抓 `_path[x]` 与「x 在哪些方向里」 ─────────────────────
def where_is(kernel, x):
    return [d.did for d in kernel.all_directions() if x in set(kernel.members_of(d))]

print("\n── 逐个删除 ──")
for i, x in enumerate(removable, 1):
    path = kernel.cone(x)                 # `remove` 会把 `_path[x]` 弹掉，先抓
    locs = where_is(kernel, x)
    off_path = [d for d in locs if d not in path]
    before = len(extras(kernel))
    kernel.remove(x)
    after = extras(kernel)
    flag = ""
    if len(after) > before:
        flag = "  ‼ 新增 extra"
    print(f"  删 {i:>2} {x}")
    print(f"      Cone(x)（插入路径，{len(path)} 层）：{list(path)}")
    print(f"      x 实际在的成员方向（{len(locs)} 个）：{locs}")
    print(f"      ★ 在成员里、**不在锥上**：{off_path}{flag}")
    if len(after) > before:
        for d, ex in after:
            if x in ex:
                print(f"         → {d.did} 的子方向多出 {x}"
                      f"；{d.did} 是 x 的成员吗：{x in set(kernel.members_of(d))}"
                      f"；{d.did} 在锥上吗：{d.did in path}")
