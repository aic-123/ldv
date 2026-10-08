"""单元测试 —— 内核不变量 / 三态 / 语料形状 / 自优化边界。

    跑法：python -m ldv.tests.run_tests

零第三方依赖（不用 pytest）—— 与 dce 的 `tests/run_tests.py` 同形：
自己数，自己报，**过 / 红分开计数**。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ldv.checks._fixtures import (  # noqa: E402
    build_keyset,
    build_reach,
    build_sequence,
    corpus_candidates,
    coverage_of,
    find_corpus,
    items,
    load,
    sequences,
)
from ldv.checks.coverage import (  # noqa: E402
    cover_leak_profile,
    soundness_profile,
)
from ldv.core import selfopt  # noqa: E402
from ldv.core.direction import (  # noqa: E402
    EVENT_INVALIDATED,
    EVENT_STAYED,
    EVENT_UNSPLITTABLE,
    ORIGIN_EXOGENOUS,
)
from ldv.core.kernel import (  # noqa: E402
    EXPOSURE_ALPHA,
    PROPENSITY_FLOOR,
    Kernel,
    Query,
    _did_order,
    exposure_propensity,
)
from ldv.core.tri import Tri, fold, is_bad_hit  # noqa: E402
from ldv.corpus.loader import CorpusError, keys_of, parse_front_matter  # noqa: E402
from ldv.plugins.keyset import KeysetPlugin  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def ok(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name + (f"  —— {detail}" if detail and not cond else ""))


def raises(name: str, fn) -> None:
    try:
        fn()
    except Exception:  # noqa: BLE001
        PASS.append(name)
        return
    FAIL.append(name + "  —— 该抛异常却没抛")


# ═══ 三态 ════════════════════════════════════════════════════════════════════

def test_tri() -> None:
    raises("Tri 不是 bool：bool(未展开) 必须抛错", lambda: bool(Tri.UNEXPANDED))
    raises("Tri 不是 bool：bool(否) 必须抛错", lambda: bool(Tri.NO))
    ok("未展开 折 是 → 未展开", fold(Tri.YES, Tri.UNEXPANDED) is Tri.UNEXPANDED)
    ok("否 折 是 → 未展开（说不清就说不清）", fold(Tri.NO, Tri.YES) is Tri.UNEXPANDED)
    ok("全否 → 否", fold(Tri.NO, Tri.NO) is Tri.NO)
    ok("全是 → 是", fold(Tri.YES, Tri.YES) is Tri.YES)
    ok("只有「否」算危险分支（§K8 不对称）",
       is_bad_hit(Tri.NO) and not is_bad_hit(Tri.YES) and not is_bad_hit(Tri.UNEXPANDED))


# ═══ 语料形状 ════════════════════════════════════════════════════════════════

def test_loader() -> None:
    text = (
        "---\n"
        'id: "x-1"\n'
        "type: 概念\n"
        'aliases: ["a", "b"]\n'
        "cues:\n"
        '  - "c1"\n'
        '  - "c2"\n'
        "source:\n"
        '  ref: "r"\n'
        "  kind: 教材\n"
        "---\n"
        "正文\n"
    )
    fields, body = parse_front_matter(text)
    ok("标量去引号", fields["id"] == "x-1")
    ok("行内列表", fields["aliases"] == ["a", "b"])
    ok("块列表", fields["cues"] == ["c1", "c2"])
    ok("一层嵌套", fields["source"] == {"ref": "r", "kind": "教材"})
    ok("正文分离", body.strip() == "正文")
    k = keys_of(fields)
    ok("键含字段名", {"id", "type", "cues", "source"} <= k)
    ok("键含枚举取值", {"type=概念", "source.kind=教材"} <= k)

    raises("没 front-matter ⇒ 报错（不猜）", lambda: parse_front_matter("没有头\n"))
    raises("行内列表没闭合 ⇒ 报错", lambda: parse_front_matter("---\na: [1, 2\n---\n"))
    raises("意外缩进 ⇒ 报错", lambda: parse_front_matter("---\na: 1\n   b: 2\n---\n"))

    # 真语料：形状之外的一律报错 ⇒ 能整份读完说明形状覆盖住了
    loaded = load()
    if loaded is None:
        PASS.append("真语料（跳过：目录不在）")
        return
    nodes, edges, dangling = loaded
    ok("真语料读得进", len(nodes) > 0, f"读到 {len(nodes)} 个")
    ok("悬挂边为空", not dangling, f"悬挂 {sorted(dangling)}")

    # ⚠️ **计数只对随仓库提交的那份成立。** 换语料（`LDV_CORPUS=…`）时断言必须跟着换 ——
    #    否则「换了一份料」会被读成「语料坏了」，而那正是「跳过 ≠ 通过」要防的混淆。
    shipped = Path(__file__).resolve().parents[1] / "corpus" / "nodes"
    if find_corpus() == shipped:
        ok("真语料读得进（随仓库提交的那份）", len(nodes) == 36, f"读到 {len(nodes)} 个")
        ok("边数与事实相符", sum(len(v) for v in edges.values()) == 47)
    else:
        PASS.append(f"真语料计数（跳过：跑的是外部语料 {find_corpus().parent.name} —— "
                    f"{len(nodes)} 项 / {sum(len(v) for v in edges.values())} 边）")

    # ── ★ 指路：**显式指定却找不到 ⇒ 报错，不许回落** ──────────────────────
    # 回落会让「`LDV_CORPUS` 打错一个字」与「指对了」**长得一模一样**：
    # 跑的是仓库里那 36 项，检查照样全绿、退出码 0。
    # ⚠️ 这条自检**必须**用 `monkeypatch` 之外的办法证明它非空转 ——
    #    下面同时验「设了不存在的名字 ⇒ 报错」与「不设 ⇒ 正常兜底」，两条都要真跑。
    saved = os.environ.get("LDV_CORPUS")
    try:
        os.environ["LDV_CORPUS"] = "ldv-no-such-corpus-xyz"
        raises("`LDV_CORPUS` 指向不存在的名字 ⇒ 报错（不静默回落到默认语料）",
               lambda: find_corpus())
        os.environ.pop("LDV_CORPUS", None)
        ok("不设 `LDV_CORPUS` ⇒ 正常兜底到随仓库提交的那份",
           find_corpus() == shipped, f"兜底到 {find_corpus()}")
        # 名字解析：`_data/<名字>/nodes` 必须在候选链里，否则「按名字指料」这条路是断的
        os.environ["LDV_CORPUS"] = "some-name"
        cands = [str(p) for p in corpus_candidates()]
        ok("`LDV_CORPUS=<名字>` 会去 `_data/<名字>/nodes` 找（仓库旁 + 上一级）",
           any(p.endswith(os.path.join("_data", "some-name", "nodes")) for p in cands)
           and sum(p.endswith(os.path.join("_data", "some-name", "nodes")) for p in cands) == 2,
           f"候选链：{cands}")
    finally:
        os.environ.pop("LDV_CORPUS", None)
        if saved is not None:
            os.environ["LDV_CORPUS"] = saved


# ═══ 内核 ════════════════════════════════════════════════════════════════════

def test_kernel() -> None:
    loaded = load()
    if loaded is None:
        return
    nodes, edges, _ = loaded
    k, _ = build_keyset(nodes)

    ok("根是外生的", k.root.origin == ORIGIN_EXOGENOUS)
    ok("根没有父", k.root.parent is None)
    ok("根秩为 1", k.root.rank == 1)
    ok("秩越大越细（子比父大 1）",
       all(c.rank == k.direction(d).rank + 1
           for d in [x.did for x in k.all_directions()]
           for c in k.children_of(k.direction(d))))
    ok("§K1 见证比自身粗",
       all(k.direction(w).rank < d.rank for d in k.all_directions() for w in d.witness))
    ok("§K3 支撑锥 = 插入路径",
       all(k.cone(i) and k.cone(i)[0] == k.root.did for i in k.items))
    ok("账本只增不改", k.ledger.verify_append_only() == [])
    ok("方向 id 按数值排（D3 在 D12 前）",
       sorted(["D3", "D12", "D100"], key=_did_order) == ["D3", "D12", "D100"])

    # 按需展开：没碰过的方向不该已展开
    fresh = Kernel(KeysetPlugin(), {i: nodes[i].as_item() for i in nodes})
    fresh.build(KeysetPlugin().merge([]))
    ok("展开是按需的（建完根还没展开任何东西）", len(fresh._expanded) == 0)  # noqa: SLF001

    # §K2 非平凡：分不开 ⇒ **这一层不建**（字面），只留一条判定事件
    k2 = Kernel(KeysetPlugin(), {"a": {"id": "a", "keys": frozenset({"k"})},
                                 "b": {"id": "b", "keys": frozenset({"k"})}})
    k2.build(KeysetPlugin().merge([]))
    kids = k2.expand(k2.root)
    ok("§K2：键集全同 ⇒ 不建这一层（成为叶）", kids == ())
    ok("§K2 判空被记下来（不是没记录）",
       any(e.kind == EVENT_UNSPLITTABLE for e in k2.ledger))
    # ★ 判定的**主体是父方向** —— 而且**没有子方向可以挂**（一个都没建）
    ok("§K2 判空挂在**父**方向上",
       all(e.did == k2.root.did for e in k2.ledger
           if e.kind == EVENT_UNSPLITTABLE))
    ok("§K2 判空后**没有**任何新方向被建出来",
       k2.stats()["方向"] == 1, str(k2.stats()))

    # 查询三态：分辨率不够 ⇒ 未展开，不是否
    res = k.query(Query(ideal=frozenset(nodes), min_rank=99))
    ok("分辨率不够 ⇒ 未展开", res.status is Tri.UNEXPANDED)
    ok("未展开时不许印成「确定没有」", "确定没有" not in res.render())

    # 倾向：内核填（模型见 test_exposure_model）
    d = k.all_directions()[0]
    rec = k.record_usage(d, {"outcome": 1.0})
    ok("内核填了倾向权重", rec is not None and 0.0 < rec.propensity <= 1.0)
    ok("「无信号」不产记录", k.record_usage(d, {"outcome": None}) is None)

    # reach：偏函数，拒绝遍历时返回未展开
    kr, _ = build_reach(nodes, edges, traverse_budget=1)
    rres = kr.query(Query(ideal=frozenset(nodes)))
    ok("reach 超预算 ⇒ 未展开（不是否）", rres.counts["未展开"] > 0, str(rres.counts))


# ═══ 曝光模型（§8.1.1） ═══════════════════════════════════════════════════════
#
# 这一段查三件事，**都不是读代码断言，而是「改一个数看输出跟不跟着变」**：
#
#     形状     位次 1 ⇒ 1.0；单调不增；下界真的生效
#     α 接线   `EXPOSURE_ALPHA` 是**显式参数** ⇒ 改它必须改变输出
#     单一实现 改 α 之后，**检查侧**（`checks/semantics._p`）与**内核侧**
#               （`record_usage` 填进记录里的那个数）必须**一起变**
#
# ⚠️ 第三条是本段存在的理由。修之前 `kernel` / `semantics._p` / `run_tests`
#    **各有一份** `max(1.0 / position, 0.05)` ⇒ 改一处、另两处**静默报旧值**，
#    而输出里只有一个数、**没有对照** ⇒ 读起来和改对了完全一样。
#    这是 `false-green` 形状 3（共享盲点），而 grep 源码挡不住它 ——
#    真正能挡住的是「**把参数改掉，看那个数动不动**」。

#: 实测锚点（`ldv/MEASUREMENTS.md` 结果十三）：Open Bandit Dataset 的 `random` 策略
#: （位次**随机分配** ⇒ 位次效应可因果识别）拟合出的 α。
#: ⚠️ 那是**横向槽位**的几何，量不了本设计的竖直列表 ⇒ 只作**量级参照**，不当取值依据。
OBD_ALPHA = (0.105, -0.003)


def test_exposure_model() -> None:
    from ldv.checks import semantics as sem
    from ldv.core import kernel as km

    ok("曝光：位次 1 的倾向 = 1.0（归一化点）",
       exposure_propensity(1) == 1.0, f"{exposure_propensity(1)}")

    seq = [exposure_propensity(k) for k in range(1, 65)]
    ok("曝光：位次越靠后倾向**不增**（单调不增）",
       all(b <= a + 1e-15 for a, b in zip(seq, seq[1:])),
       f"1→{seq[0]:.4f} 64→{seq[-1]:.4f}")

    ok("曝光：下界真的生效（不夹的话第 10^9 位会拿到 10^9 倍权重）",
       exposure_propensity(10**9) == PROPENSITY_FLOOR,
       f"{exposure_propensity(10**9)}")
    ok("曝光：下界之上**还没**生效（否则它就是个常数模型，不是指数模型）",
       exposure_propensity(3) > PROPENSITY_FLOOR,
       f"{exposure_propensity(3)}")

    # ★ α 是显式参数：把它改成 2，三处输出必须**一起**动
    ok("曝光：默认 α = 1.0（设计文档 §8.1.1 的取值；改它要留痕 —— 见结果十三）",
       EXPOSURE_ALPHA == 1.0, f"α = {EXPOSURE_ALPHA}")

    old = km.EXPOSURE_ALPHA
    loaded = load()
    try:
        km.EXPOSURE_ALPHA = 2.0
        got_fn = exposure_propensity(3)
        got_chk = sem._p(None, ("a", "b", "c"), -1)          # 位次 3
        got_k = None
        if loaded is not None:
            kx, _ = build_keyset(loaded[0])
            dirs = kx.all_directions()[:3]
            kx.show(tuple(d.did for d in dirs))
            # 记**第 3 位**那个 —— 记第 1 位的话倾向恒为 1.0，这条断言就空了
            rx = kx.record_usage(dirs[-1], {"outcome": 1.0})
            got_k = (rx.propensity, rx.extra["position"]) if rx else None
            ok("曝光：这一条真在验第 3 位（否则下面的断言是空转）",
               got_k is not None and got_k[1] == 3, f"{got_k}")
            got_k = got_k[0] if got_k else None
    finally:
        km.EXPOSURE_ALPHA = old

    ok("曝光：α 是**显式参数** ⇒ 改成 2 之后 `exposure_propensity(3)` = 1/9"
       "（否则公式是写死的 `1/位次`，标定不了）",
       abs(got_fn - 1.0 / 9.0) < 1e-12, f"{got_fn}")
    ok("★ 曝光：改 α 之后**检查侧**跟着变（`semantics._p` 没有自己藏第二份公式）",
       got_chk == f"{1.0 / 9.0:.3f}", f"报告里印的是 {got_chk}，应为 0.111")
    ok("★ 曝光：改 α 之后**内核侧**跟着变（填进使用记录的那个数也是同一个来源）",
       got_k is None or abs(got_k - 1.0 / 9.0) < 1e-12, f"记录里的倾向 {got_k}")

    ok("曝光：α 复原（本段不留全局副作用）",
       km.EXPOSURE_ALPHA == old == EXPOSURE_ALPHA)


# ═══ 自优化边界 ══════════════════════════════════════════════════════════════

def test_selfopt() -> None:
    loaded = load()
    if loaded is None:
        return
    nodes, _, _ = loaded
    k, _ = build_keyset(nodes)
    params = selfopt.Params(exogenous={"direction_functions": ["keyset"],
                                       "outermost_intent": "全空间"},
                            endogenous={"expand_priority": {}, "penalty_scale": 1.0})
    for d in k.all_directions()[:3]:
        k.record_usage(d, {"outcome": 1.0})
    after, info = selfopt.optimize(k, params)
    ok("F3：外生项指纹未变",
       after.fingerprint_exogenous() == params.fingerprint_exogenous())
    ok("F2：内生项确实被改了",
       after.fingerprint_endogenous() != params.fingerprint_endogenous())
    ok("F1：缺倾向的记录被拦下，不当 0 用", info["被拦下（缺倾向）"] == 0)
    ok("F4：内生量里没有跨方向全局评分",
       not any(x in after.endogenous for x in ("score", "objective", "global", "total")))

    # 硬拦：外生项是**只读**的 —— 写的那一刻就炸
    # ⚠️ 这里刻意用「先改、再调」的形状：指纹比对**放行**这种改动
    #    （`optimize` 在开头才取指纹，它看不见调用前发生的事），
    #    所以真正的守卫必须是只读容器。
    raises("F3：写外生项 ⇒ 当场抛（不靠事后比对）",
           lambda: params.exogenous.__setitem__("outermost_intent", "被改了"))
    raises("F3：update 外生项 ⇒ 当场抛",
           lambda: params.exogenous.update({"outermost_intent": "被改了"}))

    def leaky(kernel, p):
        p.exogenous["outermost_intent"] = "被改了"       # 调用前就改
        return selfopt.optimize(kernel, p)

    raises("F3：调用前改外生项也拦得住", lambda: leaky(k, params))


# ═══ 方向 C（序列前缀） ══════════════════════════════════════════════════════

def test_sequence() -> None:
    """方向 C 的三条硬性质 —— 它们都**不依赖真实语料里恰好有什么**。

    先用手造的极小语料把前缀语言的语义钉死，再去真语料上全量扫等价关系。
    """
    from ldv.plugins.sequence import END, SequencePlugin

    # --- 手造语料：把「顺序」这件事钉死 ---
    sp = SequencePlugin({"x": ("概念", "案例"), "y": ("案例", "概念"), "z": ()})

    ok("空前缀覆盖一切（根 = 最外层意图，不约束序列）",
       sp.covered_set(frozenset({()})) == frozenset({"x", "y", "z"}))
    ok("END 哨兵只覆盖「序列为空」的项",
       sp.covered_set(frozenset({(END,)})) == frozenset({"z"}),
       f"实际 {sorted(sp.covered_set(frozenset({(END,)})))}")
    ok("单符号前缀收下所有以它开头的项",
       sp.covered_set(frozenset({("概念",)})) == frozenset({"x"}))
    ok("加一个符号 ⇒ 覆盖只收窄，不变宽",
       sp.covered_set(frozenset({("概念", "案例")})) <= sp.covered_set(frozenset({("概念",)})))
    ok("**顺序敏感**：同样的两个符号，顺序不同 ⇒ 收下的项不同（A/B 上做不到）",
       sp.covered_set(frozenset({("概念", "案例")})) == frozenset({"x"})
       and sp.covered_set(frozenset({("案例", "概念")})) == frozenset({"y"}))
    ok("合并（空组）退化成空前缀 ⇒ 覆盖一切（合并的定义域要闭合）",
       sp.merge([]) == frozenset({()})
       and sp.covered_set(sp.merge([])) == frozenset({"x", "y", "z"}),
       f"merge([])={sorted(sp.merge([]))}")
    ok("合并的覆盖 ⊇ 每个子方向的覆盖",
       sp.covered_set(sp.merge([_fake_dir(frozenset({("概念", "案例")})),
                                _fake_dir(frozenset({("案例", "概念")}))]))
       >= frozenset({"x", "y"}))

    # --- 真语料：全量扫 ---
    loaded = load()
    if loaded is None:
        PASS.append("方向 C 真语料（跳过：目录不在）")
        return
    nodes, edges, _ = loaded
    k, plug = build_sequence(nodes, edges)

    # ① §I3 的严格等价：代价 0 ⟺ 满足
    bad: list = []
    for d in k.all_directions():
        for nid in sorted(nodes):
            zero = plug.penalty(d, nodes[nid].as_item()) == 0.0
            sat = plug.covers(frozenset(d.payload), nid)
            if zero != sat:
                bad.append((d.did, nid))
    pairs = len(k.all_directions()) * len(nodes)
    ok(f"§I3：代价 0 ⟺ 满足（全量 {pairs} 对）", not bad, f"{len(bad)} 对不一致：{bad[:2]}")

    # ② §I5 往返：前缀语言逐字不变
    rt = [d.did for d in k.all_directions()
          if plug.decode(plug.encode(d)) != frozenset(d.payload)]
    ok("§I5：编码/解码往返，前缀语言逐字不变", not rt, f"{len(rt)} 个方向往返后变了：{rt[:3]}")

    # ③ §I4 的划分在 **payload 层**也成立：
    #    已展开方向的每个成员，被两个子方向**恰好覆盖一次**
    #    （B4 查的是内核的归属分配；这一条查得更早 —— 插件提出的两个语言本身就分得开）
    bad3: list = []
    checked3 = 0
    for d in k.all_directions():
        kids = k.children_of(d)
        if len(kids) < 2:
            continue
        for nid in k.members_of(d):
            n = sum(1 for c in kids if plug.covers(frozenset(c.payload), nid))
            checked3 += 1
            if n != 1:
                bad3.append((d.did, nid, n))
    ok(f"§I4：成员被两个子方向恰好覆盖一次（{checked3} 个成员）",
       not bad3, f"{len(bad3)} 个例外：{bad3[:2]}")

    # ④ 序列是**确定性**算出来的（§8.2：依赖遍历顺序就不可复现）
    from ldv.checks._fixtures import sequences
    ok("seq(x) 确定性：跑两遍完全一样",
       sequences(nodes, edges) == sequences(nodes, edges))


# ═══ 流程 A / B / D ══════════════════════════════════════════════════════════

def test_flows() -> None:
    """三条流程的**不变量** —— 每条都能红，不是「跑完就算过」。

    流程 C 的判据在 `run_checks.py`；这里查的是 C 管不到的三件事：

        A  展示 ≠ 遍历（内核原来把前沿当展示 ⇒ 曝光位次在多层查询下是假的）
        B  插入**新项**后 M3 / B9 仍成立，且失效是「标记不删」
        D  只改内生量、不碰结构、缺倾向记录被拦下

    最要紧的一条是 A 的**非退化**断言：`_last_frontier ≠ yes` 必须**真的发生过**，
    否则「展示要显式」这件事就只是文档里的说法，没有证据。
    """
    loaded = load()
    if loaded is None:
        PASS.append("流程 A/B/D（跳过：语料目录不在）")
        return
    nodes = loaded[0]

    from ldv.checks._fixtures import items as make_items, keyset_queries
    from ldv.flow import insert_items, make_params, run_optimize, run_query

    # ── 流程 A · 运行 ──────────────────────────────────────────────────────
    k, _ = build_keyset(nodes)
    qs = keyset_queries(nodes)
    a = run_query(k, qs[0])

    ok("A：这次查询真的摆出了候选（否则下面的断言全是空转）",
       len(a.shown) > 0, f"展示 {len(a.shown)} 个")
    ok("A：展示列表 ⊆ 说「是」的方向（摆的是候选，不是否也不是未展开）",
       set(a.shown) <= set(a.result.yes))
    ok("A：展示顺序 = 候选顺序（内核不重排）", a.shown == tuple(a.result.yes))
    ok("A：位次从 1 起 ⇒ 第一个的倾向 = 1.0",
       a.records and a.records[0].extra["position"] == 1
       and a.records[0].propensity == 1.0,
       f"第一条 {a.records[0].propensity if a.records else '无'}")
    ok("A：倾向 = 曝光模型（内核按曝光位次填，插件伪造不了）",
       all(abs(r.propensity - exposure_propensity(r.extra["position"])) < 1e-12
           for r in a.records))

    # 没给反馈 ≠ 给了负反馈：前者不产记录
    a_none = run_query(k, qs[0], feedback={did: None for did in a.shown})
    ok("A：全不给反馈 ⇒ 一条记录都不产（无信号 ≠ 负信号）",
       len(a_none.records) == 0 and a_none.没给反馈 == len(a_none.shown))
    half = {did: (None if i % 2 else 1.0) for i, did in enumerate(a.shown)}
    a_half = run_query(k, qs[0], feedback=half)
    ok("A：只给一半反馈 ⇒ 记录数 = 给了反馈的那些",
       len(a_half.records) == len(a_half.shown) - a_half.没给反馈
       and 0 < a_half.没给反馈 < len(a_half.shown))

    # ★ 核心：遍历**不**设展示
    kq, _ = build_keyset(nodes)
    kq.query(qs[0])
    ok("A：遍历本身不设展示（展示是显式的一步）", kq.shown == ())
    ok("A：遍历留下的是「前沿」（内省用），不是展示",
       kq._last_frontier != () and kq.shown != kq._last_frontier)  # noqa: SLF001

    diff = 0
    for q in qs[:6]:
        kq2, _ = build_keyset(nodes)
        r2 = kq2.query(q)
        if set(kq2._last_frontier) != set(r2.yes):  # noqa: SLF001
            diff += 1
    ok("A：多层查询下「前沿」≠「说『是』的方向」**真的发生过**",
       diff > 0, f"{diff}/6 条查询上两者不同")

    # 没有展示上下文时：写「不知道」，不默默当成知道
    kn, _ = build_keyset(nodes)
    d0 = kn.all_directions()[0]
    rec = kn.record_usage(d0, {"outcome": 1.0})
    ok("A：没展示过就记账 ⇒ 标出「无展示上下文」",
       rec is not None and rec.extra["无展示上下文"] is True)
    kn.show([d0])
    rec2 = kn.record_usage(d0, {"outcome": 1.0})
    ok("A：展示过之后 ⇒ 上下文为真，位次 = 1",
       rec2 is not None and rec2.extra["无展示上下文"] is False
       and rec2.extra["position"] == 1)

    # ── 流程 B · 维护 ──────────────────────────────────────────────────────
    ids = sorted(nodes)
    all_items = make_items(nodes)
    hold = set(ids[-12:])
    init = {i: all_items[i] for i in ids if i not in hold}
    kb = Kernel(KeysetPlugin(), init)
    kb.build(KeysetPlugin().merge([]))
    for i in sorted(init):
        kb.insert(i)

    new = [(i, all_items[i]) for i in ids if i in hold]
    before_events = len(kb.ledger)
    before_dirs = kb.stats()["方向"]
    reports = insert_items(kb, new)

    ok("B：每项都出了一份报告（没漏、没多）", len(reports) == len(new))
    ok("B：M1 支撑锥从根起", all(r.cone and r.cone[0] == kb.root.did for r in reports))
    ok("B：M3 —— 变动 ⊆ Cone(x)，锥外 0 处",
       not any(r.out_of_cone for r in reports),
       f"越界：{[r.out_of_cone for r in reports if r.out_of_cone][:1]}")
    ok("B：新项真的进了结构（在根成员里）",
       all(i in kb.members_of(kb.root) for i, _ in new))
    ok("B：归属**确实**动了（每项沿锥都改了成员）—— 插入不是空转",
       all(r.changed for r in reports),
       f"没动过的：{[r.item for r in reports if not r.changed][:3]}")
    ok("B：新项出现在它锥上**每一个**方向的成员里（归属沿锥落地）",
       all(all(i in kb.members_of(kb.direction(x)) for x in r.cone)
           for r, (i, _) in zip(reports, new)))
    # ⚠️ 这一条是**等价式**，不是「账本必须长长」——
    #    真语料上 12 个新项全落进已有叶 ⇒ 一条结构事件都不产，这是**对的**。
    #    我把「账本要长长」当不变量写过一次，红了；红的是断言，不是代码。
    #    真不变量是：**账本不撒谎** —— 有事件 ⟺ 真长出了新方向。
    ok("B：账本不撒谎 —— 结构事件 ⟺ 新方向出现（不凭空记、也不漏记）",
       (len(kb.ledger) - before_events > 0) == (kb.stats()["方向"] > before_dirs),
       f"事件 +{len(kb.ledger) - before_events}，方向 +{kb.stats()['方向'] - before_dirs}")
    ok("B：B9 插入后账本仍只增不改", kb.ledger.verify_append_only() == [])

    # §K2 字面 = **不建这一层** —— 手造一个必然退化的层，比等真语料碰巧出现可靠。
    # ⚠️ 这一段原来查的是「失效方向仍在 all_directions 里（标记不删）」。
    #    字面实现之后**没有失效方向可查了** —— 那一层根本没建出来。
    #    代价搬到了**叶容量**上，所以这里改查三件事：判空记在父上、没建新方向、叶还是满的。
    kd = Kernel(KeysetPlugin(), {"a": {"id": "a", "keys": frozenset({"k"})},
                                "b": {"id": "b", "keys": frozenset({"k"})}})
    kd.build(KeysetPlugin().merge([]))
    kd.insert("a")
    kd.insert("b")
    uns = [e.did for e in kd.ledger if e.kind == EVENT_UNSPLITTABLE]
    ok("B：§K2 判空确实产生了判定事件（否则下面那条是空转）", bool(uns))
    ok("B：判空全挂在**父**方向上（没有子方向可以挂）",
       all(x == kd.root.did for x in uns), str(uns))
    ok("B：判空**没有**建出任何新方向（字面：不建这一层）",
       kd.stats()["方向"] == 1, str(kd.stats()))
    ok("B：代价搬到了叶容量上 —— 两个项都留在这个叶里",
       kd.stats()["最大叶容量"] == 2, str(kd.stats()))
    # ★ 真语料上也查一遍「没有空壳」—— 这里判空 7 次，旧实现会多出 14 个空壳。
    ok("★ 真语料：不存在空壳方向（判空 7 次，方向数里一个空壳都不多）",
       all(kb.members_of(d) for d in kb.all_directions()),
       f"空壳：{[d.did for d in kb.all_directions() if not kb.members_of(d)][:5]}")
    ok("★ 真语料：方向数 = 有子层 + 叶（不是「活方向 + 2×判空」）",
       kb.stats()["方向"] == kb.stats()["有子层"] + kb.stats()["叶"],
       str(kb.stats()))

    # ── 流程 D · 自优化 ────────────────────────────────────────────────────
    kf, _ = build_keyset(nodes)
    for q in qs[:3]:
        run_query(kf, q)                       # 攒真实使用记录
    st_before = kf.stats()
    params = make_params("keyset", "键集包含 —— 最外层意图不约束任何键")
    fd = run_optimize(kf, params)

    ok("D：外生项逐项未变（F3）",
       fd.params.fingerprint_exogenous() == params.fingerprint_exogenous())
    ok("D：内生量确实变了（F2 —— 不是空转）", fd.内生量确实变了,
       f"{fd.before} → {fd.after}")
    ok("D：**有证据地**变了（至少一个方向拿到 expand_priority）", fd.学到东西)
    ok("D：缺倾向的记录被拦下（F1），不当 0 用",
       fd.info["被拦下（缺倾向）"] == 0 and fd.info["带权记录"] > 0)
    ok("D：按方向分别更新（内生量里出现 expand_priority.<did>）",
       any(k.startswith("expand_priority.") for k in fd.params.endogenous))
    ok("D：内生量里没有跨方向全局评分（F4）",
       not any(x in fd.params.endogenous for x in ("score", "objective", "global", "total")))
    ok("D：自优化**不碰结构**（只改内生量，方向数与事件数不变）",
       kf.stats()["方向"] == st_before["方向"]
       and kf.stats()["事件"] == st_before["事件"])

    # ★ 「内生量确实变了」会**退化恒真** —— 必须单独钉住这一点，
    #   否则空转的运行会印成「成功」。
    kz, _ = build_keyset(nodes)                     # 干净内核：一条记录都没有
    fd0 = run_optimize(kz, make_params("keyset", "键集包含"))
    ok("D：0 条记录时「内生量确实变了」仍然为真 —— **所以它不能当产出判据**",
       fd0.内生量确实变了 and not fd0.学到东西,
       f"变了={fd0.内生量确实变了} 学到={fd0.学到东西}")
    ok("D：0 条记录 ⇒ 内生量的变化只是常数项（penalty_scale 1.0 → 0.0）",
       fd0.params.endogenous.get("penalty_scale") == 0.0
       and not any(k.startswith("expand_priority.") for k in fd0.params.endogenous))

    # A 的空转判据
    ka, _ = build_keyset(nodes)
    # ⚠️ 别拿「互斥要求」那个查询当空候选 —— 键集的 `命中` 允许假阳，
    #    所以它对不可能查询照样说「是」（那是 §K8 的正确形状，不是缺陷）。
    #    真正「一条候选都没有」只能用**分辨率不够**来造。
    a_none_shown = run_query(ka, Query(ideal=frozenset(nodes), min_rank=99))
    ok("A：候选为空 ⇒ 标成空转（不许与「有候选但都没反馈」混为一谈）",
       a_none_shown.空转 and not a_none_shown.records,
       f"展示 {len(a_none_shown.shown)} 个")
    ok("A：有候选时不是空转", not a.空转)

    # ── 流程 B′ · 删除（§10.2 C）─────────────────────────────────────────────
    # ⚠️ 要**真删到结构里**：挑的是已经进过结构的项，不是「删空气」。
    from ldv.flow import remove_items

    kr, _ = build_keyset(nodes)
    cover_k = coverage_of("keyset", nodes, loaded[1])
    victim = sorted(kr.items)[-6:]
    before_dirs = kr.stats()["方向"]
    rp = remove_items(kr, victim, cover=cover_k)

    ok("B′：每项都出了一份报告（没漏、没多）", len(rp) == len(victim))
    ok("B′：M1 支撑锥从根起", all(x.cone and x.cone[0] == kr.root.did for x in rp))
    ok("B′：D4/M3 —— 变动 ⊆ Cone(x)，锥外 0 处",
       not any(x.out_of_cone for x in rp),
       f"越界：{[x.out_of_cone for x in rp if x.out_of_cone][:1]}")
    ok("B′：**方向数一个都没少**（§M2 情形③「标记，不删」）",
       kr.stats()["方向"] == before_dirs)
    ok("B′：D6 —— 没有方向被真删（`_dirs` 里一个都没少）",
       not any(x.gone_directions for x in rp))
    ok("B′：D5 —— 账本只增不改", not any(x.ledger_rewritten for x in rp)
       and kr.ledger.verify_append_only() == [])
    ok("B′：D1 —— 支持集空了的方向都有失效账目",
       not any(x.unrecorded_empty for x in rp))
    ok("B′：D2/D3 —— **量过了**，且都是 0（不是「未展开」）",
       all(x.cover_leak == 0 and x.outside_members == 0 for x in rp),
       f"读数 {[(x.cover_leak, x.outside_members) for x in rp]}")
    # ★ 非退化：**真的有方向变空** —— 否则 D1/D6 查的是空气。
    ok("B′：非退化 —— 这一趟**真的有方向支持集空了**（否则 D1/D6 是空转）",
       any(x.emptied for x in rp),
       f"变空 {[x.emptied for x in rp if x.emptied][:3]}")
    ok("B′：空方向数 == 已失效方向数 > 0（结构事实与账本事实一致）",
       kr.stats()["空方向"] == kr.stats()["已失效方向"] > 0, str(kr.stats()))
    ok("B′：被删的项真的不在 `items` 里了", all(v not in kr.items for v in victim))
    ok("B′：被删的项真的不在路径方向的成员里了",
       all(x.item not in kr.members_of(kr.direction(did))
           for x in rp for did in x.cone))
    # 不传 cover ⇒ D2/D3 是「未展开」，**不是 0** —— 两者不能长得一样
    kr2, _ = build_keyset(nodes)
    rp2 = remove_items(kr2, sorted(kr2.items)[-3:])
    ok("B′：不传覆盖 oracle ⇒ D2/D3 是 `None`（未展开），**不是 0**",
       all(x.cover_leak is None and x.outside_members is None for x in rp2)
       and all("未展开" in x.render() for x in rp2))
    try:
        kr2.remove("__不存在__")
        _raised = False
    except KeyError:
        _raised = True
    ok("B′：删一个不存在的项 ⇒ 抛 KeyError，不静默当没事", _raised)


# ═══ 涌现：叶不是终态（§M2 情形④） ══════════════════════════════════════════

def test_emergence() -> None:
    """★ 本仓最深的一处实现缺陷 —— 它只有**真跑一遍维护**才看得见。

    `expand()` 原来把「分不开」也写成永久标记，于是一个方向一旦被判成叶就
    **永远是叶**。后果：树只在**第一批**项上长过层，之后插进来的项全堆进已有叶。
    §M2 情形④（涌现）因此**不可达** —— 而「涌现」正是这个设计借来的核心机制。

    四条断言，每条都能红：
      1. 手造语料：两项分不开、加第三项后**必须**长出新层
      2. 反复失败**不许**累积残骸（字面 §K2：判空**不建**方向 ⇒ 结构里只有根），
         但判定会随成员集变化而重判 —— 「结构不长」与「判定不重记」分开查；
         另查**不存在空壳方向**（旧实现每判空一次物化两个空壳，这条会红）
      3. 真语料 × **三个方向**：分批维护不改变**尺度**与**分辨率**
         （方向数、最大叶容量与一次建完逐项相同）；
         且**不许留过期的「分不开」结论**
    """
    from ldv.plugins.keyset import KeysetPlugin

    # --- ① 涌现必须可达 ---
    plug = KeysetPlugin()
    k = Kernel(plug, {"x1": {"id": "x1", "keys": frozenset({"a"})},
                      "x2": {"id": "x2", "keys": frozenset({"a"})}})
    k.build(plug.merge([]))
    k.insert("x1")
    k.insert("x2")
    ok("两项键集相同 ⇒ 根分不开，成为叶", k.children_of(k.root) == ())
    ok("「分不开」被记下来（不是没试过）", k.root.did in k._tried)  # noqa: SLF001

    k.insert("x3", {"id": "x3", "keys": frozenset({"a", "b"})})
    kids = k.children_of(k.root)
    ok("★ 加入第三项后根**真的长出了新层**（§M2 情形④ 涌现）", len(kids) == 2)
    ok("★ 涌现出来的层是非平凡的（两侧都非空）",
       len(kids) == 2 and all(k.members_of(c) for c in kids))
    ok("★ 归属被重新分配：x3 单独一侧，x1/x2 在另一侧",
       len(kids) == 2
       and sorted(len(k.members_of(c)) for c in kids) == [1, 2])
    ok("★ 长出来之后不再保留「分不开」的旧结论", k.root.did not in k._tried)  # noqa: SLF001
    ok("★ 第三项也被分到了子方向里（不是停在父上）",
       any("x3" in k.members_of(c) for c in kids))

    # --- ② 反复失败不许累积残骸 ---
    # ⚠️ 这一段原来查的是「根 + 那一对失效方向 = 3」。字面实现之后**没有那一对**了：
    #    §K2 判「不建这一层」时一个方向都不建 ⇒ 结构里**只有根**。
    #    结构上的残骸是 0，但**判定**会随输入集变化而重复 —— 两者都要查，
    #    因为「结构不长」和「判定不重记」是两件事（§K2 的「时间维度」）。
    plug2 = KeysetPlugin()
    # 只声明 y0 ⇒ 成员集**逐项长大**（2→3→4→5），每次都该重判一次
    k2 = Kernel(plug2, {"y0": {"id": "y0", "keys": frozenset({"a"})}})
    k2.build(plug2.merge([]))
    for i in range(5):
        k2.insert(f"y{i}", {"id": f"y{i}", "keys": frozenset({"a"})})
    ok("反复失败不累积残骸（字面：判空**不建**方向 ⇒ 结构里只有根）",
       k2.stats()["方向"] == 1, f"实际 {k2.stats()}")
    ok("判空只挂在**一个**方向（根）上 —— 不随重试次数增长",
       k2.stats()["§K2 判空"] == 1, f"实际 {k2.stats()}")
    ok("★ 但成员集每变一次就**重判**一次（判空事件 = 4 次变化，不是 5 次插入）",
       sum(1 for e in k2.ledger if e.kind == EVENT_UNSPLITTABLE) == 4,
       f"实际 {sum(1 for e in k2.ledger if e.kind == EVENT_UNSPLITTABLE)} 条")
    ok("同一批成员不重复判（惰性：同一输入只判一次）",
       k2._tried[k2.root.did] == frozenset({f"y{i}" for i in range(5)}))  # noqa: SLF001
    ok("代价确实搬到了叶容量上（5 项全挤在这个叶里）",
       k2.stats()["最大叶容量"] == 5, f"实际 {k2.stats()}")
    # ★ 这条是**字面 §K2 的判据**：判空**不建** ⇒ 不存在「空壳方向」。
    #   旧实现每判空一次就物化**两个**成员集为空的方向，于是
    #   `方向数 = 活方向数 + 2×判空次数`。这条断言在旧实现下**会红**。
    #   ⚠️ 现有检查都抓不到空壳：`B16` 查 `members ⊆ 覆盖`（空集 ⊆ 任何集，恒真），
    #      `B7` 查「有见证」（空壳有见证），`B4` 只查已展开的方向。
    #      所以「没有空壳」必须**单独**断言，不能指望别的检查顺带覆盖。
    ok("★ 字面 §K2：不存在空壳方向（每个方向的成员集都非空）",
       all(k2.members_of(d) for d in k2.all_directions()),
       f"空壳：{[d.did for d in k2.all_directions() if not k2.members_of(d)]}")
    ok("★ 方向数 = 有子层 + 叶，且**没有**「多出来的两个」",
       k2.stats()["方向"] == k2.stats()["有子层"] + k2.stats()["叶"]
       and k2.stats()["方向"] == 1, f"实际 {k2.stats()}")

    # --- ③ 真语料：结构不许因维护而退化 ---
    loaded = load()
    if loaded is None:
        PASS.append("涌现·真语料（跳过：目录不在）")
        return
    nodes, edges, _ = loaded
    from ldv.checks._fixtures import items as make_items

    all_items = make_items(nodes)
    ids = sorted(nodes)

    def build(plug, root, init_n: int, hold_n: int):
        kk = Kernel(plug, {i: all_items[i] for i in ids[:init_n]})
        kk.build(root)
        for i in ids[:init_n]:
            kk.insert(i)
        for i in ids[init_n:init_n + hold_n]:
            kk.insert(i, all_items[i])
        return kk

    def max_leaf(kk) -> int:
        return max((len(kk.members_of(d)) for d in kk.all_directions()
                    if not kk.children_of(d)), default=0)

    # ★ 跨**三个方向**查同一条不变量 —— 只有键集一个方向时，它可能只是巧合。
    #
    # 实测（36 项语料）：
    #     改前  一次建完 25/71/21 个方向 → 先建 6 维护 30 掉到 **3/11/7**
    #           最大叶容量 10/1/10 → **31/29/26**（退化成平表）
    #     修后  一次建完 25/71/21，先建 6 维护 30 仍是 25/71/21（方向数与叶容量逐项相同）
    #
    # ⇒ **判据是「叶容量」**（分辨率），**不是「方向数」**（记账）。
    #   §7.1 已经定了这个分界：会变的那个是记账，不变的那个才是形状。
    #   ⚠️ 而 `§I4` 允许插件定扇出之后，**方向数**这一列**确实会变**了 ——
    #      扇出在**首次展开时定形**（§K3：已分配的项不许挪走 ⇒ 扇出只能增不能改），
    #      而「首次展开时成员集里有几个符号」取决于初始批次
    #      ⇒ 先建 6 维护 30 时 sequence 是 20，一次建完是 16。
    #      变的只有记账：**叶容量逐项相同（10）**。
    #      所以断言收在叶容量上，方向数只**报出来**。
    #      （这条收窄是**实测逼出来的**，不是为了让改动通过 —— 见 `C10` §3。）
    # ⚠️ 但**不要求同构** —— 实测 `_shape()` 在分批维护与一次建完之间**不相等**。
    #    那是**当前实现（路径局部增量）的性质**，不是「增量」的固有代价
    #    （Godin 1995 既增量又顺序无关；见 `C8` N3）。
    #    要求同构就等于要求「维护必须重建成批建的样子」，那是另一个设计。
    #    ⇒ 这条差异现在由 `checks/divergence.py` **只报不判**地量出来。
    from ldv.plugins.reach import ReachPlugin
    from ldv.plugins.sequence import SequencePlugin
    from ldv.checks._fixtures import sequences

    cases = {
        "keyset": (lambda: KeysetPlugin(), lambda p: p.merge([])),
        "reach": (lambda: ReachPlugin(edges), lambda p: frozenset(nodes)),
        "sequence": (lambda: SequencePlugin(sequences(nodes, edges)),
                     lambda p: frozenset({()})),
    }
    fanout_note: list[str] = []
    for label, (mk_plug, mk_root) in cases.items():
        once_k = build(mk_plug(), mk_root(mk_plug()), 36, 0)
        inc_k = build(mk_plug(), mk_root(mk_plug()), 6, 30)
        fanout_note.append(f"{label} {len(once_k.all_directions())}"
                           f"→{len(inc_k.all_directions())}")
        ok(f"★ [{label}] 分批维护不改变**分辨率**：最大叶容量与一次建完相同"
           f"（改前会退化成 {once_k.stats()['最大叶容量']}→31 这类平表）",
           max_leaf(inc_k) == max_leaf(once_k),
           f"一次建完 {max_leaf(once_k)}，先建 6 维护 30 → {max_leaf(inc_k)}")
        ok(f"★ [{label}] 维护后仍然**不是平表**（方向数 ≥ 叶数，且叶容量没爆）",
           len(inc_k.all_directions()) >= len(
               [d for d in inc_k.all_directions() if not inc_k.children_of(d)]),
           f"方向 {len(inc_k.all_directions())} / 叶 "
           f"{len([d for d in inc_k.all_directions() if not inc_k.children_of(d)])}")
    # 方向数是**记账**，不是判据 —— 但必须**报出来**，否则「变了」没人知道。
    PASS.append("（读数）分批维护的方向数 一次建完→先建6维护30：" + "、".join(fanout_note))

    # 不变量：**不许留过期的「分不开」结论**。
    #
    # ⚠️ 这里**不**要求「每个叶都被试过」—— `§3` 明说展开是**按需**的，
    #    所以「≥2 个成员但还没被碰过」的叶是**合法**的。要求它必须被试过
    #    就等于要求预先物化。真正要防的是**过期结论**：拿一个成员集试过、
    #    失败了，之后成员集变了却还留着旧结论 —— 那正是「叶是终态」的形状。
    kk = build(KeysetPlugin(), KeysetPlugin().merge([]), 6, 30)
    stale = []
    for d in kk.all_directions():
        if kk.children_of(d):
            continue
        t = kk._tried.get(d.did)  # noqa: SLF001
        if t is not None and t != kk.members_of(d):
            stale.append((d.did, len(t), len(kk.members_of(d))))
    ok("★ 不许留过期的「分不开」结论（结论必须对应当前成员集）",
       not stale, f"{len(stale)} 个叶带着过期结论（旧集/现集）：{stale[:3]}")
    ok("★ 且真的有叶被「试过」了（否则上一条是空转）",
       len(kk._tried) > 0, f"_tried 里 {len(kk._tried)} 项")  # noqa: SLF001


def test_divergence() -> None:
    """★ 「增量 ≡ 全量」是**度量**，所以它的「能红」不是「判出违规」，
    而是「**必须能报出两个值**」—— 一个永远报同一个值的度量，信息量是 0。
    这正是「空转与通过长得一模一样」在**度量**上的形态。

    五条，每条都能红：

      ① 比较器自检：同路径判同构、项集不同判不同构 ⇒ 它不是常量
      ② 两侧项集必须一致 ⇒ 差异是**结构性**的，不是「项多寡」造成的（防假的不同构）
      ③ **尺度**：扇出固定为 2 的方向（A / B）必须逐项相同；
         扇出由插件定的方向（C，k 叉）**允许**在尺度上不同 —— 但要**报出来**；
         而**分辨率**那一列要换个尺子（见下）
      ④ 实测：扫描里**同构与不同构都出现过** ⇒ 度量在真语料上有区分力
      ⑤ 不同构**不进退出码**（度量与判据分开）

    ⚠️ ③ 原来断言**所有**方向「差异不在尺度」。`§I4` 允许插件定扇出之后，
       C 的**方向数**这一列**确实会变**（16 一次建完 → 21 先建 2 项）——
       扇出在**首次展开时定形**（§K3），而首次展开时组里有几个符号取决于初始批次。
       ⇒ ③ 拆成两半：**A / B 是判据**（它们的扇出恒为 2，尺度必须稳），
         **C 是读数**（报出来，不判）。这个拆分有依据，不是为了让改动通过：
         变的只是**记账**（方向数），**叶容量（分辨率）逐项相同**（见 §7.1）。

    ⚠️ ④ 判的是**三个方向合起来**，不是逐个方向 —— 实测 `reach` 在扫描范围内
       **从未同构**（连 `k = N−1` 都不同构），要求每个方向都出现 True 是错的。
       要证的是「这个**比较器**能报出两个值」，不是「每个方向都两种都出现」。

    ⚠️ ③ 的 C 分支**再改过一次**（`§10.2` 出路 (4) 落地之后），改动有**实测依据**，
       不是为了让改动通过：

           出路 (4) 之前   项**全都**往下走 ⇒ `叶容量` 就是分辨率
           出路 (4) 之后   项可以**停在内部节点** ⇒ `叶容量` **看不见**那部分代价
                           实测 k=2：叶容量报 **2**，真相是 **34 项堆在根上**

       ⇒ 分辨率换成 `最大停留数`（每项锥的末位方向上的项数；批建路径上它与
         `叶容量` **相等**，所以这个推广不改变原有的读数）。
       ⇒ 判据拆成两半，**两半都是收窄**：
            无滞留的 k ⇒ 停留数必须与同项集参照**相同**
            有滞留的 k ⇒ `最大停留数 > 最大叶容量`（**把那个洞钉住**）
       ⚠️ 顺带说明：改前 k=2 的「叶容量 10」是**假的绿** —— 那个结构里有
          **68 个成员落在覆盖之外**（`B16` 维护路径红）。⇒ 它报的分辨率
          不是「细」，是「细在错的地方」。

    ⚠️ ④ 在「哪天真的做到了同构」时**会红** —— 那是**信号不是故障**：
       它说明「增量 ≡ 全量」成立了，这条度量该**升级成 B 系列检查**（进退出码）。
    """
    from ldv.checks._fixtures import make_builder
    from ldv.checks.divergence import divergence_profile

    loaded = load()
    if loaded is None:
        PASS.append("增量 ≡ 全量（跳过：目录不在）")
        return
    nodes, edges, _ = loaded
    ids = sorted(nodes)

    all_iso: set[bool] = set()
    for which in ("keyset", "reach", "sequence"):
        prof = divergence_profile(make_builder(which, nodes, edges), nodes, ids)
        sc = prof["比较器自检"]
        rows = prof["各 k"]

        # ① 比较器不是常量
        ok(f"[{which}] 比较器自检：同路径必须判同构", sc["同路径必须同构"])
        ok(f"[{which}] 比较器自检：项集不同必须判不同构", sc["项集不同必须不同构"])

        # ② 项集一致 —— 防「假的不同构」
        bad_universe = [k for k, r in rows.items() if not r["项集一致"]]
        ok(f"[{which}] 每个 k 的两侧**项集一致**（否则比的是项多寡，不是结构）",
           not bad_universe, f"项集不一致的 k：{bad_universe}")
        # ★ 参照侧按**纳入项集**重建（§10.2 出路 (1)），所以「比项多寡」这件事
        #   已经被**构造**排除了。但「根覆盖之外」有多少项必须**报出来** ——
        #   不然「范围」会被读成「结构」。
        oos = {k: r["根覆盖之外"] for k, r in rows.items() if r["根覆盖之外"]}
        if oos:
            PASS.append(f"（读数）[{which}] §10.2 出路 (1)：根覆盖之外（不塞进结构）"
                        f"的项数 各 k = {oos}；参照侧按**同一项集**重建")

        # ③ 尺度：**扇出固定为 2 的方向必须稳**；扇出由插件定的方向只报不判。
        #   ⚠️ 比的是**同项集参照**（`r['参照']`），不是全集 —— 见 `divergence.py`
        #      开头：拿全集当参照会在「根覆盖之外」存在时把**范围**读成**结构**。
        f = prof["全量"]
        scale_ok = all(
            (r["方向数"], r["最大叶容量"]) == (r["参照"]["方向数"], r["参照"]["最大叶容量"])
            for r in rows.values())
        if which in ("keyset", "reach"):
            ok(f"[{which}] 扇出恒为 2 ⇒ 差异**不在尺度**：每个 k 的方向数与叶容量"
               f"都和**同项集参照**相同",
               scale_ok,
               f"全量 {f['方向数']}/{f['最大叶容量']}，"
               f"增量 {sorted({(r['方向数'], r['最大叶容量']) for r in rows.values()})}，"
               f"参照 {sorted({(r['参照']['方向数'], r['参照']['最大叶容量']) for r in rows.values()})}")
        else:
            # ★ 分辨率：**`叶容量` 是错的尺子**（§10.2 出路 (4) 之后）。
            #
            #   `叶容量` 默认「每一项都走到了叶」。出路 (4) 让项可以**停在内部节点**
            #   ⇒ 那部分代价 `叶容量` **看不见**：实测 k=2 时叶容量报 2，
            #     而真相是 34 项堆在**根**上（根是内部节点）。
            #   ⇒ 所以分辨率要用 `最大停留数`：`停(d) = |members(d) \ ∪members(子)|`
            #      （**从成员集现算**，不用 `Cone(x)` —— 锥是插入时记的，
            #        方向后来才劈开时不会延长，会把早就走到叶的项误记成「停在内部」）。
            #
            #   判据分两半，**都是收窄而不是放宽**：
            #     滞留 == 0 的 k   ⇒ 停留数必须与**同项集参照**相同（分辨率是不变量）
            #     滞留 > 0 的 k   ⇒ **至少有一处**确实被遮住
            #                        （`最大停留数 > 最大叶容量`）—— 把那个洞钉住
            #
            #   ⚠️ 后半用 `any` 而不是 `all`，因为「被遮住」只在**滞留量足够大**时
            #      才在**最大值**上露出来：实测 k=6/12/18 滞留 16/6/5，但那些项
            #      停在的方向成员数 ≤ 10 ⇒ 两个数仍然都是 10。
            #      用 `all` 会把「遮住程度不够大」误判成「没有遮住」。
            cap_ok = all(r["最大停留数"] == r["参照"]["最大停留数"]
                         for r in rows.values() if not r["滞留"])
            blind_ok = any(r["最大停留数"] > r["最大叶容量"]
                           for r in rows.values() if r["滞留"])
            ok(f"[{which}] 扇出由插件定 ⇒ 方向数**允许**变；"
               f"**无滞留的 k 上分辨率（停留数）不许变**，"
               f"**有滞留的 k 上 `叶容量` 至少有一处看不见那部分代价**",
               cap_ok and blind_ok,
               f"全量叶容量 {f['最大叶容量']} / 停留数 {f['最大停留数']}，"
               f"增量 (叶容量,停留数,滞留) "
               f"{sorted((r['最大叶容量'], r['最大停留数'], r['滞留']) for r in rows.values())}，"
               f"参照停留数 {sorted({r['参照']['最大停留数'] for r in rows.values()})}"
               f"｜无滞留判据 {cap_ok} / 有滞留判据 {blind_ok}")
            stay_read = {k: r["滞留"] for k, r in rows.items() if r["滞留"]}
            if stay_read:
                PASS.append(
                    f"（读数）[{which}] §10.2 出路 (4)：项**留在父方向**（不塞进结构、也不拒绝）"
                    f"—— 各 k 滞留数 {stay_read}；这些项停在**内部**节点上，"
                    f"所以 `叶容量` 那一列**低于**真实扫描量")
            PASS.append(
                f"（读数）[{which}] 扇出由插件定 ⇒ 方向数随初始批次变：全量 "
                f"{f['方向数']}，各 k "
                f"{sorted({r['方向数'] for r in rows.values()})}（**记账**，不进判据）")

        # ④a 每个方向都要有读数（否则那一行是空转）
        ok(f"[{which}] 扫描非空（每个方向都有读数）", bool(rows))
        all_iso |= {r["同构"] for r in rows.values()}

    # ④b 全局：两个值都出现过 ⇒ 比较器有区分力
    ok("扫描里同构与不同构**都出现过**（比较器不是常量）",
       all_iso == {True, False},
       f"只出现了 {all_iso} —— 度量退化成常量；"
       f"若因「已做到同构」而只剩 True，请把它升级成 B 系列检查")

    # ⑤ 度量不进退出码
    from ldv.run_checks import run_one

    rep = run_one("keyset", loaded)
    ok("不同构**不进退出码**（度量与判据分开）",
       not rep.red and any("增量 ≡ 全量" in n for n in rep.notes),
       f"红 {len(rep.red)} 条；notes 里有增量读数 = "
       f"{any('增量 ≡ 全量' in n for n in rep.notes)}")


def _fake_dir(payload):
    """给 `merge` 造一个方向壳 —— `merge` 只读 `payload`。"""
    from ldv.core.direction import ORIGIN_SPLIT, Direction

    return Direction(did="F", rank=2, payload=payload, witness=(),
                     origin=ORIGIN_SPLIT, parent=None)


#: `B17` 用的**合环**小图：`a ↔ b` 互相可达（同一个 SCC），`c → a`，`d` 孤立。
#: 真实语料是 **DAG**（36 项 / 47 边，无环）⇒ 等价类全是单点 ⇒ `B17` 在那里退化。
#: 要证明它不是空转，就得自己造一个有环的输入。
_CYCLE_EDGES = {
    "a": frozenset({"b"}),
    "b": frozenset({"a"}),
    "c": frozenset({"a"}),
    "d": frozenset(),
}


def _cycle_graph():
    from ldv.corpus.loader import Node

    nodes = {k: Node(id=k, fields={}, keys=frozenset({k})) for k in _CYCLE_EDGES}
    return nodes, _CYCLE_EDGES


def test_equivalence() -> None:
    """★ `B17`：**叶 = 不可分等价类**（`C8` §7 第 2 步的产物）。

    它同时是「`§K2` 的第三个选项值不值得做」的答案 —— 答案是**不值得**，
    因为判空点上**没有区分信息可搬**（见 `checks/equivalence.py` 的模块 docstring）。

    六条，每条都能红：

      ① 基线：`keyset` / `sequence` 上叶数 = 等价类数、叶容量 = 最大等价类
      ② `reach` 报「**跳过**」而不是「过」—— 等价类全是单点时两条断言恒真
      ③ oracle **独立**：`reach` 的等价类（SCC）≠ 汇点签名类 ⇒ 不是插件谓词的复述
      ④ 注入「能分也判分不开」⇒ 红；**且叶容量这个数不变** ⇒ 度量对局部退化不敏感
      ⑤ **非空转**：合环图上它**红** —— `§K2` 第二半的缺口（见 §10 留白）
      ⑥ 读数：三个方向的「可搬的判空点」都是 0

    ⚠️ ⑤ 断言的是**它现在红**。这不是「留一个已知红」，是**把缺口钉住**：
       谁把 `reach` 的 `劈开` 改成不拆 SCC，这条就会翻 —— 那时该做的是
       **更新 §10 的留白**，而不是删掉这条断言。

    ★ **⑤ 用的合环图是人工造的；缺口在公开数据集上也露了** ——
      OpenAlex 引用图切片（281 项 / 1008 边）有 6 个 ≥2 的强连通分量
      ⇒ `B17`(reach) 的读数「被拆开的等价类」正好 6 个（`ldv/tests/test_intake.py`）。
      人工小图可以被人说成「构造出来的边角情形」，公开语料不能。

    ⚠️ **2026-10-07：(b) 那一半在 `reach` 上从判据降为度量**，所以 ⑤ 断言的东西
      也跟着换了位置：

        改前  合环图上 `B17` **红**（判据非空转）
        改后  合环图上 `B17` **过**（判据**有意**不判这条），
              而**读数**「被拆开的等价类」> 0（**度量**非空转）

      ⇒ 「判据不再红」与「现象消失」是两件事。判据是**有意**收窄的
        （`reach` 的 `split` 比语义更细是 §K8 允许的假阳），现象仍在、由读数承担。
        这一条对照的**作用没变**：它仍然在证明「这条判据/读数**能**动」，
        只是从判据那一列移到了度量那一列。
      ⇒ 也正因为 (b) 在 reach 上不判了，`test_intake.py` 的判据 ② 才有机会
        从「基线守卫」回到更简单的形态 —— 但**先不动它**（那是另一处裁定）。
    """
    from ldv.checks._fixtures import equiv_classes
    from ldv.checks.equivalence import absorption_profile, b17_leaf_is_equivalence_class
    from ldv.checks._framework import Report

    loaded = load()
    if loaded is None:
        PASS.append("叶 = 不可分等价类（跳过：目录不在）")
        return
    nodes, edges, _ = loaded

    # ── ① / ② / ⑥ 三个方向各跑一遍 ──────────────────────────────────────
    skipped: list[str] = []
    for which in ("keyset", "reach", "sequence"):
        kernel, _ = {"keyset": build_keyset,
                     "reach": lambda n: build_reach(n, edges),
                     "sequence": lambda n: build_sequence(n, edges)}[which](nodes)
        cls = equiv_classes(which, nodes, edges)
        prof = absorption_profile(kernel, cls)
        rep = Report(plugin=which, expects=("B17",))
        b17_leaf_is_equivalence_class(kernel, cls, rep, which)
        a = rep.assertions[0]

        if a.result is Tri.UNEXPANDED:
            skipped.append(which)
            ok(f"[{which}] 等价类全是单点 ⇒ 报「**跳过**」而不是「过」",
               "跳过" in a.detail or "退化" in a.detail, a.detail)
        else:
            ok(f"[{which}] B17 过：叶 = 不可分等价类", a.result is Tri.YES, a.detail)
            ok(f"[{which}] 叶数 == 等价类数",
               prof["叶数"] == prof["等价类数"],
               f"叶 {prof['叶数']} vs 类 {prof['等价类数']}")
            ok(f"[{which}] 最大叶容量 == 最大等价类（分辨率**已到下界**）",
               prof["最大叶容量"] == prof["最大等价类"],
               f"{prof['最大叶容量']} vs {prof['最大等价类']}")
        ok(f"[{which}] 读数：可搬的判空点 = 0（没有区分信息可搬）",
           prof["可搬的判空点"] == 0,
           f"{prof['可搬的判空点']} 个 —— 说明插件在能分开的组上判了「分不开」")

    ok("退化方向确实出现过（否则上一条分支是空转）",
       skipped == ["reach"], f"报跳过的方向：{skipped}")

    # ── ③ oracle 独立性：等价类 ≠ 插件的「汇点签名」 ─────────────────────
    from ldv.plugins.reach import ReachPlugin

    plug = ReachPlugin(edges)
    sig_classes = {plug.signature(i) for i in nodes}
    scc_classes = {equiv_classes("reach", nodes, edges)[i] for i in nodes}
    ok("oracle 独立：reach 的等价类（SCC）**不是**「汇点签名」的复述",
       len(scc_classes) != len(sig_classes),
       f"SCC {len(scc_classes)} 类 vs 汇点签名 {len(sig_classes)} 类")

    # ── ④ 注入：能分也判分不开 ───────────────────────────────────────────
    from ldv.plugins.keyset import KeysetPlugin

    class _GiveUpEarly(KeysetPlugin):
        LIMIT = 4

        def split(self, parent, items):  # noqa: ANN001, ANN201
            if len(items) <= self.LIMIT:
                return None                 # 谎报：能分也**声明**分不开
            return super().split(parent, items)

    def build_with(plug_cls):  # noqa: ANN001, ANN202
        p = plug_cls()
        k = Kernel(p, {nid: nodes[nid].as_item() for nid in nodes})
        k.build(p.merge([]))
        for nid in sorted(nodes):
            k.insert(nid)
        return k, p

    cls_k = equiv_classes("keyset", nodes, edges)
    base_k, _ = build_with(KeysetPlugin)
    bad_k, _ = build_with(_GiveUpEarly)
    rep_b, rep_i = Report(expects=("B17",)), Report(expects=("B17",))
    b17_leaf_is_equivalence_class(base_k, cls_k, rep_b, "keyset")
    b17_leaf_is_equivalence_class(bad_k, cls_k, rep_i, "keyset")
    ok("注入「能分也判分不开」⇒ B17 红",
       rep_b.assertions[0].result is Tri.YES and rep_i.assertions[0].result is Tri.NO,
       f"基线 {rep_b.assertions[0].result} / 注入 {rep_i.assertions[0].result}")

    cap_b, cap_i = base_k.stats()["最大叶容量"], bad_k.stats()["最大叶容量"]
    ok("★ 而「最大叶容量」这个数**不变** ⇒ 度量对局部退化不敏感，所以必须是判据",
       cap_b == cap_i,
       f"基线 {cap_b} / 注入 {cap_i}（若哪天变了，这条断言要重新解释）")

    # ── ⑤ 非空转：合环图上它必须红 ──────────────────────────────────────
    cyc_nodes, cyc_edges = _cycle_graph()
    from ldv.plugins.reach import ReachPlugin as _RP

    cyc_plug = _RP(cyc_edges)
    ck = Kernel(cyc_plug, {k: cyc_nodes[k].as_item() for k in cyc_nodes})
    ck.build(frozenset(cyc_nodes))
    for nid in sorted(cyc_nodes):
        ck.insert(nid)
    cyc_cls = equiv_classes("reach", cyc_nodes, cyc_edges)
    rep_c = Report(expects=("B17",))
    b17_leaf_is_equivalence_class(ck, cyc_cls, rep_c, "reach")
    ok("★ 非空转：合环图上 B17 **过** —— (b) 在 `reach` 上**已降为度量**，不判",
       rep_c.assertions[0].result is Tri.YES, rep_c.assertions[0].detail)
    cyc_torn = absorption_profile(ck, cyc_cls)["被拆开的等价类"]
    ok("★★ 而**度量**在合环图上非空转：被拆开的等价类 > 0"
       "（判据不再红 ≠ 现象消失；现象改由读数承担）",
       cyc_torn > 0,
       f"被拆开的等价类 {cyc_torn} 个 —— 若为 0，说明这条读数也是空转")
    ok("★ 且那一层是**重复覆盖**（两个子方向覆盖完全相同 ⇒ §K2 第二半的缺口）",
       ck.plugin.reachable(ck.direction("D3").payload)
       == ck.plugin.reachable(ck.direction("D4").payload),
       f"D3 {sorted(ck.direction('D3').payload)} vs D4 {sorted(ck.direction('D4').payload)}")


def test_rebuild() -> None:
    """★ 规范重建 / 阈值重建：**买得到「增量 ≡ 全量」，但代价是 §K3**（`C8` §7 第 3 步）。

    结论是**不做**。但它必须**可查**，不能只是一段说辞 —— 所以这里断言七件事：

      ① 规范不动点确实买到「同构全量」：8/8 个 k，三个方向都是
      ② 而且 **k 指纹数 == 1** ⇒ 结果**不再依赖初始批次大小**（这是「买到」的实义）
      ③ 阈值重建（Naor–Teague，摊还 O(1)）在 **A / B** 上**买不到**：
         同构 < 8/8，且 k 指纹数 > 1 ⇒ 差异发生在**两次重建之间**
      ④ **但它在 C 上买到了**（8/8 同构、指纹 1）—— 这条**正对照**很重要：
         它证明 ③ 不是「这个方法不行」，是「**扇出固定为 2 的方向**不行」
      ⑤ 代价：规范不动点 O(n) 每次插入（`n−1` 次 / 工作量 `2+3+…+n`）；
         阈值重建压到 2 的幂的个数
      ⑥ **§K3 的代价（本步的关键）**：`B8` 在重建内核上**红**、在路径局部内核上**绿**
         —— 断言的是「**这条判据抓得到它**」，不是「留一个已知红」
      ⑦ 比较器自检全过 —— 否则「同构」这个读数不可信（同构要靠形状比对，比对器得有区分力）

    ⚠️ ④ 是 `§I4` 改成 k 叉之后**新出现的**结论。它把「不做」的理由收窄了一句：
       不是「阈值重建做不到」，是「**它换来的东西不值得**」——
       §K3 是**接口级**不变量，「结构可复现」是**实现**性质（§10.1）。
       对 C 它买得到，我们**仍然不做**，因为 §K3 更贵。

    ⚠️ ⑦ 是这一步的**地基**：若比对器无区分力，「8/8 同构」与「0/8 同构」都会报成一样。
    """
    from ldv.checks._fixtures import make_keyset_root
    from ldv.checks._framework import Report
    from ldv.checks.rebuild import make_rebuild_root, rebuild_profile
    from ldv.checks.structure import b8_invalidation_local

    loaded = load()
    if loaded is None:
        PASS.append("规范重建（跳过：目录不在）")
        return
    nodes, edges, _ = loaded
    ids = sorted(nodes)
    n = len(ids)

    #: 阈值重建的预期：**扇出恒为 2 的方向买不到，扇出由插件定的方向买得到**。
    #: 这个分界有依据 —— 扇出恒为 2 时「两次重建之间」添的项会造出新的中间层，
    #: 而那些层在「一次建完」里不存在；扇出由插件定时，只要重建时符号齐了，后面添的项
    #: 只会落进**已有**的子方向，形状就与一次建完一致。
    THRESHOLD_BUYS = {"keyset": False, "reach": False, "sequence": True}

    for which in ("keyset", "reach", "sequence"):
        prof = rebuild_profile(which, nodes, edges)
        canon, thresh = prof["规范不动点"], prof["阈值重建"]
        ks = canon["扫描的 k"]
        buys = THRESHOLD_BUYS[which]

        # ── ① / ② 规范不动点买到了 ──────────────────────────────────────
        ok(f"[{which}] 规范不动点：8/8 个 k 都同构全量",
           canon["同构的 k 数"] == ks,
           f"{canon['同构的 k 数']}/{ks}（最早同构 {canon['最早同构的 k']}）")
        ok(f"[{which}] 且 k 指纹数 == 1 ⇒ 结果不依赖初始批次大小（这才是「买到」的实义）",
           canon["k 指纹数"] == 1 and canon["k 之间互相同构"],
           f"k 指纹 {canon['k 指纹数']} 种")

        # ── ③ / ④ 阈值重建：按**扇出是否固定**分成两半 ──────────────────
        if buys:
            ok(f"[{which}] ★ 阈值重建**买到了**（扇出由插件定 ⇒ 重建时符号齐了就定形）",
               thresh["同构的 k 数"] == ks and thresh["k 指纹数"] == 1,
               f"{thresh['同构的 k 数']}/{ks}、指纹 {thresh['k 指纹数']} 种")
            PASS.append(f"（读数）[{which}] 阈值重建最早同构的 k = {thresh['最早同构的 k']}"
                        f"（扇出齐全所需的初始批次大小）")
        else:
            ok(f"[{which}] 阈值重建**买不到**：同构 < 全部（扇出恒为 2 ⇒ 两次重建之间造出新层）",
               thresh["同构的 k 数"] < ks,
               f"{thresh['同构的 k 数']}/{ks}（最早同构 {thresh['最早同构的 k']}）")
            ok(f"[{which}] 且阈值重建的 k 指纹数 > 1 ⇒ 差异发生在**两次重建之间**",
               thresh["k 指纹数"] > 1,
               f"k 指纹 {thresh['k 指纹数']} 种")

        # ── ⑤ 代价 ─────────────────────────────────────────────────────
        ok(f"[{which}] 代价：规范重建 O(n) 每次插入（{n - 1} 次 / 工作量 {sum(range(2, n + 1))}）",
           canon["重建次数"] == n - 1 and canon["重建工作量"] == sum(range(2, n + 1)),
           f"重建 {canon['重建次数']} 次 / 工作量 {canon['重建工作量']}")
        ok(f"[{which}] 阈值重建把次数压到 2 的幂的个数（摊还 O(1)）",
           thresh["重建次数"] < canon["重建次数"] and thresh["重建工作量"] < canon["重建工作量"],
           f"{thresh['重建次数']} 次 / {thresh['重建工作量']} vs {canon['重建次数']} 次 / {canon['重建工作量']}")

        # ── ⑦ 地基：比对器有区分力 ─────────────────────────────────────
        ok(f"[{which}] 比较器自检全过（否则「同构」这个读数不可信）",
           canon["比较器自检"] and thresh["比较器自检"], "有自检未过")

        # ── ⑥ §K3 的代价：B8 抓得到它 ──────────────────────────────────
        rep_path, rep_canon = Report(expects=("B8",)), Report(expects=("B8",))
        b8_invalidation_local(make_keyset_root(nodes), ids, rep_path)
        b8_invalidation_local(make_rebuild_root(which, nodes, edges), ids, rep_canon)
        ok(f"[{which}] ★ §K3 的代价：B8 在路径局部内核上**绿**、在规范重建内核上**红**",
           rep_path.assertions[0].result is Tri.YES
           and rep_canon.assertions[0].result is Tri.NO,
           f"路径局部 {rep_path.assertions[0].result} / 规范重建 {rep_canon.assertions[0].result}")
        ok(f"[{which}] 且它红是**因为越界**，不是因为「跳过」（红的理由要正当）",
           "越界" in rep_canon.assertions[0].detail,
           rep_canon.assertions[0].detail[:80])

    # ── 与 id 无关的那条读数：基线 0.00 vs 重建 > 0（度量，不进退出码）────
    prof_k = rebuild_profile("keyset", nodes, edges)["失效范围"]
    base = prof_k["基线（路径局部增量）"]["平均失效范围比"]
    reb = prof_k["规范不动点（每次重建）"]["平均失效范围比"]
    ok("★ 与 id 无关的读数有区分力：基线 0.00 < 规范重建（否则 B8 红只是比对的产物）",
       base == 0.0 and reb > 0.0, f"基线 {base} vs 重建 {reb}")

    # ── 非空转：阈值重建**确实**会重建（次数 > 1），不是「什么都没干」──────
    ok("阈值重建确实重建过（次数 > 1）—— 上面对比不是拿「空实现」在比",
       rebuild_profile("keyset", nodes, edges)["阈值重建"]["重建次数"] > 1,
       "阈值重建次数 <= 1")


def test_out_of_scope() -> None:
    """★ §10.2 出路 (1)「根记账」—— 把「最外层意图之外」从静默错位变成一条**可数的账**。

    五条，每条都能红：

      ① 批建路径**零误伤**（三个方向的根都覆盖全部项 ⇒ 一个都不许被拒）
      ② 维护路径**确实拒了**（`reach` 的根只声明了前 6 项当锚点 ⇒ 走不到的那些在外面）
      ③ 记账守恒：`认识 = 纳入 + 根覆盖之外`，且账本事件数与它逐项相符
      ④ 被拒的项**不在任何方向的成员里**（「不塞进结构」是字面执行，不是打折）
      ⑤ ★ **方向不许偏**：`命中(根,{x})` 返回「未展开」时**必须照常插入** ——
         「不知道」不许被当成「不在」（§K8 的同一条纪律：往假阴偏才是错的）

    ⚠️ ⑤ 是这条判据的**已知答案对照组**：只有「否」才是否证。
       少了它，一个「凡非『是』就拒」的实现也能让 ①–④ 全绿 ——
       而那正是把「不知道」折成「没有」，是 §K8 明令禁止的那个方向。
    """
    from ldv.core.direction import EVENT_OUT_OF_SCOPE
    from ldv.core.tri import Tri as _Tri
    from ldv.plugins.reach import ReachPlugin
    from ldv.plugins.sequence import SequencePlugin
    from ldv.checks._fixtures import items as make_items, sequences

    loaded = load()
    if loaded is None:
        PASS.append("根记账（跳过：目录不在）")
        return
    nodes, edges, _ = loaded
    all_items = make_items(nodes)
    ids = sorted(nodes)

    # ① 批建路径零误伤
    for label, mk, root in (
        ("keyset", lambda: KeysetPlugin(), lambda p: p.merge([])),
        ("reach", lambda: ReachPlugin(edges), lambda p: frozenset(nodes)),
        ("sequence", lambda: SequencePlugin(sequences(nodes, edges)), lambda p: frozenset({()})),
    ):
        plug = mk()
        k = Kernel(plug, dict(all_items))
        k.build(root(plug))
        for i in ids:
            k.insert(i)
        ok(f"★ [{label}] 批建路径**零误伤**：根的声明覆盖全部项 ⇒ 一个都不被拒",
           k.stats()["根覆盖之外"] == 0, f"实际 {k.stats()['根覆盖之外']} 项被拒")

    # ② / ③ / ④ 维护路径
    plug = ReachPlugin(edges)
    k = Kernel(plug, {i: all_items[i] for i in ids[:6]})
    k.build(frozenset(ids[:6]))
    for i in ids[:6]:
        k.insert(i)
    for i in ids[6:]:
        k.insert(i, all_items[i])
    st = k.stats()
    ok("★ [reach] 维护路径确实拒了（根只声明了前 6 项当锚点）",
       st["根覆盖之外"] > 0, f"实际 {st}")
    ok("★ 记账守恒：认识 = 纳入 + 根覆盖之外",
       st["认识"] == st["纳入"] + st["根覆盖之外"],
       f"认识 {st['认识']} ≠ 纳入 {st['纳入']} + 之外 {st['根覆盖之外']}")
    ev = [e for e in k.ledger if e.kind == EVENT_OUT_OF_SCOPE]
    ok("★ 账本事件数与 `stats()['根覆盖之外']` 逐项相符（数在账上，不是算出来的）",
       len(ev) == st["根覆盖之外"], f"事件 {len(ev)} vs 计数 {st['根覆盖之外']}")
    ok("★ 被拒的项**不在任何方向的成员里**（「不塞进结构」是字面执行）",
       not (k.placed & (set(k.items) - k.placed)),
       f"被拒 {sorted(set(k.items) - k.placed)[:3]}")
    ok("★ 且被拒的项**真的不在**结构里（否则上一条恒真、是空转）",
       set(k.items) - k.placed, "没有任何项被拒 —— 上一条就没在查东西")

    # ⑤ 方向不许偏：`命中` 返回「未展开」时必须照常插入
    class _RootSaysUnknown(ReachPlugin):
        """对照组：根对任何查询都说「未展开」。"""

        def hit(self, d, query):  # noqa: ANN001, ANN201
            if d.did == self._root_did:
                return _Tri.UNEXPANDED
            return super().hit(d, query)

    plug2 = _RootSaysUnknown(edges)
    k2 = Kernel(plug2, {i: all_items[i] for i in ids[:6]})
    k2.build(frozenset(ids[:6]))
    plug2._root_did = k2.root.did  # noqa: SLF001
    for i in ids[:6]:
        k2.insert(i)
    for i in ids[6:]:
        k2.insert(i, all_items[i])
    ok("★ [对照组] 根说「未展开」时**照常插入** —— 「不知道」不许被当成「不在」",
       k2.stats()["根覆盖之外"] == 0, f"实际 {k2.stats()}")


def _stay_unproven(k: Kernel, plugin: Any) -> list[str]:
    """② 的**谓词**：每条 `stayed` 事件上，**每个子方向都证明不收它**。

    返回违规列表（空 = 过）。**直接问插件**，不借内核的 `_refuses` ——
    借了就是「拿内核自己的判断验内核自己的判断」（`false-green` 形状 3 共享盲点）。

    抽成纯函数是为了能拿**已知错**的输入自检它（见调用处）。
    """
    bad: list[str] = []
    for e in k.ledger:
        if e.kind != EVENT_STAYED:
            continue
        d = k.direction(e.did)
        x = e.detail.get("item", "")
        q = Query(ideal=frozenset({x}))
        if any(plugin.hit(c, q) is not Tri.NO for c in k.children_of(d)):
            bad.append(f"{x}@{d.did}")
    return bad


def _stay_leaks(k: Kernel) -> int:
    """③ 后半的**谓词**：滞留项溜进子方向成员的次数（应为 0）。"""
    return sum(1 for d in k.all_directions() for x in k.stayed_of(d)
               for c in k.children_of(d) if x in k.members_of(c))


def _stay_outside_parent(k: Kernel, accessor: Any) -> list[str]:
    """③ 前半的**谓词**：滞留项**不在**父成员里的方向（应为空）。"""
    return [d.did for d in k.all_directions()
            if accessor(d) and not accessor(d) <= k.members_of(d)]


def test_stay_at_parent() -> None:
    """★ §10.2 出路 (4)「项留在父方向」—— 与出路 (1) 是**两条不同的代价**。

        出路 (1) 根记账     项按证明落在根覆盖之外 ⇒ **不塞进结构** ⇒ 代价在**范围**
        出路 (4) 留在父方向 每个子方向都证明不收它   ⇒ **留在父这一层** ⇒ 代价在**划分**

    为什么不能拒绝（那是 (3) 的读法）：拒绝会让项从**成员集**里消失，而 `B1` 的
    ground truth 正是「成员 ∩ 查询」—— 于是「健全性变绿」会**部分来自数据变少**。
    留在父方向则：项仍可检索（父的 `命中` 对它**不是「否」**）、`B1` 的答案不动，
    代价只落在划分上（`B4` 收窄成「不重，且**漏的恰好是账上那些**」）。

    每条都能红，而且**判据自己也要有区分力**（拿已知错的输入喂进谓词）：

      ① 非空转：这份夹具上**确实有滞留**（否则下面全在查空气）
      ② 字面执行：每条 `stayed` 事件所挂的方向上，**每个子方向都证明不收它**；
         且事件数 == `stats()['滞留']`（数在账上，不是算出来的）
      ③ 项**真的留在父的成员里**（留在 ≠ 拒绝），且**没有**溜进任何子方向的成员里
      ④ 代价守恒：滞留**不动范围** —— `认识 = 纳入 + 根覆盖之外` 照旧，
         且滞留**不减** `纳入`（「留在父方向」不是「被拒」的委婉说法）
      ⑤ 两条恒等式（`停` **从成员集现算**）：`停在叶上 + 停在内部 == 纳入`、
         `停在内部 == 滞留`
      ⑥ ★ **已知答案对照组**：让**非根**方向一律说「未展开」⇒ 滞留**必须为 0**。
         「不知道」不许被当成「不在」（与 §K8 同一条纪律）——
         一个「凡非『是』就拒」的实现能让 ①–⑤ 全绿，只有这条会红（实测：它**只**红这条）。
      ⑦ `叶容量` 的洞：项停在**内部**节点时 `最大停留数 > 最大叶容量`；
         而**批建**路径上两者相等 ⇒ ⑦ 那条不是恒真（`最大停留数` 是推广，不是另一个数）

    ★ ②/③ 的自检：伪造一条**已知不该有**的滞留事件（挂在一个子方向会说「是」的项上）
      ⇒ 三个谓词**必须**报出来。少了这一步，「谓词恒返回空」也能让 ②③ 全绿 ——
      那是「空转与通过长得一模一样」。
    """
    from ldv.checks._fixtures import build_incremental, make_builder, sequences as make_seqs
    from ldv.core.direction import EVENT_STAYED
    from ldv.plugins.sequence import SequencePlugin

    loaded = load()
    if loaded is None:
        PASS.append("留在父方向（跳过：目录不在）")
        return
    nodes, edges, _ = loaded
    ids = sorted(nodes)

    def _build(init: list[str]) -> Kernel:
        return build_incremental(make_builder("sequence", nodes, edges), nodes, init, ids[2:])

    # 这份夹具（sequence，先建 2 维护 34）实测滞留 32 —— 见 §10.2 出路 (4)。
    k = _build(ids[:2])
    st = k.stats()

    # ① 非空转
    ok("★ [留在父方向] 非空转：这份夹具上确实有滞留（否则下面全在查空气）",
       st["滞留"] > 0, f"滞留 {st['滞留']} —— 下面几条都没在查东西")

    # ② 字面执行：每个子方向都**证明**不收它（用插件直接问）
    ok("★ 每条 `stayed` 事件上，**每个子方向都证明不收它**（只有「否」算证明）",
       not _stay_unproven(k, k.plugin), f"有子方向没说「否」：{_stay_unproven(k, k.plugin)[:3]}")
    ev = [e for e in k.ledger if e.kind == EVENT_STAYED]
    ok("★ 且事件数 == `stats()['滞留']`（数在账上，不是算出来的）",
       len(ev) == st["滞留"], f"事件 {len(ev)} vs 计数 {st['滞留']}")

    # ③ 留在父的成员里；不溜进子方向
    ok("★ 滞留的项**真的留在父的成员里**（留在 ≠ 拒绝）",
       not _stay_outside_parent(k, k.stayed_of),
       f"不在父成员里的方向：{_stay_outside_parent(k, k.stayed_of)}")
    ok("★ 且**没有**溜进任何子方向的成员里（「每个子方向都证明不收」是字面执行）",
       _stay_leaks(k) == 0, f"泄漏 {_stay_leaks(k)} 处")

    # ── ★ 自检：三个谓词对**已知错的**输入必须有区分力 ──────────────────
    # `ids[0]` 落在子方向里（子方向对它说「是」）⇒ 给它记一条滞留就是**错的**。
    k_bad = _build(ids[:2])
    k_bad.ledger.append(EVENT_STAYED, k_bad.root.did, item=ids[0], reason="自检用伪造")
    q0 = Query(ideal=frozenset({ids[0]}))
    ok("★ [自检] 伪造的那条确实是错的（子方向对 `ids[0]` 说「是」，不是「否」）",
       any(k_bad.plugin.hit(c, q0) is Tri.YES for c in k_bad.children_of(k_bad.root)),
       "伪造目标选错了 —— 下面两条自检就查不到东西")
    ok("★ [自检] ② 的谓词有区分力：喂**已知不该有**的滞留事件必须报违规",
       _stay_unproven(k_bad, k_bad.plugin) != [], "谓词恒返回空 —— ② 是空转")
    ok("★ [自检] 泄漏谓词有区分力：同一份伪造输入必须数出泄漏",
       _stay_leaks(k_bad) > 0, "泄漏谓词恒返回 0 —— ③ 后半是空转")
    ok("★ [自检] 「滞留 ⊂ 父成员」谓词有区分力：把事件读成挂在**父**上的那些必须报违规",
       _stay_outside_parent(k_bad, lambda d: k_bad.stayed_of(k_bad.direction(d.parent))
                            if d.parent else frozenset()) != [],
       "谓词恒返回空 —— ③ 前半是空转")

    # ④ 代价守恒：滞留在**划分**上，不动**范围**
    ok("★ 滞留不动范围：`认识 = 纳入 + 根覆盖之外` 照旧成立",
       st["认识"] == st["纳入"] + st["根覆盖之外"], f"实际 {st['认识']} vs {st}")
    ok("★ 滞留**不减** `纳入`（「留在父方向」不是「被拒」的委婉说法）",
       st["纳入"] == st["认识"] and st["滞留"] > 0,
       f"纳入 {st['纳入']} / 认识 {st['认识']} / 滞留 {st['滞留']}")

    # ⑤ 两条恒等式（`停` 从成员集现算）
    ok("★ 恒等式：`停在叶上 + 停在内部 == 纳入`（`停` 从成员集现算）",
       st["停在叶上"] + st["停在内部"] == st["纳入"],
       f"{st['停在叶上']} + {st['停在内部']} ≠ {st['纳入']}")
    ok("★ 恒等式：`停在内部 == 滞留（账上）`（两边互相独立地算）",
       st["停在内部"] == st["滞留"], f"{st['停在内部']} ≠ {st['滞留']}")

    # ⑦ `叶容量` 的洞 + 「批建路径上两把尺子相等」的对照
    ok("★ `叶容量` 的洞：项停在**内部**节点时 `最大停留数 > 最大叶容量`",
       st["最大停留数"] > st["最大叶容量"],
       f"最大停留数 {st['最大停留数']} vs 最大叶容量 {st['最大叶容量']}")
    from ldv.checks._fixtures import build_keyset, build_reach, build_sequence
    batch = {"keyset": lambda: build_keyset(nodes), "reach": lambda: build_reach(nodes, edges),
             "sequence": lambda: build_sequence(nodes, edges)}
    same, detail = True, []
    for which, mk in batch.items():
        b = mk()[0].stats()
        same = same and b["最大叶容量"] == b["最大停留数"] and b["滞留"] == 0
        detail.append(f"{which}:{b['最大叶容量']}/{b['最大停留数']}/滞留{b['滞留']}")
    ok("★ 且**批建**路径上 `最大叶容量 == 最大停留数`、滞留 0（三个方向）"
       " —— ⑦ 那条 `>` 不是恒真，`最大停留数` 是**推广**不是另一个数",
       same, " / ".join(detail))

    # ⑧ ★ `叶容量` 会掉到「分辨率下界」**以下** —— 而叶**仍然**各自是单个等价类。
    #    这条钉住的是**尺子的量程**，不是结构：§7.2 的等式 `叶容量 = 最大等价类`
    #    前提是 `滞留 == 0`（每一项都走到了叶）。少了这个前提，
    #    `叶容量` 会报出一个**比下界还小**的数 —— 它不再能当「分辨率下界」用。
    from ldv.checks._fixtures import build_keyset as _bk, build_sequence as _bsq
    from ldv.checks._fixtures import equiv_classes as _eq

    classes = _eq("sequence", nodes, edges)
    max_class = max(len(v) for v in classes.values())
    leaves = [d for d in k.all_directions() if not k.children_of(d)]
    per_leaf = [len({classes[x] for x in k.members_of(d) if x in classes}) for d in leaves]
    ok("★ [量程] 滞留 > 0 时 `最大叶容量` 掉到**最大等价类以下**（这里 %d < %d）"
       " —— 它不是分辨率下界了" % (st["最大叶容量"], max_class),
       st["最大叶容量"] < max_class, f"最大叶容量 {st['最大叶容量']} vs 最大等价类 {max_class}")
    ok("★ [量程] 而**每个叶仍然各自是单个等价类** —— 掉下去的不是分辨率，是那把尺子",
       bool(leaves) and all(n == 1 for n in per_leaf), f"各叶的等价类数 {per_leaf}")
    ok("★ [量程] 下界改由 `最大停留数` 承担：`最大停留数 >= 最大等价类`",
       st["最大停留数"] >= max_class, f"{st['最大停留数']} < {max_class}")
    batch = _bsq(nodes, edges)[0].stats()
    ok("★ [量程] 对照：**批建**路径上 `滞留 == 0` ⇒ 等式 `叶容量 == 最大等价类` 成立"
       "（§7.2 的等式前提就是这一条）",
       batch["滞留"] == 0 and batch["最大叶容量"] == max_class,
       f"批建滞留 {batch['滞留']}、叶容量 {batch['最大叶容量']} vs 最大等价类 {max_class}")

    # ⑥ 已知答案对照组：非根方向一律说「未展开」⇒ 滞留必须为 0
    class _KidsSayUnknown(SequencePlugin):
        """对照组：**非根**方向对任何查询都说「未展开」。"""

        def hit(self, d, query):  # noqa: ANN001, ANN201
            if d.did != self._root_did:
                return Tri.UNEXPANDED
            return super().hit(d, query)

    plug2 = _KidsSayUnknown(make_seqs(nodes, edges))
    k2 = Kernel(plug2, {i: nodes[i].as_item() for i in ids[:2]})
    k2.build(frozenset({()}))
    plug2._root_did = k2.root.did  # noqa: SLF001
    for i in ids[:2]:
        k2.insert(i)
    for i in ids[2:]:
        k2.insert(i, nodes[i].as_item())
    st2 = k2.stats()
    # 先验对照组**真的生效了**（否则下面那条是空转：override 没打上，一切照旧）
    probe = Query(ideal=frozenset({ids[10]}))
    ok("★ [对照组] 非根方向**确实**在说「未展开」（override 真的生效）",
       all(plug2.hit(c, probe) is Tri.UNEXPANDED for c in k2.children_of(k2.root)),
       "还有子方向没说「未展开」—— 对照组没打上")
    ok("★ [对照组] 子方向说「未展开」时**滞留必须为 0** —— 「不知道」不许被当成「不在」",
       st2["滞留"] == 0, f"实际滞留 {st2['滞留']}")
    ok("★ [对照组] 且项**没有消失**：`纳入 == 认识`、`根覆盖之外 == 0`",
       st2["纳入"] == st2["认识"] and st2["根覆盖之外"] == 0, f"实际 {st2}")
    ok("★ [对照组] 且它**确实换了行为**（方向数不同）—— 不是一个「照旧」的假对照组",
       st2["方向"] != st["方向"], f"对照组方向数 {st2['方向']} == 真实组 {st['方向']}")


def test_cover_leak_baseline() -> None:
    """★ `B18` 的 baseline 守卫 —— 四条规则 + **换语料必须报「跳过」**。

    ## 为什么守卫要单独测，而不是混在「跑一遍 B18」里

    普通判据的失效模式是**永远绿**；守卫的失效模式**不一样** —— 它会**红**，
    红得理直气壮，而那句话是**假的**：

    > `B18` 的基线是**语料相关**的读数（`sequence` 维护路径 16 漏是在 36 项上量的）。
    > 换一份语料跑，`183 > 16` 会被报成**「新增覆盖不漏」** ——
    > 而真相是**「baseline 是别的语料的」**。两者在输出里长得一模一样。

    实测踩到过（`openalex-small` 281 项），所以才有了语料指纹。
    **「跑一遍 B18 是绿的」查不出这个** —— 在真语料上它本来就是绿的。

    ## 逐条钉住（每条都能红）

        ① 基线文件在、指纹 == 现算的、条目非空 —— **非空转**前提
        ② 观测 > baseline          ⇒ 红（新增违规）
        ③ baseline > 0 且 观测 == 0 ⇒ 红（条目失效 —— 不许当永久豁免）
        ④ 语料指纹不符             ⇒ **跳过**（未展开），**不是**红也不是绿
        ⑤ baseline 里没记指纹      ⇒ **跳过**（判不了 ≠ 没问题），且理由与 ④ 分得开
        ⑥ **基线文件整个不见了**   ⇒ **跳过**（这条是 ⑤ 的实测形态，见下）
        ⑦ 0 < 观测 < baseline      ⇒ 绿 + 报「可以收紧」
        ⑧ `corpus` 漏传            ⇒ 当场 `TypeError`（防御不许静默消失）
        ⑨ 真语料上这条守卫**确实在判**，且**冻结的数 == 现算的数**

    ## ★ ⑥ 是实测出来的，而且**两条臂都得跑**（`outputs/_measure_b18_missing_baseline.py`）

    「基线文件不见了」这件事，旧代码（HEAD 逐字）报的是**两种**东西，
    取决于语料有没有既存违规：

        臂 A  `keyset`/`reach`（既存违规 0 处）  观测全 0、基线默认值也全 0 ⇒ **绿**
        臂 B  加上 `sequence|maintenance`（既存 16 漏）              ⇒ **红**
              理由写的是「新增覆盖不漏：16 漏（baseline 0）」—— **那是假的**

    第一版测量脚本只跑了臂 B，于是印出「旧代码报绿」—— **与读数相反**。
    两条臂一起跑才看得出全貌：**「基线不见了」被拆成「绿」和「红」两种假象，
    而两条都不说「基线不见了」** —— 而它不是绿、也不是违规，是**判不了**。
    ⇒ 这正是 ④ 的同一个形状：「换了一份语料」/「基线被删了」/「新增违规」三者长得一模一样。

    ## ★ ④ 必须配一条**单变量对照**，否则它自己就是空转

    「总是跳过」的实现能让 ④ 全绿。所以：**同一份 obs、同一份基线条目，
    只有语料指纹不同** ⇒ 一个判得出、一个判不了。两边一对照，
    「跳过」是**指纹**触发的、不是恒跳过。这一步不能省 ——
    少了它，④ 就是「空转与通过长得一模一样」的又一个实例。

    ## 以及：真语料上必须真的在判（⑨）

    ②–⑦ 全是拿合成观测喂进去的。若它在真语料上一路「跳过」，
    那上面几条通过也说明不了它在工作 ⇒ 最后用真语料真跑一遍，
    并核对**冻结的数 == 现算的数**（否则基线过期，运行时会报「条目失效」红）。
    """
    from ldv.checks._fixtures import (
        build_incremental,
        build_keyset,
        build_reach,
        build_sequence,
        coverage_of,
        make_builder,
    )
    from ldv.checks._framework import Assertion, Report
    from ldv.checks.coverage import (
        BASELINE_PATH,
        b18_cover_leak_baseline,
        corpus_fingerprint,
        cover_leak_profile,
        load_baseline,
    )

    loaded = load()
    if loaded is None:
        PASS.append("B18 基线守卫（跳过：目录不在）")
        return
    nodes, edges, _ = loaded
    fp = corpus_fingerprint(nodes, edges)

    # ── ① 非空转前提：文件在、指纹对得上、条目非空 ────────────────────────
    doc = load_baseline()
    ok("★ [B18] 基线文件在（否则「新增」这个概念不存在）",
       BASELINE_PATH.is_file(), str(BASELINE_PATH))
    ok("★ [B18] 基线里的语料指纹 == **现算的**指纹（对不上 ⇒ 真跑时一路「跳过」）",
       doc.get("语料") == fp, f"基线 {doc.get('语料')} vs 现算 {fp}")
    ok("★ [B18] 基线条目非空（空基线 ⇒ 守卫在守空气）",
       bool(doc.get("基线")), f"{doc.get('基线')}")

    def guard(obs: dict, baseline: dict, corpus: dict) -> Assertion:
        rep = Report(plugin="(自检)")
        b18_cover_leak_baseline(obs, rep, corpus, baseline)
        return rep.assertions[-1]

    def leak(n: int, pairs: int = 1) -> dict:
        return {"漏项数": n, "漏的对数": pairs,
                "明细": [{"父": "D0", "漏项数": n}] if n else []}

    base0 = {"语料": fp, "基线": {"keyset|maintenance": {"漏项数": 0, "漏的对数": 0}}}
    base16 = {"语料": fp, "基线": {"keyset|maintenance": {"漏项数": 16, "漏的对数": 2}}}
    #: ★ 一份**故意不是本语料**的指纹。两个数各 +1 ⇒ 保证与 `fp` 一定不等
    #:（这份语料恰好是 36 项 / 47 边，写死一个别的数也行，但加 1 不会随语料失效）。
    other = {"项数": fp["项数"] + 1, "边数": fp["边数"] + 1}

    # ── ② 观测 > baseline ⇒ 红 ─────────────────────────────────────────────
    a = guard({"keyset|maintenance": leak(5)}, base0, fp)
    ok("★ [B18] 观测 > baseline ⇒ **红**（新增违规）",
       a.result is Tri.NO, f"{a.result}：{a.detail}")

    # ── ③ baseline > 0 且 观测 == 0 ⇒ 红（条目失效）────────────────────────
    a = guard({"keyset|maintenance": leak(0, 0)}, base16, fp)
    ok("★ [B18] baseline > 0 而观测 == 0 ⇒ **红**（条目失效，不许当永久豁免）",
       a.result is Tri.NO, f"{a.result}：{a.detail}")

    # ── ④ 语料指纹不符 ⇒ 跳过 ──────────────────────────────────────────────
    a = guard({"keyset|maintenance": leak(5)}, base0, other)
    ok("★ [B18] 语料指纹不符 ⇒ **跳过**（未展开），**不是**红也不是绿",
       a.result is Tri.UNEXPANDED, f"{a.result}：{a.detail}")
    ok("★ [B18] 且理由里点明「另一份语料」与「跳过 ≠ 通过」（跳过必须能读懂）",
       "另一份语料" in a.detail and "跳过 ≠ 通过" in a.detail, a.detail)

    # ── ⑤ 单变量对照：跳过由**指纹**触发，不是恒跳过 ──────────────────────
    same = guard({"keyset|maintenance": leak(5)}, base0, fp)
    diff = guard({"keyset|maintenance": leak(5)}, base0, other)
    ok("★ [B18] 单变量对照：**同一份 obs + 同一份基线**，只有语料指纹不同 ⇒ "
       "一个判得出、一个判不了 —— 「跳过」是指纹触发的，不是恒跳过",
       same.result is Tri.NO and diff.result is Tri.UNEXPANDED,
       f"指纹相符 {same.result} vs 指纹不符 {diff.result}")

    # ── ⑥ baseline 里**没记指纹** ⇒ 同样跳过（旧格式文件不许让守卫无声失效）──
    no_fp = {"基线": {"keyset|maintenance": {"漏项数": 0, "漏的对数": 0}}}
    a = guard({"keyset|maintenance": leak(5)}, no_fp, fp)
    ok("★ [B18] baseline 里没记语料指纹 ⇒ **跳过**（判不了 ≠ 没问题）",
       a.result is Tri.UNEXPANDED, f"{a.result}：{a.detail}")
    ok("★ [B18] 且这条的理由与「另一份语料」**分得开**（一个说换了料、一个说旧格式）",
       "没记语料指纹" in a.detail, a.detail)

    # ── ⑥′ **基线文件整个不见了**（`load_baseline()` 返回 `{}`）─────────────
    #    `{}` 不是 `None` ⇒ 走的是「doc 里没有 `语料`」这条路。
    #    旧代码在**这一条**上报的是绿（观测全 0 时）或红（有既存违规时），
    #    两条都不说「基线不见了」—— 见 `outputs/_measure_b18_missing_baseline.py`。
    a = guard({"keyset|maintenance": leak(0, 0)}, {}, fp)
    ok("★ [B18] 基线文件不见了 ⇒ **跳过**（旧代码在观测全 0 时报的是**绿**）",
       a.result is Tri.UNEXPANDED, f"{a.result}：{a.detail}")
    a2 = guard({"keyset|maintenance": leak(16, 2)}, {}, fp)
    ok("★ [B18] 基线文件不见了 + 有既存违规 ⇒ **跳过**（旧代码报的是红，"
       "理由「新增覆盖不漏」是**假的**：没有任何东西变差）",
       a2.result is Tri.UNEXPANDED, f"{a2.result}：{a2.detail}")
    ok("★ [B18] 而且两种情形给的是**同一句**「判不了」—— 旧行为把它们分成了绿和红",
       a.detail == a2.detail, f"{a.detail[:40]} ≠ {a2.detail[:40]}")

    # ── ⑦ 0 < 观测 < baseline ⇒ 绿 + 报「可以收紧」─────────────────────────
    a = guard({"keyset|maintenance": leak(3)}, base16, fp)
    ok("★ [B18] 0 < 观测 < baseline ⇒ **绿**，且报「可以收紧」（不是静悄悄地绿）",
       a.result is Tri.YES and "可收紧" in a.detail, f"{a.result}：{a.detail}")

    # ── ⑧ `corpus` 是必填 —— 漏传当场炸 ───────────────────────────────────
    raises("★ [B18] `corpus` 必填：漏传直接 TypeError（防御不许因少传参数而静默消失）",
           lambda: b18_cover_leak_baseline({"keyset|maintenance": leak(5)},
                                           Report(plugin="(自检)")))

    # ── ⑨ 真语料上**确实在判**，且冻结的数 == 现算的数 ─────────────────────
    #: 与 `run_checks.MAINT_INIT` 同口径（先建 6、维护其余）。
    ids = sorted(nodes)
    real: dict[str, dict] = {}
    for which in ("keyset", "reach", "sequence"):
        cover = coverage_of(which, nodes, edges)
        mk = make_builder(which, nodes, edges)
        batch = {"keyset": build_keyset(nodes),
                 "reach": build_reach(nodes, edges),
                 "sequence": build_sequence(nodes, edges)}[which][0]
        inc = build_incremental(mk, nodes, ids[:6], ids[6:])
        real[f"{which}|batch"] = cover_leak_profile(batch, cover)
        real[f"{which}|maintenance"] = cover_leak_profile(inc, cover)

    rep = Report(plugin="(自检)")
    b18_cover_leak_baseline(real, rep, fp)
    a = rep.assertions[-1]
    ok("★ [B18] 真语料上这条守卫**确实在判**（不是一路「跳过」）",
       a.result is not Tri.UNEXPANDED, f"跳过了：{a.detail}")
    ok("★ [B18] 真语料上它是**绿** —— 基线就是在本语料上冻的",
       a.result is Tri.YES, f"{a.result}：{a.detail}")

    frozen = doc.get("基线", {})
    mism = {k: (frozen.get(k, {}).get("漏项数"), v["漏项数"])
            for k, v in real.items()
            if frozen.get(k, {}).get("漏项数") != v["漏项数"]}
    ok("★ [B18] 而且**冻结的数 == 现算的数**（基线没过期 —— 否则运行时会报「条目失效」红）",
       not mism, f"对不上的：{mism}")
    #: 反向也查一遍：**只在 `sequence|maintenance` 上非零** —— 若哪个方向悄悄开始漏，
    #: 上面那条会红；若哪个方向的既存违规被修好了而基线没动，上面那条也会红。
    nonzero = {k: v["漏项数"] for k, v in real.items() if v["漏项数"]}
    ok("★ [B18] 非空条目只有 `sequence|maintenance` 一处（其余 5 条都是 0 漏）"
       " —— 基线的形状与 §1 表第四行对得上（该行 2026-10-07 已降级）",
       set(nonzero) == {"sequence|maintenance"},
       f"非零条目：{nonzero}")


def _ref_cover(which: str, nodes, edges):
    """**参考实现** —— 改动前那一版**逐字**：不规范化、不去重、正向闭包。

    它慢，但**独立**：生产实现走的是「按 payload 规范形去重 + `reach` 用反向闭包」。
    两者在同一条定义上各写一遍，对不上就说明有一边错了。

    这正是 McConnell 等 2011 把「**独立**的 checker」当硬要求的那个形状：
    拿被检查对象的算法当参照，两边会**一起**错（`false-green` 形状 3「共享盲点」）。
    """
    from ldv.checks._fixtures import SEQ_END, _forward_closure, _is_prefix, sequences

    if which == "keyset":
        keys = {i: nodes[i].keys for i in nodes}

        def cover(payload):
            req, forb = payload
            req, forb = frozenset(req), frozenset(forb)
            return frozenset(i for i in nodes
                             if req <= keys[i] and not (keys[i] & forb))

        return cover

    if which == "sequence":
        seqs = {i: tuple(v) + (SEQ_END,) for i, v in sequences(nodes, edges).items()}

        def cover(payload):
            return frozenset(i for i in nodes
                             if any(_is_prefix(tuple(p), seqs[i]) for p in payload))

        return cover

    if which == "reach":
        fwd = _forward_closure(edges, universe=nodes)

        def cover(payload):
            anchors = set(payload)
            return frozenset(x for x in nodes if fwd.get(x, frozenset()) & anchors)

        return cover

    raise ValueError(which)


def _wrong_key_cache(fn):
    """**注入**：键取错 —— 按 `len(payload)` 而不是按 payload 本身。

    真实里最容易犯的正是这个（「长度差不多就当成同一个」），
    而它的症状**不是报错**，是**静默串台**：第二个 payload 拿到第一个的覆盖
    ⇒ 判据拿错的覆盖去比 ⇒ **假绿**。

    ⚠️ 三个方向**都会撞**：`keyset` 的 payload 恒为 2 元组（`len` 恒 2）、
       `reach` 按锚点数撞、`sequence` 按前缀数撞。
    """
    memo: dict[int, frozenset[str]] = {}

    def cover(payload):
        k = len(payload)
        got = memo.get(k)
        if got is not None:
            return got
        val = fn(payload)
        memo[k] = val
        return val

    return cover


def test_cover_oracle_transparency() -> None:
    """★ 外生覆盖 oracle：**换实现不许改变答案** —— 两个改动分开对照，且三个方向都查。

    ## 为什么这条必须单独存在

    `coverage_of` 是覆盖族的 **ground truth**（`B16` / 健全性 / 覆盖不漏 / 细化量）。
    它算错时**错的是判据的答案本身**，而且错法很隐蔽：

        少算一项   ⇒ 某方向被报「成员越界」  ⇒ 假红
        多算一项   ⇒ 真的越界被盖住          ⇒ **假绿**

    「跑一遍 `run_checks` 是绿的」**查不出这两种** —— 它本来就是绿的。

    ## 依据（逐字）

    Acar / Blelloch / Harper 2002（POPL，§2 末「Side Effects」）：

    > Also, the memoization of the kind done by lazy languages will not affect the
    > correctness of change-propagation, **because the value remains the same whether
    > it has been calculated or not.**

    同一节还给了**反面**，所以「纯」是前提不是顺手一提：

    > **function caching requires purely functional code**, but our framework involves
    > side-effects in its implementation.

    ## ★ 两个改动必须**分开**对照（这是本测试的重点）

    这一次同时动了两件事，混在一起就分不清谁错了：

        改动 1  `reach` 的**单次调用算法**：正向闭包 `fwd[x] & anchors`
                ⇒ 反向闭包 `∪_{a ∈ payload} rev[a]`
        改动 2  **同一 payload 算几遍**：`Cover` 按规范形去重

    ⇒ 三臂对同一个参考实现比：

        臂 A  `dedup=False`  ⇒ 只含改动 1
        臂 B  `dedup=True`   ⇒ 改动 1 + 2
        臂 C  A vs B         ⇒ **只**含改动 2

    读法（这一步才是「分开」的用处）：

        A 红、B 红、C 绿  ⇒ 两臂**一致地**错 ⇒ 问题在**去重之前**（`canon` / `raw`）
        A 绿、B 红、C 红  ⇒ 只有带缓存的那一臂错 ⇒ 问题在**缓存**里
        A 绿、B 绿、C 红  ⇒ 不可能（C 是 A、B 的推论）—— 出现就是键的哈希/等价坏了

    ⚠️ **只留 C 是不够的**：C 是同义反复。`canon` 产出的**就是** `raw` 的入参，
       所以「键决定答案」按构造成立 —— C 查不出 `canon` **丢信息**。
       丢信息要拿**参考实现**比（A / B 干的活）。

    ## ★ 而且**对照的顺序**本身也是对照的一部分

    踩过（实测）：原来先跑一句 `nonempty = sum(1 for p in payloads if b_arm(p))`
    来证明「不是空转」，而那一步**把臂 B 的缓存烤热了** ——
    于是后面比对时每一次调用都是**命中**、返回的都是**存进去的正确值**，
    ⇒ 注入「缓存返回错值」之后，② ③ 仍然报「**0 处不同**」，变异被**掩盖**。

    ⇒ 现在的顺序是：**先把参考值全算出来**，再让每个臂各跑一遍（各用全新实例）。
      「先算参考、再跑臂」不是为了好看 —— 它是这条测试**能不能红**的前提。

    ## 逐条钉住（每条都能红）

        ① 三臂在**非空**的 payload 上比（否则「相同」可能只是「都返回空集」）
        ② 臂 A vs 参考：`reach` 的**反向闭包**那一步干净
        ③ 臂 B vs 参考：加上去重之后仍然干净
        ④ 臂 C：A 与 B 逐项相同
        ⑤ **注入**：键取错（按 `len`）⇒ 与参考必须**对不上**（判据不是空转）
        ⑥ 去重**确实在发生**（命中 > 0）—— 度量，不进退出码

    ## 变异验证（都跑过，不是推的）

        canon 丢信息（`frozenset(sorted(p)[:1])`）  ⇒ ② 红、③ 红、④ 绿
          读法：两臂**一致地**错 ⇒ 问题在**去重之前**
        缓存返回错值（`next(iter(memo.values()))`）⇒ ② 绿、③ 红、④ 红
          读法：只有带缓存的那一臂错 ⇒ 问题在**缓存**里
        （两次都是**精确**命中对应的臂，不是「一片红」—— 这就是把两个改动
          分开对照的用处。）
    """
    from ldv.checks._fixtures import build_incremental, coverage_of, make_builder

    loaded = load()
    if loaded is None:
        ok("★ [oracle] 语料在（这条测试要真语料，缺了就报「跳过」不是「过」）", False,
           "找不到语料")
        return
    nodes, edges, _ = loaded
    ids = sorted(nodes)

    total_nonempty = 0
    total_payloads = 0
    stats_line: list[str] = []

    for which in ("keyset", "reach", "sequence"):
        # --- payload 序列：两个内核的全部方向（覆盖族要的就是这些） ----------
        #: ⚠️ `build_keyset` 只收 `nodes`（它不需要图）—— 三个方向签名不齐，
        #:   所以这里用 lambda 包一层，不用「按名字取函数再统一调用」。
        mk = make_builder(which, nodes, edges)
        builders = {"keyset": lambda: build_keyset(nodes),
                    "reach": lambda: build_reach(nodes, edges),
                    "sequence": lambda: build_sequence(nodes, edges)}
        batch = builders[which]()[0]
        inc = build_incremental(mk, nodes, ids[:6], ids[6:])
        payloads: list = []
        for k in (batch, inc):
            for d in k.all_directions():
                payloads.append(d.payload)
        total_payloads += len(payloads)

        ref = _ref_cover(which, nodes, edges)                  # 改动前逐字

        # ⚠️ **先把参考值全算出来，再让每个臂各跑一遍** —— 顺序不能反。
        #    踩过：原来先跑 `nonempty = sum(1 for p in payloads if b_arm(p))`，
        #    那一步**把臂 B 的缓存烤热了**，于是后面比对时每一次调用都是**命中**、
        #    返回的都是正确值 ⇒ **变异被掩盖**（实测：注入「缓存返回错值」后
        #    ② ③ 仍然报「0 处不同」）。**「对照的顺序」本身也是对照的一部分。**
        ref_vals = [ref(p) for p in payloads]

        a_arm = coverage_of(which, nodes, edges, dedup=False)   # 臂 A：只换算法
        b_arm = coverage_of(which, nodes, edges, dedup=True)    # 臂 B：算法 + 去重
        a_vals = [a_arm(p) for p in payloads]
        b_vals = [b_arm(p) for p in payloads]

        def diff(xs, ys) -> list[tuple[int, list[str]]]:
            bad = []
            for i, (x, y) in enumerate(zip(xs, ys)):
                if x != y:
                    bad.append((i, sorted(x ^ y)[:3]))
            return bad

        # --- ① 这批 payload **不是空转**：覆盖集必须非空 --------------------
        #: 拿**参考值**判，不拿臂 —— 否则这一步又会把某个臂的缓存烤热。
        nonempty = sum(1 for v in ref_vals if v)
        total_nonempty += nonempty
        ok(f"★ [oracle·{which}] 对照用的 payload **真的算出东西**"
           f"（否则「相同」可能只是「都空」）",
           nonempty > 0, f"非空 {nonempty}/{len(payloads)}")

        # --- ② 臂 A vs 参考 -------------------------------------------------
        d_a = diff(a_vals, ref_vals)
        ok(f"★ [oracle·{which}] 臂 A（`dedup=False`）与**参考实现**逐项相同"
           f" —— 单次调用的算法那一步是干净的",
           not d_a, f"{len(payloads)} 个 payload 有 {len(d_a)} 处不同：{d_a[:2]}")

        # --- ③ 臂 B vs 参考 -------------------------------------------------
        d_b = diff(b_vals, ref_vals)
        ok(f"★ [oracle·{which}] 臂 B（生产：`dedup=True`）与**参考实现**逐项相同",
           not d_b, f"{len(payloads)} 个 payload 有 {len(d_b)} 处不同：{d_b[:2]}")

        # --- ④ 臂 C：A vs B -------------------------------------------------
        d_c = diff(b_vals, a_vals)
        ok(f"★ [oracle·{which}] 臂 C：`dedup=True` 与 `dedup=False` 逐项相同"
           f"（Acar：值算没算都一样）",
           not d_c, f"{len(payloads)} 个 payload 有 {len(d_c)} 处不同：{d_c[:2]}")

        # --- ⑤ 注入：键取错 ⇒ 与参考**必须**对不上 --------------------------
        wrong = _wrong_key_cache(ref)
        w_vals = [wrong(p) for p in payloads]
        d_w = diff(w_vals, ref_vals)
        ok(f"★ [oracle·{which}] 注入「键取错（按 len）」⇒ 与参考**必须**对不上"
           f"（判据不是空转）",
           bool(d_w), f"注入后仍然全同 —— 那 ②③ 就是空转（{len(payloads)} 个 payload）")

        # --- ⑥ 去重确实在发生（度量） ---------------------------------------
        #: ⚠️ **另起一个全新的 oracle** —— 上面那个的 memo 已经被对照跑热了，
        #:   拿它数命中会**数的是对照的热度**，不是真实调用形状。
        fresh = coverage_of(which, nodes, edges)
        for k in (batch, inc):
            for d in k.all_directions():
                fresh(d.payload)          # 健全性 / `B16`
            for d in k.all_directions():
                if k.children_of(d):
                    fresh(d.payload)
                    for kid in k.children_of(d):
                        fresh(kid.payload)  # 覆盖不漏 / 细化量
        st = fresh.stats
        ok(f"★ [oracle·{which}] 去重**确实在发生**（命中 > 0）"
           f" —— 否则缓存是直通，白占内存",
           st["命中"] > 0, f"读数 {st}")
        stats_line.append(f"{which} 调用 {st['调用']} / 命中 {st['命中']}"
                          f"（不同 payload {st['不同 payload']}）")

    ok("★ [oracle] 三个方向的 payload 都**非空**（上面那条逐方向查过，这里查总数）",
       total_nonempty > 0, f"非空合计 {total_nonempty}/{total_payloads}")
    print(f"    · 覆盖 oracle 读数：{'；'.join(stats_line)}（**度量**，不进退出码）")


def _ref_b3(kernel, plugin) -> tuple[str, str]:
    """**改动前逐字**的 `b3_penalty_comparable` —— 含那个三重循环。只返回结论。

    它存在的唯一理由：证明「删掉三重循环」**不改变判定**。
    没有它，那条删除就只是「我觉得它没用」—— 而本仓库不吃这个。
    """
    from ldv.core.interfaces import call_penalty

    bad: list[str] = []
    checked = 0
    for rank in sorted({d.rank for d in kernel.all_directions()}):
        layer = [d for d in kernel.all_directions() if d.rank == rank]
        if len(layer) < 2:
            continue
        table: dict[str, list[float]] = {}
        for d in layer:
            vals: list[float] = []
            for i in sorted(kernel.items):
                try:
                    vals.append(call_penalty(plugin, d, kernel.items[i]))
                except Exception as exc:  # noqa: BLE001 - 插件是外部代码
                    bad.append(f"{d.did} 的代价抛异常：{type(exc).__name__}: {exc}")
                    vals.append(float("nan"))
            table[d.did] = vals
            checked += len(vals)
            if any(v != v for v in vals):
                bad.append(f"{d.did} 的代价里有 NaN（不可比）")
        ids = sorted(table)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                for a, b in zip(table[ids[i]], table[ids[j]]):
                    if not (a < b or a == b or a > b):
                        bad.append(f"{ids[i]} vs {ids[j]} 的一项不可比：{a} / {b}")
                        break
    if checked == 0:
        return "未展开", ""
    return ("否" if bad else "是"), f"{len(bad)} 处不可比"


def _prod_b3(kernel, plugin) -> tuple[str, str]:
    from ldv.checks._framework import Report
    from ldv.checks.contract import b3_penalty_comparable

    rep = Report(plugin="B3 对照", expects=("B3",))
    b3_penalty_comparable(kernel, plugin, rep)
    if rep.skipped:
        return "未展开", ""
    if rep.red:
        return "否", rep.red[0].detail
    return "是", rep.assertions[0].detail


class _NaNPenalty:
    """注入：`§I3` 返回 **NaN** —— `call_penalty` 必须把它挡下来。"""

    def __init__(self, name: str = "注入·NaN") -> None:
        self.name = name

    def penalty(self, d: object, item: object) -> float:
        return float("nan")


def test_b3_reduction_premise() -> None:
    """★ `B3` 的**三重循环删掉了** —— 这条钉住「删它的前提」+「删了不改判定」。

    ## 为什么非删不可

    `b3_penalty_comparable` 原来是三层循环：层内两两配对、每对**逐项**比。
    那是 **`O(层内方向数² × 项数)`**。3907 项 / reach 有 **7813 个方向** ⇒
    **小时级**。而 `B3` 是**判据**（`B1–B20`）⇒ `--no-probes` **关不掉它**。

    ⇒ **它才是「全量 `--no-probes` 跑不完」的主因** —— 不是覆盖族（那个只有 0.69 s）。
    删掉之后 `b3` 在 3907 项上是 **38.85 s**；剩下的是 `O(Ln)` = **30,521,484 次**
    `call_penalty`，瓶颈因此**移到插件里**（见 `plugins/reach.py` 的缓存一节）。

    ## 删它的依据（一条蕴含链，不是经验）

        前提   `call_penalty` 只可能返回**有限 float**
               —— 非数 / NaN / ±inf 全在它里面抛（`core/interfaces.py:190-196`）
        推论   两个**有限 float** 之间，`a < b or a == b or a > b` **恒为真**
               —— IEEE-754 里「三个都不成立」当且仅当有一方是 NaN（无序）
        ⇒ 那个循环**永远 append 不了东西**。

    它唯一可能命中的情形，是 `b3` 自己塞进去的 `float("nan")` —— 而那一行**已经报过**。

    ## ★ 前提必须被**检查**，不许靠读代码断言

    这就是本仓库反复说的那条：**「靠读代码断言的前提」= 一条将来会静默失效的规矩。**
    ⇒ ① 直接钉 `call_penalty` 的守卫（NaN / 非数 / ±inf / 插件抛异常，四种都必须抛）。
    那一条破了，三重循环就得加回来。

    ## 逐条钉住

        ① 前提：`call_penalty` 对 NaN / 非数 / ±inf / 抛异常 —— **四种都必须抛**
        ② 正常插件：参考（旧）与生产（新）判定**相同**，且都是「是」
        ③ 注入 NaN：参考与生产判定**相同**，且都是「否」 —— 删掉循环**没把红的漏掉**
        ④ 对照不是空转：② 是「是」、③ 是「否」⇒ 这个对照**能红也能绿**
    """
    from ldv.checks._framework import Report
    from ldv.checks.contract import b3_penalty_comparable
    from ldv.core.interfaces import PluginContractError, call_penalty

    loaded = load()
    if loaded is None:
        ok("★ [B3] 语料在（不在就报跳过，不报通过）", False, "找不到语料")
        return
    nodes, edges, _ = loaded
    kernel, plugin = build_reach(nodes, edges)

    # --- ① 前提：`call_penalty` 的四种守卫 -----------------------------------
    class _Bad:
        def __init__(self, val):
            self.name = "注入"
            self._val = val

        def penalty(self, d, item):
            return self._val

    def _probe(val):
        return lambda: call_penalty(_Bad(val), next(iter(kernel.all_directions())),
                                    kernel.items[sorted(kernel.items)[0]])

    for label, val in (("NaN", float("nan")), ("+inf", float("inf")),
                       ("-inf", float("-inf")), ("非数（str）", "小")):
        try:
            _probe(val)()
            ok(f"★ [B3] 前提：`call_penalty` 对 **{label}** 必须抛"
               f"（不抛 ⇒ 三重循环就不能删）",
               False, f"它放行了 {val!r}")
        except PluginContractError:
            ok(f"★ [B3] 前提：`call_penalty` 对 **{label}** 必须抛"
               f"（不抛 ⇒ 三重循环就不能删）", True)

    class _Raiser:
        name = "注入·抛异常"

        def penalty(self, d, item):
            raise RuntimeError("故意炸")

    try:
        call_penalty(_Raiser(), next(iter(kernel.all_directions())),
                     kernel.items[sorted(kernel.items)[0]])
        ok("★ [B3] 前提：插件抛异常时 `call_penalty` 必须转成 `PluginContractError`",
           False, "它放行了")
    except PluginContractError:
        ok("★ [B3] 前提：插件抛异常时 `call_penalty` 必须转成 `PluginContractError`", True)

    # --- ② 正常插件：新旧判定必须相同 --------------------------------------
    ref_v, ref_d = _ref_b3(kernel, plugin)
    new_v, new_d = _prod_b3(kernel, plugin)
    ok("★ [B3] 正常插件：**参考（旧）与生产（新）判定相同**，且都是「是」",
       ref_v == new_v == "是", f"旧 {ref_v}（{ref_d}）/ 新 {new_v}（{new_d}）")

    # --- ③ 注入 NaN：新旧判定必须相同，且都得是「否」 ----------------------
    nan_plugin = _NaNPenalty()
    ref_v2, ref_d2 = _ref_b3(kernel, nan_plugin)
    new_v2, new_d2 = _prod_b3(kernel, nan_plugin)
    ok("★ [B3] 注入 NaN：**参考（旧）与生产（新）判定相同**，且都是「否」"
       f" —— 删掉三重循环**没把该红的漏掉**",
       ref_v2 == new_v2 == "否", f"旧 {ref_v2}（{ref_d2}）/ 新 {new_v2}（{new_d2}）")

    # --- ④ 对照不是空转 -----------------------------------------------------
    ok("★ [B3] 这个对照**能红也能绿**（② 是「是」、③ 是「否」）—— 不是空转",
       ref_v == "是" and ref_v2 == "否",
       f"正常 {ref_v} / 注入 {ref_v2}")

    # --- ⑤ 删掉的那些消息是**冗余**的，不是唯一来源 ------------------------
    #: 旧版对同一个 NaN 会报**两遍**：一次「有 NaN」，一次「两两不可比」。
    #: ⇒ 旧版的条目数**必须多于**新版。若两边一样多，说明那个循环当时根本没在跑 ——
    #:   那「删掉它」就不是等价变换，而是删掉了一个**真在工作**的东西。
    def _n(detail: str) -> int:
        head = (detail or "").split(" ")[0]
        return int(head) if head.isdigit() else -1

    ok("★ [B3] 旧版对注入报的条目数**多于**新版 —— 证明那个循环当时**确实在跑**，"
       f"而且它报的是**重复**",
       _n(ref_d2) > _n(new_d2) > 0, f"旧 {ref_d2} / 新 {new_d2}")

    # --- ⑥ 生产实现里**确实没有**那个三重循环 -------------------------------
    import inspect
    src = inspect.getsource(b3_penalty_comparable)
    ok("★ [B3] 生产实现里**确实没有**三层嵌套循环（源码级确认，防止悄悄加回来）",
       "for i in range(len(ids))" not in src,
       "源码里又出现了 `for i in range(len(ids))` —— 那个 O(L²n) 回来了")

    # --- ⑦ ★ `checked` 必须**数到每一个方向** ---------------------------------
    #: 这条专门钉一个**真实发生过的回归**：删三重循环时，那三行记账
    #: （`table[...] = vals` / `checked +=` / NaN 检查）**掉出了** `for d in layer:` 循环体
    #: ⇒ 每层**只记最后一个方向**，而 `B3` 依旧报「是」。
    #:
    #: ⚠️ 上面 ①–⑥ **全都照过**：异常条目仍按方向产生（那个内层循环没动），
    #:    所以 ⑤ 的「旧 > 新」还成立。**只有数 `checked` 抓得住它。**
    dirs = kernel.all_directions()
    n_items = len(kernel.items)
    sizes = [sum(1 for d in dirs if d.rank == r) for r in {d.rank for d in dirs}]
    expect = sum(s for s in sizes if s >= 2) * n_items
    got = _n(new_d)
    ok("★ [B3] `checked` **数到了每一个方向**（= Σ_层 层内方向数 × 项数）"
       " —— 少了就是记账行又掉出循环体了",
       expect > 0 and got == expect,
       f"报 {got} / 应 {expect}（层内方向数 {sorted(sizes, reverse=True)[:6]}，项数 {n_items}）")

    # --- ⑧ 建表的规模 = Σ_层 方向数 × 项数（**不是** 方向数² × 项数）----------
    class _Counting:
        name = "计数"

        def __init__(self, inner):
            self._inner = inner
            self.calls = 0

        def penalty(self, d, item):
            self.calls += 1
            return self._inner.penalty(d, item)

    cnt = _Counting(plugin)
    _prod_b3(kernel, cnt)
    ok("★ [B3] `call_penalty` 的调用数 = Σ_层 方向数 × 项数"
       "（建表是**线性**规模，不是两两配对）",
       cnt.calls == expect, f"实调 {cnt.calls} / 应 {expect}")


# ═══ 树性：`§10.2 D` 的持久化前提（§K5 / B13） ═══════════════════════════════

def test_tree_premise() -> None:
    """★ 「每方向**恰好一个父**」是 `§10.2 D` 的**前提**，而它原来没有任何检查守着。

    持久化的路线**押在入度上**（Driscoll 等 1989）：

        树（一个父）  ⇒ node-copying ⇒ 访问旧版本 **O(1)**
        DAG（多个父） ⇒ 只能 fat node ⇒ 访问旧版本 **O(log m)**

    而 `§K5` 的原文是 `rank = 1 + max(rank(父))` —— 那个 `max` 是**为 DAG 写的**；
    `B13` 原来只查「无环 / 无自环 / 见证更粗」⇒ **对 DAG 照样放行**
    （实测见 `outputs/_probe_b13_gap.py`：入度改成 2，`B13` 仍报「是」）。

    ⇒ 这是「空转与通过长得一模一样」的又一形态：**前提成立与否，判据上长得一样。**

    ## 五条断言

      1. 三个插件 × 批建 / 维护：**语义入度 max = 1**，外生方向入度 = 0
      2. 两条边**互为转置**（`parent` / `witness` 与 `_children` 不打架）
      3. **非退化前提**：扇出 max > 1 —— 否则「恰好一个父」可能只是
         「每个方向本来就只有一个子」的副产品，那这条断言什么都没测
      4. ★ 把入度**注入成 2**（造 DAG）⇒ `B13` 必须报「否」
      5. ★ **反向判据**：还原成树 ⇒ `B13` 必须报「是」—— 防「永远红」
    """
    from ldv.checks._fixtures import (
        build_incremental, build_keyset, build_reach, build_sequence,
        load, make_builder,
    )
    from ldv.checks._framework import Report
    from ldv.checks.structure import b13_rank_well_founded

    loaded = load()
    if loaded is None:
        PASS.append("树性（跳过：语料目录不在）")
        return
    nodes, edges, _ = loaded

    def sem_deg(k):
        deg = {d.did: 0 for d in k.all_directions()}
        fan = {}
        for p in k.all_directions():
            kids = k.children_of(p)
            fan[p.did] = len(kids)
            for c in kids:
                deg[c.did] = deg.get(c.did, 0) + 1
        return deg, fan

    def transposed(k):
        """两条边互为转置吗？返回不符处。"""
        by_id = {d.did: d for d in k.all_directions()}
        bad = []
        for p in k.all_directions():
            for c in k.children_of(p):
                if c.parent != p.did:
                    bad.append(f"{c.did}.parent={c.parent} ≠ 实际父 {p.did}")
                if c.witness != (p.did,):
                    bad.append(f"{c.did}.witness={c.witness} ≠ ({p.did},)")
        for d in k.all_directions():
            if d.origin == ORIGIN_EXOGENOUS:
                continue
            if d.parent is None or d.parent not in by_id:
                bad.append(f"{d.did}.parent={d.parent} 不可用")
            elif d.did not in [c.did for c in k.children_of(by_id[d.parent])]:
                bad.append(f"{d.did} 不在其父 {d.parent} 的子列表里")
        return bad

    # --- ① 基线：三个插件 × 两条路径，语义入度必须是 1 ------------------------
    kernels: list[tuple[str, object]] = []
    for which in ("keyset", "reach", "sequence"):
        k, _ = (build_keyset(nodes) if which == "keyset"
                else (build_reach(nodes, edges) if which == "reach"
                      else build_sequence(nodes, edges)))
        kernels.append((f"{which}·批建", k))
    ids = sorted(nodes)
    for which in ("keyset", "reach", "sequence"):
        mk = make_builder(which, nodes, edges)
        kernels.append((f"{which}·维护", build_incremental(mk, nodes, ids[:6], ids[6:])))

    worst_in, worst_fan, worst_tr = 0, 0, []
    for tag, k in kernels:
        deg, fan = sem_deg(k)
        mx = max(deg.values()) if deg else 0
        worst_in = max(worst_in, mx)
        worst_fan = max(worst_fan, max(fan.values()) if fan else 0)
        worst_tr += [f"{tag}:{m}" for m in transposed(k)]
        ok(f"★ [树性] {tag}：非根方向**恰好一个父**（语义入度 max = 1）",
           mx == 1, f"实测 {mx}")
        roots_bad = [d.did for d in k.all_directions()
                     if d.origin == ORIGIN_EXOGENOUS and deg[d.did] != 0]
        ok(f"★ [树性] {tag}：外生方向（根）**没有父**", not roots_bad, f"{roots_bad[:2]}")

    ok("★ [树性] 两条边**互为转置**（`parent` / `witness` 与 `_children` 不打架）",
       not worst_tr, f"{worst_tr[:2]}")
    ok("★ [树性] 非退化前提：扇出 max > 1"
       "（否则「恰好一个父」可能只是「每个方向本来就只有一个子」的副产品）",
       worst_fan > 1, f"扇出 max = {worst_fan}")

    # --- ④ 注入：把入度改成 2（造 DAG）⇒ B13 必须红 -------------------------
    k, _ = build_keyset(nodes)
    parents = [d for d in k.all_directions() if k.children_of(d)]
    p1, p2 = parents[0], parents[1]
    victim = k.children_of(p2)[0]
    k._children[p1.did] = tuple(k._children[p1.did]) + (victim.did,)  # noqa: SLF001
    deg, _fan = sem_deg(k)
    ok("★ [树性] 注入生效：语义入度真的变成了 2",
       max(deg.values()) == 2, f"实测 max = {max(deg.values())}")
    rep = Report(plugin="keyset", expects=("B13",))
    b13_rank_well_founded(k, rep)
    ok("★★ [树性] **`B13` 对 DAG 必须报「否」**"
       " —— 改之前它对 DAG 照样报「是」，`§10.2 D` 的前提因此没被守",
       rep.assertions[0].result is Tri.NO,
       f"B13 = {rep.assertions[0].result}；{rep.assertions[0].detail[:60]}")

    # --- ⑤ 反向判据：还原成树 ⇒ B13 必须绿（防「永远红」） -------------------
    k2, _ = build_keyset(nodes)
    rep2 = Report(plugin="keyset", expects=("B13",))
    b13_rank_well_founded(k2, rep2)
    ok("★ [树性] 反向判据：正常树上 `B13` 报「是」（防「永远红」）",
       rep2.assertions[0].result is Tri.YES,
       f"B13 = {rep2.assertions[0].result}；{rep2.assertions[0].detail[:60]}")


# ═══ 查询结果的 `hit_items` 必须**每个说「是」的方向都收** ═══════════════════

def test_query_hit_items() -> None:
    """★ `hit_items` 只在**叶**上收成员 ⇒ 停在**内部**方向的滞留项**静默地少**。

    出路 (4)（§10.2 B，已落地）让项可以停在**内部**方向。而 `query` 原来是：

        说「是」⇒ 有子方向就往下走（**不收自己的成员**）；没有子方向才收

    ⇒ 一个停在内部方向的项，**父的 `命中` 说「是」**，但它**不进** `hit_items`。
    实测（`outputs/_probe_leak_vs_stay.py`，`sequence` 先建 6 维护 30）：

        滞留项 16 个 ⇒ 进 `hit_items` 的 **0 个**

    而 `QueryResult.render()` 照样印「**命中 0 项**」—— 名字像答案、实际不全。
    这正是本仓库反复要防的形状：**少掉的部分不会让任何东西变红。**

    ## 五条断言

      1. **非退化前提**：这份夹具上**确实有滞留**（否则下面全在查空气）
      2. ★ **完整性**：每个说「是」的方向，其 `members ∩ ideal` **必须**在 `hit_items` 里
      3. **可靠性**：`hit_items ⊆ ideal`（不许把不相关的项塞进来）
      4. ★ **反向判据**：`hit_items` **不许**包含「不在任何说「是」方向成员里」的项
         —— 防「干脆把 `ideal` 原样返回」（那会让 2 变恒真）
      5. ★ **三态不许被 `hit_items` 带偏**：`status` 必须**只**由 `yes` / `unexpanded` 决定
         —— 原来的 `if hits: status = …` 是**空操作**（`fold(是, 未展开) == 未展开`），
            留着它会让「改成每个方向都收」悄悄改掉三态
    """
    from ldv.checks._fixtures import build_incremental, load, make_builder

    loaded = load()
    if loaded is None:
        PASS.append("查询命中项（跳过：语料目录不在）")
        return
    nodes, edges, _ = loaded
    ids = sorted(nodes)
    k = build_incremental(make_builder("sequence", nodes, edges),
                          nodes, ids[:6], ids[6:])

    # --- ① 非退化前提：真有滞留 -------------------------------------------
    stay = sum(len(k.stayed_of(d)) for d in k.all_directions())
    ok("★ [hit_items] 非退化前提：这份夹具上**确实有滞留**（否则下面全在查空气）",
       stay > 0, f"滞留 {stay} 项")

    # --- ②③④ 逐项查一遍 ---------------------------------------------------
    missed: list[str] = []
    extra: list[str] = []
    not_in_ideal: list[str] = []
    checked = 0
    for d in k.all_directions():
        for x in sorted(k.members_of(d)):
            q = Query(ideal=frozenset({x}))
            r = k.query(q)
            checked += 1
            if x not in r.hit_items:
                missed.append(f"{d.did}:{x}")
            if not r.hit_items <= q.ideal:
                not_in_ideal.append(d.did)
    # 反向：随机挑若干**不在任何「是」方向成员里**的项，不许出现
    for x in ids[:12]:
        q = Query(ideal=frozenset({x}))
        r = k.query(q)
        in_yes = set()
        for did in r.yes:
            in_yes |= set(k.members_of(k.direction(did)))
        if (r.hit_items - in_yes):
            extra.append(f"{x}:{sorted(r.hit_items - in_yes)[:2]}")

    ok("★ [hit_items] 完整性：每个说「是」的方向的 `members ∩ ideal` **都在** `hit_items` 里",
       not missed, f"{len(missed)}/{checked} 项没进：{missed[:3]}")
    ok("★ [hit_items] 可靠性：`hit_items ⊆ ideal`", not not_in_ideal,
       f"{not_in_ideal[:3]}")
    ok("★ [hit_items] 反向判据：`hit_items` **不许**多出「不在任何「是」方向成员里」的项"
       "（防「把 `ideal` 原样返回」）", not extra, f"{extra[:3]}")

    # --- ⑤ 三态只由 yes / unexpanded 决定 ---------------------------------
    bad_status: list[str] = []
    for x in ids[:12]:
        q = Query(ideal=frozenset({x}))
        r = k.query(q)
        want = (Tri.YES if r.yes and not r.unexpanded
                else fold(Tri.YES, Tri.UNEXPANDED) if r.yes
                else Tri.UNEXPANDED if r.unexpanded else Tri.NO)
        if r.status is not want:
            bad_status.append(f"{x}: {r.status} ≠ {want}")
    ok("★ [hit_items] 三态**只由 `yes` / `unexpanded` 决定**"
       "（原来的 `if hits: status = …` 是空操作，留着会被这个改动悄悄改掉）",
       not bad_status, f"{bad_status[:3]}")


# ═══ reach 插件缓存：前提 + 透明性 ═══════════════════════════════════════════

#: 图状态的三个载体。**缓存的前提就是它们不被写** —— 名单写在这里，别散在函数里。
_GRAPH_ATTRS = ("edges", "_rev", "_sinks")
_MUTATORS = ("add", "update", "pop", "remove", "clear", "discard", "setdefault",
             "append", "extend", "insert")


def _graph_writes_outside_init(cls: type) -> list[str]:
    """AST 扫 `cls` 的源码：图状态有没有在 `__init__` **之外**被写。

    ⚠️ **用 AST 不用 grep**：`reach.py` 的 docstring 里就写着
    「`self.edges` 只在 `__init__` 里被读」这句话 —— grep 会把**注释**
    当成写入报出来（假红），也会漏掉 `self._rev.add(...)` 这种就地改（假绿）。
    AST 只看语法上的赋值 / 调用目标。
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(cls)))
    bad: list[str] = []

    def _is_graph_attr(node: ast.AST) -> str | None:
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id == "self" and node.attr in _GRAPH_ATTRS):
            return node.attr
        return None

    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        if fn.name == "__init__":
            continue
        for sub in ast.walk(fn):
            targets: list[ast.AST] = []
            if isinstance(sub, ast.Assign):
                targets = list(sub.targets)
            elif isinstance(sub, (ast.AugAssign, ast.AnnAssign)):
                targets = [sub.target]
            for t in targets:
                for n in ast.walk(t):
                    a = _is_graph_attr(n)
                    if a:
                        bad.append(f"{fn.name}() 第 {sub.lineno} 行写了 self.{a}")
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                obj = sub.func.value
                if isinstance(obj, ast.Subscript):
                    a = _is_graph_attr(obj.value)
                    if a:
                        bad.append(f"{fn.name}() 第 {sub.lineno} 行就地改 self.{a}[...]")
                a = _is_graph_attr(obj)
                if a and sub.func.attr in _MUTATORS:
                    bad.append(f"{fn.name}() 第 {sub.lineno} 行 self.{a}.{sub.func.attr}()")
    return bad


