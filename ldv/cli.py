"""四条流程的入口 —— 工作流程 §1–§4。

    python -m ldv.cli all                  # B → A → D（默认；三个插件都跑）
    python -m ldv.cli all --plugin reach   # 只跑一个方向
    python -m ldv.cli a                    # 只跑流程 A（运行）
    python -m ldv.cli b                    # 只跑流程 B（维护）
    python -m ldv.cli d                    # 只跑流程 D（自优化）
    python -m ldv.cli r --remove 12        # 只跑流程 B′（删除，§10.2 C）
    python -m ldv.cli all --holdout 12     # 留 12 项当「维护」阶段的新项（默认 12）
    python -m ldv.cli all --remove 12      # 末尾再删 12 项（默认 0 = **不跑**）

**为什么顺序是 B → A → D**：维护把结构建全，运行才在完整结构上发生，
自优化用的就是运行攒下的记录。这个顺序也让「查询的 ground truth 覆盖全语料」
与「结构里真的有那些项」一致 —— 否则会看到「漏项」，而那只是项还没插进去。

⚠️ **流程 B′（删除）排在最后**，理由同源：删掉项之后查询的 ground truth
就不在结构里了，那时再跑 A 会看到**假的漏项**。
⚠️ **默认 `--remove 0` ⇒ B′ 整段不印**（不是印一行「删除 0 项」）：
「没跑」与「跑了但没删」必须分得开，而**不印**是最不容易读错的那种分法。
要跑就显式给 `--remove N`。

退出码：**只看护栏** —— `M3`（锥外变动）/ `F3`（外生项被改）/ **空转** / 未捕获异常。
方向数、记录数、位次这类**度量走输出，不进退出码**（继承 dce）。

⚠️ **空转也进退出码** —— 这是刻意的，也是本项目的中心纪律：
「空转与通过长得一模一样」是这里最贵的错。`python -m ldv.cli d` 单独跑时
没有使用记录，自优化**什么都没学到**；那种运行必须红，不能印个「成功」。
⚠️ **B′ 的 `D2` / `D3` 默认「未展开」**（要外生覆盖 oracle，`flow.remove_items`
的 `cover` 参数）—— 报告里印成 `**未展开**`，**不是** `0`。
"""

from __future__ import annotations

import sys

from .checks._fixtures import (
    coverage_of,
    keyset_queries,
    load,
    items as make_items,
    reach_queries,
    sequences,
)
from .core.kernel import Kernel
from .flow import (
    insert_items,
    make_params,
    remove_items,
    render_chain,
    run_optimize,
    run_query,
)
from .plugins.keyset import KeysetPlugin
from .plugins.reach import ReachPlugin
from .plugins.sequence import SequencePlugin

#: 每个方向：插件工厂 · 根 payload · 查询集 · 最外层意图（**外生，人声明**）
DIRECTIONS = ("keyset", "reach", "sequence")

INTENTS = {
    "keyset": "键集包含 —— 最外层意图不约束任何键",
    "reach": "锚点可达 —— 最外层意图 = 覆盖全部项",
    "sequence": "序列前缀 —— 最外层意图 = 空前缀，不约束序列",
}


def _assemble(name: str, nodes, edges, holdout: int, remove: int = 0):
    """造 `(内核, 查询集, 待插入的新项, 待删除的项 id, 覆盖 oracle)`。

    `holdout` 项**不放进 `build()` 的全集** —— 它们是流程 B 的「新项」。
    这样维护才真的在「先建一批、后来再添」，而不是把已声明的项重走一遍。

    `remove` 项从**已进结构**的那些里挑（`init_items` 的尾部），
    这样删除才真的碰到结构；挑「没进结构的项」等于删空气。
    """
    ids = sorted(nodes)
    hold = set(ids[-holdout:]) if holdout > 0 else set()
    all_items = make_items(nodes)

    if name == "keyset":
        plug = KeysetPlugin()
        root = plug.merge([])
        queries = keyset_queries(nodes)
    elif name == "reach":
        plug = ReachPlugin(edges)
        root = frozenset(ids)                       # 外生：覆盖一切
        queries = reach_queries(nodes, edges)
    elif name == "sequence":
        plug = SequencePlugin(sequences(nodes, edges))
        root = frozenset({()})                      # 外生：空前缀覆盖一切
        queries = reach_queries(nodes, edges)
    else:
        raise ValueError(name)

    init_items = {k: v for k, v in all_items.items() if k not in hold}
    kernel = Kernel(plug, init_items)
    kernel.build(root)
    for iid in sorted(init_items):                  # 批建：把初始项纳入结构
        kernel.insert(iid)

    new = [(i, all_items[i]) for i in sorted(hold)]
    removable = sorted(init_items)[-remove:] if remove > 0 else []
    return kernel, queries, new, removable, coverage_of(name, nodes, edges)


