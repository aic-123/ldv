# -*- coding: utf-8 -*-
"""两个 ldv + 真实问题：成果验收（2026-10-21）。

用户 2026-10-21：「构建两个 ldv 然后用真实问题检查一下成果吧」。

## 这是什么

把已经做出来的东西当验收跑一遍。两个各自独立的 ldv 系统：

    A = ext-wiki-vote        7115 项，维基百科管理员选举投票网络
                            （带派生维度 influence = max(指向节点入度)）
    B = openalex-citations   3907 项，真实论文 + 引用网络（OpenAlex）

用**真实问题**（人会问的那种）过全部三条路：

    ① 人类接口：query / drill / suggest / examples（文件层直跑）
    ② 机器接口：ldv-msg/1 协议，A 与 B 对等互问（文件层 + HTTP 层）
    ③ 沉淀路径：整段交流 → 缓冲区一次性树 → status / 查询 / 显式抛弃

## 三条老实话（边界，写在最前面）

1. **「真实问题 → 键约束」这一步在系统外**。翻译器是检索器层 §8 停止条件 4
   明确**不做**的（自动语义 = 模型代填，与立项根基冲突）。⇒ 本脚本里每条问题
   带一个**显式映射**；映射是人/AI 做的，不是系统做的。系统负责的是：
   映射错了要**拒收**、查不到要**说查不到**、答案要**可本地复算**。
2. **不评判答案内容对不对** —— 那是外生的事。这里只验**结构性质**：
   切分不重不漏（Σ各支 == 答案）、可自检（verify_result）、
   两层传输逐字相同、词汇不通必须如实拒收、RESULT 必须自称来自回答方。
3. **本脚本依赖外部语料 ⇒ 进不了 CI**。CI 侧由 [入口] 57（零结果松弛）
   与 [协议] 71/72（真实问题形状过协议 + RESULT 身份）在内置语料上守同形状。

跑法：python evidence/_realq_two_ldv.py（cwd 任意；输出自留档到同目录
_realq_two_ldv.out.txt）。退出码：0 = 全部判据过；1 = 有红。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote

#: 本文件住在 repo/evidence/ 下 ⇒ parent.parent 就是 repo 根
#: （_dialogue_two_ldv.py 里那句 parent.parent / "repo" 是从 outputs/ 时期留下来的，
#:   在 evidence/ 下会推成 repo/repo —— 本脚本不继承那个错）
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from ldv import protocol as PRT                     # noqa: E402
from ldv.corpus.loader import load_nodes            # noqa: E402
from ldv.tools import exchange_staging as XS        # noqa: E402
import entry as E                                   # noqa: E402

OUT: list[str] = []
CHECKS: list[tuple[str, str, bool, str]] = []


def say(s: str = "") -> None:
    print(s)
    OUT.append(s)


def hr(t: str) -> None:
    say()
    say("=" * 76)
    say(t)
    say("=" * 76)


def chk(no: str, name: str, ok: bool, detail: str) -> bool:
    CHECKS.append((no, name, bool(ok), detail))
    say(f"  [{'过' if ok else '红'}] {no} {name}")
    say(f"        {detail}")
    return bool(ok)


def show_node(n: dict, indent: str = "        ") -> None:
    say(f"{indent}{n.get('did')} 第{n.get('rank')}层｜这一带 {n.get('n_members')} 项"
        f"｜含答案 {n.get('n_answer')}｜精确 {n.get('precision')}"
        f"｜覆盖 {n.get('coverage')}｜往下 {n.get('n_children')} 个方向")


# ── 真实问题集：问题（人会问的）→ 显式映射（人做的，不是系统做的） ────────────
QA = [  # A = ext-wiki-vote 的词汇
    ("这个投票网络里，谁的意见有真正的影响力？", ["influence=≥100"]),
    ("有哪些人只投票、从没被任何人投票过？", ["type=出度1-3", "evidence_status=入度0"]),
    ("被很多人投票、但自己从不投票的人是谁？", ["evidence_status=入度101-1000", "type=出度0"]),
]
QB = [  # B = openalex-citations 的词汇
    ("我只要同行评审的期刊论文，别的都不要。", ["evidence_status=同行评审", "source.kind=journal"]),
    ("会议论文都在哪一带？", ["type=conference-paper"]),
    ("仓储（机构库）里的预印本有哪些？", ["evidence_status=仓储", "type=preprint"]),
]
# 两条「查不到」的真实问题：一条组合为空（走松弛），一条键拼错（走拒收）
EMPTY_A = ("出度很大、但完全不活跃的人", ["influence=0", "type=出度64+"])
EMPTY_B = ("电子书平台上的社论", ["source.kind=ebook platform", "type=editorial"])
TYPO_A = ("出度在 1 到 4 之间的人", ["type=出度1-4"])          # 真实档位是 1-3 / 4-15
TYPO_B = ("期刊论文（journal article）", ["type=journal-article"])  # 真实是 source.kind=journal + type=article
XID = "realq-20261021"


def phase0() -> tuple[E.App, E.App]:
    hr("阶段 0｜构建两个 ldv（两个各自独立的系统，不共享词汇、不共享结构）")
    t0 = time.time()
    A = E.App("ext-wiki-vote")
    say(f"  A 就绪｜{A.corpus_name}｜{len(A.nodes)} 项｜边 "
        f"{sum(len(v) for v in A.edges.values())}｜{len(A.all_keys)} 键"
        f"｜派生 {A.derived}｜建树 {A.boot_s:.1f}s（本阶段耗时 {time.time()-t0:.1f}s）")
    say(f"    who={A.who()}｜fp={A.fingerprint()}")
    say(f"    树：{A.tree}")
    for g, items in A.facets.items():
        say(f"    [{g}] {len(items)} 个可筛值｜" +
            " ".join(f"{i['key']}({i['n']})" for i in items))
    say(f"    （恒定键 {A.n_constant} 个已从 facet 剔除 —— 对全部项成立的键不是维度）")
    t0 = time.time()
    B = E.App("openalex-citations")
    say(f"  B 就绪｜{B.corpus_name}｜{len(B.nodes)} 项｜边 "
        f"{sum(len(v) for v in B.edges.values())}｜{len(B.all_keys)} 键"
        f"｜建树 {B.boot_s:.1f}s（本阶段耗时 {time.time()-t0:.1f}s）")
    say(f"    who={B.who()}｜fp={B.fingerprint()}")
    say(f"    树：{B.tree}")
    for g, items in B.facets.items():
        say(f"    [{g}] {len(items)} 个可筛值｜" +
            " ".join(f"{i['key']}({i['n']})" for i in items[:8]) +
            (" …" if len(items) > 8 else ""))
    say(f"    （恒定键 {B.n_constant} 个已从 facet 剔除）")
    say("  ⇒ 两个系统的词汇表、结构、指纹全都不同 —— 这正是要测的形状。")
    return A, B


def ask(app: E.App, text: str, req: list[str], forb: list[str] | None = None) -> dict:
    """跑一条真实问题，打印形状，返回 query() 的原始返回。"""
    forb = forb or []
    r = app.query({"require": req, "forbid": forb})
    p = r.get("requirement", {})
    say(f"  「{text}」")
    say(f"    映射：必须含 {p.get('require')}｜不含 {p.get('forbid')}"
        f"（**这步映射在系统外**，见脚本头部老实话 1）")
    if not r.get("ok"):
        say(f"    ⇒ 拒收：{str(r.get('error'))[:160]}")
        return r
    say(f"    ⇒ 答案 {r['n_answer']} 项｜{r['n_branches']} 支｜停法 {r['stopped']}"
        f"｜路径 {' → '.join(r['path']) or '（根就是这）'}｜{r['ms']} ms")
    for n in r["nodes"][:6]:
        show_node(n)
    if len(r["nodes"]) > 6:
        say(f"        …另 {len(r['nodes'])-6} 支（接口不截断，界面才截）")
    return r


def phase1(A: E.App, B: E.App) -> None:
    hr("阶段 1｜真实问题 → 人类接口（文件层直跑 App.query）")
    say("  性质判据：切分不重不漏（Σ各支含答案 == 答案总数）· 支数不虚报"
        "（n_branches == len(nodes)）· 每支指得到节点（有 did/rank）")
    results: list[tuple[str, dict]] = []
    for text, req in QA:
        results.append((f"A「{text}」", ask(A, text, req)))
    for text, req in QB:
        results.append((f"B「{text}」", ask(B, text, req)))
    bad: list[str] = []
    for tag, r in results:
        if not r.get("ok"):
            bad.append(f"{tag} 被拒收（不该）")
            continue
        s = sum(n.get("n_answer", 0) for n in r["nodes"])
        if s != r["n_answer"]:
            bad.append(f"{tag}：Σ各支 {s} ≠ 答案 {r['n_answer']}")
        if r["n_branches"] != len(r["nodes"]):
            bad.append(f"{tag}：支数 {r['n_branches']} ≠ nodes {len(r['nodes'])}")
        if any(not n.get("did") or n.get("rank") is None for n in r["nodes"]):
            bad.append(f"{tag}：有支没有 did/rank")
        if r["n_answer"] <= 0:
            bad.append(f"{tag}：答案 0 项（这条问题本该有答案）")
    chk("P1-1", "六条真实问题全部跑通，且切分不重不漏、可指认",
        not bad, f"6 条｜红 {len(bad)}" + (f"｜{bad[0]}" if bad else ""))

    # ── 往下看一层（UI 的「往下钻」） ──────────────────────────────
    r0 = results[0][1]
    # 挑一支**有子节点**的（答案全落在叶上时挑不到 ⇒ 钻根 D0；
    # 叶节点 drill 返回空 children 是正确行为，单独一条验）
    with_kids = [n for n in r0["nodes"] if n.get("n_children")]
    target = with_kids[0]["did"] if with_kids else "D0"
    d = A.drill(target, QA[0][1])
    kids = d.get("children") or []
    parent = d.get("parent") or {}
    if parent.get("n_children"):
        okr = bool(kids) and all("coverage" in c for c in kids)
        det = (f"{target} 往下 {len(kids)} 个方向｜" +
               " ".join(f"{c['did']}:含答{c['n_answer']}/{c['n_members']}"
                        f"(覆盖{c['coverage']})" for c in kids[:5]))
        name = f"下钻一层（{target}）⇒ 子节点各带覆盖率"
    else:
        okr = (kids == [] and parent.get("n_children") == 0)
        det = f"{target} 是叶（往下 0 个方向）⇒ 界面显示「已经是最细的一层」，不造假方向"
        name = f"下钻到叶（{target}）⇒ 如实报告「没有更细的」"
    chk("P1-2", name, okr, det)

    # ── 三件确定性的事：建议 / 零结果松弛 / 示例 ─────────────────────
    sa = [x["key"] for x in A.suggest("出度")["items"]]
    sb = [x["key"] for x in B.suggest("同行评审")["items"]]
    sj = [x["key"] for x in B.suggest("期刊")["items"]]
    chk("P1-3", "建议是子串匹配：「出度」命中 A 的出度档、「同行评审」命中 B 的证据档",
        bool(sa) and all("出度" in k for k in sa) and sb == ["evidence_status=同行评审"],
        f"A「出度」→ {sa[:3]}｜B「同行评审」→ {sb}")
    say(f"  ⚠️ 老实话：B 上打「期刊」⇒ {sj or '（空）'} —— 键是英文（source.kind=journal），"
        f"中文子串打不出来。建议只补「可能拼错」，不负责「把话说成键」。")
    ex_a = A.examples()["items"]
    fake = [k for x in ex_a for k in x["require"] if k not in A.all_keys]
    chk("P1-4", "示例按数据形状挑，且键都真实存在",
        len(ex_a) >= 2 and not fake, f"{len(ex_a)} 条｜假键 {fake or '无'}")

    # ── 退化路径一：键拼错 ⇒ 拒收 + 建议（不许静默 0 条） ───────────
    for tag, app, (text, req) in (("A", A, TYPO_A), ("B", B, TYPO_B)):
        r = app.query({"require": req})
        okr = (r.get("ok") is False and bool(r.get("unknown_keys"))
               and "不存在" in r.get("error", ""))
        chk(f"P1-5{tag}", f"拼错的键 ⇒ 拒收并给建议（{req[0]}）", okr,
            f"ok={r.get('ok')}｜unknown={r.get('unknown_keys')}"
            f"｜建议 {r.get('suggest')}")

    # ── 退化路径二：组合为空 ⇒ 0 项 + 松弛，且松弛可复算 ────────────
    for tag, app, (text, req) in (("A", A, EMPTY_A), ("B", B, EMPTY_B)):
        r = app.query({"require": req})
        rl = r.get("relax") or []
        replay = []
        for x in rl:
            n = len(app.answer_set(frozenset(req) - {x["drop"]}, frozenset()))
            replay.append((x["drop"], x["n_answer"], n))
        okr = (r.get("ok") is True and r.get("n_answer") == 0 and bool(rl)
               and all(a == b for _, a, b in replay))
        chk(f"P1-6{tag}", f"组合为空 ⇒ 0 项 + 松弛建议，且建议逐条复算为真（{req}）",
            okr, f"n=0｜松弛 {replay}")


def phase2(A: E.App, B: E.App) -> list[dict]:
    """机器接口：文件层 + HTTP 层，同一组消息。返回按对话顺序排好的交流记录。"""
    hr("阶段 2｜真实问题 → 机器接口（ldv-msg/1：A 与 B 对等互问）")
    say("  消息不带自然语言：QUERY = {require, forbid}（键约束）；"
        "RESULT = 对方树上的查询结果 + 原查询回声 + 指纹。")
    fpA, fpB = A.fingerprint(), B.fingerprint()
    # 对话脚本：A 用 B 的词汇问 B；B 用 A 的词汇问 A；再各拿自己的词汇问对方（必须被如实拒收）
    to_b: list[dict] = []      # A → B
    to_a: list[dict] = []      # B → A
    mid = 0

    def q(who_app: E.App, fp: str, req: list[str]) -> dict:
        nonlocal mid
        mid += 1
        m = PRT.build_query(req, [], frm=who_app.who(), fp=fp)
        # mid 带 q 前缀：RESULT 由 build_result 新建信封、不带 mid，
        # ingest 会按序号回退（0001…）⇒ 前缀错开才不会撞 id
        m["mid"] = f"q{mid:04d}"
        return m

    for text, req in QB:                       # A 拿 B 的词汇问 B
        to_b.append(q(A, fpA, req))
    for text, req in QA:                       # B 拿 A 的词汇问 A
        to_a.append(q(B, fpB, req))
    to_b.append(q(A, fpA, ["influence=≥100"]))           # A 的词汇问 B ⇒ 词汇不通
    to_a.append(q(B, fpB, ["evidence_status=同行评审"]))  # B 的词汇问 A ⇒ 词汇不通

    # ── 文件层：两条批交换（每条独立，坏的不拖垮好的） ──────────────
    tmp = Path(tempfile.mkdtemp())
    try:
        (tmp / "to_b.jsonl").write_text(
            "\n".join(json.dumps(m, ensure_ascii=False) for m in to_b) + "\n",
            encoding="utf-8")
        (tmp / "to_a.jsonl").write_text(
            "\n".join(json.dumps(m, ensure_ascii=False) for m in to_a) + "\n",
            encoding="utf-8")
        st_b = PRT.exchange_file(B, tmp / "to_b.jsonl", tmp / "out_b.jsonl")
        st_a = PRT.exchange_file(A, tmp / "to_a.jsonl", tmp / "out_a.jsonl")
        say(f"  文件层 stats：A→B {st_b}｜B→A {st_a}")
        rb = [json.loads(x) for x in
              (tmp / "out_b.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
        ra = [json.loads(x) for x in
              (tmp / "out_a.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ── 逐条判据 ───────────────────────────────────────────────────
    say("  逐条（对等：谁都能提问、谁都能回答；没有主从）：")
    record: list[dict] = []
    pairs = list(zip(to_b, rb, [B] * len(to_b))) + list(zip(to_a, ra, [A] * len(to_a)))
    bad_same: list[str] = []
    bad_verify: list[str] = []
    bad_who: list[str] = []
    for qm, rm, responder in pairs:
        v_ok, v_bad = PRT.verify_result(rm)
        local = responder.query(qm.get("payload") or {})
        same = rm.get("payload", {}).get("answer_ids") == local.get("answer_ids")
        # ★ RESULT 必须自称来自**回答方**：from = 回答方身份，fp = 回答方结构的指纹
        who_ok = (rm.get("from") == responder.who()
                  and rm.get("fp") == responder.fingerprint())
        p = rm.get("payload") or {}
        head = (f"答案 {p.get('n_answer')} 项｜{len(p.get('nodes') or [])} 支"
                if p.get("ok") else f"如实报错：{str(p.get('error'))[:60]}")
        say(f"    [{qm.get('mid')}] {qm.get('from')} → {responder.who()}"
            f"｜require {qm['payload']['require']}")
        say(f"         ⇒ {head}｜verify {v_ok}｜与本端逐字相同 {same}"
            f"｜from/fp 是回答方的 {who_ok}")
        if not v_ok:
            bad_verify.append(str(qm.get("mid")))
        if p.get("ok") and not same:
            bad_same.append(str(qm.get("mid")))
        if not who_ok:
            bad_who.append(f"{qm.get('mid')}:from={rm.get('from')}/fp={rm.get('fp')}"
                           f" 期望 from={responder.who()}/fp={responder.fingerprint()}")
        record.append(qm)
        record.append(rm)
    chk("P2-1", "每条 RESULT 都过接收方自检（不依赖发送方诚信）",
        not bad_verify, f"{len(pairs)} 条｜红 {bad_verify or '无'}")
    chk("P2-2", "成功答案与本端 query() 逐字相同（结构可传、可本地复算）",
        not bad_same, f"红 {bad_same or '无'}")
    chk("P2-3", "RESULT 的 from/fp 必须是**回答方**的（结果从哪份结构上跑出来的）",
        not bad_who, f"红 {bad_who or '无'}")

    # 跨词汇那条必须**如实拒收**，不是静默 0 条
    cross = [rm for qm, rm, _ in pairs
             if qm["payload"]["require"] == ["influence=≥100"]]
    cok = bool(cross) and cross[0]["payload"].get("ok") is False \
        and "不存在" in str(cross[0]["payload"].get("error", ""))
    chk("P2-4", "A 拿自己的词汇（influence=≥100）问 B ⇒ 如实说词汇不通，不静默 0 条",
        cok, str(cross[0]["payload"].get("error"))[:110] if cross else "（没有这条消息）")

    # ── HTTP 层：起两个真服务，同一组消息，逐字对比 ─────────────────
    say("  HTTP 层（起两个真入口，人类页面与机器接口同一个端口）：")
    urls = {"A": "http://127.0.0.1:8771", "B": "http://127.0.0.1:8772"}
    procs = {}
    try:
        for who, port in (("A", 8771), ("B", 8772)):
            procs[who] = subprocess.Popen(
                [sys.executable, "entry.py", "--port", str(port), "--corpus",
                 "ext-wiki-vote" if who == "A" else "openalex-citations"],
                cwd=str(REPO), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ready = False
        for _ in range(160):
            try:
                urllib.request.urlopen(urls["A"] + "/api/meta", timeout=2)
                urllib.request.urlopen(urls["B"] + "/api/meta", timeout=2)
                ready = True
                break
            except Exception:
                time.sleep(1.0)
        say(f"    两个入口就绪：{ready}")
        page = urllib.request.urlopen(urls["A"] + "/", timeout=10)
        body = page.read().decode("utf-8")
        chk("P2-5", "人类接口的页面在（GET / 200 且是查询页）",
            page.status == 200 and "这一带" in body,
            f"HTTP {page.status}｜{len(body)} 字节｜标题在：{'这一带' in body}")
        api = json.loads(urllib.request.urlopen(
            urls["B"] + "/api", timeout=10).read().decode("utf-8"))
        chk("P2-6", "机器接口的说明书在（GET /api 自描述：端点 + 协议 + 边界）",
            "endpoints" in api and api.get("protocol", {}).get("version") == PRT.PROTOCOL_VERSION,
            f"协议 {api.get('protocol', {}).get('version')}｜"
            f"{len(api.get('endpoints', {}))} 个端点｜边界 {len(api.get('boundaries', []))} 条")
        sug = json.loads(urllib.request.urlopen(
            urls["A"] + "/api/suggest?q=" + quote("出度"),
            timeout=10).read().decode("utf-8"))
        chk("P2-7", "HTTP 上也能拿键建议（/api/suggest，子串匹配）",
            bool(sug.get("items")) and all("出度" in x["key"] for x in sug["items"]),
            f"「出度」→ {[x['key'] for x in sug.get('items', [])][:3]}")
        bad_http: list[str] = []
        for qm, rm_f, responder in pairs:
            tgt = "B" if responder is B else "A"
            got = PRT.send_msg(urls[tgt] + "/api/msg", qm)
            a = {k: v for k, v in (got.get("payload") or {}).items() if k != "ms"}
            b = {k: v for k, v in (rm_f.get("payload") or {}).items() if k != "ms"}
            if a != b:
                bad_http.append(str(qm.get("mid")))
        chk("P2-8", "HTTP 层与文件层对同一组消息逐字相同（两条传输一个语义）",
            not bad_http, f"{len(pairs)} 条｜红 {bad_http or '无'}")
        # 坏信封 ⇒ ERROR 带理由，不静默丢弃
        bad_msg = dict(json.loads(json.dumps(pairs[0][0])))
        del bad_msg["fp"]
        err = PRT.send_msg(urls["B"] + "/api/msg", bad_msg)
        chk("P2-9", "坏信封（缺 fp）⇒ ERROR 指出缺什么，不静默丢弃",
            err.get("type") == PRT.MSG_ERROR and "fp" in str(err.get("payload", {}).get("error", "")),
            f"type={err.get('type')}｜{str(err.get('payload', {}).get('error'))[:80]}")
    finally:
        for p in procs.values():
            p.terminate()
    return record


def phase3(B: E.App, record: list[dict]) -> None:
    hr("阶段 3｜交流记录 → 缓冲区一次性树（默认抛弃；抛弃是显式动作）")
    bdir = REPO / "_data" / B.corpus_name
    assert bdir.is_dir(), bdir
    # 重跑保护：上一次的残留先显式抛弃（这正是抛弃的用途）
    st0 = XS.status(bdir)
    mine = [x["exchange"] for x in st0["一次性树"] if x["exchange"] == XID]
    if mine:
        r = XS.abandon(bdir, XID)
        say(f"  重跑保护：发现上次残留 {mine} ⇒ 先显式抛弃 {r}")
    tmp = Path(tempfile.mkdtemp())
    try:
        jl = tmp / f"{XID}.jsonl"
        jl.write_text("\n".join(json.dumps(m, ensure_ascii=False) for m in record) + "\n",
                      encoding="utf-8")
        ing = XS.ingest(bdir, jl)
        say(f"  ingest：{json.dumps(ing, ensure_ascii=False)}")
        chk("P3-1", "一次交流一棵树：一条消息一节点，撞 id 进 rejected（不覆写）",
            ing.get("ok") and ing.get("节点") == len(record) and not ing.get("撞 id 拒收"),
            f"消息 {ing.get('消息')}｜节点 {ing.get('节点')}｜撞 id {ing.get('撞 id 拒收')}")
        st1 = XS.status(bdir)
        say(f"  status：{json.dumps(st1, ensure_ascii=False)}")
        nodes_dir = bdir / XS.STAGING / XS.EXCHANGES / XID / "nodes"
        # 直接查这棵一次性树（显式指向它；正式结构默认看不见它）
        saved = os.environ.get("LDV_CORPUS")
        try:
            T = E.App(str(nodes_dir))
            say(f"  一次性树可查：{T.corpus_name}｜{len(T.nodes)} 项｜建核 {T.boot_s:.2f}s")
            counts = {}
            for t in ("QUERY", "RESULT"):
                r = T.query({"require": [f"type={t}"]})
                counts[t] = r.get("n_answer")
            n_q = sum(1 for m in record if m.get("type") == "QUERY")
            n_r = sum(1 for m in record if m.get("type") == "RESULT")
            chk("P3-2", "一次性树按 type= 可筛，且三档计数与消息条数对得上",
                counts.get("QUERY") == n_q and counts.get("RESULT") == n_r,
                f"type=QUERY {counts.get('QUERY')}/{n_q}｜type=RESULT {counts.get('RESULT')}/{n_r}")
            r_who = T.query({"require": ["who=A"]})
            chk("P3-3", "who=/round= 是字段不是键 ⇒ 拒收（想按「谁问的」筛，得 promote 后由人声明维度）",
                r_who.get("ok") is False and "不存在" in str(r_who.get("error", "")),
                str(r_who.get("error"))[:100])
            # 隔离：正式结构一个节点都没多
            main = load_nodes(bdir / "nodes")
            leak = [i for i in main if i.startswith(XID)]
            chk("P3-4", "正式结构不受影响：load_nodes(<语料>/nodes) 前后一致，无交流 id 漏入",
                len(main) == len(B.nodes) and not leak,
                f"正式 {len(main)} 项（启动时 {len(B.nodes)}）｜漏入 {leak or '无'}")
        finally:
            if saved is None:
                os.environ.pop("LDV_CORPUS", None)
            else:
                os.environ["LDV_CORPUS"] = saved
            assert os.environ.get("LDV_CORPUS") == saved, "环境变量没还原"
        ab = XS.abandon(bdir, XID)
        say(f"  abandon（默认命运，显式动作）：{json.dumps(ab, ensure_ascii=False)}")
        st2 = XS.status(bdir)
        chk("P3-5", "抛弃 = 删目录无残留；status 只报不淘汰",
            ab.get("ok") and ab.get("残留") is False
            and not [x for x in st2["一次性树"] if x["exchange"] == XID],
            f"删掉 {ab.get('删掉的节点')} 个节点｜残留 {ab.get('残留')}"
            f"｜status 剩 {st2['棵']} 棵")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def phase4() -> int:
    hr("阶段 4｜判据汇总")
    n_ok = sum(1 for c in CHECKS if c[2])
    for no, name, ok, _ in CHECKS:
        say(f"  [{'过' if ok else '红'}] {no} {name}")
    say()
    say(f"合计 {len(CHECKS)} 条｜过 {n_ok}｜红 {len(CHECKS)-n_ok}")
    out = Path(__file__).resolve().parent / "_realq_two_ldv.out.txt"
    out.write_text("\n".join(OUT) + "\n", encoding="utf-8")
    say(f"留档：{out}")
    return 0 if n_ok == len(CHECKS) else 1


def main() -> int:
    A, B = phase0()
    phase1(A, B)
    record = phase2(A, B)
    phase3(B, record)
    return phase4()


if __name__ == "__main__":
    raise SystemExit(main())