def _graph_fingerprint(plug: Any) -> tuple:
    """图状态的指纹 —— 三个载体逐项排好序。"""
    return (
        tuple(sorted((k, tuple(sorted(v))) for k, v in plug.edges.items())),
        tuple(sorted((k, tuple(sorted(v))) for k, v in plug._rev.items())),
        tuple(sorted(plug._sinks)),
    )


class _MutatingReach:
    """**已知答案的对照组**：构造之后**改图** —— 前提检查必须把它抓出来。

    没有这个类，「AST 扫不出写入」可能只是**扫描器不会扫**（空转），
    而不是**代码干净**。两者的输出长得一模一样。
    """

    def __init__(self, edges: dict[str, Any]) -> None:
        self.edges = {k: frozenset(v) for k, v in edges.items()}
        self._rev: dict[str, set[str]] = {}
        self._sinks: frozenset[str] = frozenset()

    def penalty(self, d: Any, item: Any) -> float:
        self.edges[str(item["id"])] = frozenset()      # ← 构造之后写图
        self._rev.setdefault("x", set()).add("y")      # ← 就地改
        return 0.0


def test_reach_cache_premise() -> None:
    """★ 缓存的**前提**：「图在 `__init__` 之后只读」—— 这条必须被检查。

    ## 为什么缓存需要一个前提

    插件的缓存键是 `payload`、值是**整条 BFS 输出**（见 `plugins/reach.py` 模块
    docstring）。键能选得这么粗，靠的是**同一个 payload 的答案永远一样** ——
    而这一条等价于**图不变**。

    Acar / Blelloch / Harper 2002（POPL，§2）把它点名为**关键要求**：

    > **The key requirement, therefore, is not that there are no side-effects,
    > but rather that all data is persistent** (i.e., the closure's environment
    > cannot be modified).

    ⇒ 图一旦可变，缓存里就是**过期值**：判据照旧报「是」，而它比的是**上一版图的答案**
    —— 「空转与通过长得一模一样」。

    ## 三条，每条都能红

        ① **静态**：AST 扫出「`self.edges` / `self._rev` / `self._sinks`
           只在 `__init__` 里被写」
        ② **对照**：拿一个**构造后改图**的类跑同一个扫描 ⇒ **必须扫得出来**
           （否则 ① 只是「扫描器不会扫」）
        ③ **行为**：把插件跑完一遍完整用法（`hit` / `penalty` / `split` / `merge`）
           ⇒ 图指纹**逐项未变**
    """
    from ldv.plugins.reach import ReachPlugin

    # --- ① 静态：写入只在 __init__ 里 ------------------------------------
    bad = _graph_writes_outside_init(ReachPlugin)
    ok("★ [缓存前提] `ReachPlugin` 的图状态（`edges` / `_rev` / `_sinks`）"
       "**只在 `__init__` 里被写**",
       not bad, f"`__init__` 之外有 {len(bad)} 处写入：{bad[:3]}")

    # --- ② 对照：扫描器**真的会扫** --------------------------------------
    caught = _graph_writes_outside_init(_MutatingReach)
    ok("★ [缓存前提] 对照：一个**构造后改图**的类 ⇒ 同一个扫描**必须**报出来"
       "（否则上面那条是空转）",
       len(caught) >= 2, f"只报出 {len(caught)} 处：{caught[:3]}")

    # --- ③ 行为：跑完一遍完整用法，图指纹不变 ----------------------------
    loaded = load()
    if loaded is None:
        ok("★ [缓存前提] 语料在（这条测试要真语料，缺了就报「跳过」不是「过」）",
           False, "找不到语料")
        return
    nodes, edges, _ = loaded
    plug = ReachPlugin(edges)
    before = _graph_fingerprint(plug)

    k, _ = build_reach(nodes, edges)
    items_sorted = sorted(k.items)
    for d in k.all_directions():
        plug.hit(d, Query(ideal=frozenset({items_sorted[0]})))
        plug.reachable(frozenset(d.payload))
        for i in items_sorted:
            plug.penalty(d, k.items[i])
        if k.children_of(d):
            plug.merge(list(k.children_of(d)))          # 子方向是 `Direction` 对象
    for i in items_sorted:
        plug.signature(i)

    after = _graph_fingerprint(plug)
    ok("★ [缓存前提] 行为侧：跑完 `hit` / `penalty` / `split` / `merge` 之后"
       "**图指纹逐项未变**",
       before == after,
       "图被改过 —— 缓存里的值是过期的（假绿）")
    st = plug.cache_stats()
    ok("★ [缓存前提] 这一遍确实**用到了缓存**（命中 > 0）—— 否则前提查的是空气",
       st["distance 命中"] > 0 and st["reachable 命中"] > 0, f"读数 {st}")
    print(f"    · reach 缓存读数：{st}（**度量**，不进退出码）")