def run_one(name: str, holdout: int, only: str | None,
            remove: int = 0) -> tuple[bool, str]:
    """跑一个方向。返回 `(护栏是否全过, 追踪文本)`。"""
    loaded = load()
    if loaded is None:
        return False, "⚠ 找不到语料 —— 四条流程都跑不起来（这不是通过）"
    nodes, edges, _ = loaded

    kernel, queries, new, removable, cover = _assemble(name, nodes, edges, holdout, remove)
    st0 = kernel.stats()
    head = (f"═══ 方向 {name} ═══\n"
            f"  初始：{st0['方向']} 个方向（§K2 判空 {st0['§K2 判空']}），"
            f"装进 {len(kernel.items)} 项，事件 {st0['事件']} 条；"
            f"待维护 {len(new)} 项"
            + (f"；待删除 {len(removable)} 项" if removable else ""))

    b = a = d = r = None
    # 账本逐段快照 —— 只在段落**之间**取，所以每段增量是真的可核对的
    n = [len(kernel.ledger)]
    if only in (None, "b"):
        b = insert_items(kernel, new)
    n.append(len(kernel.ledger))
    if only in (None, "a"):
        # 跑三条查询 —— 记录累积下来给流程 D 用
        for q in queries[:3]:
            a = run_query(kernel, q)
    n.append(len(kernel.ledger))
    if only in (None, "d"):
        d = run_optimize(kernel, make_params(name, INTENTS[name]))
    n.append(len(kernel.ledger))
    # ★ 删除**排在最后** —— 删掉项之后查询的 ground truth 就不在结构里了。
    #   那两条（`D2`/`D3`）要外生覆盖 oracle，这里把 `cover` 传进去 ⇒ 它们是**量过**的。
    #   ⚠️ `only == "r"` 且没给 `--remove` ⇒ 设成**空表**（而不是 `None`）：
    #      那样「B′ 空转」这条护栏才会开火 —— 「没跑」与「跑了但没删」不能共用一行。
    if only in (None, "r"):
        if removable or only == "r":
            r = remove_items(kernel, removable, cover=cover)
    n.append(len(kernel.ledger))

    st1 = kernel.stats()
    usage = st1["使用记录"]
    grew = sum(x.events for x in (b or ()))
    # ⚠️ 「A 段 = 展示 + 展开」必须分开报。查询**也会按需展开**（§R1 会调 `expand`），
    #    所以 A 段的账本增量**不等于**记录数 —— 把两者当成一回事，
    #    「账本可核对」这句话就是假的（原来那版就是）。
    tail = (f"  终态：{st1['方向']} 个方向（§K2 判空 {st1['§K2 判空']}，"
            f"叶容量 最大 {st1['最大叶容量']} / 平均 {st1['平均叶容量']}）"
            f"，使用记录 {usage} 条；空方向 {st1['空方向']}"
            f"（已失效 {st1['已失效方向']}）\n"
            f"  账本：初始 {n[0]} → B {n[1]}（结构 +{n[1] - n[0]}）"
            f" → A {n[2]}（展示 +{usage}、**查询时展开** +{n[2] - n[1] - usage}）"
            f" → D {n[3]}（自优化**不写账本**）"
            + (f" → B′ {n[4]}（删除 +{n[4] - n[3]}）" if r is not None else ""))

    # ── 护栏 ───────────────────────────────────────────────────────────────
    # ★ 空转也要拦。理由：**空转与通过长得一模一样**（本项目从头到尾在防这个）。
    #   `cli d` 不先跑 A ⇒ 没有记录 ⇒ 旧版照样印「内生量变了」＋退出码 0。
    guards: list[str] = []
    if b is not None:
        if not b:
            guards.append("B 空转：没有可插入的项（--holdout 0？）")
        elif any(x.out_of_cone for x in b):
            guards.append("M3 违规：变动跑到 Cone(x) 之外")
        elif n[1] - n[0] != grew:
            guards.append(f"报告与账本对不上：报告说结构动了 {grew} 次，"
                          f"账本只长了 {n[1] - n[0]} 条")
    if a is not None and a.空转:
        guards.append("A 空转：一条候选都没摆出来")
    if d is not None:
        if not d.学到东西:
            guards.append("D 空转：没有带权记录 ⇒ 内生量只动了常数项，不是学到了东西"
                          "（先跑 A 攒记录，或直接 `python -m ldv.cli all`）")
        elif n[3] != n[2]:
            guards.append("D 动了结构：自优化只许改内生量，不许写账本")
    if r is not None:
        if not r:
            guards.append("B′ 空转：没有可删除的项（--remove 0？）")
        else:
            bad = [x for x in r if x.违规]
            for x in bad:
                guards.append("B′ " + "；".join(x.违规))

    body = render_chain(a, b, d, r)
    if guards:
        body += "\n  ‼ 护栏：" + "；".join(guards)
    return not guards, "\n".join([head, body, tail])


def main(argv: list[str]) -> int:
    args = argv[1:]
    which = [a for a in args if not a.startswith("-")]
    plugin = "all"
    if "--plugin" in args:
        plugin = args[args.index("--plugin") + 1]
    holdout = 12
    if "--holdout" in args:
        holdout = int(args[args.index("--holdout") + 1])
    # ⚠️ 默认 **0 = 不跑 B′**，而且**整段不印**（见模块 docstring）——
    #    「没跑」与「跑了但没删」不能共用一行，而**不印**是最不容易读错的分法。
    remove = 0
    if "--remove" in args:
        remove = int(args[args.index("--remove") + 1])

    steps = [a for a in which if a in ("a", "b", "d", "r")]
    only = steps[0] if len(steps) == 1 else None
    targets = DIRECTIONS if plugin == "all" else (plugin,)

    print(f"四条流程 · 流程 C（构建验收）在 `python -m ldv.run_checks`")
    print(f"本轮跑：{'A+B+D' if only is None else only.upper()}｜方向：{list(targets)}"
          f"｜维护新项 {holdout}"
          + (f"｜删除 {remove}" if remove else "｜**不删**（`--remove N` 才跑 B′）"))
    print()

    all_ok = True
    for name in targets:
        ok, text = run_one(name, holdout, only, remove)
        all_ok &= ok
        print(text)
        print()

    print("═══ 汇总 ═══")
    print(f"  护栏（M3 锥外变动 / F3 外生项被改 / 空转 / D1–D6）：{'全过' if all_ok else '‼ 有违规'}")
    print(f"  退出码 {0 if all_ok else 1}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
