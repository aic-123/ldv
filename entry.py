# -*- coding: utf-8 -*-
# -*- coding: utf-8 -*-
"""只读查询入口 —— **给的是「这一带」，不是「结果列表」**（2026-10-20）。

## 它和 `serve.py` 不是一件事

    `serve.py`    阶段 3 的壳：**按租户跑流水线**（建 / 判 / 报告），有任务队列与限流
    `entry.py`    本文件：**只读查询入口** —— 不建、不写、不排队

设计见 [`docs/服务化设计.md`](docs/服务化设计.md) §9。

## 一次查询的答案 = 树上的节点（或分叉处的几支）

    Query(require, forbid)  ⇒  若干节点，每个带：它代表什么 / 多粗 / 往下几个方向

⇒ 界面只有三件：**需求栏** · **这一带** · **往下钻**。

## 「对应粗细」怎么定 —— 递归下钻，直到每一支都由单一子节点装不下

    在某节点 d 上，P = 答案 ∩ d 的成员
    只有一个子节点含 P 的全部 ⇒ 往下（这一支还没分叉）
    ≥2 个子节点各含 P 的一部分 ⇒ 在 d 切开，每支递归
    没有子节点 ⇒ 这一支到底了

⚠️ **不用**插件的 `hit` 定粗细：`keyset.hit` 只在**矛盾**时说「否」
   （`req ∩ q.forbid` 或 `forb ∩ q.require`），所以根的两个子节点**几乎永远都说「是」**
   ⇒ 每条需求都会停在同一个分叉点（实测：4 条不同需求给出同一对节点）。
   `hit` 是**健全性的界**（允许假阳，`§K8`），不是**分辨率的尺**。这两件事不能混用。

## 两个接口（**页面只给人类那一个**）

    人类（页面）：GET /
    AI（JSON）  ：GET /api   ← 自描述（端点 / 参数 / 返回形状 / 边界）
                  GET /api/meta · /api/suggest · /api/examples
                  GET|POST /api/query · POST /api/drill
                  --batch in.jsonl --out out.jsonl（不起服务）

⚠️ 机器接口**不在页面上露出**（用户 2026-10-20 裁定：「隐藏起来……留给 ai 有一个
   可以使用的成熟路径即可」）⇒ 它的说明书就是 `GET /api` 与本文件。

## 不做语义翻译（这是**停**下来的一条路，不是没做）

`docs/分层方向视图-检索器层.md` §8 停止条件 4：「把「一段处境 → 语义」做成自动的
（语义推断 / 模型）」⇒ **停**，与「禁止模型代填内容」的立项根基直接冲突。
理由不只是红线：**用户无法复核** —— 翻译歪了，他看到的是「0 项」，
与「材料里真没有」长得一模一样。
⇒ 改用三件确定性的事：**子串建议** / **零结果松弛** / **按数据形状挑的示例**。

## 三条硬边界

1. **只读**：不 `insert` / `remove` / 落盘。
2. **单进程串行**：`query` 会写（`expand`）⇒ 一把全局锁，**不声称线程安全**。
3. **只绑 127.0.0.1**：无鉴权，**不是对外服务**。

用法：`python entry.py [--port 8765] [--corpus NAME] [--batch IN --out OUT]`
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import threading
import time
import traceback
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ldv import protocol as PROTO
from ldv.checks._fixtures import build_keyset, build_reach
from ldv.corpus.loader import load_edges, load_nodes

FIELDS = ("require", "forbid")
UI_PLUGINS = ("keyset",)

#: 默认语料 —— 实测**内容最多、节点也不少**的那份（7115 项 ⇒ 37 个方向 / 8 层）。
#:
#: ⚠️ 选它不是因为项多，是因为**项多且节点不塌**。实测（2026-10-20，同一套判据）：
#:
#:     语料              项     方向   层   可选键  维度
#:     内置 36           36     25     7    13      3
#:     arxiv-taxonomy   155     13     7     7      1
#:     cora-labeled    2708     13     7     7      1
#:     crossref          74     53    27    27      1
#:     ext-wiki-vote   7115     37     8     9      2   ← 这份
#:
#: ⇒ **项多 ≠ 节点多**：cora 2708 项只有 13 个方向，内置 36 项反而有 25 个。
#:   节点数由**维度**决定，不由项数决定 ⇒ 这份要再涨，只能加维度（且维度要有根据）。
DEFAULT_CORPUS = "ext-wiki-vote"


class App:
    def __init__(self, corpus: str | None = None) -> None:
        import os
        if corpus:
            os.environ["LDV_CORPUS"] = corpus
        from ldv.checks._fixtures import find_corpus
        root = find_corpus()
        if root is None:
            raise SystemExit("找不到语料（用 --corpus 指定，或设 LDV_CORPUS）")
        t0 = time.time()
        self.nodes = load_nodes(root)
        self.edges, _ = load_edges(self.nodes)
        _n = root.parent.name if root.name == "nodes" else root.name
        self.corpus_name = "内置语料（ldv/corpus）" if _n == "corpus" else _n

        # ★ 派生维度：`_data/<corpus>/derived/*.json`（`{"_meta":…, "values":{id: 值}}`）
        #   ⚠️ **不写回材料**：`_data/*` 是所有语料基线的输入，往节点里加字段会让
        #      键集变 ⇒ 树变 ⇒ 所有基线一起漂。派生量只在这一层叠加，删文件即回到原状。
        self.derived: dict[str, str] = {}
        ddir = Path(root).parent / "derived"
        if ddir.is_dir():
            for f in sorted(ddir.glob("*.json")):
                try:
                    doc = json.loads(io.open(f, encoding="utf-8").read())
                    field, vals = str(doc["_meta"]["field"]), doc["values"]
                except Exception:
                    continue
                hit = 0
                for nid, v in vals.items():
                    if nid in self.nodes:
                        self.nodes[nid].keys = frozenset(
                            set(self.nodes[nid].keys) | {f"{field}={v}"})
                        hit += 1
                self.derived[field] = f"{hit} 项（{f.name}）"
        # 只建 keyset；reach 按需（实测：3783 项把 reach 也建了 ⇒ 启动 333 s → 3.6 s）
        self.kernels: dict[str, object] = {}
        self.kernels["keyset"] = build_keyset(self.nodes)[0]
        self._known = ("keyset", "reach")
        self.boot_s = time.time() - t0
        # 树的规模（方向数 / 最大叶）—— 加维度前后要能对比，所以报出来
        _k = self.kernels["keyset"]
        _ds = _k.all_directions()
        _leaves = [d for d in _ds if not _k.children_of(d)]
        self.tree = {"方向数": len(_ds), "层数": max(d.rank for d in _ds),
                     "最大叶": max((len(_k.members_of(d)) for d in _leaves), default=0)}
        self.titles = {nid: str(n.fields.get("title") or n.fields.get("name") or nid)
                       for nid, n in self.nodes.items()}
        self.keys_of = {nid: frozenset(n.keys) for nid, n in self.nodes.items()}
        #: 全语料出现过的键 —— 用来**拒收拼错的键**。
        #  ⚠️ 不拒收会怎样：`type=出度1-4`（打错一个数字）⇒ 答案 0 ⇒ 界面上与
        #     「材料里真没有」**长得一模一样**。那是这个仓库最忌的「退化与正常长得一样」。
        self.all_keys = frozenset().union(*self.keys_of.values()) if self.keys_of else frozenset()
        self.facets, self.n_constant = self._facets()
        self.lock = threading.Lock()
        self.n_queries = 0

    # ── facet：剔掉「对全部项都成立」的键（那是常量，不是维度） ────────────
    def _facets(self) -> tuple[dict[str, list[dict]], int]:
        counts: dict[str, dict[str, int]] = {}
        for ks in self.keys_of.values():
            for k in ks:
                g = k.split("=", 1)[0] + "=" if "=" in k else "（无前缀的键）"
                d = counts.setdefault(g, {})
                d[k] = d.get(k, 0) + 1
        total = len(self.nodes)
        out: dict[str, list[dict]] = {}
        n_const = 0
        for g, kv in counts.items():
            keep = []
            for k, n in sorted(kv.items(), key=lambda x: (-x[1], x[0])):
                if n >= total:          # 勾了不筛掉任何东西 ⇒ 常量，不是维度
                    n_const += 1
                    continue
                keep.append({"key": k, "n": n})
            if keep:
                out[g] = keep[:40]
        return dict(sorted(out.items(), key=lambda kv: -max(i["n"] for i in kv[1]))), n_const

    def ensure(self, plugin: str):
        if plugin in self.kernels:
            return self.kernels[plugin]
        if plugin not in self._known:
            raise KeyError(plugin)
        with self.lock:
            if plugin in self.kernels:
                return self.kernels[plugin]
            k = (build_reach(self.nodes, self.edges)[0] if plugin == "reach"
                 else build_keyset(self.nodes)[0])
            self.kernels[plugin] = k
            return k

    def meta(self) -> dict:
        return {"corpus": self.corpus_name, "nodes": len(self.nodes),
                "edges": sum(len(v) for v in self.edges.values()),
                "plugins": list(self._known), "built": sorted(self.kernels),
                "ui_plugins": list(UI_PLUGINS), "facets": self.facets,
                "n_constant_keys": self.n_constant,
                "derived": self.derived,
                "tree": self.tree,
                "boot_s": round(self.boot_s, 2), "n_queries": self.n_queries}

    # ── 需求 → 答案集（直接遍历，确定） ────────────────────────────────────
    def answer_set(self, req: frozenset[str], forb: frozenset[str]) -> set[str]:
        return {nid for nid, ks in self.keys_of.items()
                if req <= ks and not (ks & forb)}

    # ── 节点的对外形状 ────────────────────────────────────────────────────
    def _node(self, k, d, ans: set[str] | None = None, status: str = "") -> dict:
        mem = k.members_of(d)
        pl = d.payload
        if isinstance(pl, tuple) and len(pl) == 2:
            must, mustnot = sorted(pl[0]), sorted(pl[1])
        else:
            must, mustnot = (sorted(pl) if pl else []), []
        out = {"did": d.did, "rank": d.rank, "n_members": len(mem),
               "must": must, "mustnot": mustnot,
               "n_children": len(k.children_of(d)), "status": status}
        if ans is not None:
            out["n_answer"] = len(ans & mem)
            #: 答案有多少落在这一带（**召回**）—— 根节点必然是 1.0，不说明问题
            out["coverage"] = round(len(ans & mem) / max(1, len(ans)), 3)
            #: 这一带里有多少是答案（**精确**）—— 这个才回答「这一带准不准」
            out["precision"] = round(len(ans & mem) / max(1, len(mem)), 3)
        return out

    def _resolve(self, k, req, forb):
        """把答案集**切成几支**，每一支都是「装不下就能再分」的最细节点。

        用户 2026-10-20 选 C：「先说横跨 N 支，再列 N 个」。

            在某节点 d 上，`P = 答案 ∩ d 的成员`
            只有一个子节点含 P 的全部 ⇒ 往下（这一支还没分叉）
            有 ≥2 个子节点各含 P 的一部分 ⇒ **在 d 这里切开**，每支递归
            没有子节点 ⇒ 这一支到底了

        ⇒ 答案集中 ⇒ 返回 1 个节点（与旧行为**逐字相同**）
        ⇒ 答案散开 ⇒ 返回 N 个节点，且**它们合起来正好是答案**（不重不漏）
        """
        ans = self.answer_set(req, forb)
        if not ans:
            return [self._node(k, k.root, ans, "没有项满足这组约束")], [], "empty", ans
        path: list[str] = []
        d = k.root
        # 先走「单支」的那一段（这一段的路径要报出来）
        while True:
            kids = k.children_of(d)
            if not kids:
                return [self._node(k, d, ans, "已经是最细的一层")], path, "leaf", ans
            parts = [(c, ans & k.members_of(c)) for c in kids]
            parts = [(c, p) for c, p in parts if p]
            if len(parts) == 1 and parts[0][1] == ans:
                path.append(d.did)
                d = parts[0][0]
                continue
            break
        if len(parts) == 1:                     # 剩下那点不全在这一支（滞留项）⇒ 停在这
            return [self._node(k, d, ans, "剩下的项留在这一层，子方向装不下")], path, "leaf", ans
        nodes: list[dict] = []
        for c, p in parts:
            nodes.extend(self._spread(k, ans, c))
        why = f"答案横跨 {len(nodes)} 支，每支都装不下全部"
        return nodes, path, "split", ans

    def _spread(self, k, full: set[str], d, depth: int = 0) -> list[dict]:
        """在 `d` 之下继续切，直到每一支都由单一子节点装不下为止。"""
        if depth > 64:                          # 防病态深树（正常到不了）
            return [self._node(k, d, full, "太深了，停在这是为了不递归爆栈")]
        kids = k.children_of(d)
        if not kids:
            return [self._node(k, d, full, "已经是最细的一层")]
        parts = [(c, full & k.members_of(c)) for c in kids]
        parts = [(c, p) for c, p in parts if p]
        mine = full & k.members_of(d)
        if len(parts) == 1 and parts[0][1] == mine:
            return self._spread(k, full, parts[0][0], depth + 1)
        if not parts:                           # 理论上不会（mine 非空）
            return [self._node(k, d, full, "")]
        out: list[dict] = []
        for c, _p in parts:
            out.extend(self._spread(k, full, c, depth + 1))
        return out

    # ── ① 建议：用户打「出度」也要能匹配到 `type=出度1-3` ──────────────────
    def suggest(self, q: str, limit: int = 8) -> dict:
        """**纯字符串匹配**（子串 + 前缀优先），不涉及任何语义。

        ⚠️ 为什么不用 `<datalist>`：它只按**开头**匹配，而键是 `type=出度1-3`，
           用户打「出度」永远匹配不到 ⇒ 必须自己按**子串**匹配。
        ⚠️ 裸字段名（`id` / `title`）不进建议：它们恒真、零区分力（facet 也隐藏了）。
        """
        q = (q or "").strip().lower()
        if not q:
            return {"ok": True, "items": []}
        cnt = Counter()
        for ks in self.keys_of.values():
            for k in ks:
                cnt[k] += 1
        hit = [k for k in self.all_keys if "=" in k and q in k.lower()]
        hit.sort(key=lambda k: (not k.lower().startswith(q), -cnt.get(k, 0), k))
        return {"ok": True, "items": [{"key": k, "n": cnt.get(k, 0)} for k in hit[:limit]]}

    # ── ② 零结果松弛：去掉哪个键就有结果（逐键试一遍，确定性） ──────────────
    def _relax(self, req: frozenset[str], forb: frozenset[str]) -> list[dict]:
        out: list[dict] = []
        for k in sorted(req):
            sub = self.answer_set(req - {k}, forb)
            if sub:
                out.append({"side": "require", "drop": k, "n_answer": len(sub)})
        for k in sorted(forb):
            sub = self.answer_set(req, forb - {k})
            if sub:
                out.append({"side": "forbid", "drop": k, "n_answer": len(sub)})
        out.sort(key=lambda x: -x["n_answer"])
        return out[:3]

    # ── ④ 示例：**按数据形状挑的**，不是猜用户想问什么 ──────────────────────
    def examples(self, k: int = 3) -> dict:
        flat = [(x["n"], x["key"]) for v in self.facets.values() for x in v]
        if not flat:
            return {"ok": True, "items": []}
        flat.sort(reverse=True)
        out = [{"label": f"覆盖最多的一支（{flat[0][0]} 项）", "require": [flat[0][1]]},
               {"label": f"最稀的一支（{flat[-1][0]} 项）", "require": [flat[-1][1]]}]
        gs = list(self.facets)
        if len(gs) >= 2 and k >= 3:
            a, b = self.facets[gs[0]][0], self.facets[gs[1]][0]
            out.append({"label": "两个维度交叉", "require": [a["key"], b["key"]]})
        return {"ok": True,
                "note": "这几条是按**数据形状**挑的（最大/最小/交叉），不是猜你想问什么",
                "items": out[:k]}

    def query(self, spec: dict) -> dict:
        plugin = str(spec.get("plugin") or "keyset")
        if plugin not in self._known:
            return {"ok": False, "error": f"没有插件 {plugin!r}（有 {list(self._known)}）"}
        extra = sorted(set(spec) - set(FIELDS) - {"plugin"})
        if extra:
            return {"ok": False, "error": f"不认识的字段 {extra} —— 只认 {list(FIELDS)}"}
        req = frozenset(spec.get("require") or ())
        forb = frozenset(spec.get("forbid") or ())
        # ★ 拼错的键要**当场拒收**，不能静默返回 0 条 ——
        #   「键写错了」与「材料里真没有」在界面上长得一模一样。
        unknown = sorted(k for k in (req | forb) if k not in self.all_keys)
        if unknown:
            import difflib
            hint = {k: difflib.get_close_matches(k, self.all_keys, n=3, cutoff=0.5)
                    for k in unknown}
            return {"ok": False,
                    "error": ("这些键在语料里不存在：" +
                              "、".join(f"`{k}`" for k in unknown) +
                              "。可能是拼错了 —— 最近的几个：" +
                              "；".join(f"`{k}` → " + ("、".join(f"`{x}`" for x in v) or "（没有相近的）")
                                       for k, v in hint.items())),
                    "unknown_keys": unknown, "suggest": hint}
        k = self.ensure(plugin)
        t0 = time.perf_counter()
        with self.lock:                      # `query` 会写（expand）⇒ 串行
            nodes, path, why, ans = self._resolve(k, req, forb)
        ms = (time.perf_counter() - t0) * 1000
        self.n_queries += 1
        # ⚠️ **接口不截断**：`nodes` 给全部支，`n_branches` 与 `len(nodes)` 恒等。
        #    只有界面才截显示（否则「Σ各支 == 答案」在客户端无法验证 —— 实测踩到：
        #    空需求 31 支被截成 12 支，合计 3610 ≠ 7115，看起来像切分漏了）。
        #    需要截断的调用方自己传 `max_branches`，那时 `branches_shown` 会小于 `n_branches`。
        cap = int(spec.get("max_branches") or 0)
        shown = nodes[:cap] if cap > 0 else nodes
        return {"ok": True, "plugin": plugin,
                "requirement": {"require": sorted(req), "forbid": sorted(forb)},
                # 1 支时 `node` 仍给（兼容旧调用方）；多支时 `node` 为 null，看 `nodes`
                "node": nodes[0] if len(nodes) == 1 else None,
                "nodes": shown,
                "n_branches": len(nodes),
                "branches_shown": len(shown),
                "path": path, "stopped": why,
                "n_answer": len(ans),
                "answer_ids": sorted(ans),
                "answer_titles": [self.titles.get(i, i) for i in sorted(ans)],
                # ② 零结果时给「去掉哪个键就有结果」——逐键试一遍，确定性
                "relax": (self._relax(req, forb) if not ans else []),
                "ms": round(ms, 2)}

    def fingerprint(self) -> str:
        """**结构指纹** —— 「这是在**哪份结构**上跑出来的」（协议的 `fp` 段）。

        ⚠️ 不含时间戳：指纹要**稳定可比**（同结构同值），不是「跑的时间」。
        ⚠️ 它只描述**形状**（语料名 / 项数 / 键数 / 方向数 / 各支条数），
           不含内容 —— 所以它是「同一份材料的同一棵树」的票据，不是内容哈希。
        ⚠️ 没有它，一条 RESULT 无法自证是在哪棵树上跑的 —— 那就是
           `§T2` 那条「看着都对、其实配错」的协议版。
        """
        import hashlib
        h = hashlib.sha1()
        h.update(self.corpus_name.encode("utf-8"))
        for part in (len(self.nodes), len(self.all_keys),
                     self.tree["方向数"], self.tree["最大叶"]):
            h.update(("|" + str(part)).encode())
        return h.hexdigest()[:16]

    def who(self) -> str:
        """本端在协议里的身份。默认「语料名@ldv」—— 不做网络身份管理。"""
        return f"{self.corpus_name}@ldv-entry"

    def msg(self, msg) -> dict:
        """**对等消息入口**：收一条信封 ⇒ 回一条信封（`/api/msg` 用）。

        形状照 `ldv.protocol`（内容层）：五查 envelope ⇒ 只跑 `QUERY` ⇒
        RESULT 必须过 `verify_result` 才发出。**拒收带理由，不静默。**
        """
        ok, why = PROTO.check_envelope(msg)
        if not ok:
            return PROTO.error_envelope(why, frm=self.who())
        if msg.get("type") != PROTO.MSG_QUERY:
            return PROTO.error_envelope(
                f"只处理 {PROTO.MSG_QUERY}，收到 {msg.get('type')!r}（对端角色可能反了）",
                frm=self.who())
        try:
            res = self.query(msg.get("payload") or {})
            r = PROTO.build_result(msg, res)
            v_ok, v_bad = PROTO.verify_result(r)
            if not v_ok:                      # 自己产出的结果必须过自己的自检
                return PROTO.error_envelope(f"本端结果自检未过：{v_bad[:2]}", frm=self.who())
            return r
        except Exception as e:                # 一条坏不拖垮调用方
            return PROTO.error_envelope(f"{type(e).__name__}: {e}", frm=self.who())

    def drill(self, did: str, req=(), forb=()) -> dict:
        k = self.ensure("keyset")
        try:
            d = k.direction(did)
        except Exception as e:
            return {"ok": False, "error": f"没有这个节点 {did!r}：{e}"}
        ans = self.answer_set(frozenset(req), frozenset(forb))
        return {"ok": True, "parent": self._node(k, d, ans),
                "children": [self._node(k, c, ans) for c in k.children_of(d)]}

    def batch(self, specs: list[dict]) -> dict:
        out = []
        for s in specs:
            try:
                out.append(self.query(s))
            except Exception as e:                 # 一条坏不拖垮整批
                out.append({"ok": False, "error": f"{type(e).__name__}: {e}"})
        return {"ok": True, "n": len(out), "results": out}

    # ── 给 AI 的说明书（前端不展示，HTTP 上可读） ──────────────────────────
    def api_doc(self) -> dict:
        return {
            "what": "把一条需求（键约束）映射成树上的一个节点。不排序、不打分、不返回结果列表。",
            "endpoints": {
                "GET /api": "本说明",
                "GET /api/meta": "语料规模 + 可用键（facet 的来源）",
                "GET /api/query?require=a,b&forbid=c": "一条需求 → 一个节点（或 N 支）",
                "GET /api/suggest?q=出度": "键的建议（**子串**匹配，不涉及语义）",
                "GET /api/examples": "按数据形状挑的示例需求（最大/最小/交叉）",
                "POST /api/query": '{"require":["…"],"forbid":["…"]} 或 {"queries":[…]}（批处理）',
                "POST /api/drill": '{"did":"D3","require":["…"]} 往下看一层，子节点带覆盖率',
                "POST /api/msg": "**对等消息**（`ldv-protocol`）：收一条信封回一条信封",
            },
            "protocol": {
                "version": PROTO.PROTOCOL_VERSION,
                "types": list(PROTO.KNOWN_TYPES),
                "shape": "信封 = proto/type/from/fp/ts/payload；缺一 ⇒ 拒收并给理由",
                "message": "QUERY = {require,forbid}（键约束，无自然语言）；"
                           "RESULT = 本端 query() 的返回 + 原查询回声 + fp",
                "self_check": "verify_result：n_answer==len(answer_ids) 且 Σ各支==n_answer"
                              " —— 接收方不依赖发送方诚信",
                "forbidden": "跨系统标量字段（score/rank/order/weight/priority/rating）"
                             " ⇒ 出现即拒收（E-1/E-2 的协议延伸）",
                "file_transport": "python -m ldv.protocol --in in.jsonl --out out.jsonl",
            },
            "query_response": {
                "nodes": "答案切成的若干支（1 支 = 落在一带；N 支 = 横跨 N 带，合起来正好是答案）",
                "node": "只有 1 支时才给（兼容旧调用方）；多支时为 null，看 nodes",
                "n_branches": "真实支数",
                "n_answer": "答案项数（直接遍历，确定）",
                "answer_ids": "答案项 id", "answer_titles": "答案项标题",
                "path": "从根走到分叉处的节点 id", "stopped": "leaf|split|empty",
                "nodes[].precision": "这一支里有多少是答案（准不准）",
                "nodes[].coverage": "答案有多少落在这一支（散没散）",
                "relax": "只有 0 结果时给：去掉哪个键就有几项（逐键试过，确定性）",
            },
            "boundaries": ["只读", "单进程串行（query 会 expand）", "只绑 127.0.0.1，无鉴权"],
            "batch": "--batch in.jsonl --out out.jsonl（不起服务，一行一条需求）",
        }


APP: App | None = None


class Handler(BaseHTTPRequestHandler):
    server_version = "ldv-entry/0.4"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False, indent=1).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _url(self):
        """`http.server` 按 iso-8859-1 解 request-line ⇒ 中文参数会乱码（实测命中 0）。"""
        try:
            return urlparse(self.path.encode("iso-8859-1").decode("utf-8"))
        except (UnicodeEncodeError, UnicodeDecodeError):
            return urlparse(self.path)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode("utf-8") or "{}")

    def do_GET(self) -> None:  # noqa: N802
        u = self._url()
        if u.path in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return
        if u.path == "/api":
            self._json(APP.api_doc())
            return
        if u.path == "/api/meta":
            self._json(APP.meta())
            return
        if u.path == "/api/query":
            qs = parse_qs(u.query)
            spec: dict = {}
            for f in FIELDS:
                spec[f] = [x for c in (qs.get(f) or []) for x in c.split(",") if x]
            self._json(APP.query(spec))
            return
        if u.path == "/api/suggest":
            self._json(APP.suggest((parse_qs(u.query).get("q") or [""])[0]))
            return
        if u.path == "/api/examples":
            self._json(APP.examples())
            return
        if u.path == "/api/drill":
            qs = parse_qs(u.query)
            self._json(APP.drill(
                (qs.get("did") or [""])[0],
                [x for c in (qs.get("require") or []) for x in c.split(",") if x],
                [x for c in (qs.get("forbid") or []) for x in c.split(",") if x]))
            return
        self._json({"ok": False, "error": "没有这个路径"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        u = self._url()
        try:
            body = self._body()
        except Exception as e:
            self._json({"ok": False, "error": f"JSON 解析失败：{e}"}, 400)
            return
        try:
            if u.path == "/api/drill":
                self._json(APP.drill(str(body.get("did") or ""),
                                     body.get("require") or (), body.get("forbid") or ()))
            elif u.path == "/api/msg":      # 对等消息（ldv-msg/1 信封）
                # ⚠️ 必须用**已解析的** body：流读一次就没了，再调会读到空
                self._json(APP.msg(body))
            elif u.path == "/api/query":
                if isinstance(body, dict) and "queries" in body:
                    self._json(APP.batch(list(body["queries"])))
                elif isinstance(body, list):
                    self._json(APP.batch(body))
                else:
                    self._json(APP.query(body))
            else:
                self._json({"ok": False, "error": "没有这个路径"}, 404)
        except Exception:
            self._json({"ok": False, "error": traceback.format_exc(limit=3)}, 500)


PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>这一带</title>
<style>
:root{--bg:#fbfbfd;--fg:#1c1f23;--mut:#6b7280;--line:#e5e7eb;--acc:#2563eb;
--card:#fff;--soft:#f3f4f6;--req:#1d4ed8;--forb:#b91c1c}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.65 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:760px;margin:0 auto;padding:56px 24px 100px}
h1{margin:0 0 6px;font-size:16px;font-weight:650;letter-spacing:.3px}
.sub{color:var(--mut);font-size:12.5px;margin-bottom:30px}
.sub b{color:var(--fg);font-weight:600}
.bar{display:flex;gap:8px;margin-bottom:10px}
input[type=text]{flex:1;padding:13px 16px;border:1px solid var(--line);border-radius:12px;
font-size:15px;background:#fff;outline:none}
input[type=text]:focus{border-color:var(--acc);box-shadow:0 0 0 3px #2563eb14}
button{padding:12px 16px;border:1px solid var(--line);border-radius:12px;background:#fff;
font-size:14px;cursor:pointer;color:var(--fg)}
button.primary{background:var(--acc);color:#fff;border-color:var(--acc);font-weight:600}
button:hover{border-color:#c7cdd6}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:2px 0 6px;min-height:24px}
#sugg{display:flex;flex-wrap:wrap;gap:6px;margin:-4px 0 8px}
.sg{font-size:12.5px;padding:4px 9px;border-radius:8px;border:1px solid #bfdbfe;
background:#eff6ff;color:#1d4ed8;cursor:pointer;font-family:ui-monospace,Consolas,monospace}
.sg:hover{background:#dbeafe}
.sg b{color:#6b7280;font-weight:500;margin-left:6px;font-size:11px}
#ex{margin:0 0 10px}
.exl{font-size:12px;color:var(--mut);margin-right:6px}
.ex{font-size:12.5px;padding:4px 10px;border-radius:999px;border:1px dashed var(--line);
color:#374151;cursor:pointer;margin-right:6px;background:#fff}
.ex:hover{border-color:var(--acc);color:var(--acc)}
.rl{display:inline-block;font-size:12.5px;padding:5px 10px;border-radius:8px;
border:1px solid #fde68a;background:#fffbeb;color:#92400e;cursor:pointer;margin:4px 6px 0 0}
.rl:hover{background:#fef3c7}
.chip{font-size:12.5px;padding:4px 9px;border-radius:999px;border:1px solid var(--line);
background:var(--soft);cursor:pointer}
.chip.req{background:#dbeafe;border-color:#93c5fd;color:var(--req)}
.chip.forb{background:#fee2e2;border-color:#fca5a5;color:var(--forb);text-decoration:line-through}
.facets{margin:6px 0 28px}
.facets summary{cursor:pointer;font-size:12.5px;color:var(--mut);padding:3px 0}
.fg{margin:8px 0 12px}
.fg .g{font-size:12px;color:var(--mut);margin-bottom:4px}
.f{display:inline-block;font-size:12.5px;padding:3px 9px;margin:0 5px 5px 0;
border:1px solid var(--line);border-radius:8px;cursor:pointer;background:#fff}
.f:hover{background:var(--soft)}
.f.on{background:#dbeafe;border-color:#93c5fd;color:var(--req);font-weight:600}
.f.off{background:#fee2e2;border-color:#fca5a5;color:var(--forb);text-decoration:line-through}
.f b{color:var(--mut);font-weight:500;margin-left:5px;font-size:11px}
.ans{border:1px solid var(--line);border-radius:14px;background:var(--card);padding:20px 22px;
margin-bottom:12px}
.ans .lbl{font-size:12px;color:var(--mut);margin-bottom:7px}
.ans .what{font-size:17px;font-weight:600;line-height:1.55}
.ans .what code{font-size:13px;background:#eef2ff;color:#3730a3;padding:2px 7px;
border-radius:6px;font-weight:500}
.ans .meta{font-size:12.5px;color:var(--mut);margin-top:10px}
.ans .meta b{color:var(--fg);font-weight:600;font-variant-numeric:tabular-nums}
.ans .why{font-size:12.5px;color:#374151;margin-top:10px;padding-top:10px;
border-top:1px dashed var(--line)}
.ans .more{margin-top:14px}
.tiny{font-size:12px;color:var(--mut);margin-top:8px}
.tiny a{color:var(--acc);text-decoration:none}
.empty{color:var(--mut);font-size:13.5px;padding:24px 0;text-align:center}
.bar2{height:5px;background:var(--soft);border-radius:3px;overflow:hidden;margin-top:8px}
.bar2 i{display:block;height:100%;background:#93c5fd}
</style></head><body><div class="wrap">
<h1>这一带</h1>
<div class="sub">写下需求，给你<b>树上的一个节点</b> —— 对上哪一带，就给哪一带。</div>

<div class="bar">
  <input type="text" id="q" autocomplete="off"
    placeholder="键，空格分隔；前面加 - 表示不含（打几个字就有建议）">
  <button class="primary" id="go">找</button>
</div>
<div id="sugg"></div>
<div id="ex"></div>
<div class="chips" id="chips"></div>
<details class="facets" id="fbox"><summary>或者点选（点一下＝必须含，再点＝必须不含）</summary>
<div id="facets"></div></details>

<div id="out"><div class="empty">写下需求，或展开点选。</div></div>
</div>
<script>
const S={req:new Set(),forb:new Set(),meta:null,last:null};
const $=id=>document.getElementById(id);
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

async function boot(){
  S.meta=await (await fetch('/api/meta')).json();
  renderFacets(); renderChips(); loadExamples();
}
// ④ 示例：按**数据形状**挑的（最大/最小/交叉），不是猜你想问什么
async function loadExamples(){
  try{
    const r=await (await fetch('/api/examples')).json();
    S.examples=r.items||[];
    $('ex').innerHTML='<span class="exl">试试：</span>'+S.examples.map((x,i)=>
      '<span class="ex" data-i="'+i+'">'+esc(x.label)+'</span>').join('');
    [...$('ex').querySelectorAll('.ex')].forEach(e=>e.onclick=()=>{
      const x=S.examples[+e.dataset.i];
      S.req=new Set(x.require); S.forb=new Set();
      renderFacets();renderChips();run();
    });
  }catch(e){}
}
// ① 建议：按**子串**匹配（打「出度」要能匹配 `type=出度1-3`；`<datalist>` 做不到）
async function onInput(){
  const v=$('q').value.trim().split(/\s+/).pop()||'';
  if(!v){$('sugg').innerHTML='';return}
  try{
    const r=await (await fetch('/api/suggest?q='+encodeURIComponent(v))).json();
    const items=r.items||[];
    $('sugg').innerHTML=items.map(it=>'<span class="sg" data-k="'+esc(it.key)+'">'+
      esc(it.key)+'<b>'+it.n+'</b></span>').join('');
    [...$('sugg').children].forEach(e=>e.onclick=()=>{
      S.req.add(e.dataset.k); S.forb.delete(e.dataset.k);
      $('q').value=''; $('sugg').innerHTML='';
      renderFacets();renderChips();run();
    });
  }catch(e){}
}
function renderFacets(){
  const box=$('facets'); box.innerHTML='';
  for(const [g,items] of Object.entries(S.meta.facets)){
    const d=document.createElement('div'); d.className='fg';
    d.innerHTML='<div class="g">'+esc(g)+'</div>';
    for(const it of items){
      const e=document.createElement('span'); e.className='f';
      if(S.req.has(it.key))e.classList.add('on');
      if(S.forb.has(it.key))e.classList.add('off');
      e.innerHTML=esc(it.key)+'<b>'+it.n+'</b>';
      e.onclick=()=>toggle(it.key);
      d.appendChild(e);
    }
    box.appendChild(d);
  }
}
function toggle(k){
  if(S.req.has(k)){S.req.delete(k);S.forb.add(k)}
  else if(S.forb.has(k)){S.forb.delete(k)}
  else{S.req.add(k)}
  renderFacets();renderChips();run();
}
function chip(k,cls){
  const e=document.createElement('span'); e.className='chip '+cls; e.textContent=k;
  e.onclick=()=>{if(cls==='req'){S.req.delete(k);S.forb.add(k)}else{S.forb.delete(k)}
    renderFacets();renderChips();run();};
  return e;
}
function renderChips(){
  const c=$('chips'); c.innerHTML='';
  for(const k of S.req)c.appendChild(chip(k,'req'));
  for(const k of S.forb)c.appendChild(chip(k,'forb'));
  if(!S.req.size&&!S.forb.size)
    c.innerHTML='<span class="tiny" style="margin:0">（还没有约束 —— 等于看整棵树）</span>';
}
function parseBar(){
  const v=$('q').value.trim(); if(!v)return;
  for(const t of v.split(/\s+/)){
    if(!t)continue;
    if(t.startsWith('-')){S.forb.add(t.slice(1));S.req.delete(t.slice(1))}
    else{S.req.add(t);S.forb.delete(t)}
  }
  $('q').value='';
}
function nodeCard(n,main,ansTotal){
  const must=n.must.length?n.must.join(' '):'∅';
  const mustnot=n.mustnot.length?n.mustnot.join(' '):'∅';
  let cov='';
  // ⚠️ 主卡片与下钻卡片要显示**不同的比率**，别搞混：
  //    主卡片（这一带准不准）→ **精确** = 这一带里有多少是答案
  //      实测：`入度0` 有 4734 项，散在根的两个子方向里 ⇒ 停在根（7115 项），精确 66%
  //    下钻卡片（答案怎么散开）→ **召回** = 你的答案有多少落在这一支
  if(main){
    if(n.n_answer!==undefined&&n.precision!==undefined&&n.precision<1){
      const p=Math.round(100*n.precision);
      cov='<div class="meta">这一带 <b>'+n.n_members+'</b> 项里，有 <b>'+n.n_answer+
          '</b> 项符合你的需求（'+p+'%）<div class="bar2"><i style="width:'+p+'%"></i></div></div>';
    }
  }else if(ansTotal&&n.n_answer!==undefined&&n.coverage!==undefined){
    const p=Math.round(100*n.coverage);
    cov='<div class="meta">你的 '+ansTotal+' 项里，落在这一支有 <b>'+n.n_answer+
        '</b> 项（'+p+'%）<div class="bar2"><i style="width:'+p+'%"></i></div></div>';
  }
  return '<div class="ans">'+
    (main?'<div class="lbl">你问的是这一带</div>':'')+
    '<div class="what"><code>'+esc(n.did)+'</code> 必须含 '+esc(must)+
      '　不含 '+esc(mustnot)+'</div>'+
    '<div class="meta">第 <b>'+n.rank+'</b> 层 · <b>'+n.n_members+'</b> 项 · 往下 '+
      (n.n_children?('<b>'+n.n_children+'</b> 个方向'):'没有更细的了')+'</div>'+
    cov+
    (n.status?'<div class="why">'+esc(n.status)+'</div>':'')+
    (n.n_children?'<div class="more"><button onclick="drill(\''+n.did+'\')">往下看一层</button></div>':'')+
    '</div>';
}
async function run(){
  parseBar();renderFacets();renderChips();
  const body={require:[...S.req],forbid:[...S.forb]};
  const r=await (await fetch('/api/query',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
  const out=$('out');
  if(!r.ok){out.innerHTML='<div class="empty" style="color:#b91c1c">'+esc(r.error)+'</div>';return}
  S.last=r;
  if(!r.n_answer){
    // ② 零结果松弛：告诉用户「去掉哪个键就有结果」——逐键试过，不是猜
    const rl=(r.relax||[]);
    out.innerHTML='<div class="empty">没有项满足这组约束。</div>'+
      (rl.length? '<div style="text-align:center;font-size:12.5px;color:#6b7280">'+
        '去掉一个就有了：</div><div style="text-align:center">'+
        rl.map((x,i)=>'<span class="rl" data-i="'+i+'">去掉 '+
          (x.side==='forbid'?'不含 ':'')+esc(x.drop)+' → <b>'+x.n_answer+'</b> 项</span>').join('')+
        '</div>' : '');
    [...out.querySelectorAll('.rl')].forEach(e=>e.onclick=()=>{
      const x=rl[+e.dataset.i];
      if(x.side==='forbid') S.forb.delete(x.drop); else S.req.delete(x.drop);
      renderFacets();renderChips();run();
    });
    return;
  }
  const ns=r.nodes||(r.node?[r.node]:[]);
  const foot='<div class="tiny">答案 <b>'+r.n_answer+'</b> 项 · 路径：'+
    (r.path.length?r.path.join(' → ')+' → 这':'根就是这')+'　·　'+r.ms+' ms</div>';
  if(ns.length===1){
    out.innerHTML=nodeCard(ns[0],true,r.n_answer)+foot;
    return;
  }
  // C：先说横跨几支，再列各支。**界面才截显示**（接口给全，见 App.query 的注释）。
  const CAP=12;
  const show=ns.slice(0,CAP);
  const more=ns.length>CAP
    ? '（只列前 '+CAP+' 支，共 '+ns.length+' 支）' : '';
  out.innerHTML='<div class="ans" style="padding:14px 20px">'+
    '<div class="what" style="font-size:15.5px">你这需求<b>横跨 '+r.n_branches+' 支</b>'+more+'</div>'+
    '<div class="meta">没有单独一支装得下全部 '+r.n_answer+' 项 —— 所以下面分开给，而不是给一个更大的祖先</div>'+
    '</div>'+show.map(c=>nodeCard(c,false,r.n_answer)).join('')+foot;
}
async function drill(did){
  const r=await (await fetch('/api/drill',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({did,require:[...S.req],forbid:[...S.forb]})})).json();
  const out=$('out');
  if(!r.ok){out.innerHTML='<div class="empty" style="color:#b91c1c">'+esc(r.error)+'</div>';return}
  const total=S.last?S.last.n_answer:0;
  if(!r.children.length){
    out.innerHTML='<div class="tiny" style="margin-bottom:10px">'+
      '<a href="#" onclick="run();return false">← 回到这一带</a></div>'+
      '<div class="empty">'+esc(did)+' 已经是最细的一层了。</div>';
    return;
  }
  out.innerHTML='<div class="tiny" style="margin-bottom:10px">'+
    '<a href="#" onclick="run();return false">← 回到这一带</a>　'+
    esc(did)+' 往下 '+r.children.length+' 个方向</div>'+
    r.children.map(c=>nodeCard(c,false,total)).join('');
}
$('go').onclick=run;
$('q').addEventListener('keydown',e=>{if(e.key==='Enter')run()});
$('q').addEventListener('input',onInput);
boot();
</script></body></html>"""