def test_reach_cache_transparency() -> None:
    """★ 缓存**不许改变数值** —— 三臂对照，两个改动分开查。

    ## 这次同时动了两件事

        改动 1  **算法**：`distance` 从「每项一遍正向 BFS」改成
                「一遍**多源反向** BFS ⇒ 所有项的距离」
        改动 2  **缓存**：键 `payload`，值 = 整条 BFS 输出

    混在一起就分不清谁错了 ⇒ 三臂对**参考实现**比：

        臂 REF      **改动前逐字**的 `distance` / `reachable` / `signature`
        臂 NO-CACHE 改动 1，缓存关
        臂 CACHE    改动 1 + 2

    读法：

        REF≠NO-CACHE           ⇒ 问题在**算法**（多源反向 BFS 写错了）
        NO-CACHE 绿、CACHE 红  ⇒ 问题在**缓存**（串台 / 过期）
        两臂一致地红           ⇒ 问题在**两边共用的那部分**（图的构造）

    ⚠️ **顺序**：先把参考值**全**算出来，再让每个臂各跑一遍（各用全新实例）。
       反过来的话，预热步骤会把某个臂的缓存烤热 ⇒ 注入被掩盖
       （`test_cover_oracle_transparency` 实测踩过这一条）。

    ## 逐条钉住

        ① 对照用的 payload **真的算出东西**（距离不全为 0）—— 否则「相同」可能只是「都空」
        ② 臂 NO-CACHE vs REF 逐项相同（**算法**那一步干净）
        ③ 臂 CACHE vs REF 逐项相同（**Acar：值算没算都一样**）
        ④ 臂 CACHE vs NO-CACHE 逐项相同（只含缓存那一步）
        ⑤ **注入**「缓存返回错值（串台）」⇒ ③ 必须红，而 ② 必须**仍然绿**
           —— 精确命中「问题在缓存里」，不是一片红
        ⑥ 缓存确实在命中（度量，不进退出码）
    """
    from ldv.plugins.reach import ReachPlugin

    loaded = load()
    if loaded is None:
        ok("★ [缓存透明] 语料在（这条测试要真语料，缺了就报「跳过」不是「过」）",
           False, "找不到语料")
        return
    nodes, edges, _ = loaded
    edges = {k: frozenset(v) for k, v in edges.items()}
    rev: dict[str, set[str]] = {k: set() for k in edges}
    for src, dsts in edges.items():
        for dst in dsts:
            rev.setdefault(dst, set()).add(src)

    k, _ = build_reach(nodes, edges)
    items_sorted = sorted(k.items)
    dirs = sorted(k.all_directions(), key=lambda d: (d.rank, d.did))

    #: 生产顺序（`checks/contract.py` 的 `b3`）：同层内**同一个 payload 连续用满 n 次**
    pairs: list[tuple[Any, str]] = []
    for rank in sorted({d.rank for d in dirs}):
        layer = [d for d in dirs if d.rank == rank]
        if len(layer) < 2:
            continue
        for d in layer:
            for i in items_sorted:
                pairs.append((d, i))
    payloads = [frozenset(d.payload) for d, _ in pairs]

    # --- 参考实现（改动前逐字）—— **先全算出来** ------------------------
    ref_pen = [_ref_distance(edges, i, frozenset(d.payload)) for d, i in pairs]
    ref_reach = [_ref_reachable(rev, frozenset(d.payload)) for d in dirs]
    ref_sig = [_ref_signature(edges, i) for i in items_sorted]

    # --- ① 对照不是空转：距离里必须有非 0 项 ----------------------------
    nonzero = sum(1 for v in ref_pen if v)
    ok("★ [缓存透明] 对照用的代价值**真的算出东西**（有非 0 距离）"
       "—— 否则「相同」可能只是「都返回同一个常数」",
       nonzero > 0, f"非 0 {nonzero}/{len(ref_pen)}")

    # --- 三个臂（各用全新实例） ------------------------------------------
    probe = Query(ideal=frozenset({items_sorted[0]}))

    def arm(cache: bool, cls: type = ReachPlugin):
        p = cls(edges, cache=cache)
        #: 代价按 `b3` 的顺序：`for d in layer: for i in items` —— 同一 payload 连续 n 次
        pen = [p.penalty(d, k.items[i]) for d, i in pairs]
        rea = []
        for d in dirs:
            #: ⚠️ 同一个 payload **连着问两遍**才是生产的形状：`hit` 内部**也**调
            #:    `reachable`（`b2` 的 109k 次就是它）。只问一遍的话可达那一路
            #:    命中恒为 0 —— 量出来的是**测试的形状**，不是生产的。
            rea.append(p.reachable(frozenset(d.payload)))   # 第一遍：算
            p.hit(d, probe)                                 # 第二遍：命中
        sig = [p.signature(i) for i in items_sorted]
        return p, pen, rea, sig

    p_nc, pen_nc, rea_nc, sig_nc = arm(False)
    p_c, pen_c, rea_c, sig_c = arm(True)

    def diff(xs, ys) -> list[str]:
        return [f"#{i}: {x!r} ≠ {y!r}" for i, (x, y) in enumerate(zip(xs, ys)) if x != y]

    # --- ② 算法那一步：NO-CACHE vs REF -----------------------------------
    d_pen = diff(pen_nc, ref_pen)
    ok("★ [缓存透明] 臂 NO-CACHE 的 `penalty` 与**参考实现**逐项相同"
       "（多源反向 BFS 那一步干净）",
       not d_pen, f"{len(pairs)} 对里有 {len(d_pen)} 处不同：{d_pen[:2]}")
    ok("★ [缓存透明] 臂 NO-CACHE 的 `reachable` / `signature` 与参考逐项相同",
       not diff(rea_nc, ref_reach) and not diff(sig_nc, ref_sig),
       f"reachable {len(diff(rea_nc, ref_reach))} 处 / "
       f"signature {len(diff(sig_nc, ref_sig))} 处")

    # --- ③ 加上缓存：CACHE vs REF ----------------------------------------
    d_pen_c = diff(pen_c, ref_pen)
    ok("★ [缓存透明] 臂 CACHE 的 `penalty` 与**参考实现**逐项相同"
       "（Acar：值算没算都一样）",
       not d_pen_c, f"{len(pairs)} 对里有 {len(d_pen_c)} 处不同：{d_pen_c[:2]}")
    ok("★ [缓存透明] 臂 CACHE 的 `reachable` / `signature` 与参考逐项相同",
       not diff(rea_c, ref_reach) and not diff(sig_c, ref_sig),
       f"reachable {len(diff(rea_c, ref_reach))} 处 / "
       f"signature {len(diff(sig_c, ref_sig))} 处")

    # --- ④ 只含缓存那一步：CACHE vs NO-CACHE ------------------------------
    d_c = diff(pen_c, pen_nc)
    ok("★ [缓存透明] 臂 CACHE 与 NO-CACHE 的 `penalty` 逐项相同（只含缓存那一步）",
       not d_c, f"{len(d_c)} 处不同：{d_c[:2]}")

    # --- ⑤ 注入：缓存**串台** ⇒ ③ 红，② 仍绿 -----------------------------
    #: ⚠️ 注入必须**继承** `ReachPlugin` 而不是包一层：`penalty` 内部调的是
    #:    `self.distance` / `self._distances`，包一层的话 `self` 是**被包的实例**，
    #:    覆盖永远轮不到 —— 注入会静默失效（那才是最坏的一种「注入」）。
    class _WrongKeyReach(ReachPlugin):   # noqa: D401
        """**注入**：键取错 —— 命中时返回缓存里**第一个** payload 的结果。

        真实里最容易犯的正是这个（「反正都是同一批项，差不多就复用」），
        而它的症状**不是报错**，是**静默串台**：第二个 payload 拿到第一个的答案
        ⇒ 判据拿错的值去比 ⇒ **假绿**。所以它必须**只**打缓存那一路。
        """

        def _distances(self, anchors: frozenset[str]) -> dict[str, float]:
            if self.cache and self._dist_cache:
                self.n_distance_hit += 1
                return next(iter(self._dist_cache.values()))     # ← 串台
            return super()._distances(anchors)

        def reachable(self, anchors: Any) -> frozenset[str]:
            if self.cache and self._reach_cache:
                self.n_reachable_hit += 1
                return next(iter(self._reach_cache.values()))    # ← 串台
            return super().reachable(anchors)

    _, pen_bad, rea_bad, sig_bad = arm(True, _WrongKeyReach)
    d_bad = diff(pen_bad, ref_pen)
    ok("★ [缓存透明] 注入「缓存返回**别的 payload** 的值（串台）」"
       "⇒ 与参考**必须**对不上（③ 不是空转）",
       bool(d_bad), f"注入后仍然全同 —— 那 ③ 就是空转（{len(pairs)} 对）")
    _, pen_bad_nc, _, _ = arm(False, _WrongKeyReach)
    ok("★ [缓存透明] 同一次注入**精确**命中：关掉缓存那一臂**仍然绿**"
       "（⇒ 问题在缓存里，不是一片红）",
       not diff(pen_bad_nc, ref_pen),
       f"关掉缓存也红了 {len(diff(pen_bad_nc, ref_pen))} 处 ⇒ 注入没打在缓存上")
    ok("★ [缓存透明] 同一次注入在 `reachable` 那一路也**必须**对不上",
       bool(diff(rea_bad, ref_reach)),
       "可达缓存那一路注入后仍然全同 ⇒ 那条注入没打上")
    ok("★ [缓存透明] 注入也**只**打 `penalty` / `reachable` 两处缓存，"
       "`signature` 那一路不受影响（对照，说明注入不是无差别污染）",
       not diff(sig_bad, ref_sig), f"{len(diff(sig_bad, ref_sig))} 处不同")

    # --- ⑥ 缓存确实在命中（度量） ----------------------------------------
    st = p_c.cache_stats()
    ok("★ [缓存透明] 缓存**确实在命中**（命中 > 0）—— 否则缓存是直通，白占内存",
       st["distance 命中"] > 0 and st["reachable 命中"] > 0, f"读数 {st}")
    print(f"    · reach 缓存读数：调用 {st['distance 调用']} / 命中 {st['distance 命中']}"
          f"（可达 {st['reachable 调用']}/{st['reachable 命中']}）"
          f"（**度量**，不进退出码）")


