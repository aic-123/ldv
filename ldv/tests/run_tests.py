"""单元测试 —— 内核不变量 / 三态 / 语料形状 / 自优化边界。

    跑法：python -m ldv.tests.run_tests

零第三方依赖（不用 pytest）—— 与 dce 的 `tests/run_tests.py` 同形：
自己数，自己报，**过 / 红分开计数**。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ldv.checks._fixtures import build_keyset, build_reach, build_sequence, load  # noqa: E402
from ldv.core import selfopt  # noqa: E402
from ldv.core.direction import EVENT_UNSPLITTABLE, ORIGIN_EXOGENOUS  # noqa: E402
from ldv.core.kernel import (  # noqa: E402
    PROPENSITY_FLOOR,
    Kernel,
    Query,
    _did_order,
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
    ok("真语料读得进", len(nodes) == 36, f"读到 {len(nodes)} 个")
    ok("悬挂边为空", not dangling, f"悬挂 {sorted(dangling)}")
    ok("边数与事实相符", sum(len(v) for v in edges.values()) == 47)


# ═══ 内核 ════════════════════════════════════════════════════════════════════

def test_kernel() -> None:
    loaded = load()
    if loaded is None:
        return
    nodes, edges, _ = loaded
    k, plug = build_keyset(nodes)

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

    # 倾向：内核填，均匀曝光
    d = k.all_directions()[0]
    rec = k.record_usage(d, {"outcome": 1.0})
    ok("内核填了倾向权重", rec is not None and 0.0 < rec.propensity <= 1.0)
    ok("「无信号」不产记录", k.record_usage(d, {"outcome": None}) is None)

    # reach：偏函数，拒绝遍历时返回未展开
    kr, pr = build_reach(nodes, edges, traverse_budget=1)
    rres = kr.query(Query(ideal=frozenset(nodes)))
    ok("reach 超预算 ⇒ 未展开（不是否）", rres.counts["未展开"] > 0, str(rres.counts))


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
    nodes, edges, _ = loaded

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
    ok("A：倾向 = 1/位次（内核按曝光位次填，插件伪造不了）",
       all(abs(r.propensity - max(1.0 / r.extra["position"], PROPENSITY_FLOOR)) < 1e-9
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
    res = kq.query(qs[0])
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
         扇出由插件定的方向（C，k 叉）**允许**在尺度上不同 —— 但要**报出来**
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

        # ③ 尺度：**扇出固定为 2 的方向必须稳**；扇出由插件定的方向只报不判。
        f = prof["全量"]
        scale_ok = all((r["方向数"], r["最大叶容量"]) == (f["方向数"], f["最大叶容量"])
                       for r in rows.values())
        cap_ok = all(r["最大叶容量"] == f["最大叶容量"] for r in rows.values())
        if which in ("keyset", "reach"):
            ok(f"[{which}] 扇出恒为 2 ⇒ 差异**不在尺度**：每个 k 的方向数与叶容量都和全量相同",
               scale_ok,
               f"全量 {f['方向数']}/{f['最大叶容量']}，"
               f"增量 {sorted({(r['方向数'], r['最大叶容量']) for r in rows.values()})}")
        else:
            # ★ 分辨率（叶容量）**仍然必须稳** —— 它是不变量。
            ok(f"[{which}] 扇出由插件定 ⇒ 方向数**允许**变，但**分辨率不许变**"
               f"（每个 k 的最大叶容量都与全量相同）",
               cap_ok,
               f"全量叶容量 {f['最大叶容量']}，"
               f"增量 {sorted({r['最大叶容量'] for r in rows.values()})}")
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
        b17_leaf_is_equivalence_class(kernel, cls, rep)
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
    b17_leaf_is_equivalence_class(base_k, cls_k, rep_b)
    b17_leaf_is_equivalence_class(bad_k, cls_k, rep_i)
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
    b17_leaf_is_equivalence_class(ck, cyc_cls, rep_c)
    ok("★ 非空转：合环图上 B17 **红** —— a/b 互相可达却被劈到两个子方向",
       rep_c.assertions[0].result is Tri.NO, rep_c.assertions[0].detail)
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


def main() -> int:
    for fn in (test_tri, test_loader, test_kernel, test_selfopt, test_sequence,
               test_flows, test_emergence, test_divergence, test_equivalence,
               test_rebuild):
        fn()
    total = len(PASS) + len(FAIL)
    for f in FAIL:
        print(f"  [红] {f}")
    print(f"共 {total} 条：过 {len(PASS)}，红 {len(FAIL)}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
