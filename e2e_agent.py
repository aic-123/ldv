# -*- coding: utf-8 -*-
"""**端到端**：把服务当成一个「不知道内部实现」的客户端来用一遍（2026-10-29）。

## 为什么要有这个文件

P1 的 25 条判据都是**进程内**的：同一个 Python 进程里起 `ThreadingHTTPServer`，
直接改 `Handler.root` / `Handler.store`。那验证的是「函数行为对」，**不是**「一条真流程走得完」：

    · `main()` 的启动路径（认证拒绝、调度器线程、参数解析）一次都没被走过；
    · 客户端拿到的**只有 HTTP** —— 它没有 `serve` 模块可以改；
    · 「进程重启之后还在不在」「语料坏了会不会拖死整个服务」这类问题，进程内测不出来。

所以这个脚本：**起真进程**（`python serve.py --tokens …`）、**只走 HTTP**，
按一个 agent 的真实顺序走一遍。失败不中断（好看全貌），最后给退出码。

## 它**不**是什么

· 不是 LLM：这里的「agent」是**按 agent 会怎么调**写的客户端，不是模型。它的价值在
  **顺序与状态**（下钻要在刚问过之后、配额要跨请求累计、重启要不丢账），不在语义。
· 不是性能测试：只报每步耗时，不做基准。
· 不碰真语料：自己写 8 个节点到临时租户里。**唯一的例外**是租户根放在 `…/_data/tenants`，
  因为内核只在 `_data/` 下才写使用记录（`entry.App._usage_dir`），放在别处
  「下钻 → 记录」这一环就静默失效 —— 那就测不到它了。

## 它守的三件「进程内判据测不到」的事

1. **坏语料不许拖死连接**：一个重复 id 的节点，`/run` 曾经**直接断连**（客户端拿到
   `RemoteDisconnected`，日志里只有 traceback）。现在必须 500 + 说清哪个文件。
2. **readyz 不许说谎**：坏语料时 `/ask` 全 500 而 `/readyz` 曾经照样 200。
   现在失败一次之后必须 **503** 并指明是哪个租户；修好之后要**自己回到 200**。
3. **重启不丢账**：账本、租户、token 全在磁盘上，重启后行数只增不减、问得出同样的答案。

用法：

    python e2e_agent.py              # 跑一遍，最后给退出码
    python e2e_agent.py --keep       # 保留临时目录（排障用）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent

#: 临时租户里的 8 个节点：两族（论文 / 代码），各带链；`evidence_status` 是**跨族**的键
NODES = [
    ("p1", "论文", ("p2", "p3")),
    ("p2", "论文", ("p3",)),
    ("p3", "论文", ()),
    ("p4", "论文", ()),
    ("p5", "论文", ()),
    ("c1", "代码", ("c2",)),
    ("c2", "代码", ()),
    ("c3", "代码", ()),
]


class Report:
    """一行一步。**失败不抛** —— 抛了就只剩第一个错，看不到后面还有几处坏的。"""

    def __init__(self) -> None:
        self.rows: list[tuple[bool, str, float, str]] = []

    def check(self, name: str, cond, detail: str = "", t0: float | None = None) -> bool:
        ok = bool(cond)
        self.rows.append((ok, name, (time.perf_counter() - t0) if t0 else 0.0, detail))
        print(("  ✓ " if ok else "  ✗ ") + name + (f"　｜{detail}" if detail else ""),
              flush=True)
        return ok

    def summary(self) -> int:
        n = len(self.rows)
        bad = [r for r in self.rows if not r[0]]
        print("\n" + "═" * 74)
        print(f"端到端：{n} 步｜过 {n - len(bad)}｜红 {len(bad)}"
              f"｜耗时 {sum(r[2] for r in self.rows):.1f}s")
        if bad:
            print("\n红的是这些：")
            for _ok, name, _t, detail in bad:
                print(f"  · {name}\n      {detail}")
        print("═" * 74)
        return 0 if not bad else 1


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Client:
    """真客户端：只认 HTTP，不 import 服务的任何模块。"""

    def __init__(self, base: str):
        self.base = base
        self.headers: list[dict] = []

    def req(self, method: str, path: str, token: str | None = None,
            params: dict | None = None, timeout: float = 120) -> tuple[int, object, dict]:
        url = self.base + path
        if params:
            url += "?" + urlencode(params)
        r = urllib.request.Request(url, method=method)
        if token:
            r.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                code, raw, hd = resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as e:
            code, raw, hd = e.code, e.read(), dict(e.headers)
        except Exception as exc:                  # noqa: BLE001 - 断连也是一种「结果」，要看见
            return 0, f"{type(exc).__name__}: {exc}", {}
        hd = {k.lower(): v for k, v in hd.items()}
        self.headers.append(hd)
        text = raw.decode("utf-8", "replace")
        try:
            return code, json.loads(text), hd
        except ValueError:
            return code, text, hd

    def get(self, path: str, **kw):
        return self.req("GET", path, **kw)

    def post(self, path: str, **kw):
        return self.req("POST", path, **kw)

    def delete(self, path: str, **kw):
        return self.req("DELETE", path, **kw)


def write_node(dirp: Path, nid: str, typ: str, rels) -> None:
    """最小合法节点。`relations` 为空时写 `[]`（与 `_data/*` 的既有写法一致）。"""
    lines = ["---", f'id: "{nid}"', f'type: "{typ}"', f'title: "节点 {nid}"',
             'evidence_status: "原始记录"']
    if rels:
        lines.append("relations:")
        lines += [f'  - "{r}"' for r in rels]
    else:
        lines.append("relations: []")
    lines += ["---", "", f"{nid} 的正文。", ""]
    (dirp / f"{nid}.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="ldv 服务层端到端（真进程 + 真 HTTP）")
    ap.add_argument("--keep", action="store_true", help="保留临时目录")
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args(argv[1:])

    rep = Report()
    tmp = Path(tempfile.mkdtemp(prefix="ldv-e2e-"))
    # ⚠️ 路径里有 `_data`：内核只在这个条件下写使用记录（`entry.App._usage_dir`）。
    #    不是「为了过判据改路径」，是「不这样这一环就是静默不生效」。
    root = tmp / "_data" / "tenants"
    root.mkdir(parents=True)
    tokens_file = tmp / "tokens.json"
    log_file = tmp / "server.log"
    port = args.port or free_port()
    base = f"http://127.0.0.1:{port}"
    cli = Client(base)
    proc: subprocess.Popen | None = None

    def start() -> subprocess.Popen:
        log = log_file.open("a", encoding="utf-8")
        return subprocess.Popen(
            [sys.executable, "serve.py", "--tokens", str(tokens_file),
             "--root", str(root), "--port", str(port), "--tick-interval", "2"],
            cwd=str(ROOT), stdout=log, stderr=log)

    def stop(p: subprocess.Popen | None) -> None:
        if p is None:
            return
        p.terminate()
        try:
            p.wait(timeout=15)
        except subprocess.TimeoutExpired:
            p.kill()

    def wait_ready(timeout: float = 45.0) -> tuple[bool, str]:
        t0 = time.time()
        last = ""
        while time.time() - t0 < timeout:
            try:
                code, obj, _ = cli.get("/readyz", timeout=3)
                last = f"{code}"
                if code == 200:
                    return True, last
            except Exception as exc:              # noqa: BLE001
                last = type(exc).__name__
            time.sleep(0.2)
        return False, last

    try:
        # ── ① 造 token（走 CLI，不自己拼 JSON）──────────────────────────
        t0 = time.perf_counter()
        cp = subprocess.run([sys.executable, "serve.py", "--mint-token", "e2e-admin",
                             "--tenant", "*"], cwd=str(ROOT),
                            capture_output=True, text=True, timeout=120)
        out = (cp.stdout or "") + (cp.stderr or "")
        m_tok = re.search(r"(ldv_[A-Za-z0-9_\-]{20,})", out)
        m_sum = re.search(r'"sha256":\s*"([0-9a-f]{64})"', out)
        tok_admin = m_tok.group(1) if m_tok else ""
        digest_ok = bool(m_sum) and bool(tok_admin) and (
            hashlib.sha256(tok_admin.encode()).hexdigest() == m_sum.group(1))
        rep.check("① 造 token：CLI 起得来，且**打印的摘要 == 真 token 的 sha256**",
                  cp.returncode == 0 and bool(tok_admin) and digest_ok,
                  f"rc={cp.returncode}｜token {len(tok_admin)} 字符｜摘要对上={digest_ok}",
                  t0)

        tok_agent = "ldv_e2e_agent_" + "a" * 20
        tok_tiny = "ldv_e2e_tiny_" + "b" * 20
        tok_out = "ldv_e2e_out_" + "c" * 20
        tokens_file.write_text(json.dumps({"tokens": [
            {"name": "e2e-admin", "sha256": hashlib.sha256(tok_admin.encode()).hexdigest(),
             "tenant": "*"},
            {"name": "e2e-agent", "sha256": hashlib.sha256(tok_agent.encode()).hexdigest(),
             "tenant": "demo", "quota_per_min": 200},
            {"name": "e2e-tiny", "sha256": hashlib.sha256(tok_tiny.encode()).hexdigest(),
             "tenant": "demo", "quota_per_min": 1},
            {"name": "e2e-outsider", "sha256": hashlib.sha256(tok_out.encode()).hexdigest(),
             "tenant": "other"},
        ]}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

        # ── ② 起服务：没租户时**必须**不 ready ───────────────────────────
        t0 = time.perf_counter()
        proc = start()
        up = False
        for _ in range(300):
            try:
                code, _, _ = cli.get("/healthz", timeout=2)
                if code == 200:
                    up = True
                    break
            except Exception:                     # noqa: BLE001
                pass
            time.sleep(0.1)
        code_r, body_r, _ = cli.get("/readyz")
        rep.check("② 起进程：/healthz 200，但**一个租户都没有 ⇒ /readyz 503**（不是 200 敷衍）",
                  up and code_r == 503 and isinstance(body_r, dict)
                  and body_r.get("ready") is False,
                  f"healthz up={up}｜readyz {code_r}", t0)

        # ── ③ 建库 + **空库上先问一句**（第一次接触的真实姿势）───────────
        t0 = time.perf_counter()
        code_n, _, _ = cli.post("/tenants/demo", token=tok_admin)
        code_e, body_e, _ = cli.get("/demo/run", token=tok_admin,
                                    params={"plugin": "keyset"})
        code_z, a0, _ = cli.get("/demo/ask", token=tok_admin,
                                params={"require": "type=论文"})
        rep.check("③ 建租户：201，且**空库立刻能跑**（200 + 正文是「装进 0 项」）",
                  code_n == 201 and code_e == 200 and "装进 0 项" in str(body_e),
                  f"建 {code_n}｜空库跑 {code_e}", t0)
        rep.check("③ 空库上问：200 + empty_corpus（不是「你键写错了」）",
                  code_z == 200 and isinstance(a0, dict) and a0.get("empty_corpus") is True
                  and "空的" in str(a0.get("error")),
                  f"ask {code_z}｜{str(a0.get('error'))[:40] if isinstance(a0, dict) else a0}")
        rep.check("③ 建完之后 /readyz 变 200（前一版的 503 是「还没建好」不是「坏了」）",
                  cli.get("/readyz")[0] == 200, "")

        # ── ④ 喂料：文件一落盘就该被看到，不用重启 ────────────────────────
        t0 = time.perf_counter()
        nd = root / "demo" / "nodes"
        for nid, typ, rels in NODES:
            write_node(nd, nid, typ, rels)
        code_a, a1, hd_a = cli.get("/demo/ask", token=tok_agent,
                                   params={"require": "type=论文"})
        n_ans = a1.get("n_answer") if isinstance(a1, dict) else None
        rep.check("④ 喂料：8 个节点落盘后**立刻**被问到（不重启、不 refresh），论文 5 项",
                  code_a == 200 and n_ans == 5,
                  f"ask {code_a}｜n_answer={n_ans}", t0)
        rep.check("④ 响应带协议头，每一支都有 did（客户端靠它下钻）",
                  hd_a.get("x-ldv-protocol") == "ldv-http/1"
                  and isinstance(a1, dict) and a1.get("nodes")
                  and all("did" in x for x in a1.get("nodes")),
                  f"protocol={hd_a.get('x-ldv-protocol')}｜"
                  f"支={[x.get('did') for x in (a1.get('nodes') or [])][:4]}")

        # ── ⑤ 两种「不是成功也不是失败」的答案要分开 ─────────────────────
        t0 = time.perf_counter()
        code_u, a2, _ = cli.get("/demo/ask", token=tok_agent,
                                params={"require": "type=会议"})
        rep.check("⑤ 问结构里没有的键 ⇒ 200 + out_of_structure（「这个维度我没有」）",
                  code_u == 200 and isinstance(a2, dict)
                  and a2.get("out_of_structure") is True,
                  f"ask {code_u}｜{str(a2.get('error'))[:40]}", t0)
        code_p, _, hd_p = cli.get("/demo/ask", token=tok_agent,
                                  params={"require": "type=论文", "whatever": "1"})
        rep.check("⑤ 不认识的参数 ⇒ 400 + bad-request（不静默忽略）",
                  code_p == 400 and hd_p.get("x-ldv-error") == "bad-request",
                  f"{code_p}｜{hd_p.get('x-ldv-error')}")

        # ── ⑥ 分叉：问一个**跨族**的键 ⇒ 真有多个支，才谈得上「先看哪一支」──
        t0 = time.perf_counter()
        code_b, allq, _ = cli.get("/demo/ask", token=tok_agent,
                                  params={"require": "evidence_status=原始记录"})
        br = (allq.get("nodes") if isinstance(allq, dict) else []) or []
        rep.check("⑥ 问全库 ⇒ 真的分叉（n_answer=8，≥2 支）",
                  code_b == 200 and allq.get("n_answer") == 8 and len(br) >= 2,
                  f"ask {code_b}｜支={[(x.get('did'), x.get('n_answer')) for x in br]}", t0)

        # ── ⑦ 下钻：唯一真实的「有用」信号；只改次序，不改集合 ─────────────
        t0 = time.perf_counter()
        target = br[-1]["did"] if br else None
        code_d, d1, _ = cli.post("/demo/drill", token=tok_agent, params={"did": target})
        recorded = d1.get("recorded") if isinstance(d1, dict) else None
        usage_files = list((root / "demo" / "usage").rglob("*")) \
            if (root / "demo" / "usage").is_dir() else []
        rep.check("⑦ 下钻最后一支：200 且**真的记下来了**（recorded=True，usage/ 落了文件）",
                  code_d == 200 and recorded is True and bool(usage_files),
                  f"drill {code_d}｜recorded={recorded}｜usage {len(usage_files)} 个文件",
                  t0)
        _c, allq2, _ = cli.get("/demo/ask", token=tok_agent,
                               params={"require": "evidence_status=原始记录"})
        same = (isinstance(allq2, dict)
                and sorted(allq.get("answer_ids") or []) == sorted(allq2.get("answer_ids") or []))
        first_did = ((allq2.get("nodes") or [{}])[0].get("did")
                     if isinstance(allq2, dict) else None)
        rep.check("⑦ 下钻只改次序：答案**集合不变**，`ordered_by` 变 usage、首支变成刚钻的那支",
                  same and allq2.get("ordered_by") == "usage" and first_did == target,
                  f"集合不变={same}｜ordered_by={allq2.get('ordered_by')}"
                  f"｜首支 {first_did} vs 钻的 {target}")
        code_w, _, hd_w = cli.post("/demo/drill", token=tok_agent,
                                   params={"did": target, "verdict": "wrong"})
        rep.check("⑦ agent 不能判错：verdict=wrong ⇒ 403（判错只能人来给）",
                  code_w == 403 and hd_w.get("x-ldv-error") == "forbidden",
                  f"{code_w}｜{hd_w.get('x-ldv-error')}")

        # ── ⑧ 并发：两个客户端同时问，**答案集合**必须一样 ────────────────
        t0 = time.perf_counter()
        outs: list[tuple[int, object]] = []

        def _one() -> None:
            outs.append(cli.get("/demo/ask", token=tok_agent,
                                params={"require": "evidence_status=原始记录"})[:2])

        ths = [threading.Thread(target=_one) for _ in range(4)]
        [x.start() for x in ths]
        [x.join() for x in ths]
        sets = {tuple(sorted((o.get("answer_ids") or []))) for c, o in outs
                if c == 200 and isinstance(o, dict)}
        n_ok = sum(1 for c, _ in outs if c == 200)
        rep.check("⑧ 4 路并发同一问：全部 200，且**答案集合去重后只有 1 种**",
                  n_ok == 4 and len(sets) == 1,
                  f"200 {n_ok}/4｜集合 {len(sets)} 种"
                  f"（正文会因 `ms` 耗时字段不同，别拿整串做 golden）", t0)

        # ── ⑨ 议程：现在轮到谁 ───────────────────────────────────────────
        t0 = time.perf_counter()
        code_g, ag, _ = cli.get("/demo/agenda", token=tok_agent, params={"k": "0"})
        keys = set(ag) if isinstance(ag, dict) else set()
        rep.check("⑨ 问议程：200，且给的是「轮到谁」（到点 / 未到 / 阈值 都在）",
                  code_g == 200 and {"到点", "未到", "阈值"} <= keys,
                  f"{code_g}｜字段 {sorted(keys)[:6]}", t0)

        # ── ⑩ 调度器：**一个请求都不发**，它自己要走一格 ──────────────────
        t0 = time.perf_counter()
        ledger = root / "demo" / "agenda.jsonl"
        before = len(ledger.read_text(encoding="utf-8").splitlines()) if ledger.is_file() else 0
        time.sleep(5.0)                       # interval=2s ⇒ 至少该走两格
        after = len(ledger.read_text(encoding="utf-8").splitlines()) if ledger.is_file() else 0
        _cm, met, _ = cli.get("/metrics.json", token=tok_admin)
        ticks = (met.get("调度器") or {}).get("已 tick") if isinstance(met, dict) else None
        rep.check("⑩ 调度器**自己**走了一格（这 5 秒里客户端一个请求都没发）",
                  isinstance(ticks, int) and ticks >= 1 and after > before,
                  f"已 tick={ticks}｜账本 {before} → {after} 行", t0)

        # ── ⑪ 异步任务：提交 → 轮询 → done ────────────────────────────────
        t0 = time.perf_counter()
        code_s, sub, _ = cli.post("/demo/run", token=tok_agent,
                                  params={"plugin": "keyset"})
        tid = sub.get("task_id") if isinstance(sub, dict) else None
        status, result = "?", ""
        deadline = time.time() + 60
        while tid and time.time() < deadline:
            _c, st, _ = cli.get(f"/tasks/{tid}", token=tok_agent)
            if isinstance(st, dict):
                status = st.get("status")
                result = str(st.get("result") or "")
                if status in ("done", "error"):
                    break
            time.sleep(0.1)
        rep.check("⑪ 异步：202 + task_id，轮询到 done 且**结果非空**",
                  code_s == 202 and bool(tid) and status == "done" and len(result) > 200,
                  f"提交 {code_s}｜状态 {status}｜结果 {len(result)}B", t0)

        # ── ⑪b 计数：**在重启之前**查（计数器是进程内的，重启归零 —— 见 ⑰）───
        t0 = time.perf_counter()
        _c, met0, _ = cli.get("/metrics.json", token=tok_admin)
        c0 = (met0.get("计数") or {}) if isinstance(met0, dict) else {}
        need0 = {"提问", "下钻", "问议程", "提交任务", "请求"}
        rep.check("⑪b /metrics 计数：提问 / 下钻 / 问议程 / 提交任务都在（能对上做的事）",
                  need0 <= set(c0),
                  f"缺 {sorted(need0 - set(c0))}｜有 {sorted(c0)[:8]}", t0)

        # ── ⑫ 坏语料：**不许拖死连接**，也不许让 readyz 说谎 ──────────────
        t0 = time.perf_counter()
        (nd / "dup.md").write_text("\n".join([
            "---", 'id: "p1"', 'type: "论文"', 'evidence_status: "原始记录"',
            'title: "重复 id"', "---", "", "x", ""]), encoding="utf-8")
        # ⚠️ 这里**不能**带 refresh：`/run` 只认 plugin / only / holdout，
        #    多传一个就是客户端写错（400）—— 判据本身要写对，否则红的是判据。
        code_br_run, body_br_run, _ = cli.get("/demo/run", token=tok_admin,
                                             params={"plugin": "keyset"})
        code_br_ask, body_br_ask, _ = cli.get("/demo/ask", token=tok_agent,
                                             params={"require": "type=论文", "refresh": "1"})
        rep.check("⑫ 坏语料（重复 id）：/run 与 /ask 都 **500 + 说清是哪一处**，"
                  "不是断连（以前是 RemoteDisconnected）",
                  code_br_run == 500 and code_br_ask == 500
                  and "重复的 id" in str(body_br_run) and "重复的 id" in str(body_br_ask),
                  f"run {code_br_run}｜ask {code_br_ask}｜"
                  f"{str(body_br_ask)[:60]}", t0)
        code_rz, body_rz, _ = cli.get("/readyz")
        faults = (body_rz.get("已知装载失败") or {}) if isinstance(body_rz, dict) else {}
        rep.check("⑫ 坏语料之后 readyz **503**，并指名是哪个租户（不是 200 敷衍）",
                  code_rz == 503 and "demo" in faults,
                  f"readyz {code_rz}｜已知装载失败 {list(faults)}")
        (nd / "dup.md").unlink()
        code_fix_run, _, _ = cli.get("/demo/run", token=tok_admin, params={"plugin": "keyset"})
        code_fix_rz, _, _ = cli.get("/readyz")
        rep.check("⑫ 修好之后**自己回到 200 / ready**（不用重启，也不用手动清状态）",
                  code_fix_run == 200 and code_fix_rz == 200,
                  f"run {code_fix_run}｜readyz {code_fix_rz}")

        # ── ⑬ 配额：429 且带 Retry-After ─────────────────────────────────
        t0 = time.perf_counter()
        c1_, _, _ = cli.get("/demo/ask", token=tok_tiny, params={"require": "type=论文"})
        c2_, _, h2_ = cli.get("/demo/ask", token=tok_tiny, params={"require": "type=论文"})
        rep.check("⑬ 配额：1 次/分 ⇒ 第 2 次 429，且带 Retry-After",
                  c1_ == 200 and c2_ == 429 and "retry-after" in h2_,
                  f"{c1_} → {c2_}｜Retry-After={h2_.get('retry-after')}", t0)

        # ── ⑭ 认证 / 授权 / 不存在：三种失败必须分得开 ────────────────────
        t0 = time.perf_counter()
        n401, _, h401 = cli.get("/demo/ask", params={"require": "type=论文"})
        n403, _, h403 = cli.get("/demo/ask", token=tok_out, params={"require": "type=论文"})
        n404, _, h404 = cli.get("/nope/ask", token=tok_admin, params={"require": "type=论文"})
        kinds = {h401.get("x-ldv-error"), h403.get("x-ldv-error"), h404.get("x-ldv-error")}
        rep.check("⑭ 401 / 403 / 404 三种失败**互不相同**（不会把「过期」当成「挂了」）",
                  (n401, n403, n404) == (401, 403, 404) and len(kinds) == 3,
                  f"{n401}/{n403}/{n404}｜{sorted(k for k in kinds if k)}", t0)

        # ── ⑭b 轮换：追加一条同名新条目 ⇒ 重启后**新旧都能用**（双 key 重叠）──
        t0 = time.perf_counter()
        cp_r = subprocess.run([sys.executable, "serve.py", "--rotate", "e2e-agent",
                               "--tokens", str(tokens_file), "--expires-days", "7"],
                              cwd=str(ROOT), capture_output=True, text=True, timeout=120)
        out_r = (cp_r.stdout or "") + (cp_r.stderr or "")
        m_new = re.search(r"(ldv_[A-Za-z0-9_\-]{20,})", out_r)
        m_sum2 = re.search(r'"sha256":\s*"([0-9a-f]{64})"', out_r)
        m_exp = re.search(r'"expires_at":\s*"([0-9-]{10})"', out_r)
        tok_new = m_new.group(1) if m_new else ""
        appended = False
        if tok_new and m_sum2:
            doc = json.loads(tokens_file.read_text(encoding="utf-8"))
            doc["tokens"].append({"name": "e2e-agent",
                                  "sha256": m_sum2.group(1),
                                  "tenant": "demo", "quota_per_min": 200,
                                  "expires_at": m_exp.group(1) if m_exp else None})
            tokens_file.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n",
                                   encoding="utf-8")
            appended = True
        rep.check("⑭b 轮换：`--rotate` 造出**新** token（摘要不同）且带 expires_at，"
                  "追加进 tokens.json 后与旧条目并存",
                  cp_r.returncode == 0 and bool(tok_new) and tok_new != tok_agent
                  and bool(m_exp) and appended,
                  f"rc={cp_r.returncode}｜新 token {len(tok_new)} 字符｜"
                  f"到期 {m_exp.group(1) if m_exp else '—'}", t0)

        # ── ⑮ 重启：账本、租户、token 都还在 ─────────────────────────────
        t0 = time.perf_counter()
        lines_before = len(ledger.read_text(encoding="utf-8").splitlines()) if ledger.is_file() else 0
        stop(proc)
        proc = start()
        ok_ready, why = wait_ready()
        code_after, a_after, _ = cli.get("/demo/ask", token=tok_agent,
                                         params={"require": "type=论文"})
        lines_after = len(ledger.read_text(encoding="utf-8").splitlines()) if ledger.is_file() else 0
        code_newtok, _, _ = cli.get("/demo/ask", token=tok_new,
                                    params={"require": "type=论文"})
        rep.check("⑮ 重启：readyz 200、**旧 token 仍有效**、账本一行没丢、同样的 5 项",
                  ok_ready and code_after == 200
                  and (a_after.get("n_answer") if isinstance(a_after, dict) else None) == 5
                  and lines_after >= lines_before,
                  f"ready={ok_ready}({why})｜ask {code_after}｜账本 {lines_before} → {lines_after}",
                  t0)
        rep.check("⑮ 双 key 重叠：轮换后的**新 token 也能用**（换 key 不用停机）",
                  code_newtok == 200, f"新 token {code_newtok}")

        # ── ⑯ 删 → 进回收站 → 挪回来 → 又能用 ───────────────────────────
        t0 = time.perf_counter()
        code_del, delt, _ = cli.delete("/tenants/demo", token=tok_admin)
        trash = Path(str(delt.get("已移入回收站"))) if isinstance(delt, dict) else None
        gone, _, _ = cli.get("/demo/ask", token=tok_admin, params={"require": "type=论文"})
        moved = ""
        if trash is not None and trash.is_dir():
            shutil.move(str(trash), str(root / "demo"))
            moved = "挪回"
        back, a_back, _ = cli.get("/demo/ask", token=tok_admin,
                                  params={"require": "type=论文", "refresh": "1"})
        rep.check("⑯ 删除=进回收站（可恢复）：删后 404，挪回来又能问（200 / 5 项）",
                  code_del == 200 and gone == 404 and back == 200
                  and (a_back.get("n_answer") if isinstance(a_back, dict) else None) == 5,
                  f"删 {code_del}｜删后 {gone}｜恢复后 {back}（{moved}）", t0)

        # ── ⑰ 观测：计数、日志、协议头 ───────────────────────────────────
        t0 = time.perf_counter()
        code_met, met2, _ = cli.get("/metrics.json", token=tok_admin)
        counts = (met2.get("计数") or {}) if isinstance(met2, dict) else {}
        # 重启过 ⇒ 计数器只剩重启之后发生的（提问 / 请求）。这是**已知边界**，
        # 不是漏：要跨重启的读数就得外采，`/metrics` 只答「这个进程」。
        need = {"提问", "请求"}
        log_text = log_file.read_text(encoding="utf-8", errors="replace") if log_file.is_file() else ""
        log_lines = [ln for ln in log_text.splitlines() if ln.startswith("{")]
        parsed = 0
        for ln in log_lines[-20:]:
            try:
                d = json.loads(ln)
                parsed += int({"req", "code", "ms", "path"} <= set(d))
            except ValueError:
                pass
        rep.check("⑰ 观测：重启后 /metrics 仍有本轮计数（重启前的已归零）；访问日志是可解析 JSON",
                  code_met == 200 and need <= set(counts) and parsed >= 5,
                  f"计数 {sorted(counts)[:8]}｜日志 {len(log_lines)} 行，可解析 {parsed}", t0)
        import importlib.util as _ilu
        code_txt, body_txt, hd_txt = cli.get("/metrics", token=tok_admin)
        has_lib = _ilu.find_spec("prometheus_client") is not None
        if has_lib:
            ok_txt = (code_txt == 200
                      and str(body_txt).startswith("# HELP ldv_shell_events_total")
                      and "text/plain" in (hd_txt.get("content-type") or ""))
            detail = f"{code_txt}｜{str(hd_txt.get('content-type'))}"
        else:
            # 没装库时**回退成 JSON 且说明原因** —— 回退要看得见，不许冒充标准格式
            ok_txt = (code_txt == 200 and isinstance(body_txt, dict)
                      and "json" in str(body_txt.get("格式", "")))
            detail = f"{code_txt}｜JSON 回退（未装 prometheus_client）"
        rep.check("⑰ `/metrics` 是标准出口：装了库 ⇒ Prometheus 文本；"
                  "没装 ⇒ 明确回退 JSON（不冒充）",
                  ok_txt, detail)

        rep.check("⑰ 每个响应都有协议头（本轮共 %d 个响应）" % len(cli.headers),
                  all(h.get("x-ldv-protocol") == "ldv-http/1" for h in cli.headers if h),
                  "缺头下标：" + str([i for i, h in enumerate(cli.headers)
                                      if h and h.get("x-ldv-protocol") != "ldv-http/1"])[:80])
    finally:
        stop(proc)
        if args.keep:
            print(f"\n临时目录留着：{tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    return rep.summary()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