def _ref_distance(edges: dict[str, frozenset[str]], item_id: str,
                  anchors: frozenset[str]) -> float:
    """**改动前逐字**的 `ReachPlugin.distance` —— 每项一遍**正向** BFS。"""
    from collections import deque

    if item_id in anchors:
        return 0.0
    seen = {item_id}
    q = deque([(item_id, 0)])
    while q:
        cur, dist = q.popleft()
        for nxt in edges.get(cur, ()):
            if nxt in anchors:
                return float(dist + 1)
            if nxt not in seen:
                seen.add(nxt)
                q.append((nxt, dist + 1))
    return float(len(edges) + 1)


def _ref_reachable(rev: dict[str, set[str]], anchors: frozenset[str]) -> frozenset[str]:
    """**改动前逐字**的 `ReachPlugin.reachable` —— 单源反向 BFS。"""
    from collections import deque

    seen = set(anchors)
    q = deque(seen)
    while q:
        cur = q.popleft()
        for prev in rev.get(cur, ()):
            if prev not in seen:
                seen.add(prev)
                q.append(prev)
    return frozenset(seen)


def _ref_signature(edges: dict[str, frozenset[str]], item_id: str) -> frozenset[str]:
    """**改动前逐字**的 `ReachPlugin.signature`（含每次重算 `sinks`）。"""
    from collections import deque

    sinks = {n for n in edges if not edges[n]}
    if item_id in sinks:
        return frozenset({item_id})
    seen = {item_id}
    q = deque([item_id])
    found: set[str] = set()
    while q:
        cur = q.popleft()
        if cur in sinks:
            found.add(cur)
            continue
        for nxt in edges.get(cur, ()):
            if nxt not in seen:
                seen.add(nxt)
                q.append(nxt)
    return frozenset(found) or frozenset({item_id})