def run_batch(app: App, inp: Path, outp: Path) -> int:
    n = 0
    with io.open(inp, encoding="utf-8") as f, io.open(outp, "w", encoding="utf-8") as g:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                g.write(json.dumps(app.query(json.loads(line)), ensure_ascii=False) + "\n")
            except Exception as e:
                g.write(json.dumps({"ok": False, "error": f"坏行：{e}"}, ensure_ascii=False) + "\n")
            n += 1
    print(f"{n} 行 → {outp}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--corpus", default=None,
                    help=f"语料名（默认 {DEFAULT_CORPUS}；也可用 LDV_CORPUS 环境变量）")
    ap.add_argument("--batch", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    global APP
    # 默认用**真实分类表**，不用仓库那 36 项 demo —— 入口是给人用的，得先有内容。
    # ⚠️ 这里是「换材料」，不是「造材料」：155 项全部来自 arXiv 官方分类表，
    #    `filled_by` 逐项写着出处。**不代填任何内容**（立项红线）。
    corpus = a.corpus or os.environ.get("LDV_CORPUS") or DEFAULT_CORPUS
    APP = App(corpus)
    print(f"语料 {APP.corpus_name}｜{len(APP.nodes)} 项｜建树 {APP.boot_s:.2f}s")
    if a.batch:
        return run_batch(APP, Path(a.batch), Path(a.out or "out.jsonl"))
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"→ http://127.0.0.1:{a.port}/   （Ctrl-C 停）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n停了")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