# ═══ 删除路径（§10.2 C） ═════════════════════════════════════════════════════
#
# 设计文档 §10.2 C 要六条检查 `D1–D6`，**都跑在删除路径上**。它们**不是** `B` 编号
# （§10.2 D 的持久化判据才用 `P` 编号），所以不进 `run_checks`，落在这里。
#
# ⚠️ 六条写在文档里**不等于六条都能红**。所以每条配一个**已知答案的对照组**
#    （一个错一处的变异内核），并**逐条**印出「基线绿 / 注入红」。
#    任何一条注入后不红，它就是空转 —— 而空转与通过长得一模一样。

def _del_build(cls, which, nodes, edges):
    """用给定的内核类（`Kernel` 或其变异子类）走一遍**与生产相同的批建**。"""
    from ldv.plugins.reach import ReachPlugin
    from ldv.plugins.sequence import SequencePlugin

    if which == "keyset":
        plug = KeysetPlugin()
        root = plug.merge([])
    elif which == "reach":
        plug = ReachPlugin(edges, traverse_budget=10_000)
        root = frozenset(nodes)
    elif which == "sequence":
        plug = SequencePlugin(sequences(nodes, edges))
        root = frozenset({()})
    else:
        raise ValueError(which)
    k = cls(plug, items(nodes))
    k.build(root)
    for nid in sorted(nodes):
        k.insert(nid)
    return k


def _d1_empty_needs_record(kernel) -> list[str]:
    """`D1` 空方向必须带失效记录 —— `members(d) == ∅ ⇒ 必须有一条「已失效」账目`。

    这是 `remove()` 第 ② 件（记账）的守卫。**不记账与记了账在结构上长得一样**
    —— 都只是「成员集空了」，所以必须单独查账本。
    """
    bad = []
    for d in kernel.all_directions():
        if kernel.members_of(d):
            continue
        has = any(e.kind == EVENT_INVALIDATED and e.detail.get("members_after") == 0
                  for e in kernel.ledger.events_for(d.did))
        if not has:
            bad.append(d.did)
    return bad


def _d2_cover_leak(kernel, cover) -> int:
    """`D2` 删除后 `覆盖(父) ⊆ ∪覆盖(子)` 仍成立 —— `B18` 的删除路径版本。"""
    return cover_leak_profile(kernel, cover)["漏项数"]


def _d3_soundness(kernel, cover) -> int:
    """`D3` 删除后 `members(父) ⊆ 覆盖(父)` 仍成立 —— `B16` 的删除路径版本。"""
    return soundness_profile(kernel, cover)["越界成员数"]


def _d4_change_within_cone(mem_before: dict, kernel, path: tuple) -> list[str]:
    """`D4` 变动集合 ⊆ `Cone(x)` —— `B8` 的删除路径版本。"""
    out = []
    for did, mem in mem_before.items():
        if set(kernel._members.get(did, ())) != mem and did not in path:
            out.append(did)
    return out


def _ledger_lines(ledger) -> list[tuple]:
    """账本的**外部**指纹 —— 不用 `Ledger._digest`（那玩意儿只由 `append` 维护，
    就地改 `detail` 它看不见；而 `detail` 是**可变字典**，`Event` 冻结拦不住它）。"""
    return [(e.seq, e.kind, e.did,
             tuple(sorted((k, repr(v)) for k, v in e.detail.items())))
            for e in ledger]


def _d5_append_only(lines_before: list[tuple], kernel) -> list[str]:
    """`D5` 账本只增不改 —— `B9` 的删除路径版本。"""
    now = _ledger_lines(kernel.ledger)
    bad = []
    if len(now) < len(lines_before):
        bad.append(f"账本变短：{len(lines_before)} → {len(now)}")
    for i, fp in enumerate(lines_before):
        if i >= len(now) or now[i] != fp:
            bad.append(f"第 {i} 条被改写")
    return bad


def _d6_states_separable(kernel, before_dirs, nonexistent: str = "D9999") -> list[str]:
    """`D6` 「已失效」与「从来没存在过」分得开 —— `§K4`。

    分得开靠**两样都在**：方向仍在 `_dirs` 里查得到 + 账本里有记录。
    两样缺一，一个「支持集空了的旧方向」与一个「从没建过的编号」就**分不开**了
    —— 而 `§M2 情形③` 的原话正是「这两个必须分得开」。
    """
    bad = []
    now = {d.did for d in kernel.all_directions()}
    for did in sorted(before_dirs - now):
        bad.append(f"{did} 删前在 `_dirs` 里、删后不见了（真删了方向）")
    for d in kernel.all_directions():
        if not kernel.members_of(d) and kernel.ledger.status_of(d.did) == "不存在":
            bad.append(f"{d.did} 空了却报「不存在」")
    if kernel.ledger.status_of(nonexistent) != "不存在":
        bad.append(f"{nonexistent} 从未存在却报 {kernel.ledger.status_of(nonexistent)!r}")
    return bad


# --- 六个对照内核 —— 各错一处 -----------------------------------------------

class _NoLedger(Kernel):
    """`D1` 对照：去成员，但**不记失效**。"""

    def remove(self, item_id: str) -> tuple[str, ...]:
        if item_id not in self.items:
            raise KeyError(item_id)
        path = tuple(self._path.get(item_id, ()))
        del self.items[item_id]
        for did in path:
            self._members.get(did, set()).discard(item_id)
        self._path.pop(item_id, None)
        return path


class _DropChild(Kernel):
    """`D2` 对照：把路径上最深的方向从**父的 `_children`** 里摘掉（拆父子边）。"""

    def remove(self, item_id: str) -> tuple[str, ...]:
        path = super().remove(item_id)
        for did in reversed(path):
            par = self._dirs[did].parent
            if par is not None:
                self._children[par] = tuple(
                    c for c in self._children.get(par, ()) if c != did)
                break
        return path


class _GhostMember(Kernel):
    """`D3` 对照：往某个方向的成员里塞一个**语料之外**的项。"""

    def __init__(self, plugin, items):
        super().__init__(plugin, items)
        self._done = False

    def remove(self, item_id: str) -> tuple[str, ...]:
        path = super().remove(item_id)
        if path and not self._done:
            self._members.setdefault(path[-1], set()).add("__幽灵项__")
            self._done = True
        return path


class _OffPath(Kernel):
    """`D4` 对照：动一个**不在 `Cone(x)` 上**的方向。

    ⚠️ 选**去掉一个成员**，不选加一个：实测 `keyset` / `sequence` 上
       `覆盖(d) == members(d)`（payload 是精确的）⇒ 加任何项都会顺手把 `D3`
       （`members ⊆ 覆盖`）弄红，对照就不「精确命中」了。去掉成员则**只缩**
       `members`，`⊆ 覆盖` 仍成立 ⇒ 只有 `D4` 红。留一个成员，免得顺手弄空 ⇒ 惊动 `D1`。
    """

    def __init__(self, plugin, items):
        super().__init__(plugin, items)
        self._done = False

    def remove(self, item_id: str) -> tuple[str, ...]:
        path = super().remove(item_id)
        if self._done:
            return path
        for d in self.all_directions():
            if d.did in path:
                continue
            mem = self._members.get(d.did, set())
            if len(mem) >= 2:
                mem.discard(sorted(mem)[0])
                self._done = True
                break
        return path


class _Tamper(Kernel):
    """`D5` 对照：就地改一条**既有**账目的 `detail`（`Event` 冻结，`detail` 没冻）。"""

    def __init__(self, plugin, items):
        super().__init__(plugin, items)
        self._done = False

    def remove(self, item_id: str) -> tuple[str, ...]:
        path = super().remove(item_id)
        if not self._done and len(self.ledger._events) > 0:
            self.ledger._events[0].detail["__篡改__"] = True
            self._done = True
        return path


class _DeleteDir(Kernel):
    """`D6` 对照：把空方向从 `_dirs` 里**摘掉**（真删方向），账本原样保留。

    ⚠️ 它**同时**会破 `D2` —— 被删的方向若曾覆盖东西，它的覆盖就没了。
       这是**真事实**（两条裁决耦合），不是对照没做干净：设计文档 §10.2 C
       明写「三条处置**互相关联**」。所以这一格是**唯一**一个「带红」的对照，
       测试里**显式记下**这件事，而不是把它当噪声盖掉。
    """

    def __init__(self, plugin, items):
        super().__init__(plugin, items)
        self._cover = None

    def remove(self, item_id: str) -> tuple[str, ...]:
        path = super().remove(item_id)
        empty = [did for did in reversed(path)
                 if did in self._dirs and not self._members.get(did)]
        if not empty:
            return path
        if self._cover is not None:            # 挑覆盖最小的删 —— 尽量少破 D2
            empty.sort(key=lambda did: len(self._cover(self._dirs[did].payload)))
        did = empty[0]
        par = self._dirs[did].parent
        if par is not None:
            self._children[par] = tuple(
                c for c in self._children.get(par, ()) if c != did)
        self._dirs.pop(did, None)
        return path


_DEL_MUTANTS: dict[str, type] = {
    "D1": _NoLedger, "D2": _DropChild, "D3": _GhostMember,
    "D4": _OffPath, "D5": _Tamper, "D6": _DeleteDir,
}


def _del_arm(cls, which, nodes, edges, cover, frac: int = 3) -> dict:
    """建内核 ⇒ 删最后 `1/frac` 的项 ⇒ 返回六条检查的读数。

    ⚠️ 逐项删、每步都查 —— 取「**任意一步触发的最大违规**」。
       一条检查要能红，一次就够；取最大是为了让「擦肩而过」也算数。
    """
    kernel = _del_build(cls, which, nodes, edges)
    if hasattr(kernel, "_cover"):
        kernel._cover = cover
    before_dirs = {d.did for d in kernel.all_directions()}
    mem_before = {did: set(m) for did, m in kernel._members.items()}
    lines_before = _ledger_lines(kernel.ledger)

    out = {"D1": 0, "D2": 0, "D3": 0, "D4": 0, "D5": 0, "D6": 0}
    for v in sorted(nodes)[len(nodes) * (frac - 1) // frac:]:
        path = kernel.remove(v)
        out["D1"] += len(_d1_empty_needs_record(kernel))
        out["D2"] = max(out["D2"], _d2_cover_leak(kernel, cover))
        out["D3"] = max(out["D3"], _d3_soundness(kernel, cover))
        out["D4"] += len(_d4_change_within_cone(mem_before, kernel, path))
        out["D5"] += len(_d5_append_only(lines_before, kernel))
        out["D6"] += len(_d6_states_separable(kernel, before_dirs))
        # 每步之后刷新快照：下一条的「变动」是相对**上一步**
        mem_before = {did: set(m) for did, m in kernel._members.items()}
        lines_before = _ledger_lines(kernel.ledger)
    return out


def test_deletion_path() -> None:
    """★ `§10.2 C` 删除流程的六条检查 `D1–D6` —— 每条都要能红。

    ## 为什么是六条、为什么每条都要配对照

    设计文档 §10.2 C 把六条写在**同一条流程**上，而它们各自守的是不同的东西：

        D1  空方向必须带失效记录        守卫 `remove()` 的第 ② 件（记账）
        D2  删除后 `覆盖(父) ⊆ ∪覆盖(子)` `B18` 的删除路径版本
        D3  删除后 `members(父) ⊆ 覆盖(父)` `B16` 的删除路径版本
        D4  变动集合 ⊆ `Cone(x)`         `B8` 的删除路径版本
        D5  账本只增不改                 `B9` 的删除路径版本
        D6  「已失效」与「从来没存在过」分得开  `§K4`

    ⚠️ **`D2` / `D3` 在真 `Kernel` 上按构造恒绿** —— 删除既不碰 `payload`
       （`§K4` 不可变）也不碰语料，`覆盖` 两边都不动。所以它们不是「会开火的判据」，
       是**守卫**：谁把 `remove()` 改成会动覆盖（比如采纳 `③ 向上收缩父 payload`），
       它们就开火。读数见 `ldv/MEASUREMENTS.md` 结果十一。

    ⚠️ **`D6` 的对照会带红 `D2`** —— 真删方向时，被删方向若曾覆盖东西，
       父的覆盖就漏了。这是**真事实**（两条裁决耦合，见 §10.2 C「三条处置互相关联」），
       测试显式记录它，不当噪声盖掉。

    ## 读数（随仓库 36 项语料，删最后 1/3）

        方向      基线   D1对照  D2对照  D3对照  D4对照  D5对照  D6对照
        keyset    全 0    31      13       1       1       1     22（另带 D2=3）
        reach     全 0   104       9       1       1       1     78（另带 D2=9）
        sequence  全 0     1      25       1       1       1      1（另带 D2=1）
    """
    loaded = load()
    if loaded is None:
        ok("★ [删除路径] 语料在（这条测试要真语料，缺了就报「红」不是「过」）",
           False, "找不到语料")
        return
    nodes, edges, _ = loaded

    for which in ("keyset", "reach", "sequence"):
        cover = coverage_of(which, nodes, edges)
        base = _del_arm(Kernel, which, nodes, edges, cover)
        ok(f"★ [删除路径·{which}] 基线：`D1–D6` **全绿**（真 `Kernel.remove` 干净）",
           all(v == 0 for v in base.values()), f"基线不干净：{base}")

        for code, cls in _DEL_MUTANTS.items():
            shot = _del_arm(cls, which, nodes, edges, cover)
            ok(f"★ [删除路径·{which}] {code} 对照 `{cls.__name__}`："
               f"基线绿、注入红（否则这条是空转）",
               base[code] == 0 and shot[code] > 0,
               f"基线={base[code]} 注入={shot[code]}")
            others = {k: v for k, v in shot.items() if k != code and v > 0}
            print(f"    · {which:8s} {code} ← {cls.__name__:12s} "
                  f"基线 {base[code]} → 注入 {shot[code]}"
                  + (f"；另带红 {others}（D6 对照与 D2 耦合，见 docstring）"
                     if others else "；只此一条红"))


# ═══ 持久化（§10.2 D 的 P6） ═════════════════════════════════════════════════
#
# `§10.2 D` 的裁决：**持久化层只认 `_children` 为权威边**，
# `parent` / `witness` **从 `_children` 重算，不独立存**。
#
# 四条判据 + 两个「错一处」的对照。⚠️ 对照的**关键性质**：
#   两个对照在**未漂移**的存档上产出**完全相同**的结果 —— 它们**只在漂移后才分得开**。
#   所以「读回来对」这一条**单独不足以**证明只存了一份；
#   注入必须造出**漂移的存档**。

def _persist_build(cls, which, nodes, edges, locks=None):
    from ldv.core.locking import LockedKernel
    from ldv.plugins.reach import ReachPlugin
    from ldv.plugins.sequence import SequencePlugin

    if which == "keyset":
        plug = KeysetPlugin()
        root = plug.merge([])
    elif which == "reach":
        plug = ReachPlugin(edges, traverse_budget=10_000)
        root = frozenset(nodes)
    elif which == "sequence":
        plug = SequencePlugin(sequences(nodes, edges))
        root = frozenset({()})
    else:
        raise ValueError(which)
    assert cls is not LockedKernel
    k = cls(plug, items(nodes))
    k.build(root)
    for nid in sorted(nodes):
        k.insert(nid)
    k.remove(sorted(nodes)[0])          # 让删除路径的账也进存档
    return k, plug


def _drift(data: dict, how: str) -> dict:
    """把一份**带派生边**的存档弄漂 —— `how` 是漂多少。

    ⚠️ 这里漂的是**派生边**（`parent` / `witness`），**不动权威边**（`子`）。
    那正是「各存一份」的失效形态：两份**曾经**一致，后来只改了一份。
    """
    rows = [r for r in data["方向"] if r.get("parent") is not None]
    if not rows:
        return data
    if how == "全量":
        rows = rows
    elif how == "一处":
        rows = rows[len(rows) // 2:len(rows) // 2 + 1]
    else:
        raise ValueError(how)
    first = data["方向"][0]["did"]
    for r in rows:
        r["parent"] = first
        r["witness"] = [first]
    return data


def test_persistence() -> None:
    """★ `§10.2 D` 的 **P6**：落盘只认权威边，派生边**读回时现算**。

    七条断言：

      1. **往返逐字相同**（三个方向 × 批建 + 删一项）—— `from_dict(to_dict(k))` 的内核
         与原件在 `_fingerprint` 上**逐项相等**
      2. **存档里没有派生边** —— `方向` 的每一行**不含** `parent` / `witness`
      3. **派生边可由权威边重算** —— 与内存里的 `parent` / `witness` 逐项相符
      4. **存档幂等** —— 同一份结构产出**同一份字节**（`canon` 定序，不是 `repr`）
      5. ★ **注入：漂移的存档** —— 权威 loader 读回**仍等于原件**（绿）
      6. ★ **对照：信任派生边的 loader** —— 同一份漂移存档 ⇒ 读回**不等于**原件（红）
      7. ★ **反向判据**：**未漂移**时两个 loader **同输出** ⇒ 对照**只在漂移后分得开**
         （防「这条判据其实只是复述了 loader 的实现」）
    """
    import json

    from ldv.checks._fixtures import load
    from ldv.core import persist as P
    from ldv.core.kernel import Kernel as _K

    loaded = load()
    if loaded is None:
        PASS.append("持久化（跳过：语料目录不在）")
        return
    nodes, edges, _ = loaded

    whichs = ("keyset", "reach", "sequence")

    # --- ①②③④ 基线 ---------------------------------------------------------
    for which in whichs:
        k, plug = _persist_build(_K, which, nodes, edges)
        data = P.to_dict(k)
        k2 = P.from_dict(json.loads(json.dumps(data)), plug)

        ok(f"★ [P6] {which}：**往返逐字相同**（方向/子/成员/锥/账本/使用/编号逐项相等）",
           P._fingerprint(k) == P._fingerprint(k2),
           f"方向 {len(k.all_directions())}｜账本 {len(k.ledger)} 条")

        leaked = [r["did"] for r in data["方向"] if "parent" in r or "witness" in r]
        ok(f"★ [P6] {which}：存档的 `方向` 里**没有** `parent` / `witness`"
           "（有它就等于「各存一份」）", not leaked, f"{leaked[:3]}")

        derived = P.derive_parents({p: tuple(v) for p, v in data["子"].items()},
                                   data["根"])
        bad = [d.did for d in k.all_directions() if derived.get(d.did) != d.parent]
        bad += [d.did for d in k.all_directions()
                if d.witness != (() if d.parent is None else (d.parent,))]
        ok(f"★ [P6] {which}：**派生边可由权威边重算**（`parent` / `witness` 逐项相符）",
           not bad, f"不符 {bad[:3]}")

        s1 = json.dumps(P.to_dict(k), sort_keys=True)
        s2 = json.dumps(P.to_dict(P.from_dict(json.loads(s1), plug)), sort_keys=True)
        ok(f"★ [P6] {which}：**存档幂等**（同一份结构 ⇒ 同一份字节）",
           s1 == s2, f"{len(s1)} 字节")
        print(f"    · {which:8s} 往返 ✓｜存档 {len(s1):>7} 字节"
              f"｜派生边 {len(leaked)} 行｜重算不符 {len(bad)}｜幂等 {s1 == s2}")

    # --- ⑤⑥⑦ 漂移注入 + 对照 ------------------------------------------------
    for which in whichs:
        k, plug = _persist_build(_K, which, nodes, edges)
        want = P._fingerprint(k)

        for how in ("全量", "一处"):
            drifted = _drift(P._to_dict_storing_derived(k), how)
            a = P.from_dict(json.loads(json.dumps(drifted)), plug)
            b = P._from_dict_trusting_derived(json.loads(json.dumps(drifted)), plug)
            ok(f"★ [P6] {which}·{how}漂移：**权威 loader 读回仍等于原件**"
               "（它根本不看那两栏）", P._fingerprint(a) == want,
               "漂了派生边也不该动结构")
            ok(f"★★ [P6] {which}·{how}漂移：**信任派生边的 loader 读回 ≠ 原件**"
               "（这条就是「各存一份」的失效形态）", P._fingerprint(b) != want,
               f"漂了 {how}")
            print(f"    · {which:8s} {how}漂移：权威 loader "
                  f"{'==' if P._fingerprint(a) == want else '≠'} 原件"
                  f"｜双份 loader {'==' if P._fingerprint(b) == want else '≠'} 原件"
                  + ("  ← ★ 对照在这里分开了" if P._fingerprint(b) != want else ""))

        # ⑦ 反向判据：未漂移时两者**同输出**
        clean = P._to_dict_storing_derived(k)
        a0 = P.from_dict(json.loads(json.dumps(clean)), plug)
        b0 = P._from_dict_trusting_derived(json.loads(json.dumps(clean)), plug)
        ok(f"★ [P6] {which}：**未漂移**时两个 loader **同输出**"
           "（⇒ 对照只在漂移后分得开，光测「读回来对」是测不出「只存了一份」的）",
           P._fingerprint(a0) == P._fingerprint(b0) == want,
           "未漂移 ⇒ 两条路都对")

    # --- ⑧ 权威边自相矛盾 ⇒ 当场报错，不静默取一个 ---------------------------
    k, plug = _persist_build(_K, "keyset", nodes, edges)
    data = P.to_dict(k)
    parents = [p for p, kids in data["子"].items() if kids]
    victim = data["子"][parents[1]][0]
    data["子"][parents[0]] = list(data["子"][parents[0]]) + [victim]
    raised = False
    try:
        P.from_dict(json.loads(json.dumps(data)), plug)
    except P.PersistenceError:
        raised = True
    ok("★★ [P6] **权威边不是树**（同一个子两个父）⇒ `from_dict` 当场报错，"
       "不静默取一个 —— 「静默取一个」与「这份存档本来是好的」在读回的结构上长得一样",
       raised, "改 `子` 之后 DAG 存档")

    # --- ⑨ 格式号不符 ⇒ 报错 -------------------------------------------------
    bad_fmt = dict(P.to_dict(k))
    bad_fmt["格式"] = "ldv-kernel/0"
    raised = False
    try:
        P.from_dict(bad_fmt, plug)
    except P.PersistenceError:
        raised = True
    ok("★ [P6] 格式号不符 ⇒ 报错（不猜、不降级）", raised, "格式号改成 0")

    # --- ⑩ 派生状态不落盘：`reach` 的缓存，读回后代价读数逐项相同 -------------
    k, plug = _persist_build(_K, "reach", nodes, edges)
    before = {d.did: [plug.penalty(d, k.items[i]) for i in sorted(k.members_of(d))]
              for d in k.all_directions()}
    k2 = P.from_dict(json.loads(json.dumps(P.to_dict(k))), plug)
    after = {d.did: [plug.penalty(d, k2.items[i]) for i in sorted(k2.members_of(d))]
             for d in k2.all_directions()}
    ok("★ [P6] `reach` 的缓存**不落盘**（派生状态），但读回后 `代价` **逐项相同**"
       " —— 同一条纪律在插件那一层的实例",
       before == after, f"{len(before)} 个方向")

    # --- ⑪ ★ P6 是 P1–P5 的**兑现**：读回的内核也要过 `B13` -------------------
    #
    # `B13`（含 P4 树性）只保证**内存里**两条边不打架。落盘/读回是**第二个入口** ——
    # 一份漂移的存档读回来，两条边就打架了，而「检查还在、还绿」正是 `§10.2 D`
    # 那句 ⚠️ 说的症状。所以这里拿 `B13` 去量**读回来的**内核。
    from ldv.checks._framework import Report
    from ldv.checks.structure import b13_rank_well_founded

    for which in whichs:
        k, plug = _persist_build(_K, which, nodes, edges)
        clean_loaded = P.from_dict(json.loads(json.dumps(P.to_dict(k))), plug)
        rc = Report(plugin=which, expects=("B13",))
        b13_rank_well_founded(clean_loaded, rc)

        drifted = _drift(P._to_dict_storing_derived(k), "一处")
        trust = P._from_dict_trusting_derived(json.loads(json.dumps(drifted)), plug)
        rt = Report(plugin=which, expects=("B13",))
        b13_rank_well_founded(trust, rt)

        ok(f"★★ [P6] {which}：**读回的内核也要过 `B13`**（树性在第二个入口上仍成立）",
           rc.assertions[0].result is Tri.YES,
           f"B13 = {rc.assertions[0].result}；{rc.assertions[0].detail[:60]}")
        ok(f"★★ [P6] {which}：**漂移存档 + 信任派生边的 loader ⇒ `B13` 报「否」**"
           "（「检查还在、还绿」正是这里的反例 —— 它这次没绿）",
           rt.assertions[0].result is Tri.NO,
           f"B13 = {rt.assertions[0].result}；{rt.assertions[0].detail[:60]}")
        print(f"    · {which:8s} 读回后 B13：只认权威边 {rc.assertions[0].result}"
              f"｜信任派生边 {rt.assertions[0].result}"
              f"（{str(rt.assertions[0].detail)[:40]}…）")


# ═══ 并发（§10.2 D 的 P8） ═══════════════════════════════════════════════════
#
# `§10.2 D` 的裁决：**锁沿路径局部化，根是公共争用点**。
#
# ⚠️ 这里的判据守的是**作用域**，不是「线程安全」。两件事必须分开：
#   一个把锁的范围搞对、却漏了共享状态的实现，与一个真的安全的实现，
#   在「作用域」这个读数上**长得一模一样**。

def _lock_scope(k, fn, *a):
    """跑一次写操作，读**实际拿过**的锁集合（不是「应该拿的」）。"""
    k.locks.taken.clear()
    fn(*a)
    return {did for _, did in k.locks.taken}


def _mutex_broken(locks, key: str = "D0", timeout: float = 0.4) -> bool:
    """**确定性**地测互斥：用 barrier 逼两个线程**同时**想进同一个临界区。

        正确锁   第一个进去、卡在 barrier 上、超时；第二个被挡在外面
                 ⇒ 两个都超时 ⇒ 返回 False（互斥成立）
        空锁     两个都进去 ⇒ barrier 放行 ⇒ 返回 True（互斥被破）

    ⚠️ 为什么**不用**并发压力测试：实测（`outputs/_probe_locks.py` ⑤）
       **空锁**在 8 线程 × 120 项的压力下也**全绿** —— 临界区太短、GIL 帮了忙。
       一个「空锁与真锁长得一样」的测法，测不出任何东西。
    """
    import threading

    both: list[bool] = []
    barrier = threading.Barrier(2, timeout=timeout)

    def worker() -> None:
        with locks.guard(key):
            try:
                barrier.wait()
                both.append(True)
            except threading.BrokenBarrierError:
                pass

    ts = [threading.Thread(target=worker) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return bool(both)


def test_lock_scope() -> None:
    """★ `§10.2 D` 的 **P8**：锁沿**路径**局部化。

    五条断言（三个方向各一遍）：

      1. **作用域 = 锥**（`insert` / `remove` 拿的锁**恰好**是它走过的路径）
      2. **根是唯一公共争用点**（所有锥的**公共**交 == `{根}`）
      3. **与扇出无关**（对照：扇出锁的集合**随扇出变** ⇒ 红）
      4. ★ **互斥**（barrier 探针）：真锁绿、**空锁红**（已知答案）
      5. ★ **对照**：全局锁 / 扇出锁 ⇒ 判据 1、2 红（真耦合，见下）
    """
    from ldv.checks._fixtures import load
    from ldv.core.kernel import Kernel as _K
    from ldv.core.locking import FanoutLocks, GlobalLocks, LockedKernel, NoLocks

    loaded = load()
    if loaded is None:
        PASS.append("并发（跳过：语料目录不在）")
        return
    nodes, edges, _ = loaded
    ids = sorted(nodes)
    whichs = ("keyset", "reach", "sequence")

    def build_locked(which, locks=None):
        from ldv.plugins.reach import ReachPlugin
        from ldv.plugins.sequence import SequencePlugin

        if which == "keyset":
            plug = KeysetPlugin()
            root = plug.merge([])
        elif which == "reach":
            plug = ReachPlugin(edges, traverse_budget=10_000)
            root = frozenset(nodes)
        else:
            plug = SequencePlugin(sequences(nodes, edges))
            root = frozenset({()})
        k = LockedKernel(plug, items(nodes), locks=locks)
        k.build(root)
        for nid in ids:
            k.insert(nid)
        return k

    # --- ①②③ 基线 -----------------------------------------------------------
    for which in whichs:
        k = build_locked(which)
        bad_i = [x for x in ids if _lock_scope(k, k.insert, x) != set(k.cone(x))]
        ok(f"★ [P8] {which}：**作用域 = 锥**（`insert` 拿的锁恰好是它走过的路径）",
           not bad_i, f"不符 {len(bad_i)}/{len(ids)}：{bad_i[:2]}")

        # ⚠️ `remove` 会真的把项删掉 ⇒ 另起一个内核；而且锥要在**删之前**取
        #    （`remove` 结尾会 `_path.pop`，删完再问 `cone()` 是空元组）。
        kr = build_locked(which)
        bad_r = []
        for x in ids[:8]:
            want = set(kr.cone(x))
            if _lock_scope(kr, kr.remove, x) != want:
                bad_r.append(x)
        ok(f"★ [P8] {which}：**作用域 = 锥**（`remove` 同）",
           not bad_r, f"不符 {len(bad_r)}/8：{bad_r[:2]}")

        cones = [set(k.cone(x)) for x in ids]
        cones = [c for c in cones if c]
        common = set.intersection(*cones) if cones else set()
        ok(f"★ [P8] {which}：**根是唯一公共争用点**（所有锥的公共交 == {{根}}）",
           common == {k.root.did}, f"公共交 = {sorted(common)}，根 = {k.root.did}")

        path_sizes = [len(_lock_scope(k, k.insert, x)) for x in ids]
        k2 = build_locked(which)
        k2.locks = FanoutLocks(
            lambda did, _k=k2: [c.did for c in _k.children_of(_k.direction(did))])
        fan_sizes = [len(_lock_scope(k2, k2.insert, x)) for x in ids]
        ok(f"★ [P8] {which}：作用域**与扇出无关** —— 路径锁 max {max(path_sizes)}"
           f" < 扇出锁 max {max(fan_sizes)}（扇出不常数 ⇒ 扇出锁的集合无界）",
           max(path_sizes) < max(fan_sizes),
           f"路径 {max(path_sizes)} vs 扇出 {max(fan_sizes)}")
        print(f"    · {which:8s} 作用域 = 锥（insert {len(ids)}/{len(ids)}、"
              f"remove {8 - len(bad_r)}/8）｜公共交 {sorted(common)}"
              f"｜路径锁 max {max(path_sizes)} vs 扇出锁 max {max(fan_sizes)}")

    # --- ④ 互斥（barrier，**确定性**） ---------------------------------------
    from ldv.core.locking import PathLocks as _PL
    ok("★ [P8] **互斥成立**：真锁下两个线程不能同时进同一个方向的临界区"
       "（barrier 逼它们同时进 ⇒ 超时 ⇒ 没进去过）",
       not _mutex_broken(_PL()), "PathLocks")
    ok("★★ [P8] **已知答案对照**：**空锁**下两个线程**同时进去**了 ⇒ 探针会红",
       _mutex_broken(NoLocks()), "NoLocks")
    ok("★ [P8] 全局锁也互斥（它只是**范围太大**，不是没锁）",
       not _mutex_broken(GlobalLocks()), "GlobalLocks")

    # --- ⑤ 对照：全局锁 / 扇出锁 ⇒ 判据 1、2 红 ------------------------------
    #
    # ⚠️ **真耦合**：判据 1（作用域 = 锥）与判据 2（公共交 = 根）在**本批语料上**
    #    由同一个事实推出（锥都从根开头、且没有别的方向在所有锥上）⇒
    #    两个对照会**同时**带红两条。这里**照实打印**，不挑一条来报 ——
    #    一个「只报自己那条」的对照是**被挑选过的**证据。
    for which in whichs:
        for code, mk in (("全局锁", GlobalLocks), ("扇出锁", None)):
            if mk is None:
                k3 = build_locked(which)
                k3.locks = FanoutLocks(
                    lambda did, _k=k3: [c.did for c in _k.children_of(_k.direction(did))])
            else:
                k3 = build_locked(which, locks=mk())
            s1 = [x for x in ids if _lock_scope(k3, k3.insert, x) != set(k3.cone(x))]
            # ⚠️ 这里量的是**锁管理器实际拿的**作用域，**不是**内核的锥 ——
            #    拿内核的锥去比，对照当然还是「公共交 = 根」（锥没变），
            #    判据 2 就永远红不了。**读数要跟读数比。**
            scopes = [_lock_scope(k3, k3.insert, x) for x in ids]
            common = set.intersection(*scopes) if scopes else set()
            ok(f"★ [P8] {which}·{code} 对照：**作用域 ≠ 锥** ⇒ 判据 1 红"
               "（否则判据 1 是空转）", bool(s1), f"不符 {len(s1)}/{len(ids)}")
            ok(f"★ [P8] {which}·{code} 对照：**公共交 ≠ {{根}}** ⇒ 判据 2 红",
               common != {k3.root.did}, f"公共交 {len(common)} 个：{sorted(common)[:3]}")

    # --- ⑥ 反向判据：单线程路径**逐字不变**（钩子默认空操作） -----------------
    k = build_locked("keyset")
    k0 = _K(k.plugin, k.items)
    k0.build(k.plugin.merge([]))
    for nid in ids:
        k0.insert(nid)
    ok("★ [P8] **反向判据**：`LockedKernel`（锥上拿锁）与 `Kernel`（空钩子）"
       "跑出来**逐字相同** —— 加锁不改语义（否则「作用域」这件事是拿正确性换的）",
       [(d.did, d.rank, d.payload, d.parent) for d in k.all_directions()]
       == [(d.did, d.rank, d.payload, d.parent) for d in k0.all_directions()]
       and k.stats() == k0.stats(),
       f"方向 {len(k.all_directions())} vs {len(k0.all_directions())}")


def test_views() -> None:
    """流程 E · 抽象层 —— `core/views.py` 的算法 + `checks/abstraction.py` 的七条判据。

    分八段，**每段都要有已知答案**（本仓库的老纪律：合成图上的已知答案是对照组里
    最便宜的那一档，「跑一遍看看」不构成对照）：

        ① 核心算法   `Q` 与**手推值**逐块比；唯一性；两个 oracle 的已知答案
        ② 三态对照   设计稿 §9 要求的「过 / 红 / 跳过各一例」
        ③ 判据的**独立**性   `§A1` 不被 `B16` 蕴含（否则它只是换个写法）
        ④ 全链路     真内核 + 合成 spec ⇒ `build_views` + 七条判据
        ⑤ 外生门禁   `spec_for` 的三种跳过 + 「声明了却坏了」是**报错**
        ⑥ 闸门自己   `cap` 要在**时间与内存上**都拦得住（实测踩到过挂住）
        ⑦ committed 声明  `view_spec.json` 读得进来、非退化、七条判据全跑
        ⑧ 合成对照   `§A1`–`§A7` 的已知答案（含 `§A6` / `§A7` 的**分支拆分**）

    ⚠️ ⑥ / ⑦ / ⑧ 是**三件不同的事**，都要有：⑥ 验「搜不完时会不会如实报跳过」，
       ⑦ 验「生产里那份声明成不成立」，⑧ 验「判据本身会不会红、会不会**红错地方**」。
       少任何一段，对应的那种缺陷都没有东西守着。
    """
    from ldv.checks._framework import Report
    from ldv.checks.abstraction import (
        VIEW_CODES,
        VIEW_SPEC_PATH,
        ViewSet,
        a1_known_answer,
        a1_soundness,
        a2_stable,
        a3_coarsest,
        a4_category,
        a4_known_answer,
        a5_known_answer,
        a5_ledger,
        a6_branch_split,
        a6_known_answer,
        a6_roundtrip,
        a7_known_answer,
        a7_propagate,
        build_views,
        known_answer_controls,
        ledger_entries,
        load_spec_file,
        reading_ctx,
        spec_for,
        subtree_of,
        view_profile,
        warranted_of,
    )

    def _result(rep, code: str) -> Tri:
        for a in rep.assertions:
            if a.code == code:
                return a.result
        return Tri.UNEXPANDED

    from ldv.core.views import (
        ViewSpec,
        _set_partitions,
        coarser_stable_exists,
        coarsest_stable_refinement,
        partition_of,
        refines,
        restrict_spec,
        stable,
        view_parts,
    )

    # --- ① 核心算法 ---------------------------------------------------------
    U = ("a", "b", "c", "d")
    cases = [
        ("`E = ∅` ⇒ `Q = P`", [U], [], [["a", "b", "c", "d"]]),
        ("`E = {a→b}` ⇒ 只有 `{a}` 能分出来",
         [U], [("a", "b")], [["a"], ["b", "c", "d"]]),
        # ⚠️ `E` 的两条边必须**各往对方的块里指**，两块才都切得开。
        #    `{(a,c), (b,d)}` 两条都朝外 ⇒ `E⁻¹({a,b}) = ∅`、`E⁻¹({c,d}) = {a,b}`
        #    整个包住 `{a,b}` ⇒ **一刀都切不动**，`Q` 就是 `P`。
        #    （这个期望值原来写错了，是这条测试自己抓出来的。）
        ("`P` 两块、`E` 双向各一条 ⇒ 两块都被切开",
         [["a", "b"], ["c", "d"]], [("a", "c"), ("c", "a")],
         [["a"], ["b"], ["c"], ["d"]]),
        ("`P` 两块、`E` 只连一条 ⇒ 只切一刀",
         [["a", "b"], ["c", "d"]], [("a", "c")], [["a"], ["b"], ["c", "d"]]),
        ("`E` 只朝块内指 ⇒ 一刀都切不动，`Q = P`",
         [["a", "b"], ["c", "d"]], [("a", "c"), ("b", "d")],
         [["a", "b"], ["c", "d"]]),
    ]
    for label, p, e, want in cases:
        spec = ViewSpec(universe=U, partition=tuple(frozenset(b) for b in p),
                        relation=frozenset(e))
        q = coarsest_stable_refinement(spec)
        got = [sorted(b) for b in q]
        ok(f"★ [E] 最粗稳定细化：{label}", got == want, f"实测 {got}，期望 {want}")
        ok(f"★ [E] 它**稳定**且是 `P` 的细化：{label}",
           stable(spec, q)[0] and refines(spec, q))
        ok(f"★ [E] 它**没有更粗的稳定划分**：{label}",
           coarser_stable_exists(spec, q)[0] is False)

    # 唯一性 —— 「视图不用挑」这句话的**全部**依据（设计稿 §3）
    import random as _rnd
    _rng = _rnd.Random(11)
    bad_u = []
    for _ in range(120):
        n = _rng.randint(1, 9)
        u = tuple(f"x{i}" for i in range(n))
        perm = list(u)
        _rng.shuffle(perm)
        p, i = [], 0
        while i < n:
            k = _rng.randint(1, min(3, n - i))
            p.append(frozenset(perm[i:i + k]))
            i += k
        e = frozenset((x, y) for x in u for y in u if _rng.random() < 0.25)
        s1 = ViewSpec(universe=u, partition=tuple(p), relation=e)
        s2 = ViewSpec(universe=tuple(_rng.sample(list(u), n)),
                      partition=tuple(_rng.sample(p, len(p))), relation=e)
        q1, q2 = coarsest_stable_refinement(s1), coarsest_stable_refinement(s2)
        if q1 != q2 or not stable(s1, q1)[0] or not refines(s1, q1):
            bad_u.append((q1, q2))
    ok("★★ [E] **唯一性**：打乱 `P` 的块序 / `U` 的序 ⇒ 逐字相同的 `Q`"
       "（120 组随机；「视图不用挑」的全部依据）", not bad_u, f"{len(bad_u)} 组不一致")

    # 离散划分**总是**稳定 —— `§A3` 注入用的那一档，是个小定理
    spec = ViewSpec(universe=U, partition=(frozenset(U),), relation=frozenset())
    disc = tuple(frozenset({x}) for x in U)
    ok("★ [E] **离散划分总是稳定**（单元素块对任何 `E⁻¹(B)` 都整个在内或整个在外）",
       stable(spec, disc)[0] is True)
    ok("★ [E] 但它**通常不是最粗** ⇒ `§A3` 能红（注入就吃这一条）",
       coarser_stable_exists(spec, disc)[0] is True)

    ok("★ [E] `_set_partitions` 的规模是 **Bell 数**（不是 `2^n`）—— "
       "写成子集枚举会把同一个粗化数很多遍，而**重复枚举看不出来**",
       [len(list(_set_partitions(n))) for n in range(1, 8)]
       == [1, 2, 5, 15, 52, 203, 877])

    # `ViewSpec` 的三条校验 —— **每一条都要能抛**，否则「声明错了」会被静默吞掉
    raises("★ [E] `P` 不是 `U` 的划分 ⇒ `ViewSpec` 抛", lambda: ViewSpec(
        universe=U, partition=(frozenset({"a", "b"}),), relation=frozenset()))
    raises("★ [E] `P` 的块重叠 ⇒ `ViewSpec` 抛", lambda: ViewSpec(
        universe=U, partition=(frozenset({"a", "b"}), frozenset({"b", "c"}),
                               frozenset({"d"})), relation=frozenset()))
    raises("★ [E] `E` 的端点不在 `U` 里 ⇒ `ViewSpec` 抛", lambda: ViewSpec(
        universe=U, partition=(frozenset(U),), relation=frozenset({("a", "z")})))
    raises("★ [E] `U` 里有重复元素 ⇒ `ViewSpec` 抛", lambda: ViewSpec(
        universe=("a", "a"), partition=(frozenset({"a"}),), relation=frozenset()))

    # --- ② 三态对照（设计稿 §9 要求的「过 / 红 / 跳过 各一例」） ----------------
    fails = known_answer_controls()
    ok("★★ [E] **已知答案对照组**：合成图上三态各一例，逐条对上",
       not fails, "；".join(fails))
    ok("★★ [E] `§A1` 三态：基线 过、掐掉一项 红",
       a1_known_answer(False) is Tri.YES and a1_known_answer(True) is Tri.NO)
    _rep = Report(plugin="(合成)")
    a1_soundness(ViewSet(spec=spec, q=(), views=()), _rep)
    ok("★ [E] `§A1` 视图集合为空 ⇒ **跳过**（不是过）",
       _result(_rep, "A1") is Tri.UNEXPANDED)

    # --- ③ `§A1` **不被** `B16` 蕴含 ----------------------------------------
    #
    # 这是这一层最容易做成「换个写法」的地方：若「具体化」=「把成员的覆盖并起来」，
    # `§A1` 就**恒同真**（`B16` 全绿 ⇒ 它全绿），一条不提供独立信息的判据。
    # 它的实际内容是「**合并**（§I2）之后还盖不盖得住」——
    # 合并是**另一个函数**，它可能把覆盖收窄到装不下某个成员。
    nodes, edges, _ = load()
    kernel, plug = build_keyset(nodes)
    cover = coverage_of("keyset", nodes, edges)
    dirs = [d for d in kernel.all_directions() if kernel.members_of(d)][:4]
    ids = [d.did for d in dirs]
    spec4 = ViewSpec(universe=tuple(ids), partition=(frozenset(ids),),
                     relation=frozenset({(ids[0], ids[1]), (ids[2], ids[3])}))

    class _MergeUnionsReq(KeysetPlugin):
        """注入：`合并` 取 `req` 的**并**（正确做法是取**交**）⇒ 覆盖被**收窄**。"""

        def merge(self, ds):  # noqa: ANN001, ANN201
            if not ds:
                return frozenset(), frozenset()
            req = frozenset().union(*[frozenset(d.payload[0]) for d in ds])
            forb = frozenset().union(*[frozenset(d.payload[1]) for d in ds])
            return (req, forb)

    rep_good, rep_bad = Report(plugin="(合成)"), Report(plugin="(合成)")
    a1_soundness(build_views(kernel, spec4, cover, plug), rep_good)
    a1_soundness(build_views(kernel, spec4, cover, _MergeUnionsReq()), rep_bad)
    b16_rep = Report(plugin="(合成)")
    from ldv.checks.coverage import b16_members_covered
    b16_members_covered(kernel, cover, b16_rep, path="§A1 独立性对照")
    ok("★★ [E] `§A1` 的**独立性**：规范构造下 过；`合并` 取错（并而非交）⇒ **红**",
       _result(rep_good, "A1") is Tri.YES and _result(rep_bad, "A1") is Tri.NO)
    ok("★★ [E] 而**同一个注入下 `B16` 仍然是绿的** ⇒ `§A1` **不是** `B16` 的换写法"
       "（它的内容是「合并之后还盖得住」，不是「成员 ⊆ 自己的覆盖」）",
       _result(b16_rep, "B16") is Tri.YES)

    # --- ④ 全链路：真内核 + 合成 spec ----------------------------------------
    spec4b = ViewSpec(universe=tuple(ids),
                      partition=(frozenset(ids[:2]), frozenset(ids[2:])),
                      relation=frozenset({(ids[0], ids[2])}))
    vs = build_views(kernel, spec4b, cover, plug)
    rep = Report(plugin="(视图)", expects=VIEW_CODES)
    a1_soundness(vs, rep)
    a2_stable(spec4b, vs.q, rep)
    a3_coarsest(spec4b, vs.q, rep)
    # ⚠️ 这里**故意**给空的读数声明：要验的正是「一条读数都没声明 ⇒ **跳过**，
    #    **不许默认成 distributive**」（设计稿 §10 停止条件 2）。
    a4_category(view_parts(vs.q, lambda d: subtree_of(kernel, d)),
                reading_ctx(kernel, spec4b), [], rep)
    a5_ledger(ledger_entries(kernel, spec4b), vs,
              lambda did, item: warranted_of(kernel, did, item),
              frozenset(kernel.items), rep)
    with tempfile.TemporaryDirectory() as _td:
        a6_roundtrip(vs, kernel, cover, plug, rep, path=Path(_td) / "views.json")
    ok("★ [E] 全链路（真内核 + 合成 spec）：`build_views` 出来的 `Q` 上 "
       "`§A1`/`§A2`/`§A3`/`§A6` 全绿",
       all(_result(rep, c) is Tri.YES for c in ("A1", "A2", "A3", "A6")),
       "；".join(a.line() for a in rep.assertions))
    ok("★★ [E] 而 `§A4`/`§A5` 在这条链路上**跳过**（没声明读数、账为空）—— "
       "**跳过不是红，但也不是过**：判据的适用范围由「它在这条方向上有没有内容」"
       "决定。默认成「过」的话，「空转」与「通过」就再也分不开了",
       all(_result(rep, c) is Tri.UNEXPANDED for c in ("A4", "A5")),
       "；".join(a.line() for a in rep.assertions))
    prof = view_profile(vs)
    print(f"    · 全链路读数：{prof['方向数']} 个方向 ⇒ {prof['视图数']} 张视图，"
          f"最大一块 {prof['最大块']}｜具体化 {prof['具体化']} vs 成员 {prof['成员']}")

    # --- ⑤ 外生 spec 的门禁：三种跳过 + 「声明了却坏了」是**报错** --------------
    corpus = {"项数": len(nodes), "边数": sum(len(v) for v in edges.values())}
    s, why = spec_for({}, "keyset", corpus)
    ok("★ [E] 外生项**未声明** ⇒ 跳过（不是猜一个）", s is None and "未声明" in why)
    s, why = spec_for({"语料": {"项数": 1, "边数": 1}, "方向": "keyset"}, "keyset", corpus)
    ok("★ [E] 指纹对不上 ⇒ 跳过，且说清是**换了语料**", s is None and "另一份语料" in why)
    s, why = spec_for({"语料": corpus, "方向": "reach"}, "keyset", corpus)
    ok("★ [E] 声明写的是**别的方向** ⇒ 跳过", s is None and "另一条方向" in why)
    raises("★★ [E] **声明了却坏了**（`P` 不是划分）⇒ **报错**，不是跳过 ——"
           "「写错了」与「还没写」必须分得开，否则写错的声明看起来像没写",
           lambda: spec_for({"语料": corpus, "方向": "keyset", "universe": ["a", "b"],
                             "partition": [["a"]], "relation": []}, "keyset", corpus))
    s, why = spec_for({"语料": corpus, "方向": "keyset",
                       "universe": list(ids), "partition": [list(ids)],
                       "relation": [[ids[0], ids[1]]]}, "keyset", corpus)
    ok("★ [E] 声明**齐全** ⇒ 解得出 spec（跳过的那三种之外还有一条能过的路）",
       s is not None and len(s.universe) == len(ids))

    # --- ⑥ 闸门自己：`cap` 必须在**时间与内存上**都拦得住 --------------------
    #
    # ⚠️ 这一段不是「顺手加的保险」：**先物化、再拿物化出来的 `len` 去比上限**
    #    的写法在 `k = 25` 时**不是报跳过，是挂住**（`Bell(25) ≈ 4.6e18`；
    #    `outputs/_probe_cap.py`：20 秒超时、`timeout` 退出码 124）。
    #
    # 「**搜不完**」与「**还在搜**」在只看输出的时候长得一模一样 ——
    # 本仓库一直在防的形状，只不过这一次它长在**闸门自己**身上。
    # 所以判据不能是「跑完再比」，只能是「**先算规模再跑**」。
    import time as _time

    big_u = tuple(f"d{i}" for i in range(25))
    big_spec = ViewSpec(universe=big_u, partition=(frozenset(big_u),),
                        relation=frozenset((f"d{i}", f"d{i + 1}") for i in range(24)))
    big_q = partition_of([frozenset({x}) for x in big_u])
    t0 = _time.monotonic()
    has, why, seen = coarser_stable_exists(big_spec, big_q)
    dt = _time.monotonic() - t0
    ok(f"★★ [E] 闸门：`Bell(25)` 的大组 ⇒ **如实报跳过**（`None`）而不是挂住"
       f"（候选数 {seen}，用时 {dt * 1000:.1f} ms）",
       has is None and "没搜完" in why and dt < 2.0,
       f"has={has}、用时 {dt:.3f}s、说明 {why!r}")
    boundary = []
    for m in (5, 6, 7, 8):
        u = tuple(f"d{i}" for i in range(m))
        sp = ViewSpec(universe=u, partition=(frozenset(u),),
                      relation=frozenset((f"d{i}", f"d{i + 1}") for i in range(m - 1)))
        boundary.append(coarser_stable_exists(
            sp, partition_of([frozenset({x}) for x in u]))[0] is not None)
    ok("★ [E] 闸门边界：`Bell(k) ≤ cap` 时**搜得完并给出结论**"
       "（不是一律跳过 —— 一律跳过与搜不完长得一样）", all(boundary))

    # --- ⑦ committed 的外生声明：`ldv/checks/view_spec.json` ------------------
    #
    # ⚠️ 与 `test_injections` 那三条视图注入**分工不同**，两边都不许省：
    #
    #     注入（自带 `P`/`E`）   验「**判据**能不能红」—— 与配置文件在不在无关
    #     本段（读那个文件）     验「**committed 的声明**成不成立」
    #
    # 只验前者的话，声明文件坏了没人知道：`run_checks` 会把它报成**跳过**，
    # 而「跳过」与「过」在**退出码上都是 0**。
    doc = load_spec_file()
    ok(f"★★ [E] committed 的外生声明在（`{VIEW_SPEC_PATH.name}`）—— "
       "不在的话 `run_checks` 的 `(视图)` 那组**三条全跳过**，而退出码照样 0",
       bool(doc), f"没找到 {VIEW_SPEC_PATH}")
    if doc:
        s, why = spec_for(doc, str(doc.get("方向") or ""), corpus)
        ok("★★ [E] 它解得出来（指纹 / 方向都对得上 —— 否则报的是**跳过**）",
           s is not None, why)
        if s is not None:
            which = str(doc["方向"])
            k2, p2 = build_keyset(nodes)
            cv2 = coverage_of(which, nodes, edges)
            vs2 = build_views(k2, s, cv2, p2)
            rep2 = Report(plugin="(视图)", expects=VIEW_CODES)
            a1_soundness(vs2, rep2)
            a2_stable(s, vs2.q, rep2)
            a3_coarsest(s, vs2.q, rep2)
            a4_category(view_parts(vs2.q, lambda d: subtree_of(k2, d)),
                        reading_ctx(k2, s), doc.get("读数") or [], rep2)
            a5_ledger(ledger_entries(k2, s), vs2,
                      lambda did, item: warranted_of(k2, did, item),
                      frozenset(k2.items), rep2)
            import tempfile as _tf

            with _tf.TemporaryDirectory() as _td:
                a6_roundtrip(vs2, k2, cv2, p2, rep2, path=Path(_td) / "views.json")
            # ★ `§A7` 要一次**结构变动**。与 `run_checks.view_report` **同一处口径**：
            #   把 `universe` 里最后长出来的那个方向拿掉 ⇒ 传播回来。
            old_q2 = coarsest_stable_refinement(
                restrict_spec(s, frozenset({s.universe[-1]})))
            a7_propagate(old_q2, s, rep2)
            ok("★★ [E] committed 声明上**一条红的都没有** —— "
               "有一条常驻的红等于没人再看红",
               all(_result(rep2, c) is not Tri.NO for c in VIEW_CODES),
               "；".join(a.line() for a in rep2.assertions))
            ok("★★ [E] `§A1`–`§A4` + `§A6` + `§A7` 在 committed 声明上**全过**（不是跳过）",
               all(_result(rep2, c) is Tri.YES
                   for c in ("A1", "A2", "A3", "A4", "A6", "A7")),
               "；".join(a.line() for a in rep2.assertions))
            # ⚠️ `§A5` 在批建路径上**本来就该跳过**（账为空）—— 但「跳过」不是免检：
            #   跳过的**理由**必须是「账为空」。理由写错了就说明它跳错了地方，
            #   而那正是「空转与通过长得一模一样」的另一种形态。
            a5 = _result(rep2, "A5")
            a5_detail = next((a.detail for a in rep2.assertions if a.code == "A5"), "")
            ok("★ [E] `§A5` 在 committed 声明上跳过时，理由必须是「账为空」"
               "（批建路径上 `滞留 == 0`、`根覆盖之外 == 0`）—— "
               "跳过 ≠ 通过，但**跳错了地方**也 ≠ 跳过",
               a5 is Tri.YES or "账为空" in a5_detail,
               f"A5={a5}，理由={a5_detail!r}")
            p2p = view_profile(vs2)
            ok("★ [E] committed 声明**非退化**（视图数 < 方向数）—— "
               "两者相等是**读数**不是失败，但那样这一层什么都没压，"
               "`§A3` 也就没有内容可判",
               p2p["视图数"] < p2p["方向数"],
               f"{p2p['方向数']} 个方向 ⇒ {p2p['视图数']} 张视图")
            print(f"    · committed 声明读数：{p2p['方向数']} 个方向 ⇒ "
                  f"{p2p['视图数']} 张视图，最大一块 {p2p['最大块']}｜"
                  f"具体化 {p2p['具体化']} vs 成员 {p2p['成员']}｜"
                  f"读数声明 {len(doc.get('读数') or [])} 条，`§A5` {a5}")

    # --- ⑧ `§A1`–`§A7` 的**合成**对照 ----------------------------------------
    #
    # ⚠️ 与 ⑦ 分工不同，两边都不许省：
    #
    #     ⑦ 读 **committed 声明**（真语料）  验「生产里那份声明成不成立」
    #     ⑧ 用**合成图**（已知答案）          验「判据本身会不会红、会不会红错地方」
    #
    # ⚠️ 合成图上的答案必须是**手推**出来的，不许「跑一遍看看再把结果抄成期望」——
    #    后者只会把实现现在的行为固化下来，包括它的错。
    ok("★ [E] `§A1` 合成对照：具体化掐掉一项 ⇒ 红（没掐 ⇒ 绿）",
       a1_known_answer(False) is Tri.YES and a1_known_answer(True) is Tri.NO,
       f"没掐={a1_known_answer(False)}、掐了={a1_known_answer(True)}")
    a4_fails = a4_known_answer()
    ok("★★ [E] `§A4` 合成对照：三类各一例 + 见证成立/不成立各一例 + "
       "「指不到实现」一例 + 两条跳过 —— 共 9 条**手推**答案",
       not a4_fails, "；".join(a4_fails))
    ok("★ [E] `§A5` 合成对照：方向指不到任何视图 ⇒ 红（指得到 ⇒ 绿）",
       a5_known_answer(False) is Tri.YES and a5_known_answer(True) is Tri.NO,
       f"指得到={a5_known_answer(False)}、指不到={a5_known_answer(True)}")
    ok("★ [E] `§A6` 合成对照：真实现 ⇒ 绿；两个对照实现都上 ⇒ 红",
       a6_known_answer(False) is Tri.YES and a6_known_answer(True) is Tri.NO,
       f"真实现={a6_known_answer(False)}、对照对={a6_known_answer(True)}")
    a6_fails = a6_branch_split()
    ok("★★ [E] `§A6` **分支拆分**（逐分支实测，不是从散文里认）："
       "只上 `to_d` 时 ② 亮而 ③ **不亮** ⇒「漂移只有在两个对照都上时"
       "才可能发生」这句话是**实测**的；而 ③ 不亮就意味着"
       "**只用 `to_d` 的注入会让「读回后 §A1 仍成立」永远没被验过**",
       not a6_fails, "；".join(a6_fails))
    a7_fails = a7_known_answer()
    ok("★★ [E] `§A7` 合成对照（四条路 + 两条自检，**逐分支**断言）："
       "真传播 ①② 都不亮；`propagate_naive`（挂到父块）**① 亮**；"
       "全量重算（冒充传播）**② 亮** —— 两者**互不替代**；"
       "外加「传播 ⊑ 重算」那条**定理**的自检",
       not a7_fails, "；".join(a7_fails))


def main() -> int:
    for fn in (test_tri, test_loader, test_kernel, test_exposure_model, test_selfopt,
               test_sequence, test_flows, test_emergence, test_divergence,
               test_equivalence, test_rebuild, test_out_of_scope, test_stay_at_parent,
               test_cover_leak_baseline, test_cover_oracle_transparency,
               test_b3_reduction_premise, test_reach_cache_premise,
               test_reach_cache_transparency, test_tree_premise,
               test_deletion_path, test_persistence, test_lock_scope,
               test_query_hit_items, test_views):
        fn()
    total = len(PASS) + len(FAIL)
    for f in FAIL:
        print(f"  [红] {f}")
    print(f"共 {total} 条：过 {len(PASS)}，红 {len(FAIL)}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
