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
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ldv import protocol as PROTO
from ldv.checks._fixtures import build_keyset, build_reach
from ldv.core import clock
from ldv.core import declare as DECL
from ldv.core.interfaces import WeightedRecord
from ldv.corpus.loader import load_edges, load_nodes
from ldv.tools import usage_store

FIELDS = ("require", "forbid", "impact", "max_branches", "reject_below")

#: `spec` 里允许出现、但**不是查询维度**的键（`auto_declare` 要在校验前放行，
#: 否则「带自动声明的重发」会被自己的字段白名单挡掉 —— 实测踩过）。
SPEC_EXTRA = ("plugin", "auto_declare", "why")
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
#: 优先语料 —— 实测**内容最多、节点也不少**的那份（7115 项 ⇒ 37 个方向 / 8 层）。
#: 选它的判据是「项多且节点不塌」，不是单纯项多（实测：cora 2708 项只有 13 个方向，
#: 内置 36 项反而有 25 个 ⇒ **项多 ≠ 节点多**，节点数由**维度**决定）。
#:
#: ★★ 2026-10-21 **本地 vs 远端的区分**：它住在 `_data/` 下，而 `_data/*`
#:   **不进仓库**（.gitignore：外部语料只进「声明 + 认识」两个小文件）。
#:   把它写成**无条件的默认值** ⇒ 新 clone 上 `python entry.py` 直接起不来
#:   （实测 2026-10-21：`find_corpus()` 返回 None ⇒ SystemExit「找不到语料」）。
#:   ⇒ 改成**回退链**：优先语料的 `nodes/` **在场才算默认**，
#:     否则用随仓库提交的内置语料（`ldv/corpus`，36 项 —— 小，但**任何 clone 上都在**）。
PREFERRED_CORPUS = "ext-wiki-vote"


def default_corpus() -> str:
    """优先语料的 `nodes/` 不在 ⇒ 回落**内置语料**（空名 = 仓内那份）。

    ⚠️ 这不是「猜用户想要什么」，是**不让入口在新 clone 上死掉**。
       兜底与首选如实分开报（`meta.corpus_from`）—— 把兜底说成首选，
       正是「看起来一样、其实配错了」那种形状。
    """
    root = Path(__file__).resolve().parent
    if (root / "_data" / PREFERRED_CORPUS / "nodes").is_dir():
        return PREFERRED_CORPUS
    return ""                    # 内置语料：find_corpus 的最后一级兜底


class App:
    def __init__(self, corpus: str | None = None, *, root=None,
                 usage_now=None) -> None:
        """`corpus` 按**名字**找语料（走 `LDV_CORPUS` + 候选链）；
        `root` 直接给**目录**（服务壳走这条：它已经知道租户在哪，不该再靠环境变量）。

        ⚠️ `corpus` 那条路会**改进程环境变量** `LDV_CORPUS`（2026-10-20 起就是这样）。
           单进程单语料时没问题，但一个进程服务多个租户时，两个并发请求会互相覆盖 ——
           而「覆盖」的表现是**安静地读错语料**，不是报错。`root=` 就是为此加的：
           它一个环境变量都不碰。P1 的 `/ask` 走 `root=`。
        """
        import os
        if root is not None:
            root = Path(root)
            if not root.is_dir():
                raise SystemExit(f"语料目录不存在：{root}")
        else:
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

        # ★ 派生维度：`<语料>/derived/*.json`（`{"_meta":…, "values":{id: 值}}`）
        #   ⚠️ **不写回材料**：`_data/*` 是所有语料基线的输入，往节点里加字段会让
        #      键集变 ⇒ 树变 ⇒ 所有基线一起漂。派生量只在这一层叠加，删文件即回到原状。
        self.derived: dict[str, str] = {}
        # ★ 派生维度的 `_meta` 也**读出来**（2026-10-26）。
        #   原来这里只取 `_meta["field"]`，于是 `derive` 强制要求的
        #   `rationale` / `derived_from` 写进 sidecar 后**没有任何读取路径** ——
        #   `derive` 的 docstring 说「`derived_from` 决定答案里敢不敢把它当事实」，
        #   而代码从没读过它：**收了税没修路**。
        #   ⚠️ 这里只**读出来**，不参与任何判断（判据 `S44` 守「原样透传」）。
        self.derived_meta: dict[str, dict] = {}
        # ⚠️ `find_corpus()` 对**内置语料**返回 `nodes/`，对**外部语料**返回语料根
        #    ⇒ 不能一律 `.parent`（2026-10-23 实测：外部语料的 sidecar 会去
        #    `_data/derived` 找，**永远找不到且静默跳过** ⇒ 派生维度对外部语料全失效）。
        _croot = Path(root).parent if Path(root).name == "nodes" else Path(root)
        ddir = _croot / "derived"
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
                # `_meta` 原样留着（含 what / why / derived_from / generated_by /
                # derived_at）。`file` 是补的：调用方要知道是哪份 sidecar。
                self.derived_meta[field] = dict(doc.get("_meta") or {},
                                                file=f.name)
        # ★ 值谓词声明（`predicates.json` 的 `字段谓词` 段，2026-11-02）
        #   与 `derived/` 同一侧：**只加键，不改材料**。删掉那一段，结构立刻回到原状。
        #   ⚠️ 只在 `_data/` 下的语料允许**再写**（与 usage 同一条守卫）：
        #      仓库内语料是基线输入，不能让一次查询把它改了。
        self._corpus_root = _croot
        self._can_declare = ("_data" in Path(_croot).parts)
        self.declared: list[dict] = DECL.load(_croot)
        self._decl_ledger: dict = {}
        self.declared_added = 0
        if self.declared:
            self.declared_added = DECL.apply(self.nodes, self.declared, self._decl_ledger)
        # ★ 停层（方向函数外生参数，2026-11-02 敲定）：语料根 `direction.json`
        #   `{"min_leaf": 2}` = 「叶<2 即停」—— keyset 切开后任一侧 < min_leaf
        #   ⇒ 这一 层不建。缺文件 / 坏值 ⇒ 1 = 现状（每侧非空即可）。
        #   与 usage/config.json 同一条规矩：外生落盘，不改代码；删文件即回默认。
        self._min_leaf = 1
        _dj = _croot / "direction.json"
        if _dj.is_file():
            try:
                self._min_leaf = max(1, int(json.loads(
                    io.open(_dj, encoding="utf-8").read()).get("min_leaf", 1)))
            except Exception:
                self._min_leaf = 1        # 坏配置回默认，不猜
        # 只建 keyset；reach 按需（实测：3783 项把 reach 也建了 ⇒ 启动 333 s → 3.6 s）
        self.kernels: dict[str, object] = {}
        self._known = ("keyset", "reach")
        self.lock = threading.Lock()
        self.n_queries = 0
        self._rebuild_structure(reload_usage=False)
        self.boot_s = time.time() - t0
        # ── 使用记录（§4.6 G5 表里「使用者入口」那一格）────────────────────
        # ⚠️ 只在 **`_data/` 下**的语料启用：内置语料的 root 也是 `.../nodes`
        #    （`ldv/corpus/nodes`），按 name == "nodes" 判会把**仓库目录**当成
        #    可写目标 —— 实测当场抓到（`_usage_dir` 落成 `repo/ldv/corpus`）。
        #    `_data/*` 被 gitignore ⇒ 写在那里不算污染仓库。
        _rp = Path(root).resolve()
        self._usage_dir = (_rp.parent if (_rp.name == "nodes" and "_data" in _rp.parts)
                           else None)
        self._last_shown: list[str] = []
        self._last_qfp = ""
        self._tendency: dict[str, float] = {}
        self.usage_loaded = 0
        #: 支级跳过的阈值（`None` = 不跳）。**外生**：每次 `query` 从 spec 取。
        self._reject_below: float | None = None
        # 遗忘的「现在」——**必须可注入**：衰减依赖当前时刻，判据要能固定它，
        # 否则同一条判据在不同时间跑出不同结果（那就是不可复现）。
        self._usage_now = usage_now
        # 半衰期：默认常量；`_load_usage` 里若语料自带 `usage/config.json`
        # （`half_life_hours`）则被覆盖 —— 外生参数落盘，不改代码（2026-11-02）。
        self._half_life: float = usage_store.HALF_LIFE_DAYS
        self._load_usage()

    # ── 使用记录：加载 / 记录 / 清空 ─────────────────────────────────────────
    def type_of(self, nid: str) -> str:
        """项的 `type` —— 给 `reflect.report(type_of=...)` 用。

        ★ 「没被收录进系统的数据」按类型拆，靠的就是这个：材料自带的
          `type` 是**人写在 front matter 里的字段**，不是系统猜的语义。
        ⚠️ 缺 `type` 的项返回 `"未知"` —— **不许**当成「没有这一类」
           （那会让「没写」和「确实没有」长得一样）。
        """
        n = self.nodes.get(nid)
        if n is None:
            return "未知"
        return str(n.fields.get("type") or "未知")

    def _load_usage(self) -> None:
        """把历史使用记录灌进 `kernel.usage`，并算出每个方向的**衰减后**倾向。

        ★★ **记录不改结构**：它只进 `kernel.usage`（一个 list），
           树的项数 / 键集 / 指纹一个都不动。判据 `[接入] ②` 守这条 ——
           这也是「使用记录**不进** `nodes/`」的兑现点（`§K9`）。
        ★★ **遗忘只降权、不删**：倾向由 `usage_store.aged_tendency` 按记录年龄
           加权算（半衰期 `HALF_LIFE_DAYS`）；记录本身一条不少。
           把半衰期关掉（`0`）⇒ 与加衰减之前**逐字相同**（判据 `[遗忘] ④` 的对照）。
        """
        if self._usage_dir is None:
            return
        # 半衰期从语料自带的 `usage/config.json` 读（缺 ⇒ 默认 30 天，逐字不变）
        self._half_life = usage_store.load_half_life(self._usage_dir)
        recs = []
        for r in usage_store.load(self._usage_dir):
            try:
                recs.append(WeightedRecord(did=r["did"], outcome=float(r["outcome"]),
                                           propensity=float(r["propensity"]),
                                           note=r.get("note", ""),
                                           # `at` 是遗忘的输入；放进 extra 不改 dataclass
                                           extra={"at": r.get("at", "")}))
            except ValueError:
                continue                       # 倾向越界的记录不收（§K7 / B11）
        if not recs:
            return
        k = self.kernels["keyset"]
        k.usage.extend(recs)
        self.usage_loaded = len(recs)
        # ★ 遗忘在这里发生：按记录年龄降权（**记录一条不删**）
        self._tendency = usage_store.aged_tendency(self._usage_dir, now=self._usage_now,
                                                   half_life_days=self._half_life)

    def _attention(self, shown: list[dict]) -> dict:
        """把「为什么这么排」作为**只读派生读数**说出来（提案 `exp/m1/ATTENTION_READOUT.md`）。

        ⚠️ **纯派生**：每次由 `usage/*.md` 现算，**不落盘、不接受写入**。
           想改它，唯一的办法是产生新记录（`drill` → `promote`）——
           否则「只追加不覆写 / 曝光不算分 / 时间由内核给」这三条会被绕过（V13-A）。
        ⚠️ **不含分数、不含排名**：字段名避开 `protocol.BANNED_FIELDS`
           （score / weight / priority / rating）。给的是**事实**（条数、天数），
           判断「这算不算重视」留给读者 —— 系统不充当裁判（E-1）。
        ⚠️ **可移除性（I5）**：删掉本字段，`nodes` / `ordered_by` / `n_answer` 逐字不变。
           它不参与任何计算，只是把已有排序的理由说出来；若有人让排序去读它，这条就破了。
        ⚠️ `last_used_days` 为 `null` = 记录缺时间戳 ⇒ 按 `decay_weight` 的约定**不衰减**
           （判据 D3）：既不猜它「很老」，也不猜它「很新」。
        ⚠️ `rows` 与 `nodes` **同序、只含有记录的方向**（I1/I2）——不替没出现过的方向编故事。
        """
        hl = self._half_life          # 语料自带 `usage/config.json` 可改（外生参数落盘）
        # ⚠️ 取值域与 `ordered_by` 相同（`default` / `usage`），**不新造枚举值**：
        #    §S1 要求输出里每个字符串都能指回材料或进协议白名单，而白名单里
        #    本来就有 `default` / `usage` 这两个「排序依据」的取值（2026-11-01 实测抓到的红）。
        if self._usage_dir is None:
            return {"basis": "default", "half_life_days": hl,
                    "n_with_records": 0, "rows": []}
        # ⚠️ 默认走 `clock.now()`（2026-11-02）：`clock.now()` 默认就是
        #    `datetime.now()` ⇒ 输出逐字不变；但它能被 `clock.frozen()` 接管，
        #    否则这一行会让「虚拟时间」在读数里漏回 wall clock。
        now = self._usage_now or clock.now()
        per: dict[str, dict] = {}
        for r in usage_store.load(self._usage_dir):
            did = str(r.get("did") or "")
            p = float(r.get("propensity") or 0.0)
            if not did or not (0.0 < p <= 1.0):
                continue                   # 与 `aged_tendency` 同口径（§K7 / B11）
            e = per.setdefault(did, {"n": 0, "ats": []})
            e["n"] += 1
            if r.get("at"):
                e["ats"].append(str(r["at"]))
        rows = []
        for n in shown:                    # 与 `nodes` 同序 ⇒ 读者能对上号（I1）
            e = per.get(n["did"])
            if not e:
                continue
            ats = sorted(e["ats"])
            days = None
            span = 0
            if ats:
                try:
                    days = max(0, (now - datetime.fromisoformat(ats[-1])).days)
                    if len(ats) >= 2:
                        span = max(0, (datetime.fromisoformat(ats[-1])
                                       - datetime.fromisoformat(ats[0])).days)
                except ValueError:         # 时间戳坏了 ⇒ 当作「不知道时间」
                    days, span = None, 0
            rows.append({"did": n["did"], "n_records": e["n"],
                         "last_used_days": days, "span_days": span})
        return {"basis": "usage" if rows else "default", "half_life_days": hl,
                "n_with_records": len(rows), "rows": rows}

    def _record_use(self, k, d, *, outcome: float = 1.0) -> dict | None:
        """下钻 = 这次「这个方向有用」的**真实**信号（不是合成反馈）。

        走 `usage_store.promote`：从**缓冲区**里把这一支提升成正式记录。
        ⚠️ 不在缓冲里的支**不许 promote** —— 否则就是把「翻到了」当成「用了」。
        ⚠️ 没被下钻的方向**不记负信号**（`§K6`：没看见 ≠ 看见了不好）。

        ★ **`outcome` 可以是负的**（2026-10-25，外部评审 D1）——「这一支被推翻」
          与「这一支有用」是同一本账的两个方向，不是两套真值标准。
          `core/selfopt.collect` 只按 `propensity` 过滤，负值天然进倾向
          （半衰期同样作用于它 ⇒ **时变**可表达，布尔谓词表达不了）。
        ⚠️ 负值**只改倾向、不改候选集** —— 见 `_rejected_dirs` 与 `kernel.py:851`
          的警告（`invalidated` 不参与检索，否则项还在语料里 ⇒ 假阴）。

        ★ **§I7 在这里被消费**：下钻那一刻 `plugin.record(d)` 的输出随记录落盘。
          没有它，一条记录只知道「哪个方向有用」，不知道「**那一刻它长什么样**」——
          而维护会往方向里加项、劈开会造新方向 ⇒ 倾向说的「D5 有用」
          可能对着一个已经不是当初的 D5。契约见 `docs/插件契约.md`。
        ⚠️ `record()` 抛异常**直接传播**（与 §I3 / §I4 同策，这一层不静默）。
        ⚠️ snapshot 只是**留痕**：倾向计算不读它（读了就是使用记录驱动结构，§K9）。
        """
        if self._usage_dir is None:
            return None
        rec = k.record_usage(d, {"outcome": outcome})
        if rec is None:
            return None
        snap = json.dumps(k.plugin.record(d), ensure_ascii=False, sort_keys=True)
        # ⚠️ `at` 必须与**读数**用同一个「现在」（2026-11-02）：本 App 注入了
        #    `usage_now` 就跟着它走，否则交给 `promote` 的 `clock.now()`。
        #    写入用 A、读取用 B ⇒ 年龄算错；若 A > B，年龄为负，
        #    `0.5 ** (负/30) > 1` —— 权重反向增长，曲线是炸的不是平的。
        # ★ 每次被消费的调用都是一次巩固（2026-11-03 用户确认）：不同题目的
        #   两次消费是两个真实事件，各记一条 —— 记账 qfp 加**事件戳**（每次唯一）。
        #   间隔重复的「间隔」由衰减因子表达，不由去重表达。
        #   ⚠️ staging 校验仍用原 qfp（「必须在刚展示的候选里」的闸不动）；
        #     `at` 与事件戳取**同一时刻**，读写同一「现在」。
        stamp = self._usage_now or clock.now()
        use_at = stamp.isoformat(timespec="microseconds")
        out = usage_store.promote(self._usage_dir, qfp=self._last_qfp, did=d.did,
                                  propensity=rec.propensity, snapshot=snap,
                                  outcome=outcome, at=use_at,
                                  event_qfp=f"{self._last_qfp}@{stamp.strftime('%Y%m%dT%H%M%S%f')}",
                                  note="drill-wrong" if outcome < 0 else "drill")
        if out.get("written"):
            self._last_shown = [x for x in self._last_shown if x != d.did]
            # ★ 记完**当场刷新倾向** —— `_load_usage` 只在 `__init__` 跑一次，
            #   不刷新的话 `drill` 返回的 `tendency_after` 是**记之前**的旧值
            #   （读数骗人：刚判错的支看起来「还没被推翻」）。
            self._tendency = usage_store.aged_tendency(self._usage_dir,
                                                       now=self._usage_now,
                                                       half_life_days=self._half_life)
        return out

    def clear_usage(self) -> dict:
        """显式清空 —— **唯一的删除路径**，不做自动淘汰。清完次序回默认。"""
        if self._usage_dir is None:
            return {"ok": False, "error": "内置语料不启用使用记录"}
        n = usage_store.clear(self._usage_dir)
        self.kernels["keyset"].usage.clear()
        self._tendency = {}
        self.usage_loaded = 0
        return {**n, "次序": "已回到默认（无倾向）"}

    # ── facet：剔掉「对全部项都成立」的键（那是常量，不是维度） ────────────
    # ── 结构：建 / 重建 ─────────────────────────────────────────────────
    def _rebuild_structure(self, *, reload_usage: bool = True) -> None:
        """键集变了就整棵重建（声明谓词之后要调）。

        ⚠️ 只重建 `keyset`；`reach` 若已建过就丢掉 —— 它读的是同一份 `keys`，
           留着就是**旧键的树**，比没有更坏。
        """
        self.kernels = {"keyset": build_keyset(self.nodes, min_leaf=self._min_leaf)[0]}
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
        if reload_usage:
            self._load_usage()

    # ── 值谓词：声明 / 提案 ─────────────────────────────────────────────
    def propose(self, keys: list[str]) -> list[dict]:
        """对不认识的键逐条给证据：是**拼错**、**缺维度**，还是**缺谓词**。"""
        return [DECL.evidence(self.nodes, k) for k in keys]

    def declare(self, key: str, why: str = "", *, by: str = "agent",
                allow_empty: bool = False) -> dict:
        """声明一条值谓词并**立刻**重建结构（只对 `_data/` 下的语料开放）。

        返回里 `生效` 才是关键：0 命中的谓词永远不会进 `all_keys`，
        所以「写进文件了」不等于「能用了」。这两件事必须分开报。
        """
        if not self._can_declare:
            return {"ok": False, "error": "这份语料不是可写语料（不在 `_data/` 下）—— "
                                          "声明只对用户语料开放，仓库内基线语料一个字都不改"}
        r = DECL.add(self._corpus_root, key, why=why, by=by,
                     nodes=self.nodes, allow_empty=allow_empty)
        if not r.get("ok"):
            return r
        if r.get("already"):
            r["生效"] = key in self.all_keys
            return r
        self.declared = DECL.load(self._corpus_root)
        self.declared_added = DECL.apply(self.nodes, [r["记录"]], self._decl_ledger)
        self._rebuild_structure()
        r["生效"] = key in self.all_keys
        r["加键"] = self.declared_added
        r["树"] = dict(self.tree)
        return r

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
                 else build_keyset(self.nodes, min_leaf=self._min_leaf)[0])
            self.kernels[plugin] = k
            return k

    def meta(self) -> dict:
        builtin = self.corpus_name == "内置语料（ldv/corpus）"
        return {"corpus": self.corpus_name,
                # 本地 / 远端的区别如实报在这里：外部语料不进仓库，
                # 新 clone 上入口自动兜底到内置那份 —— 不许把兜底印成首选。
                "corpus_from": "builtin" if builtin else "external",
                "corpus_note": ("" if not builtin else
                                "外部语料在 `_data/` 下、**不进仓库** ⇒ 这份是内置兜底；"
                                "`--corpus ext-wiki-vote` 需要本地有语料"),
                "nodes": len(self.nodes),
                "edges": sum(len(v) for v in self.edges.values()),
                "plugins": list(self._known), "built": sorted(self.kernels),
                "ui_plugins": list(UI_PLUGINS), "facets": self.facets,
                "n_constant_keys": self.n_constant,
                "derived": self.derived,
                "min_leaf": self._min_leaf,
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

    def _rejected_dirs(self, k, req: frozenset) -> tuple[set[str], set[str]]:
        """被推翻、且这次查询**没有明确问到**的方向（外部评审 C2，2026-10-25）。

        判定两条，都**不涉语义**：

            ① `倾向 < reject_below` —— 负事件累计（`drill(verdict="wrong")`），
               半衰期同样作用于它 ⇒ **时变**可表达（布尔谓词表达不了）
            ② 查询 require **不包含**该支的全部 `must` ⇒ 只是宽泛覆盖 ⇒ 可跳过；
               反过来 `require ⊇ must` = 用户明确问到它 ⇒ **不跳**

        ⚠️ `reject_below` **外生**（`spec` / 透镜声明）—— 系统不自己发明阈值。
           不设 ⇒ 返回空 ⇒ 与从前的行为**逐字相同**（判据守这一条）。
        ⚠️ 跳过是**产品决定**（人说这一支错了 ⇒ 它的项不再作为答案），
           所以差额必须如实报（`rejected_items`）—— 不许静默改变候选集。
        ⚠️ 与 `kernel.py:851` 对齐：`invalidated` 只进账本、不进检索（否则项还在
           语料里 ⇒ 假阴）。本方法的跳过发生在**入口的切支结果**上、带差额，
           不是内核层的静默假阴。
        """
        rb = self._reject_below
        if rb is None or not self._tendency:
            return set(), set()
        rejected: set[str] = set()
        for d in k.all_directions():
            t = self._tendency.get(d.did)
            if t is None or not (t < rb):
                continue
            pl = d.payload
            must = set(pl[0]) if isinstance(pl, tuple) and len(pl) == 2 else set(pl or ())
            if req and req >= must:            # 明确问到 ⇒ 不跳
                continue
            rejected.add(d.did)
        if not rejected:
            return set(), set()
        lost: set[str] = set()
        for did in rejected:
            lost |= set(k.members_of(k.direction(did)))
        return rejected, lost

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
        # ★ 支级跳过（外部评审 C2）：被推翻、且没被明确问到的支不摆出来，
        #   它们的项从答案里剔除 —— **差额如实报**（query 的 rejected_items）。
        rej_dids, lost = self._rejected_dirs(k, req)
        if lost:
            ans = set(ans) - lost
            if not ans:
                return ([self._node(k, k.root, ans,
                                    "满足约束的项都在被推翻的支里 —— 要看得把 "
                                    "require 说全（含该支的 must），或调 reject_below")],
                        [], "rejected", ans, sorted(rej_dids), len(lost))
        if not ans:
            return [self._node(k, k.root, ans, "没有项满足这组约束")], [], "empty", ans, [], 0
        path: list[str] = []
        d = k.root
        # 先走「单支」的那一段（这一段的路径要报出来）
        while True:
            kids = k.children_of(d)
            if not kids:
                return [self._node(k, d, ans, "已经是最细的一层")], path, "leaf", ans, [], 0
            parts = [(c, ans & k.members_of(c)) for c in kids]
            parts = [(c, p) for c, p in parts if p and c.did not in rej_dids]
            if len(parts) == 1 and parts[0][1] == ans:
                path.append(d.did)
                d = parts[0][0]
                continue
            break
        if len(parts) == 1:                     # 剩下那点不全在这一支（滞留项）⇒ 停在这
            return ([self._node(k, d, ans, "剩下的项留在这一层，子方向装不下")],
                    path, "leaf", ans, [], 0)
        nodes: list[dict] = []
        for c, p in parts:
            nodes.extend(self._spread(k, ans, c, skip=rej_dids))
        why = f"答案横跨 {len(nodes)} 支，每支都装不下全部"
        return nodes, path, why, ans, sorted(rej_dids), len(lost)

    def _spread(self, k, full: set[str], d, depth: int = 0,
                skip: frozenset[str] | set[str] = frozenset()) -> list[dict]:
        """在 `d` 之下继续切，直到每一支都由单一子节点装不下为止。

        `skip` = 被推翻、不摆出来的方向（外部评审 C2）—— 在分叉处就滤掉，
        所以它们既不出现在结果里，也不参与「装不下」的判定。
        """
        if depth > 64:                          # 防病态深树（正常到不了）
            return [self._node(k, d, full, "太深了，停在这是为了不递归爆栈")]
        kids = k.children_of(d)
        if not kids:
            return [self._node(k, d, full, "已经是最细的一层")]
        parts = [(c, full & k.members_of(c)) for c in kids]
        parts = [(c, p) for c, p in parts if p and c.did not in skip]
        mine = full & k.members_of(d)
        if len(parts) == 1 and parts[0][1] == mine:
            return self._spread(k, full, parts[0][0], depth + 1, skip=skip)
        if not parts:                           # 正常情况下不会（mine 非空）；skip 后可能
            return []
        out: list[dict] = []
        for c, _p in parts:
            # ⚠️ `skip` 必须一路传到底 —— 漏一层，被推翻的支就会在**更深**的
            #   分叉处重新出现（判据只测了第一层 ⇒ 那种漏是全绿的）。
            out.extend(self._spread(k, full, c, depth + 1, skip=skip))
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
        # ── 组合算子（v0.1.58 derive 推算 / v0.1.59 solve 未知数）：
        #    独立通道，别和检索混。I5 式可移除：spec 不带二者 ⇒ 下面原路径逐字不变。
        if "查字典" in spec:
            return self._lookup_dict(spec["查字典"])
        if "对话" in spec:
            return self._converse(spec["对话"])
        if "联想" in spec:
            w = str(spec["联想"]).strip()
            got = self._dict_associate(w)
            if not got:
                return {"ok": False,
                        "弃答": f"字典里没有「{w}」——没有释义就没有联想，不凭空想。"}
            return {"ok": True, "词": w, "拼音": got.get("拼音"),
                    "部首": got.get("部首"), "笔画": got.get("笔画"),
                    "释义头": got.get("释义头"), "联想词": got.get("联想"),
                    "依据": [{"原文": f"字典释义的「～」构词记号（refs/xinhua_word.json）",
                              "可复算": "把释义中含～的短串换回本字即得"}]}
        _combo = [k for k in ("derive", "solve", "moral", "分析", "成文", "变形", "反义", "主题", "造句", "评估", "端口", "借算", "mongo", "接口定义", "借阅", "查询", "外查", "装配", "过程", "差距", "建议", "推演") if k in spec]
        if _combo:
            if len(spec) > 1 or len(_combo) > 1:
                return {"ok": False,
                        "error": "derive / solve / moral / 成文 / 变形 / 反义 / 评估 / 端口"
                                 " 是独立的组合问法——别和检索字段（require/forbid/…）"
                                 "混在一个 spec 里，也别多个一起发"}
            if _combo[0] == "推演":
                # 推演路由（v0.2.28，用户 2026-11-10「教它推演的路由，即所谓的
                # X 到 Y 是什么意思」）：X→Y 是一条有方向的边（前提/规则/结论/依据），
                # 不是「有关」。规矩在 conc/538，链在 形态: 推演 的节点里。
                return self._infer(spec["推演"])
            if _combo[0] == "借算":
                # 借算路由（v0.2.22，用户 2026-11-09「教它怎么调用计算器」）：
                # 算不了的运算 ⇒ 自己把算式递出去算，拿回来**标外部算得**。
                # 《借算规程》conc/530 是它的文档；三字段的规矩照《端口调用规程》。
                return self._borrow(spec["借算"])
            if _combo[0] == "装配":
                # 装配路由（v0.2.8，用户 2026-11-08「教装配」）：目标 + 零件 → 装配单。
                # **教的是形状**（conc/439《装配规范》），不是某一件事的路径。
                return self._assemble(spec["装配"])
            if _combo[0] == "过程":
                # 过程路由（v0.2.9，用户 2026-11-09）：读装配台账，按补齐史的依赖推顺序。
                return self._process(spec["过程"])
            if _combo[0] == "差距":
                # 差距路由（v0.2.12，用户：创造 = 分清契约与需求的差距，在已定之外）
                return self._gap(spec["差距"])
            if _combo[0] == "建议":
                # 建议路由（v0.2.13，用户：教 LDV 什么是建议——它自己提，不由 agent 接手）
                return self._advice(spec["建议"])
            if _combo[0] == "外查":
                # 对外检索 + 自学（v0.2.3）：带出处才学，学进来标未核，复核归人
                return self._outward(spec["外查"])
            if _combo[0] == "查询":
                # 预算受限的检索（v0.2.2）：精确命中就用，预算花完就交人
                return self._lookup(spec["查询"])
            if _combo[0] == "借阅":
                # 图书借阅路由（v0.1.99）：按规程做判定，不落库——判定权在内核，
                # 写库由调用方按「待写」执行。
                return self._lending(spec["借阅"])
            if _combo[0] == "接口定义":
                # API 定义路由（v0.1.97）：按教过的规范**自己装配** OpenAPI 3.0——
                # 端点/结构/常量全来自语料，路由只解析组装。
                return self._api_def(spec["接口定义"])
            if _combo[0] == "mongo":
                # MongoDB 端口调用路由（v0.1.93）：LDV 自己按线协议开 27017——
                # 协议要点教在 conc/396，地址人配，收发在 mongowire。
                return self._mongo(spec["mongo"])
            if _combo[0] == "端口":
                # 端口调用路由（v0.1.91）：LDV **主动调端口**并消费返回——
                # 契约是教导的（conc/393），地址是人配的，发起与收账是路由的。
                return self._port_call(spec["端口"])
            if _combo[0] == "评估":
                # 评估路由（v0.1.87，用户裁定：路由代替机制——需要的时候表现出
                # 评估）：自查三条内建在路由里，conc/369 是它的文档。
                # ①判错支 ②三方对账 ③场面单清点——产出报告：平/悬/要人拍板。
                return self._self_audit()
            from ldv.core import compose as _CP
            fn = {"derive": _CP.derive, "solve": _CP.solve, "moral": _CP.moral,
                  "分析": _CP.moral, "成文": _CP.成文, "变形": _CP.变形,
                  "反义": _CP.反义, "主题": _CP.主题, "造句": _CP.造句}[_combo[0]]
            arg = ({"事": spec[_combo[0]]}
                   if _combo[0] in ("分析", "成文") else spec[_combo[0]])
            # ── 答题账本（v0.1.65）：作答是一次记录——先对账，答完落账 ──
            import json as _json
            _key = _json.dumps({_combo[0]: arg}, ensure_ascii=False, sort_keys=True)
            prior = self._recall_answer(_key)
            if _combo[0] == "造句":
                _w = str(arg.get("词") or "")
                semnet = (lambda cb, _a=[c for c in _w if "\u4e00" <= c <= "\u9fff"]:
                          self._sem_path(_a, cb))
                result = fn(arg, run_query=self.query, nodes=self.nodes,
                            semnet=semnet)
            else:
                result = fn(arg, run_query=self.query, nodes=self.nodes)
            # ── 借算续接（v0.2.22，用户 2026-11-09「直接通过触发器端到端调用」）──
            #    算不了的运算（derive/solve 拒在运算符）⇒ **它自己**照单据发端口，
            #    拿回的值标「外部算得」。**不经过 agent、不需要桥接挪单子**——
            #    因为这张单子本来就是它自己的（《借算规程》conc/530）。
            #    ⚠️ 只在触发器配了地址时才发；没配就照旧是那张单据（不发明目标）。
            if (isinstance(result, dict) and not result.get("ok")
                    and isinstance(result.get("借算"), dict)):
                got = self._borrow(result["借算"], timeout=2.0)
                result["借算结果"] = got
                if got.get("ok") and got.get("借到值"):
                    result["外部算得"] = {k: got.get(k)
                                          for k in ("算得", "编号", "取值字段", "说法", "来源")}
            if isinstance(result, dict) and result.get("ok"):
                if prior:
                    result["上次作答"] = prior
                self._record_answer(_key, result)
            # 问前自问（v0.1.70）：造句拒于"没词卡" ⇒ 先查一遍字典再开口
            if (_combo[0] == "造句" and isinstance(result, dict)
                    and not result.get("ok") and "词卡" in str(result.get("弃答", ""))
                    and arg.get("词")):
                got = self._dict_associate(str(arg["词"]).strip())
                if got:
                    result["字典自问"] = {
                        "释义头": got.get("释义头"), "联想词": got.get("联想"),
                        "拼音": got.get("拼音"), "部首": got.get("部首"),
                        "长说明": "开口之前先问了一遍字典——它有这个词的"
                                  "释义与联想，缺的只是词类。"}
            return result
        plugin = str(spec.get("plugin") or "keyset")
        if plugin not in self._known:
            return {"ok": False, "error": f"没有插件 {plugin!r}（有 {list(self._known)}）"}
        extra = sorted(set(spec) - set(FIELDS) - set(SPEC_EXTRA))
        if extra:
            return {"ok": False, "error": f"不认识的字段 {extra} —— 只认 {list(FIELDS)}"}
        req = frozenset(spec.get("require") or ())
        forb = frozenset(spec.get("forbid") or ())
        # 判错支默认隐匿（v0.1.82，用户裁定）：标了 `裁决: 判错` 的节点**保持
        # 被忽略**——普通检索自动 forbid 该分支；**显式点名可查**：调用方在
        # require 或 forbid 里显式写了 `裁决=判错`（查废弃档/管理动作）时不再注入。
        _BAD = "裁决=判错"
        if (_BAD in self.all_keys            # 只对声明过该键的语料注入（夹具/旧库零影响）
                and _BAD not in req and _BAD not in forb):
            forb = forb | {_BAD}
        # 已归并的原始份默认隐匿（v0.1.82 合取机制）：合取节点占一个位置，
        # 历史逐份点名可查（显式 require 合取=已归并 时不再注入）
        _MERGED = "合取=已归并"
        if (_MERGED in self.all_keys
                and _MERGED not in req and _MERGED not in forb):
            forb = forb | {_MERGED}
        # ★ **空库要单独说**（2026-10-29 端到端抓到）：一个刚建好、还没喂材料的库，
        #   `all_keys` 是空集 ⇒ 下面那条「拼错的键」判据会把**任何**问题都判成
        #   「这些键在语料里不存在……可能是拼错了」，而 `suggest` 又给不出相近的。
        #   于是第一次问一个新库，用户看到的是「你打错了」——
        #   真正的原因（**这里还没有材料**）一个字都没说。
        #   ⚠️ 与「超出结构」仍然分开：那个说的是「结构里没这个维度」，这个说的是
        #      「连结构都还没有」。两种状态要能分辨，否则人会去改一个本来没错的参数。
        if not self.nodes:
            return {"ok": False, "empty_corpus": True, "plugin": plugin,
                    "requirement": {"require": sorted(req), "forbid": sorted(forb)},
                    "out_of_structure": False,
                    "error": "这个库还是空的（0 项）：**还没有材料可问**，不是键写错了",
                    "hint": "先往 `<语料>/nodes/` 放节点（一节点一个 `.md`），再问一次。"}
        # ★ 拼错的键要**当场拒收**，不能静默返回 0 条 ——
        #   「键写错了」与「材料里真没有」在界面上长得一模一样。
        # ★★ 2026-11-02：不认识的键**不再一律当拼错**。三种要分开：
        #     ① 拼错（有相近的键）             ⇒ 建议改名
        #     ② 缺谓词（`金额>=3000` 这种形状，字段在、值在）⇒ **当场给提案**，
        #        批准后立刻可用（`declare`）；这才是「回答的同时要求新谓词」
        #     ③ 缺维度（字段根本不在材料里）   ⇒ 指向 `request.py` 那条路
        unknown = sorted(k for k in (req | forb) if k not in self.all_keys)
        if unknown:
            import difflib
            hint = {k: difflib.get_close_matches(k, self.all_keys, n=3, cutoff=0.5)
                    for k in unknown}
            props = self.propose(unknown)
            # 提案里「可声明」的那些，才是真正能闭环的
            ready = [p["键"] for p in props if p.get("可声明")]
            if spec.get("auto_declare") and ready:
                did = [self.declare(k, why=str(spec.get("why") or "查询时自动声明"))
                       for k in ready]
                if any(d.get("生效") for d in did):
                    retry = {k: v for k, v in spec.items() if k != "auto_declare"}
                    out = self.query(retry)
                    out["declared"] = did
                    return out
            return {"ok": False,
                    "error": ("这些键不在词汇表里：" +
                              "、".join(f"`{k}`" for k in unknown) + "。" +
                              (f"其中 {len(ready)} 条**可以当场声明**"
                               f"（字段和取值都在材料里，只是还没有这个谓词）："
                               f"重发时带 `auto_declare: true`，或先调 `POST /api/declare`。"
                               if ready else
                               "没有一条能靠声明解决 —— 见 need_predicate 里的理由。")),
                    "unknown_keys": unknown, "suggest": hint,
                    # `need_predicate` 是**请求**，不是错误码：每条都带证据，
                    # 说明它属于上面哪一种，以及能不能就地声明。
                    "need_predicate": props,
                    # ★★ **超出结构 ≠ 查不到**（2026-10-22 新增）。
                    #    「键不在词汇表」是一个**结构信号** —— 不需要理解语义，
                    #    所以它不撞「不做语义推理」。而「键在、组合为空」是另一回事：
                    #    那说明**结构里有这个维度，只是这批材料没命中**。
                    #    ⇒ 两者必须分开报：前者该请人介入，后者只是「这一格是空的」。
                    "out_of_structure": True,
                    "out_of_structure_note":
                        "这个问题**超出本结构的词汇** —— 它不代表材料里没有，"
                        "只代表这里没有这个维度。要不要换一种问法，或者找真人？"}
        k = self.ensure(plugin)
        t0 = time.perf_counter()
        # ★ `reject_below` —— 支级跳过的阈值（外部评审 C2）。**外生**：
        #   调用方/透镜声明，系统不自己发明；`None` ⇒ 不跳（与从前逐字相同）。
        self._reject_below = (float(spec["reject_below"])
                              if spec.get("reject_below") is not None else None)
        with self.lock:                      # `query` 会写（expand）⇒ 串行
            nodes, path, why, ans, rej, n_lost = self._resolve(k, req, forb)
            # ── 按历史倾向排「先看哪一支」——**只改次序，不改集合** ──────────
            #   ⚠️ 这是「接入」的全部作用面：`answer_ids` / `n_answer` /
            #      `n_branches` 排序前后必须**逐字相同**（判据 `[接入] ③` 守这条）。
            #      参数只影响「先说哪一支」，不影响「有哪些答案」——
            #      与「答案是位置」的定位一致：答案 = 节点集合，次序只是界面。
            ordered_by = "default"
            if self._tendency and len(nodes) > 1:
                nodes = sorted(nodes,
                               key=lambda n: (-self._tendency.get(n["did"], 0.0), n["did"]))
                ordered_by = "usage"
            # §8.1：展示是**显式一步**，不是遍历的副作用（否则曝光位次是假的）
            k.show([n["did"] for n in nodes])
            self._last_shown = [n["did"] for n in nodes]
        ms = (time.perf_counter() - t0) * 1000
        self.n_queries += 1
        self._last_qfp = f"{self.fingerprint()}|{'&'.join(sorted(req))}"
        # ★ 「查到了都记录」—— 但只进**缓冲区**，**不算有用**。
        #   被摆出来只是曝光；下钻才 promote 成 outcome = +1。
        #   ⚠️ 缓冲不参与倾向（`usage_store.load` 不递归进 `_staging/`）。
        if self._usage_dir is not None and self._last_shown:
            usage_store.stage(self._usage_dir, qfp=self._last_qfp,
                              shown=self._last_shown)
        # ⚠️ **接口不截断**：`nodes` 给全部支，`n_branches` 与 `len(nodes)` 恒等。
        #    只有界面才截显示（否则「Σ各支 == 答案」在客户端无法验证 —— 实测踩到：
        #    空需求 31 支被截成 12 支，合计 3610 ≠ 7115，看起来像切分漏了）。
        #    需要截断的调用方自己传 `max_branches`，那时 `branches_shown` 会小于 `n_branches`。
        cap = int(spec.get("max_branches") or 0)
        shown = nodes[:cap] if cap > 0 else nodes
        # ★ 反事实（2026-10-24 接线）：`impact` 为真才算「删掉这些答案，结构会怎么变」。
        #   **只算不改**（`core/impact.removal_impact`），与入口的只读定位一致。
        #   ⚠️ 默认**不算** —— 它是 O(方向数 × 成员数)，而默认路径的读数一个都不能动。
        impact_doc = None
        if spec.get("impact"):
            # ⚠️ **绝对导入**：`entry.py` 是仓库根的顶层脚本（不是包），
            #   `from .core import ...` 会在运行时抛
            #   `ImportError: attempted relative import with no known parent package`
            #   —— 接线矩阵当场判它未接线，是对的。
            from ldv.core import impact as IM

            impact_doc = IM.removal_impact(k, ans)
        return {"ok": True, "plugin": plugin,
                "impact": impact_doc,
                "requirement": {"require": sorted(req), "forbid": sorted(forb)},
                # 「先看哪一支」由谁定：default = 无历史；usage = 按使用记录排的
                # ⚠️ 它只描述**次序**，不是分数、不可跨系统比较（协议禁词那一类）
                "ordered_by": ordered_by,
                # ★ 「超出结构」的另一半：键都在词汇表里 ⇒ 这个问题**落在结构内**
                #   （命中 0 项只是「这一格是空的」，不是「这里没有这个维度」）
                "out_of_structure": False,
                # 1 支时 `node` 仍给（兼容旧调用方）；多支时 `node` 为 null，看 `nodes`
                "node": nodes[0] if len(nodes) == 1 else None,
                "nodes": shown,
                "n_branches": len(nodes),
                "branches_shown": len(shown),
                # ★ 三个字段让账平（外部评审 A2）：截断会让 `Σ各支 ≠ n_answer`，
                #   客户端一算就以为切分漏了（实测：31 支截成 12，3610 ≠ 7115）。
                #   客户端验算 `shown + hidden == total`，断言从 `==` 放宽到 `≤`。
                "branches_total": len(nodes),
                "branches_hidden": len(nodes) - len(shown),
                # ★ 支级跳过的读数（外部评审 C2）—— 跳过了哪些支、因此少了哪些项，
                #   **必须可算**：`skipped_rejected` 非空时，答案比不跳时少
                #   `rejected_items` 项（判据守这条账）。
                "skipped_rejected": rej,
                "rejected_items": n_lost,
                "reject_below": self._reject_below,
                "path": path, "stopped": why,
                "n_answer": len(ans),
                "answer_ids": sorted(ans),
                "answer_titles": [self.titles.get(i, i) for i in sorted(ans)],
                # ★ 项级否定（外部评审 C1 的「项级」那一半）：**只标记，不改候选集** ——
                #   判断权交给调用方（「不越权」）。来源是节点 front matter 的
                #   `verdict`（材料自己写的）。与支级跳过（`reject_below`）分工：
                #   项级 = 这一条被推翻（照常返回，带标记）；
                #   支级 = 这一支被推翻（不摆出来，带差额）。
                #   ⚠️ 只列**有标记的**项 —— 空映射与「全都没标记」不是一回事，
                #      但也不给每个项塞一个空串（那会让每条答案都变长）。
                "answer_verdicts": {
                    i: v for i in sorted(ans)
                    if (v := str((self.nodes[i].fields or {}).get("verdict") or ""))},
                # ② 零结果时给「去掉哪个键就有结果」——逐键试一遍，确定性
                "relax": (self._relax(req, forb) if not ans else []),
                # ★ 注意力读数（提案 `exp/m1/ATTENTION_READOUT.md`）：把「为什么这么排」
                #   说出来。**只读派生**、零分数、不参与计算（删掉它不影响上面任何一个读数，
                #   判据 I5）。数据来源只有 `usage/*.md`，写入口仍只有 `drill`。
                "attention": self._attention(shown),
                "ms": round(ms, 2)}

    def chain(self, spec: dict) -> dict:
        """查一次，并把每支的**链路**（根 → 该支）一并给出。

        ★ 与 `query()` 的差别只有一处：`query` 回**并列的支**（有哪些答案），
          `chain` 额外回**走到每一支的那条路**（怎么想到的）。答案本身逐字相同
          —— `answer_ids` / `n_answer` 与直接 `query` 必须一致（判据守这条）。

        ★★ 只读投影：不展开、不插入、不排序、不改任何方向。
           跑前跑后结构签名相同（判据 `[链路] ④`）。

        ⚠️ `chains` 只覆盖**摆出来的**支（`max_branches` 截断时不含隐藏的）。
           要全部就传足够大的 `max_branches`。
        """
        from ldv.core import chain as CH

        res = self.query(spec)
        if not res.get("ok"):
            return res
        k = self.ensure(str(spec.get("plugin") or "keyset"))
        cs = CH.chains(k, [n["did"] for n in res.get("nodes") or []],
                       require=spec.get("require") or (),
                       forbid=spec.get("forbid") or ())
        out = dict(res)
        out["chains"] = cs
        out["chain_summary"] = CH.summary(cs)
        # ── 折叠视图（v0.1.55，`exp/m2/CHAIN_CHANGE_PLAN.md` §1.1–1.2）──
        #    同字段连续二元切分合并为字段步；筛选步数与人的字段决策同单位
        #    （V17 实测人均 1.86）。只读：`步` 的顺序不动，树不动。
        n_ans = res.get("n_answer")
        out["folded"] = CH.folded(k, [n["did"] for n in res.get("nodes") or []],
                                  require=spec.get("require") or (),
                                  forbid=spec.get("forbid") or (),
                                  n_answer=n_ans)
        # ── 答案完备性声明（§1.4；V17②：人漏检 3/7 题，完备性是本系统对人的
        #    差异化优势，此前只在账目里没说出来）。无假阴（§K8）保证：
        #    呈现支数 == 总支数 ⇒ 答案无遗漏；被截断时**如实说截断**。
        n_shown = len(res.get("nodes") or [])
        n_br = res.get("n_branches")
        if n_br is not None:
            out["答案完备性"] = {"n_answer": n_ans, "呈现支数": n_shown,
                                 "总支数": n_br, "无遗漏": n_shown == n_br}
        return out

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
            # ★ from/fp 是**回答方**的（本端）—— 2026-10-21 真实问题验收抓到：
            #   曾经从 query_msg 拷贝 ⇒ 跨结构时 RESULT 自称来自提问方的结构。
            r = PROTO.build_result(msg, res, frm=self.who(), fp=self.fingerprint())
            v_ok, v_bad = PROTO.verify_result(r)
            if not v_ok:                      # 自己产出的结果必须过自己的自检
                return PROTO.error_envelope(f"本端结果自检未过：{v_bad[:2]}", frm=self.who())
            return r
        except Exception as e:                # 一条坏不拖垮调用方
            return PROTO.error_envelope(f"{type(e).__name__}: {e}", frm=self.who())

    def drill(self, did: str, req=(), forb=(), *, verdict: str = "",
              actor: str = "") -> dict:
        k = self.ensure("keyset")
        try:
            d = k.direction(did)
        except Exception as e:
            return {"ok": False, "error": f"没有这个节点 {did!r}：{e}"}
        # ★ 负事件（外部评审 D1，2026-10-25）：`verdict="wrong"` = 「这一支被推翻」。
        #   **判错权限与「已验证」同级 —— 只能外部授予**：`actor` 不是 `human`
        #   一律拒（agent 只能「提议待核」）。否则 agent 会把自己读不懂的标成错的。
        #   ⚠️ `actor` 与 `filled_by` 同款：**调用方声明，本层不验身份** ——
        #      这里守的是「接口不发给 agent」，不是「能证明是人」。
        outcome = 1.0
        if verdict == "wrong":
            if actor != "human":
                return {"ok": False,
                        "error": ("判错（verdict=wrong）只能外部授予："
                                  f"actor={actor!r} 不是 human。"
                                  "agent 可以提议，但不能判错"),
                        "hint": "人确认后由人调用，或把这一支写进材料的 verdict 字段"}
            outcome = -1.0
        elif verdict and verdict != "good":
            return {"ok": False, "error": f"verdict 只认 good / wrong，给的是 {verdict!r}"}
        # 下钻 = 「这一支有用」的唯一**真实**信号（入口没有「点赞」界面，也不合成反馈）
        used = self._record_use(k, d, outcome=outcome)
        ans = self.answer_set(frozenset(req), frozenset(forb))
        tend = self._tendency.get(d.did) if self._tendency else None
        return {"ok": True, "parent": self._node(k, d, ans),
                "children": [self._node(k, c, ans) for c in k.children_of(d)],
                # ★ 这次记的是什么（负事件也要如实报，不许与「有用」共用一句话）
                "verdict": verdict or "good",
                "outcome": outcome,
                "tendency_after": tend,
                "tendency_note": ("这一支现在的倾向（含刚记的这笔）"
                                  if tend is not None else "这一支还没有倾向读数"),
                # 记了就报，没记就报**为什么** —— 不许静默，也不许「成功与失败共用一句话」
                "recorded": bool((used or {}).get("written")),
                "record_note": (
                    (f"已记录（{'这一支被推翻' if outcome < 0 else '下钻 = 有用'}）；"
                     f"这批还剩 {used.get('remaining', 0)} 支待看")
                    if (used or {}).get("written")
                    else (used or {}).get("why")
                    or ("未启用：内置语料不写使用记录" if self._usage_dir is None
                        else "这一支不在刚展示的候选里，不记"))}
        # ── 自问层出箱钩子（docs/自问层设计.md §V2）：使用态语料上，落盘即可能出箱。
        #    只写 outbox.jsonl（只追加），不改本返回；非使用态 ⇒ 内部直接跳过。
        self.selfask_emit()

    def review(self, node_id: str, *, by: str = "", 依据: str = "") -> dict:
        """复核外部学来的东西（v0.2.5，用户 2026-11-08 裁定：「采信改已核」做成动件）。

        **采信与判错同级，都是外部授予**：`by != "human"` 一律拒——
        agent 可以提议「这条该核了」，但不能自己把 未核 改成 已核。
        只有人核过出处，外部摘录才许当规程用（《自学规程》conc/418）。
        ⚠️ 与 `drill(verdict="wrong")` 同一个守则：这里守的是「接口不发给 agent」，
           不是「能证明是人」——`by` 是调用方声明。
        """
        import datetime as _dt
        import json as _j
        import re as _re

        if by != "human":
            return {"ok": False,
                    "error": (f"复核只能人做：by={by!r} 不是 human。"
                              "agent 可以提议待核，但不能替人把 未核 改成 已核"),
                    "hint": "人核对出处后由人调用：review(node_id, by=\"human\")"}
        nid = str(node_id or "").strip()
        n = self.nodes.get(nid)
        if n is None:
            return {"ok": False, "error": f"没有这个节点 {node_id!r}"}
        f = getattr(n, "fields", {}) or {}
        if str(f.get("来源") or "") != "外部":
            return {"ok": False,
                    "error": (f"{nid} 的来源是 {f.get('来源')!r}，不是外部——"
                              "自家教导不经这道复核")}
        if str(f.get("采信") or "") != "未核":
            return {"ok": False,
                    "error": (f"{nid} 的采信是 {f.get('采信')!r}，不是未核——"
                              "已核的不再核（防重复记账）")}
        nd = self._nodes_dir()
        if nd is None:
            return {"ok": False, "error": "内置语料不写记忆"}
        p = nd / (nid.replace("/", "__") + ".md")
        if not p.is_file():
            hit = [q2 for q2 in nd.glob("*.md")
                   if _re.search(rf"^id:\s*{_re.escape(nid)}\s*$",
                                 q2.read_text(encoding="utf-8"), _re.M)]
            if not hit:
                return {"ok": False, "error": f"找不到 {nid} 的节点文件"}
            p = hit[0]
        txt = p.read_text(encoding="utf-8")
        today = _dt.date.today().isoformat()
        new = _re.sub(r"^采信:\s*未核\s*$",
                      f"采信: 已核\n核于: {today}\n核人: human",
                      txt, count=1, flags=_re.M)
        new = new.replace("**采信=未核**——待人复核，未核不许当规程用",
                          f"**采信=已核**（human 于 {today} 核过出处"
                          + (f"：{依据}" if 依据 else "") + "）")
        if new == txt:
            return {"ok": False,
                    "error": "节点里没有可改的 采信: 未核 行（文件与内存不一致？）"}
        p.write_text(new, encoding="utf-8", newline="\n")
        self._reload_after_learn()
        base = (self._corpus_root if hasattr(self, "_corpus_root")
                else nd.parent)
        try:
            with (base / "review_ledger.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(_j.dumps({"ts": _dt.datetime.now().isoformat(timespec="seconds"),
                                   "id": nid, "name": f.get("name"),
                                   "采信": "未核→已核", "by": "human",
                                   "依据": 依据}, ensure_ascii=False) + "\n")
        except OSError:
            pass
        return {"ok": True, "id": nid, "name": f.get("name"), "采信": "已核",
                "核于": today, "核人": "human", "文件": p.name,
                "依据": [{"原文": "《自学规程》：学进来的标 来源=外部/采信=未核，"
                                  "复核归人（conc/418）",
                          "可复算": f"{p.name} 的 front matter：采信/核于/核人"}]}

    def taught(self, node_ids, *, at=None) -> dict:
        """教学入账（2026-11-03，用户纠正）：节点被喂入并安放进树 = 真实事件。

        对每个节点：取它在 keyset 树上**根→叶的方向链**，每一层记一条
        `usage_store.teach`（outcome=+1，qfp=`teach:<node_id>@<事件戳>`——
        每次教学是独立真实事件，同一节点重复教各记一条）。
        ★ 倾向是方向树的抽象投影——账挂在 did 上，不另立节点级的账。
        ★ `at`：教学发生的真实时刻（回填历史传历史时刻；缺省 = 现在）。
          半衰期照常作用 ⇒ 久未复习的教学会褪色。
        ⚠️ 方向链是**记录时刻**的树投影；树的形状会变，所以快照（§I7）
           随记录落盘留痕——「这条账记的时候这支长什么样」。
        """
        if self._usage_dir is None:
            return {"ok": False, "error": "未启用：内置语料不写使用记录"}
        k = self.ensure("keyset")
        # ★ 每次教学都是一次真实事件（2026-11-03 用户指示「同等级的知识可以
        #   重复几次」）：同一节点被再教一次，各记一条 —— 记账 qfp 加**事件戳**
        #   （每次唯一），与 drill 同款。间隔重复的「间隔」由衰减因子表达，
        #   不由去重表达。`at` 与事件戳取**同一时刻**，读写同一「现在」。
        ev_at = (self._usage_now if self._usage_now is not None
                 else (at if at is not None else clock.now()))
        use_at = ev_at.isoformat(timespec="microseconds")
        stamp = ev_at.strftime("%Y%m%dT%H%M%S%f")
        snap_at = self._usage_now or clock.now()
        wrote = skipped = 0
        per_node = []
        for nid in node_ids:
            if nid not in self.nodes:
                per_node.append({"node": nid, "ok": False, "why": "没有这个节点"})
                continue
            d = k.root
            chain = [d]
            while True:
                kids = [c for c in k.children_of(d) if nid in k.members_of(c)]
                if not kids:
                    break
                d = kids[0]
                chain.append(d)
            recs = []
            for cd in chain:
                snap = json.dumps(k.plugin.record(cd), ensure_ascii=False,
                                  sort_keys=True)
                r = usage_store.teach(self._usage_dir, did=cd.did, node_id=nid,
                                      at=use_at, snapshot=snap,
                                      event_qfp=f"teach:{nid}@{stamp}")
                recs.append({"did": cd.did, "written": bool(r.get("written"))})
                wrote += 1 if r.get("written") else 0
                skipped += 0 if r.get("written") else 1
            per_node.append({"node": nid, "ok": True,
                             "chain_len": len(chain),
                             "written": sum(1 for x in recs if x["written"]),
                             "already": sum(1 for x in recs if not x["written"])})
        # ── 教学自动开词（v0.1.64，用户"这应该是通的"）：新教节点的名字
        #    立即成为可检索的键——教与问之间的最后一环接上。
        #    只开 name=<名>（最小）；已开跳过；失败记警告不阻断教学。
        auto_keys: list[str] = []
        if self._can_declare:
            for nid in node_ids:
                n = self.nodes.get(nid)
                nm = n.fields.get("name") if n else None
                if not nm:
                    continue
                key = f"name={nm}"
                if key in self.all_keys:
                    continue
                d = self.declare(key, why="教导自动开词（名字随教学入词汇表）",
                                 by="owner")
                if d.get("生效"):
                    auto_keys.append(key)
                else:
                    per_node.append({"node": nid, "ok": True,
                                     "warn": f"自动开词失败：{key}"})

        # ── 开词对齐检查（v0.1.66）：全库扫欠账，漏一个补一个 ——
        #    自动开词只管本次新增，存量漏网（历史教导/外部写文件）在这里兜底。
        aligned: list[str] = []
        for nid in self.nodes:
            nm = self.nodes[nid].fields.get("name")
            if not nm:
                continue
            key = f"name={nm}"
            if key in self.all_keys:
                continue
            d = self.declare(key, why="开词对齐检查：全库扫欠账补漏", by="owner")
            if d.get("生效"):
                aligned.append(key)
            else:
                per_node.append({"node": nid, "ok": True,
                                 "warn": f"对齐开词失败：{key}"})

        # 账变了，重读倾向
        self._tendency = usage_store.aged_tendency(self._usage_dir, now=self._usage_now,
                                                   half_life_days=self._half_life)
        # ── 自问层出箱钩子：教学落盘同样可能出箱（喂进来的知识改变了树）──
        self.selfask_emit()
        return {"ok": True, "wrote": wrote, "already": skipped,
                "自动开词": auto_keys, "对齐开词": aligned,
                "nodes": per_node, "tendency_dirs": len(self._tendency)}

    # ── 自问层（v0.1.56，docs/自问层设计.md）─────────────────────────────

    def _nodes_dir(self):
        """议程账本（`agenda.jsonl`）的位置锚：`语料根/nodes`（`agenda_path` 的约定）。
        ⚠️ `_usage_dir` 本身就是**语料根**（usage/ 在它里面），不是 usage 子目录。
        内置语料 ⇒ None。"""
        return (self._usage_dir / "nodes") if self._usage_dir else None

    def selfask(self, context_keys=(), *, cap: int = 5, k: int = 0) -> dict:
        """自问句清单（**只读**）。三问：矛盾 / 待展开 / 无词。

        原料全部来自既有结构（反思待办 / 议程激活 / 处境对词表），本层只做
        组合与成句——不发明维度（§K9）、不改候选集（§X2）、不打分。
        证据逐字可解析；**无证据的句子不生成**，空清单带 `空因`。
        """
        from ldv.core import selfask as SF
        return SF.report(self.ensure("keyset"), nodes_dir=self._nodes_dir(),
                         context_keys=context_keys, now=self._usage_now,
                         cap=cap, k=k, known_keys=set(self.all_keys),
                         nodes=self.nodes)

    def selfask_emit(self, context_keys=()) -> dict:
        """出箱：把自问句写进 `<语料根>/outbox.jsonl`（只追加）。

        **只在「使用态」语料上发生**（语料根自带 `usage/config.json`）——
        演示语料 / 测试语料不出箱。**自反馈禁令**：出箱永不写 `agenda.jsonl`。
        发射点 = 既有操作（drill / taught）的收尾段——不自起进程，搭顺风车。

        v0.1.89 触发器（用户裁定：给 LDV 配触发器，允许它调用端口）：
        出箱有新行且 `<语料根>/trigger.json` 启用时，**主动 POST 出箱摘要
        到配置的端口**——配置是人写的（url/事件/开关），LDV 不发明目标。
        POST 失败如实写 `trigger.log`（一行一条，不静默、不重试轰炸）。
        """
        from ldv.core import selfask as SF
        nd = self._nodes_dir()
        if nd is None:
            return {"出箱": 0, "跳过": "未启用：内置语料不写使用记录"}
        res = SF.emit(self.ensure("keyset"), nodes_dir=nd,
                      context_keys=context_keys, now=self._usage_now,
                      known_keys=set(self.all_keys))
        # ── 触发器：出箱有新增 ⇒ POST（配置驱动，失败如实登记）──
        from pathlib import Path as _P
        try:
            n_new = int(res.get("出箱") or 0)
            if n_new > 0:
                base = _P(self._corpus_root) if hasattr(self, "_corpus_root") else nd.parent
                cfg_p = base / "trigger.json"
                if cfg_p.is_file():
                    import json as _j
                    cfg = _j.loads(cfg_p.read_text(encoding="utf-8") or "{}")
                    if cfg.get("enabled") and cfg.get("url"):
                        lines = (base / "outbox.jsonl").read_text(encoding="utf-8").splitlines()
                        latest = []
                        for ln in lines[-max(1, n_new):]:
                            try:
                                latest.append(_j.loads(ln))
                            except ValueError:
                                continue
                        payload = _j.dumps({"来源": "LDV", "事件": "出箱",
                                            "条数": n_new, "最新": latest[:5]},
                                           ensure_ascii=False).encode("utf-8")
                        import urllib.request as _u
                        req = _u.Request(str(cfg["url"]), data=payload,
                                         headers={"Content-Type": "application/json"})
                        # 直连（同 _port_call）：环境代理会把本机/内网端口劫成 502
                        _op = (_u.build_opener(_u.ProxyHandler({}))
                               if str(cfg.get("proxy") or "direct") == "direct" else None)
                        try:
                            with ((_op.open(req, timeout=2) if _op
                                   else _u.urlopen(req, timeout=2))) as r:
                                res["触发器"] = {"posted": True,
                                                 "url": str(cfg["url"]),
                                                 "status": int(getattr(r, "status", 200))}
                        except Exception as exc:  # noqa: BLE001
                            import datetime as _dt
                            with (base / "trigger.log").open("a", encoding="utf-8") as f:
                                f.write(f"{_dt.datetime.now().isoformat(timespec='seconds')}"
                                        f"\tFAIL\t{cfg['url']}\t{type(exc).__name__}:{exc}\n")
                            res["触发器"] = {"posted": False,
                                             "url": str(cfg["url"]),
                                             "error": f"{type(exc).__name__}:{exc}"[:120]}
        except Exception as exc:  # noqa: BLE001 —— 触发器永不影响出箱本身
            res["触发器"] = {"posted": False, "error": str(exc)[:120]}
        return res

    def _dict_load(self):
        """懒加载语料根 refs/ 下的真字典（新华字典开源数据），进程内缓存。"""
        if getattr(self, "_dict_cache", None) is not None:
            return self._dict_cache
        from pathlib import Path as _P
        import json as _json
        root = _P(self._corpus_root)
        out = {"字": {}, "部首": {}, "音": {}, "歇后语": []}
        for fname, slot in (("refs/xinhua_word.json", "字"),
                            ("refs/xiehouyu.json", "歇后语")):
            f = root / fname
            if not f.exists():
                continue
            try:
                data = _json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if slot == "字":
                for e in data:
                    w = str(e.get("word") or "")
                    if not w:
                        continue
                    out["字"][w] = e
                    rad = e.get("radicals")
                    if rad:
                        out["部首"].setdefault(str(rad), []).append(w)
                    py = str(e.get("pinyin") or "").replace("\u0261", "g")
                    for part in py.replace("，", " ").split():
                        out["音"].setdefault(part, []).append(w)
            else:
                out["歇后语"] = data
        self._dict_cache = out
        return out

    _CONV_OPS = {"加": "+", "减": "-", "乘": "*", "除": "/"}

    @staticmethod
    @staticmethod
    def _float(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _port_read(status, raw, *, rule: str = "") -> dict:
        """读回执（v0.1.92）：入站报文的解读规则——conc/395 的可执行形态。

        判序（实测踩过两次才定下来）：
          **先看 status，再看 body**——5xx 无论 body 写什么都是对方故障；
          400 系列的 body 里是"我的报文缺什么"，是信息不是噪音。
        五类结论，判据是**对方的话**，不是我的猜：
          受理     2xx + 有编号         → 记编号，凭据齐
          驳回     2xx 但 ok=false      → 对方收到了、明确不收，按它给的原因改
          我方错   4xx                  → 报文格式/凭据问题，同一份不重发
          对方故障 5xx / 无 status       → 不重试，报人
          读不懂   空/非 JSON/2xx 无编号 → 说读不懂，给原文片段，不猜
        """
        import json as _j
        raw = (raw or "").strip()
        code = int(status or 0)

        back = None
        if raw:
            try:
                v = _j.loads(raw)
                back = v if isinstance(v, dict) else {"值": v}
            except ValueError:
                back = None

        if code == 0 or code >= 500:
            return {"结论": "对方故障", "可重试": "否——报人",
                    "对方返回": back if back is not None else raw[:200],
                    "依据": f"status {code}：对方自己出了问题，重试也白搭"
                            f"（5xx 先于报文内容判，conc/395）"}
        if not raw:
            return {"结论": "读不懂", "可重试": "否——报人", "原文": "",
                    "依据": "回执是空的——空回执不是受理（conc/395）"}
        if back is None:
            return {"结论": "读不懂", "可重试": "否——报人", "原文": raw[:200],
                    "依据": "回执不是 JSON——读不懂就不猜（conc/395）"}
        if 400 <= code < 500:
            miss = back.get("缺") or back.get("缺少") or []
            return {"结论": "我方格式错",
                    "可重试": "否——改格式再发，同一份不重发",
                    "缺": miss if isinstance(miss, list) else [str(miss)],
                    "对方返回": back,
                    "依据": f"status {code}：4xx 是我方报文的问题，不是对方的错（conc/395）"}
        if back.get("ok") is False:
            return {"结论": "驳回", "可重试": "否——按对方给的原因改内容",
                    "原因": str(back.get("错误") or back.get("原因") or "")[:200],
                    "对方返回": back,
                    "依据": "2xx 但 ok=false：对方收到了、明确不收（conc/395）"}
        # 凭据判定（v0.1.98）：凭据**不止编号**——编号 / 受理号 / 已创建 / 已写入 /
        # 收据号 / 流水号 / 回执号 / 结果 id，任何能指认"对方确实办了事"的具名字段
        # 都算凭据（实测：文件创建回执只带「已创建」，旧规则把它判成读不懂）。
        # **但 ok=true 本身不算凭据**——它只说没报错，不说办了事，防线保留。
        cred = None
        for k in ("编号", "受理号", "已创建", "已写入", "收据号", "流水号",
                  "回执号", "结果号", "结果id", "id", "ID", "Id"):
            v = str(back.get(k) or "").strip()
            if v:
                cred = (k, v)
                break
        # ★ 凭据可能在对象里（v0.2.25，用户 2026-11-10「他没有可依据的路径」）：
        #   顶层扫完没找到，**只有语料里教过读法**（《回执凭据层》conc/532）时才
        #   往里看**一层**。行为由教导授权，代码只执行这条路：
        #   没教过 ⇒ rule 为空 ⇒ 与加这条之前逐字相同（I5——删掉那个节点，
        #   这里立刻退回旧样）。
        #   ⚠️ 只下一层：数组里的 id、更深层的 id 可能是别的单子，认了就是把
        #      别人的号记成这张单的号。那条界線教在节点正文里，这里只守它。
        nested_from = ""
        if cred is None and rule:
            for _pk, _pv in back.items():
                if not isinstance(_pv, dict):
                    continue
                for k in ("编号", "受理号", "已创建", "已写入", "收据号", "流水号",
                          "回执号", "结果号", "结果id", "id", "ID", "Id"):
                    v = str(_pv.get(k) or "").strip()
                    if v:
                        cred = (k, v)
                        nested_from = str(_pk)
                        break
                if cred is not None:
                    break
        if cred is None:
            return {"结论": "读不懂", "可重试": "否——报人", "原文": raw[:200],
                    "依据": "2xx 但一个凭据字段都没有——ok 只说明没报错，"
                            "不说明办了事（conc/395）"}
        path_ = f"{nested_from}.{cred[0]}" if nested_from else cred[0]
        return {"结论": "受理", "可重试": "否——已受理",
                "受理人": str(back.get("受理") or back.get("受理人") or ""),
                "凭据": f"{path_}={cred[1]}",
                "编号": cred[1] if cred[0] in ("编号", "受理号") else "",
                "对方返回": back,
                "依据": (f"2xx + 凭据（{path_}）⇒ 受理（conc/395 凭据白名单）"
                         + (f"；顶层没有，按《回执凭据层》（{rule}）往里看一层"
                            if nested_from else ""))}

    def _borrow(self, spec, *, timeout: float = 5.0) -> dict:
        """借算路由（v0.2.22，用户 2026-11-09「干脆教它怎么调用计算器」）。

        **算不了的运算，不许硬算，也不许就此停**：把算式原样递出去算，
        拿回来的是「外部算得」——复述对方编号，绝不说成自己算的。
        《借算规程》conc/530 是它的文档；信封三字段照《端口调用规程》conc/393。

        输入两种（都收，图的是「单据能原样递回来」）：
          - 借算单本身（`derive` 弃答里的 `借算`，带 做什么/为什么/请谁处理/算式）
          - 简写 `{"算式": "0.96 ^ 12", "为什么": "…", "请谁处理": "计算器"}`
        发不发**不由本函数猜**：地址没配、对方不通，照规程如实回报，不重试。
        ⚠️ `timeout` 由调用方给：**自动续接**用短超时（默认 2s，算不出的题不该拖住主链），
           显式 `{"借算": …}` 用 5s（人明确在等这个值）。
        """
        if isinstance(spec, str):
            spec = {"算式": spec}
        if not isinstance(spec, dict):
            return {"ok": False,
                    "弃答": "借算要「算式」（或一张借算单）——把要算什么说清，才递得出去。"}
        expr = str(spec.get("算式") or "").strip()
        if not expr:
            return {"ok": False,
                    "弃答": "借算没有「算式」——不知道算什么都递不出去，我不替你编一条。"}
        # 三字段：单据里带的照用；简写就按规程补齐（为什么/请谁处理是**规矩**，不是猜）
        ask = {"做什么": str(spec.get("做什么") or f"算 {expr} 的值").strip(),
               "为什么": str(spec.get("为什么")
                          or "内核只认加减乘除，这个运算没学过，不硬算").strip(),
               "请谁处理": str(spec.get("请谁处理") or "计算器").strip()}
        r = self._port_call(ask, timeout=timeout)
        out = {"算式": expr, "请求": ask,
               "来源": "外部算得（不是我算的）",
               "依据": list(r.get("依据") or []) + [
                   {"原文": f"《借算规程》conc/530：算不了的递出去，"
                            f"拿回的值标「外部算得」并复述编号",
                    "可复算": f"重发同一条借算（算式 {expr}），对方应给出同一编号下的同一值"}]}
        if not r.get("ok"):
            out.update({"ok": False,
                        "弃答": "借算没成 —— " + str(r.get("弃答") or "对方没受理")
                                + "（按规程不重试，如实回报）",
                        "回执": r.get("回执"), "解读": r.get("解读")})
            return out
        # ── 受理：把值从回执里读出来（读不到就说读不到，不猜）──────────────
        back = (r.get("回执") or {}).get("对方返回")
        val, vkey = None, None
        if isinstance(back, dict):
            for k in ("值", "结果", "数值", "value", "result", "answer"):
                if isinstance(back.get(k), (int, float)) and not isinstance(back.get(k), bool):
                    val, vkey = back[k], k
                    break
        out.update({"编号": str((r.get("解读") or {}).get("凭据") or ""),
                    "回执": r.get("回执"), "解读": r.get("解读")})
        if val is None:
            # 对方说「受理」但**没有可读的数值** ⇒ 这次借算**没成**。
            # ⚠️ `ok` 必须 False（2026-11-09 实测）：受理只说明它收到了，
            #    「收到但没给值」被报成 ok=True，调用方就会以为算出来了。
            out.update({"ok": False, "借到值": False,
                        "弃答": "借算没成 —— 对方受理了，但回执里没有可读的数值"
                                "（不是「值为 0」，是**没有**）。值没拿到就是没拿到，"
                                "不替你从别处凑一个。",
                        "长说明": "受理 ≠ 给值：凭据只能证明它收到了，"
                                  "证明不了它算出了什么。"})
            return out
        out.update({"ok": True, "借到值": True, "算得": val, "取值字段": vkey,
                    "说法": (f"{expr} = {val}（外部算得，凭据 "
                             f"{out.get('编号') or '对方未给编号'}）")})
        # 算式原样留着可复算；数值不是语料字段 ⇒ **不回查语料、不当成有名字的数**
        out["长说明"] = (f"这个值不是我算的：算式 {expr} 原样交给了外部，"
                         f"对方按 {out.get('编号')} 回了 {vkey}={val}。"
                         "要把它当知识，得有人核过再教（来源=外部、采信=未核）。")
        return out

    def _credential_rule(self) -> str:
        """语料里教过「凭据在对象里」这条读法吗（conc/532《回执凭据层》）。

        返回节点 id；空串 = 没教过 ⇒ `_port_read` 不往里看，仍报「读不懂」。
        ★ **行为由教导授权**：读法存在语料里，代码只执行这条路。删掉那个节点，
          读回执立刻退回 v0.1.98 的样子（I5 可移除性）。
        ★ 这不是「猜对方的结构」——教的是「往里看一层」，不是「这一层有什么」；
          真往里看之后没有具名字段，照样报读不懂（不把 ok=true 升格）。
        """
        for nid, n in self.nodes.items():
            if str((n.fields or {}).get("name") or "") == "回执凭据层":
                return nid
        return ""

    def _port_retry_rule(self) -> dict:
        """语料里教过哪几条「重发」的路（v0.2.26）。

        - `改格式`  ← conc/533《改格式重发》：4xx 是写法问题时可改一次再发一次
        - `故障重试` ← conc/534《对方故障有界重试》：5xx/连接失败可原样重发
        空串 = 没教过 ⇒ `_port_call` 一次都不重发，与 v0.2.25 逐字相同。
        ★ **行为由教导授权**：能不能重发、怎么改，全在语料里；代码只执行这条路。
          把这两个节点删掉，端口调用立刻退回「发一次、4xx 报人、5xx 报人」的旧样（I5）。
        """
        out = {"改格式": "", "故障重试": ""}
        for nid, n in self.nodes.items():
            nm = str((n.fields or {}).get("name") or "")
            if nm == "改格式重发":
                out["改格式"] = nid
            elif nm == "对方故障有界重试":
                out["故障重试"] = nid
        return out

    def _missing_rule(self) -> dict:
        """语料里教过《报缺单》没有（v0.2.27，用户 2026-11-10「给它补能够报缺路由的能力」）。

        - `单`   ← conc/536《报缺单》：缺客观事实时出单，而不是一句「报人」
        - `分类` ← conc/535《客观事实与可推事实》：六类缺口的表（类别/谁能给/给了怎么用）
        两样**都在**才算教过：单子要有表才填得出「谁能给」，只有单子没表就会编人。
        任一缺 ⇒ 返回空 ⇒ `_port_call` 弃答与 v0.2.26 逐字相同（I5）。
        ★ 行为由教导授权，代码只有算子：归哪一类、表里写谁给，全在语料。
        """
        out = {"单": "", "分类": ""}
        for nid, n in self.nodes.items():
            nm = str((n.fields or {}).get("name") or "")
            if nm == "报缺单":
                out["单"] = nid
            elif nm == "客观事实与可推事实":
                out["分类"] = nid
        return out if (out["单"] and out["分类"]) else {"单": "", "分类": ""}

    def _missing_table(self, cls_id: str) -> dict:
        """从《客观事实与可推事实》正文里读那张六类缺口表（v0.2.27）。

        返回 `{类别: (情形, 谁能给, 给了之后怎么用)}`。读不到就是空表——
        **空表不许补默认值**：补了就等于代码替语料说了「谁能给」。
        """
        n = self.nodes.get(cls_id)
        body = str(getattr(n, "body", "") or "")
        tab = {}
        for line in body.splitlines():
            s = line.strip()
            if not s.startswith("|"):
                continue
            cells = [c.strip() for c in s.strip("|").split("|")]
            if len(cells) < 4:
                continue
            k = cells[0].replace("*", "").strip()
            if not k or k == "类别" or set(k) <= set("-: "):
                continue
            tab[k] = (cells[1], cells[2], cells[3])
        return tab

    # ── 推演路由（v0.2.28）────────────────────────────────────────────────
    # 「X 到 Y」= 一条**有方向的边**：前提、规则、结论、依据，缺一件不算边。
    # 规矩全在 conc/538《推演的路》；各条链在 `形态: 推演` 的节点里，每跳自带文献。
    # 这里是**算子**：找节点、读边、连路径、标缺口——一句业务判断都不写。
    _INFER_EDGE_SRC = (
        r"^-\s*从[「『](?P<x>.+?)[」』]\s*(?:→|->)\s*到[「『](?P<y>.+?)[」』]"
        r"\s*｜\s*规则[:：](?P<rule>.+?)"
        r"\s*｜\s*依据[:：](?P<ref>.+?)\s*$")

    def _infer_rule(self) -> str:
        """语料里教过《推演的路》没有。空串 = 没教过 ⇒ 推演路由不成立。"""
        for nid, n in self.nodes.items():
            if str((n.fields or {}).get("name") or "") == "推演的路":
                return nid
        return ""

    @staticmethod
    def _infer_key(s) -> str:
        """比对用归一化：去空白与引号差异。只用于找边，不改写原文。"""
        import re as _re
        return _re.sub(r"[\s「」『』“”\"']+", "", str(s or ""))

    def _infer_edges(self) -> list:
        """扫语料里 `形态: 推演` 的节点，把正文中的跳读成边。

        读不出「前提/规则/依据」三项的行**跳过**（缺一件不算边，不补默认值）。
        """
        import re as _re
        pat = _re.compile(self._INFER_EDGE_SRC)
        out = []
        for nid, n in self.nodes.items():
            f = n.fields or {}
            if str(f.get("形态") or "") != "推演":
                continue
            for line in str(getattr(n, "body", "") or "").splitlines():
                m = pat.match(line.strip())
                if not m:
                    continue
                x, y = m.group("x").strip(), m.group("y").strip()
                rule, ref = m.group("rule").strip(), m.group("ref").strip()
                if not (x and y and rule and ref):
                    continue
                out.append({"前提": x, "结论": y, "规则": rule, "依据": ref,
                            "链": str(f.get("链") or ""), "出自": nid})
        return out

    def _infer(self, spec) -> dict:
        """从一组已知（`从`）沿教过的边走到目标（`到`），逐跳交路径。

        - 边有方向：只走 前提→结论，绝不反走；
        - 最多 5 跳（《推演的路》「次数」段），超了报「路太长，分段问」；
        - 到不了 ⇒ 报缺：目标有哪些入边、每条入边还差哪个前提——**不自己补边**。
        """
        rule_id = self._infer_rule()
        if not rule_id:
            return {"ok": False,
                    "弃答": "没有《推演的路》——X 到 Y 是什么，我没学过形状，不硬猜。"}
        spec = spec if isinstance(spec, dict) else {}
        starts = spec.get("从")
        if isinstance(starts, str):
            starts = [starts]
        starts = [str(s) for s in (starts or []) if str(s or "").strip()]
        goal = str(spec.get("到") or "").strip()
        if not starts or not goal:
            return {"ok": False,
                    "弃答": "推演要两样：`从`（已知，可多条）和 `到`（想得到的）。"
                            "少一个我就不走——起点的名字由你给，不由我编。"}
        edges = self._infer_edges()
        if not edges:
            return {"ok": False, "依据": [{"原文": f"《推演的路》（{rule_id}）",
                                          "可复算": "语料里 形态: 推演 的节点为零"}],
                    "报缺": [{"缺什么": "推演的边", "哪一类": "缺内容",
                              "对方原话": "语料里没有 形态: 推演 的节点",
                              "要谁给": "教导方：按《推演的路》四条字段写跳",
                              "给了怎么用": "落成 形态: 推演 节点后本条即可走"}],
                    "弃答": "路是空的：一条边都没教过，走不了。"}

        by_x, into = {}, {}
        for e in edges:
            by_x.setdefault(self._infer_key(e["前提"]), []).append(e)
            into.setdefault(self._infer_key(e["结论"]), []).append(e)

        gk = self._infer_key(goal)
        start_k = {self._infer_key(s): s for s in starts}
        prev, seen, frontier = {}, dict(start_k), list(start_k)
        found = gk in start_k
        hops = 0
        while frontier and not found and hops < 5:
            hops += 1
            nxt = []
            for k in frontier:
                for e in by_x.get(k, []):
                    yk = self._infer_key(e["结论"])
                    if yk in seen:
                        continue
                    seen[yk] = e["结论"]
                    prev[yk] = (k, e)
                    if yk == gk:
                        found = True
                        break
                    nxt.append(yk)
                if found:
                    break
            frontier = nxt

        base = [{"原文": f"《推演的路》（{rule_id}）",
                 "可复算": "每条边都能回它出自的 形态: 推演 节点，逐字对"}]
        if found:
            path, cur = [], gk
            while cur in prev:
                k, e = prev[cur]
                path.append(e)
                cur = k
            path.reverse()
            return {"ok": True, "从": starts, "到": goal, "跳数": len(path),
                    "路径": [{"前提": e["前提"], "规则": e["规则"], "结论": e["结论"],
                              "依据": e["依据"], "链": e["链"], "出自": e["出自"]}
                             for e in path],
                    "可达": sorted(seen.values())[:20],
                    "依据": base + [{"原文": f"{e['出自']}：{e['依据']}", "可复算": e["规则"]}
                                    for e in path],
                    "note": "逐跳可复核；哪一跳最弱由依据决定，不由我打分"}

        need = into.get(gk, [])
        miss = []
        for e in need:
            pk = self._infer_key(e["前提"])
            if pk in seen:
                continue
            # 这个前提本身能不能从起点推出来？只看**一层**，不替人递归猜。
            # 三态分开，瞒不得：
            #   ① 它没有入边        ⇒ 缺的是**一个观测值**（人给了就能用）
            #   ② 有入边且能接上    ⇒ 缺的是**一个中间结论**（再问一次它怎么来的）
            #   ③ 有入边但接不上    ⇒ 缺的是**一条路**（补这个值也没用）
            has_in = pk in into
            reachable = any(self._infer_key(x["前提"]) in seen
                            for x in into.get(pk, []))
            if not has_in:
                kind, how = "缺一个观测值", "人给了就能用（它是这条链的起点观测）"
            elif reachable:
                kind, how = "缺一个中间结论", "把它当 `从` 再问一次（它的来路你已经有）"
            else:
                kind, how = ("缺一条路",
                             f"把它当 `从` 再问一次；但它也没有从你起点过来的路"
                             f" ⇒ 接不上的是**一条路**，不是缺一个值")
            miss.append({"缺什么": e["前提"], "哪一类": "缺内容",
                         "对方原话": f"要「{goal}」，得先有它，这是《{e['规则']}》的前提",
                         "要谁给": "观测方或教导方（补这一跳）",
                         "缺的形态": kind, "给了怎么用": how,
                         "依据": e["依据"], "出自": e["出自"]})
        if not need:
            return {"ok": False, "从": starts, "到": goal,
                    "可达": sorted(seen.values())[:20], "依据": base,
                    "报缺": [{"缺什么": f"到「{goal}」的任何一条边", "哪一类": "缺内容",
                              "对方原话": "语料里没有以它为结论的跳",
                              "要谁给": "教导方（要么补这一跳，要么承认推不过去）",
                              "给了怎么用": "补边后再问；不补就只能说『没有这条路』"}],
                    "弃答": f"到不了「{goal}」：没有哪条教过的边以它作结论。"
                            f"上面这些是我从起点走得到的，不多不少。"}
        return {"ok": False, "从": starts, "到": goal,
                "可达": sorted(seen.values())[:20], "报缺": miss, "依据": base,
                "弃答": f"到不了「{goal}」：还得再有几个前提之一（共 {len(miss)} 条），"
                        f"缺的那几条在报缺单里——不猜、不跳。"}

    @staticmethod
    def _missing_kind(read) -> tuple:
        """把「没办成」归到语料那张表里的类别（返回 (类别, 缺什么)）。

        只是归类算子：类别名与语料表第一列**逐字对齐**，归不上就返回空
        （宁可报「说不清是哪一类」，也不硬塞）。
        """
        if not isinstance(read, dict):
            return "", ""
        back = read.get("对方返回")
        back = back if isinstance(back, dict) else {}
        fld = str(back.get("field") or "").strip()
        reason = str(back.get("reason") or back.get("error") or "").strip()
        concl = str(read.get("结论") or "")
        if concl == "我方格式错":
            lk = fld.lower()
            rl = reason.lower()
            # ★ 先看 reason：缺必填 / 类型错，**不管字段叫不叫 date**，缺的都是内容。
            #   （v0.2.27 实测踩到：A-12 缺 date 被判成「缺日序依据」，日序那类指的是
            #     日期写了但日序定不了，不是没写日期。）
            if ("missing" in rl or "required" in rl or "缺" in reason
                    or "invalid_type" in rl or "type" in rl):
                return "缺内容", (fld or "必填字段")
            if "date" in lk or "日" in lk:
                return "缺日序依据", (fld or "date")
            return "缺内容", (fld or reason or "报文内容")
        if concl == "读不懂":
            return "缺凭据事实", "凭据"
        if concl == "对方故障":
            return "缺对方可用性", "对方可用性"
        if concl == "驳回":
            return "缺内容", (fld or "内容")
        return "", ""

    @staticmethod
    def _fix_by_receipt(fields, back) -> tuple:
        """按对方回执把**写法**改成对方要的写法（返回 (新定义, 改动清单)）。

        只改写法，不改内容——同一天换个写法、同一币种换个名字。
        改不出来的（不在枚举里的币种、缺的字段、日序定不了）一律返回空：不猜。
        「改什么」的规矩教在 conc/533 里，这里只放**算子**（怎么换写法）。
        """
        import re as _re

        if not isinstance(fields, dict) or not isinstance(back, dict):
            return fields, []
        fld = str(back.get("field") or "").strip()
        # 真实服务两种写法都常见：{reason,detail} / {error,message}
        reason = str(back.get("reason") or back.get("error") or "").strip()
        detail = str(back.get("detail") or back.get("message") or "")
        if reason in ("missing_required_field", "invalid_enum_value_too_far"):
            return fields, []          # 缺内容 / 枚举外的值：不猜

        # 对方给的枚举：先看结构化字段，再从 detail 的 ['GBP', 'EUR', 'USD'] 里抓
        enum = back.get("enum") or back.get("枚举") or []
        if not isinstance(enum, list):
            enum = []
        if not enum:
            m = _re.search(r"\[([^\]]*)\]", detail)
            if m:
                enum = [x.strip().strip("'\"") for x in m.group(1).split(",")]
                enum = [x for x in enum if x]
        # 对方给的样例（日序由它定；没有就不猜）
        example = str(back.get("example") or back.get("样例") or "").strip()

        out = dict(fields)
        diffs = []

        def _try_date(key):
            v = str(out.get(key) or "").strip()
            if not v:
                return
            m = _re.fullmatch(r"(\d{1,4})[/.\-](\d{1,2})[/.\-](\d{1,4})", v)
            if not m:
                return
            a, b, c = m.group(1), m.group(2), m.group(3)
            y = mo = d = None
            if len(a) == 4:                       # YYYY?M?D —— 次序本身没歧义
                y, mo, d = a, b, c
            elif len(c) == 4:                     # 前两位是年：由样例定日序
                y = c
                # 日序只认样例；样例给的是 YYYY-MM-DD 的中间两位=月
                if len(example) >= 10 and example[4] not in "0123456789":
                    em = _re.fullmatch(r"(\d{4})(\D)(\d{1,2})\2(\d{1,2})", example)
                    if em:
                        g3, g4 = int(em.group(3)), int(em.group(4))
                        if g3 == int(a) and g4 == int(b):
                            mo, d = a, b             # 样例说明首位是月（M/D）
                        elif g3 == int(b) and g4 == int(a):
                            mo, d = b, a             # 样例说明首位是日（D/M）
                if mo is None and int(a) > 12 and int(b) <= 12:
                    d, mo = a, b                 # 同批能定序：>12 的只能是日
                elif mo is None and int(b) > 12 and int(a) <= 12:
                    mo, d = a, b
                if mo is None:
                    return                       # 定不了日序 ⇒ 不猜
            else:
                return
            try:
                y, mo, d = int(y), int(mo), int(d)
                import datetime as _dt0
                _dt0.date(y, mo, d)              # 日子得真存在（不把 02/30 改出一个假日期）
            except (TypeError, ValueError):
                return
            nv = f"{y:04d}-{mo:02d}-{d:02d}"
            if nv != v:
                out[key] = nv
                diffs.append(f"{key} {v} → {nv}")

        def _try_enum(key):
            v = str(out.get(key) or "").strip()
            if not v or not enum:
                return
            if v in enum:
                return
            for cand in enum:
                cv = str(cand)
                # 长名：GBP_STERLING → GBP（取前缀且前缀在枚举里）
                if v.upper().startswith(cv.upper() + "_") or \
                   v.upper().startswith(cv.upper()) and len(v) > len(cv):
                    out[key] = cv
                    diffs.append(f"{key} {v} → {cv}")
                    return
                # 大小写/分隔符：gbp → GBP
                if v.upper().replace("_", "").replace("-", "") == cv.upper().replace("_", ""):
                    out[key] = cv
                    diffs.append(f"{key} {v} → {cv}")
                    return

        # 回执点名了字段就只改它；没点名就按「日期/币种」各试一次（仍受上面那些不猜的约束）
        for key in (list(out) if not fld else [fld]):
            if key not in out:
                continue
            lk = str(key).lower()
            if "date" in lk or "日" in lk:
                _try_date(key)
            elif "curr" in lk or "币" in lk:
                _try_enum(key)
        return out, diffs

    def _port_call(self, spec, *, timeout: float = 5.0) -> dict:
        """端口调用路由（v0.1.91）：LDV 主动调端口，并**消费返回**。

        三件事分开：**契约由教导规定**（conc/393《端口调用规程》：三字段必填、
        返回必须消费、失败不重试）；**地址由人配置**（trigger.json 的 url——
        同一个端口两种用法：出箱时是它通知我们，这里是**我们主动调它**）；
        **发起与收账由路由做**。三字段缺一个就拒——缺的它不替人编。
        """
        import json as _j
        import time as _t
        import urllib.request as _u
        from pathlib import Path as _P

        if not isinstance(spec, dict):
            return {"ok": False, "弃答": "端口调用要 {做什么/为什么/请谁处理} 三字段。"}
        need = ("做什么", "为什么", "请谁处理")
        miss = [k for k in need if not str(spec.get(k) or "").strip()]
        if miss:
            r0 = {"ok": False,
                  "弃答": "《端口调用规程》三字段必填，缺：" + "、".join(miss)
                          + "——缺的我不会替你编。"}
            # 报缺（v0.2.27）：本地就缺的三字段也是客观事实（conc/535「缺三字段」）
            _m0 = self._missing_rule()
            if _m0["单"]:
                _row0 = self._missing_table(_m0["分类"]).get("缺三字段")
                r0["报缺"] = [{"缺什么": k, "哪一类": "缺三字段",
                               "对方原话": "还没发出去（本地就缺）",
                               "要谁给": _row0[1] if _row0 else "",
                               "给了之后怎么用": _row0[2] if _row0 else ""}
                              for k in miss]
                if not _row0:
                    for _it in r0["报缺"]:
                        _it["note"] = "语料表里没有「缺三字段」——谁给，我不替你定"
                r0["弃答"] += f"（缺 {len(miss)} 条客观事实：缺三字段——报缺，不猜）"
            return r0
        nd = self._nodes_dir()
        base = (_P(self._corpus_root) if hasattr(self, "_corpus_root")
                else (nd.parent if nd else _P(".")))
        cfg = {}
        cfg_p = base / "trigger.json"
        if cfg_p.is_file():
            try:
                cfg = _j.loads(cfg_p.read_text(encoding="utf-8") or "{}")
            except ValueError:
                cfg = {}
        url = str(cfg.get("url") or "").strip()
        if not url:
            r1 = {"ok": False,
                  "弃答": "没有配置端口——端口地址是人配的（trigger.json），我不发明目标。"}
            _m1 = self._missing_rule()
            if _m1["单"]:
                _row1 = self._missing_table(_m1["分类"]).get("缺地址")
                _it1 = {"缺什么": "端口地址", "哪一类": "缺地址",
                        "对方原话": "还没发出去（本地就缺）",
                        "要谁给": _row1[1] if _row1 else "",
                        "给了之后怎么用": _row1[2] if _row1 else ""}
                if not _row1:
                    _it1["note"] = "语料表里没有「缺地址」——谁给，我不替你定"
                r1["报缺"] = [_it1]
                r1["弃答"] += "（缺 1 条客观事实：缺地址——报缺，不猜）"
            return r1
        # ── 出站：按 conc/394《端口报文格式》组信封——格式是教的，不是临时拼的 ──
        import datetime as _dt
        now = _dt.datetime.now()
        seq = 1
        try:
            _lp = base / "port_calls.jsonl"
            if _lp.is_file():
                seq = len(_lp.read_text(encoding="utf-8").splitlines()) + 1
        except OSError:
            pass
        reqno = f"req-{now.strftime('%Y%m%d%H%M%S')}-{seq:04d}"
        payload = {k: str(spec[k]).strip() for k in need}
        # 「创建定义」通道（v0.1.97）：带 定义（结构化内容）+ 目标（落地文件名）时，
        # 动作切成"创建定义"——端口那边只管落盘，**内容是本内核给的**。
        extra = {}
        if isinstance(spec.get("定义"), dict):
            extra["定义"] = spec["定义"]
        if spec.get("目标"):
            extra["目标"] = str(spec["目标"]).strip()
        act = ("创建定义" if (extra.get("定义") and extra.get("目标"))
               else "端口调用")
        envelope = {"协议": "ldv-port/1", "请求号": reqno,
                    "时间戳": now.isoformat(timespec="seconds"),
                    "来源": "LDV", "动作": act, **payload, **extra}
        def _log(row):
            try:
                with (base / "port_calls.jsonl").open("a", encoding="utf-8") as f:
                    f.write(_j.dumps(row, ensure_ascii=False) + "\n")
            except OSError:
                pass

        import datetime as _dt
        import urllib.error as _ue
        t0 = _t.time()
        # 代理策略（v0.1.91）：默认**直连**——本工具面向内网/本地端口，
        # 环境代理会把 127.0.0.1 的调用也劫走（实测回了 502 而不是连接被拒）。
        # 要调公网且必须走代理的，配置里写 "proxy": "env"。
        _direct = str(cfg.get("proxy") or "direct") == "direct"

        def _post(body_bytes):
            """发一次，返回 (status, raw, err)。4xx/5xx **也是回执**，不能当连接失败吞掉
            ——否则 400（我方格式错）会被误报成对方故障，对方的"缺哪个字段"也全丢了
            （v0.1.92 实测抓到）。"""
            try:
                _opener = _u.build_opener(_u.ProxyHandler({})) if _direct else None
                req = _u.Request(url, data=body_bytes,
                                 headers={"Content-Type": "application/json"})
                with ((_opener.open(req, timeout=timeout) if _opener
                       else _u.urlopen(req, timeout=timeout))) as r:
                    return (int(getattr(r, "status", 200)),
                            r.read().decode("utf-8", "replace"), "")
            except _ue.HTTPError as he:
                try:
                    raw_h = he.read().decode("utf-8", "replace")
                except Exception:  # noqa: BLE001
                    raw_h = ""
                return int(getattr(he, "code", 0) or 0), raw_h, ""
            except Exception as exc:  # noqa: BLE001
                return 0, "", f"{type(exc).__name__}: {str(exc)[:100]}"

        # ── 重发路（v0.2.26）：规则来自语料，代码只执行 ──
        #    `改格式`  ← conc/533：4xx 是写法问题 ⇒ 改一次、重发一次
        #    `故障重试` ← conc/534：5xx/连接失败 ⇒ 原样重发，最多 2 次
        #    两个都没教过 ⇒ 循环第一次就 break，与 v0.2.25 逐字相同（I5）。
        rules = self._port_retry_rule()
        cur_extra = dict(extra)
        fix_log = []          # 改了什么：字段 旧 → 新
        fault_retries = 0
        attempts = []         # 每次尝试的读数（只留最后一次 = 看不出重发过）
        read = None
        status, raw, err = 0, "", ""
        last_env = envelope
        MAX_TRIES = 5         # 硬闸：3 次故障 + 1 次改格式，留余量，防跑飞
        for attempt in range(1, MAX_TRIES + 1):
            if attempt > 1:   # 重发换新请求号，便于逐次对账
                reqno = f"req-{now.strftime('%Y%m%d%H%M%S')}-{seq:04d}-r{attempt - 1}"
            last_env = {"协议": "ldv-port/1", "请求号": reqno,
                        "时间戳": now.isoformat(timespec="seconds"),
                        "来源": "LDV", "动作": act, **payload, **cur_extra}
            status, raw, err = _post(_j.dumps(last_env, ensure_ascii=False).encode("utf-8"))
            if err:
                read = {"结论": "对方故障", "可重试": "否——报人", "原文": err,
                        "依据": "连接失败/超时属对方故障，不是报文问题（conc/395）"}
            else:
                # 入站：按 conc/395 读对方的回话（五类结论）。`rule` 是语料教的
                # 读法（conc/532）；没教过就是空串，只扫顶层。
                read = self._port_read(status, raw, rule=self._credential_rule())
            ok_a = read.get("结论") == "受理"
            rec_a = {"url": url, "status": status,
                     "耗时毫秒": int((_t.time() - t0) * 1000),
                     "对方返回": (read.get("对方返回") if read.get("对方返回") is not None
                                  else (read.get("原文") or ""))}
            attempts.append({"尝试": attempt, "请求号": reqno, "status": status,
                             "结论": read.get("结论"),
                             "对方返回": rec_a["对方返回"]})
            _log({"ts": _dt.datetime.now().isoformat(timespec="seconds"),
                  "请求号": reqno, "尝试": attempt, "信封": last_env,
                  **rec_a, "解读": read, "ok": ok_a})
            if ok_a:
                break
            # ① 4xx：教过《改格式重发》且能机械改写法 ⇒ 改一次、只改一次
            if (read.get("结论") == "我方格式错" and rules["改格式"] and not fix_log):
                fixed, diffs = self._fix_by_receipt(cur_extra.get("定义") or {},
                                                    read.get("对方返回"))
                if diffs:
                    cur_extra["定义"] = fixed
                    fix_log.extend(diffs)
                    continue
            # ② 5xx/连接失败：教过《对方故障有界重试》⇒ 报文不动，重发，最多 2 次
            if (read.get("结论") == "对方故障" and rules["故障重试"]
                    and fault_retries < 2):
                fault_retries += 1
                continue
            break

        ok = read.get("结论") == "受理"
        back = read.get("对方返回")
        receipt = {"url": url, "status": status,
                   "耗时毫秒": int((_t.time() - t0) * 1000),
                   "尝试次数": len(attempts),
                   "对方返回": back if back is not None else (read.get("原文") or "")}
        out = {"ok": ok, "请求号": reqno, "信封": last_env,
               "回执": receipt, "解读": read,
               "依据": [{"原文": "《端口报文格式》信封七字段 + 《端口回执解读》五类结论"
                                "（conc/394、conc/395）"
                                + ("＋《改格式重发》（" + rules["改格式"] + "）"
                                   if fix_log else "")
                                + ("＋《对方故障有界重试》（" + rules["故障重试"] + "）"
                                   if len(attempts) > 1 else ""),
                         "可复算": "port_calls.jsonl 里同一请求号的信封与解读成对——"
                                   "编号由对方生成，本地无法伪造"}]}
        if fix_log:
            out["改动"] = fix_log          # 改了什么：字段 旧 → 新（可复算）
        if len(attempts) > 1:
            out["尝试"] = attempts          # 每一次的 status 与对方原话
        if not ok:
            # ── 报缺路由（v0.2.27）：没办成而缺的是**客观事实** ⇒ 出报缺单，不猜 ──
            #    规矩全在语料（conc/536 报缺单 + conc/535 那张六类表）；这里只有算子：
            #    归哪一类（_missing_kind）、表里谁给（_missing_table）。没教过 ⇒ 走旧样。
            _mr = self._missing_rule()
            _kind, _what = self._missing_kind(read)
            if _mr["单"] and _kind:
                _row = self._missing_table(_mr["分类"]).get(_kind)
                _say = ""
                if isinstance(back, dict):
                    _say = str(back.get("detail") or back.get("message")
                               or back.get("reason") or back.get("error") or "")
                if not _say:
                    _say = str(read.get("原文") or "")
                _item = {"缺什么": _what, "哪一类": _kind,
                         "对方原话": (_say[:200] if _say else "对方没说"),
                         "要谁给": _row[1] if _row else "",
                         "给了之后怎么用": _row[2] if _row else ""}
                if _row is None:
                    _item["note"] = "语料那张表里没有这一类——谁给，我不替你定"
                out["报缺"] = [_item]
                _tail = ""
                if len(attempts) > 1:
                    _tail = f"；试了 {len(attempts)} 次（见「尝试」）"
                if fix_log:
                    _tail += f"；改过（{'；'.join(fix_log)}）仍是 4xx"
                out["弃答"] = (f"没办成，缺 1 条客观事实（{_kind}：{_what}）"
                               f"——报缺单已出，不猜（conc/536；依据 conc/535）" + _tail)
            else:
                parts = []
                if read.get("原因"):
                    parts.append(str(read["原因"])[:120])
                if read.get("缺"):
                    parts.append("对方指出缺：" + "、".join(str(x) for x in read["缺"]))
                if not parts and read.get("原文"):
                    parts.append("回执原文：" + str(read["原文"])[:80])
                if not parts and read.get("对方返回") is not None:
                    parts.append("对方返回：" + str(read["对方返回"])[:80])
                tail = str(read.get("可重试") or "")
                if read.get("结论") == "对方故障" and rules["故障重试"]:
                    tail = (f"按《对方故障有界重试》原样重发了 {len(attempts) - 1} 次仍挂"
                            f"——报人（不再试）")
                elif read.get("结论") == "我方格式错" and rules["改格式"] and not fix_log:
                    tail = "改不出可依据的写法（日序定不了 / 值不在枚举里 / 缺的是内容）——不猜，报人"
                elif read.get("结论") == "我方格式错" and fix_log:
                    tail = (f"改过（{'；'.join(fix_log)}）重发一次仍是 4xx——不再猜第二回，报人")
                out["弃答"] = (f"对方没有受理（{read.get('结论')}）"
                               + ("：" + "；".join(parts) if parts else "")
                               + f"——{tail}")
        return out

    def _api_def(self, spec) -> dict:
        """API 定义路由（v0.1.97）：按教过的规范**自己产出** OpenAPI 3.0 定义。

        内容全部来自语料——《API 定义规范》的端点行与结构行、《图书借阅规程》的常量行；
        路由只做**装配**（解析 → 组装），不发明端点、不补字段、不改业务规则。
        "教导即配置"：加/改端点只改节点，代码一行不动。
        """
        if not isinstance(spec, dict):
            spec = {}
        title = str(spec.get("名称") or "图书借阅")
        api_ver = str(spec.get("版本") or "1.0.0")

        def _body(name):
            r = self.query({"require": ["name=" + name]})
            ids = list(r.get("answer_ids") or [])
            if not ids:
                return "", []
            return (str(getattr(self.nodes.get(ids[0]), "body", "") or ""), ids)

        spec_nm = str(spec.get("规范") or "API 定义规范")
        rule_nm = str(spec.get("业务规程") or "图书借阅规程")
        spec_body, spec_ids = _body(spec_nm)
        rule_body, rule_ids = _body(rule_nm)
        if not spec_body:
            return {"ok": False, "弃答": "《API 定义规范》查不到——没有规范就不出定义（不发明端点）。"}
        if not rule_body:
            return {"ok": False, "弃答": "《图书借阅规程》查不到——没有业务规则就不出定义。"}

        types = {"string": {"type": "string"}, "integer": {"type": "integer"},
                 "number": {"type": "number"}, "boolean": {"type": "boolean"},
                 "date": {"type": "string", "format": "date"}}

        def _num_or(x, default=None):
            try:
                v = float(x)
            except (TypeError, ValueError):
                return default
            return int(v) if v == int(v) else v

        def _type(t: str) -> dict:
            """解析类型标注（v0.2.1 扩展的三种写法，让条文里的约束落得下来）：

            `number[0,100,0.1]`  → minimum / maximum / multipleOf
            `string{正常|缺考}`   → enum
            `number[...]|null`   → nullable（缺考没有分数，不是 0 分）
            """
            import re as _r
            raw = str(t or "string").strip()
            enum = None
            m = _r.search(r"\{([^}]*)\}", raw)
            if m:
                enum = [x.strip() for x in m.group(1).split("|") if x.strip()]
                raw = (raw[:m.start()] + raw[m.end():]).strip()
            rng = None
            m2 = _r.search(r"\[([^\]]*)\]", raw)
            if m2:
                rng = [x.strip() for x in m2.group(1).split(",")]
                raw = (raw[:m2.start()] + raw[m2.end():]).strip()
            nullable = "|null" in raw or raw.endswith("?")
            base_name = raw.replace("|null", "").strip().rstrip("?") or "string"
            sch = dict(types.get(base_name, {"type": "string"}))
            if rng:
                lo = _num_or(rng[0]) if len(rng) > 0 else None
                hi = _num_or(rng[1]) if len(rng) > 1 else None
                st = _num_or(rng[2]) if len(rng) > 2 else None
                if lo is not None:
                    sch["minimum"] = lo
                if hi is not None:
                    sch["maximum"] = hi
                if st:
                    sch["multipleOf"] = st
            if enum:
                sch["enum"] = enum
            if nullable:
                sch["nullable"] = True
            return sch
        code_desc = {"200": "成功", "201": "已创建", "400": "参数不合法",
                     "404": "找不到资源", "409": "业务规则冲突"}

        def _split_top(s: str, sep: str = ",") -> list:
            """按分隔符切，但**括号里的分隔符不算**。

            两处都踩过：类型标注 `number[0,100,0.1]` 里的逗号、枚举
            `string{正常|缺考}` 里的竖线——粗暴 split 会把一行切碎，
            `score:number[0` 这种半截就成了 string，枚举干脆整段丢失。
            """
            out, buf, depth = [], [], 0
            for ch in str(s or ""):
                if ch in "[{（(":
                    depth += 1
                elif ch in "]})）":
                    depth = max(0, depth - 1)
                if ch == sep and depth == 0:
                    out.append("".join(buf))
                    buf = []
                else:
                    buf.append(ch)
            if "".join(buf).strip():
                out.append("".join(buf))
            return [x.strip() for x in out if x.strip()]

        def _camel(p: str) -> str:
            out = []
            for seg in [x for x in p.strip("/").split("/") if x]:
                out.append("By" + seg[1:-1].capitalize() if seg.startswith("{")
                           else seg.capitalize())
            return "".join(out)

        eps, schemas, consts, secs, auths = [], {}, {}, [], []
        section = None
        for raw in spec_body.splitlines():
            s = raw.strip()
            if s in ("端点:", "端点："):
                section = "eps"
                continue
            if s in ("结构:", "结构："):
                section = "schemas"
                continue
            if s in ("安全:", "安全："):
                section = "sec"
                continue
            if s in ("授权:", "授权："):
                section = "auth"
                continue
            if not s.startswith("- ") or "|" not in s:
                continue
            parts = _split_top(s[2:], "|")
            if section == "eps" and len(parts) >= 4:
                head = parts[0].split()
                if len(head) < 2:
                    continue
                plist = parts[2].replace("参数:", "").replace("参数：", "")
                clist = parts[3].replace("响应:", "").replace("响应：", "")
                eps.append({"method": head[0].lower(), "path": head[1],
                            "summary": parts[1],
                            "params": _split_top(plist),
                            "codes": _split_top(clist)})
            elif section == "auth" and len(parts) >= 2:
                def _kv(x):
                    return x.split("=", 1)[1].strip() if "=" in x else ""
                auths.append({"ep": parts[0], "主体": _kv(parts[1]),
                              "规则": _kv(parts[2]) if len(parts) > 2 else "",
                              "例外": _kv(parts[3]) if len(parts) > 3 else ""})
            elif section == "sec" and len(parts) >= 3:
                secs.append({"名": parts[0], "scheme": parts[1],
                             "头": parts[2],
                             "说明": parts[3] if len(parts) > 3 else ""})
            elif section == "schemas" and len(parts) >= 2:
                props = {}
                flist = parts[1].replace("字段:", "").replace("字段：", "")
                for fv in _split_top(flist):
                    fv = fv.strip()
                    if ":" in fv:
                        fn, ft = fv.split(":", 1)
                        props[fn.strip()] = _type(ft)
                schemas[parts[0].strip()] = {"type": "object", "properties": props}

        for raw in rule_body.splitlines():
            s = raw.strip()
            if s.startswith("常量:") or s.startswith("常量："):
                for kv in s[3:].split("；"):
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        consts[k.strip()] = v.strip()

        if not eps:
            return {"ok": False,
                    "弃答": "规范里没有可解析的端点行（格式：- 方法 路径 | 说明 | 参数: … | 响应: 码=结构）。"}

        paths = {}
        for e in eps:
            pth = e["path"]
            pp = [seg[1:-1] for seg in pth.split("/")
                  if seg.startswith("{") and seg.endswith("}")]
            params_spec = [{"name": nm, "in": "path", "required": True,
                            "schema": {"type": "string"}} for nm in pp]
            bprops, breq = {}, []
            # 参数写法（v0.2.1）：`q:名` / `q?:名`（查询，恒可选）/ `b:名`（请求体必填）
            # / `b?:名`（请求体可选，PATCH 用）；后面可跟 `:类型`，类型走 _type 解析。
            # 以前 b: 一律 string、b: 一律必填——PATCH 的「只改一个字段」根本表达不出来。
            import re as _r3
            for prm in e["params"]:
                _m = _r3.match(r"^(q|b)(\?)?:([^:]+)(?::(.+))?$", prm.strip())
                if not _m:
                    continue
                _where, _opt = _m.group(1), bool(_m.group(2))
                _nm, _ty = _m.group(3).strip(), _m.group(4)
                _sch = _type(_ty or "string")
                if _where == "q":
                    params_spec.append({"name": _nm, "in": "query",
                                        "required": False, "schema": _sch})
                else:
                    bprops[_nm] = _sch
                    if not _opt:
                        breq.append(_nm)
            op = {"summary": e["summary"],
                  "operationId": e["method"] + _camel(pth)}
            if params_spec:
                op["parameters"] = params_spec
            if bprops:
                sname = e["method"].capitalize() + _camel(pth) + "Request"
                schemas[sname] = {"type": "object", "required": breq,
                                  "properties": bprops}
                op["requestBody"] = {"required": True,
                                     "content": {"application/json": {
                                         "schema": {"$ref": "#/components/schemas/" + sname}}}}
            resp = {}
            for c in e["codes"]:
                code, _, sref = c.partition("=")
                code, sref = code.strip(), sref.strip()
                item = {"description": code_desc.get(code, "响应")}
                if sref:
                    if sref.endswith("[]"):
                        sch = {"type": "array",
                               "items": {"$ref": "#/components/schemas/" + sref[:-2]}}
                    else:
                        sch = {"$ref": "#/components/schemas/" + sref}
                    item["content"] = {"application/json": {"schema": sch}}
                resp[code] = item
            paths.setdefault(pth, {})[e["method"]] = dict(op, responses=resp)

        # 业务硬规则（v0.1.97，应 agent 指出的"规则只隐含在 409 里"而补）：
        # 从规程正文抽含 不许/不得/最多/逾期/不做/禁止 的整句，原样放进 x-business-rules
        # ——**不概括、不改写**，抽的是原文。
        import re as _re
        rules = []
        # 先摊平：语料里的正文是**按行折的**，直接在 \n 上切会把一句话切成两半
        # （实测：人员管理只抽出 1 条，还带 ** 残迹）。摊平后再按句号/分号切。
        # 常量行不是规则，先摘掉（否则「工号唯一=是」会被当成一条规则抽进来）
        _no_const = _re.sub(r"常量\s*[：:][^\n]*", "", rule_body)
        flat = (_no_const.replace("**", "").replace("\r", "")
                .replace("\n", "").strip())
        # 括号里的分号不是句子边界（"（工号是人的唯一标识；重号说明建错了人）"
        # 被切开过），先把括号内的分号换成顿号再切
        flat = _re.sub(r"（[^）]*）", lambda m: m.group().replace("；", "、"), flat)
        for sent in _re.split(r"[。；]", flat):
            t = sent.strip(" 　\t:-—")
            # 去掉开头的「XX规程：」——规则本身才是要抽的东西
            t = _re.sub(r"^\S{2,12}?规程[：:]", "", t).strip()
            if len(t) >= 8 and any(k in t for k in (
                    "不许", "不得", "最多", "逾期", "不做", "禁止", "唯一",
                    "一律", "只能", "拒建", "拒录", "脱敏", "需要审批", "留痕")):
                rules.append(t)

        # 安全方案（有 401 的操作才挂 security——规范里没写的不硬加）
        schemes = {}
        for sc in secs:
            schemes[sc["名"]] = {"type": "http", "scheme": sc["scheme"],
                                 "description": (sc["说明"] + f"（凭据头 {sc['头']}）").strip()}
        # 授权（v0.2.1）：每段挂到对应操作的 x-authorization——"谁能看哪一条"进定义，
        # 不再只是 securityScheme 里的一句散文（agent 指出的正是这个缺维度）
        for a in auths:
            _h = str(a.get("ep") or "").split()
            if len(_h) < 2:
                continue
            _op = (paths.get(_h[1]) or {}).get(_h[0].lower())
            if _op is None:
                continue
            _op["x-authorization"] = {"主体": a["主体"], "规则": a["规则"],
                                      "例外": a["例外"]}
        if schemes:
            # paths 已经装配好了，就挂在这里（别再去改 eps——那时候 op 已经生成）
            for _pth, _item in paths.items():
                for _m, _op in _item.items():
                    if any(c.startswith("401") for c in (_op.get("responses") or {})):
                        _op["security"] = [{k: [] for k in schemes}]
        doc = {"openapi": "3.0.3",
               "info": {"title": (title if title.endswith("API") else title + " API"),
                        "version": api_ver,
                        "description": (f"按 LDV 教导的《{rule_nm}》"
                                        f"与《{spec_nm}》装配"),
                        "x-rules": consts,
                        "x-business-rules": rules},
               "paths": paths,
               "components": ({"schemas": schemas, "securitySchemes": schemes}
                              if schemes else {"schemas": schemas})}
        return {"ok": True, "定义": doc,
                "统计": {"端点": len(eps), "结构": len(schemas), "常量": len(consts),
                         "业务规则": len(rules), "安全方案": len(schemes),
                         "授权段": len(auths)},
                "来源": [{"原文": (f"《{spec_nm}》端点行/结构行/安全段 + "
                                          f"《{rule_nm}》常量行与业务规则"
                                          f"（{('、'.join(spec_ids + rule_ids)) or '节点 id 缺'}）"),
                          "可复算": "paths 与 components 逐项来自节点正文，路由只解析装配"}],
                "节点": spec_ids + rule_ids}

    @staticmethod
    def _auth_check(token, who_req, secret: str = "") -> dict:
        """验凭据（v0.2.0）——《鉴权规程》conc/411 的可执行形态。

        **只做四件事，顺序不许换**：格式 → 签名 → 有效期 → 身份相符。
        第五件（"凭据没写这个权限，大概也行"）不做——那就是猜。
        返回 {"拦": True, "码": 401/403, "原因": …} 或 {"拦": False, "谁", "到期"}。
        """
        import base64 as _b64
        import datetime as _dt2
        import hashlib as _hl
        import hmac as _hm
        import json as _j2

        if not secret:
            return {"拦": True, "码": 401,
                    "原因": "《鉴权规程》：**密钥没配**（配置里 auth_secret）——"
                            "没密钥验不了签，就不受理；不凭「看着像自己人」放行。"}
        if not token:
            return {"拦": True, "码": 401,
                    "原因": "《鉴权规程》：**没有凭据 ⇒ 401 不受理**。"
                            "自报的「我是某某」不是身份——自报的身份谁都能报。"}
        raw = str(token).strip()
        if raw.lower().startswith("bearer "):
            raw = raw[7:].strip()
        if raw.count(".") != 1:                                   # ① 格式
            return {"拦": True, "码": 401,
                    "原因": "《鉴权规程》：凭据**格式坏**——应是「载荷.签名」，"
                            "少一段或多一段都算坏。"}
        p64, sig = raw.split(".", 1)
        try:
            payload_raw = _b64.urlsafe_b64decode(p64 + "=" * (-len(p64) % 4))
        except Exception:
            return {"拦": True, "码": 401,
                    "原因": "《鉴权规程》：凭据**格式坏**——载荷不是合法编码。"}
        want = _hm.new(secret.encode("utf-8"), payload_raw, _hl.sha256).hexdigest()
        if not _hm.compare_digest(want, sig.strip().lower()):     # ② 签名
            return {"拦": True, "码": 401,
                    "原因": "《鉴权规程》：**签名对不上**——凭据被改过，或不是这把密钥签的。"}
        try:
            pay = _j2.loads(payload_raw.decode("utf-8"))
        except ValueError:
            return {"拦": True, "码": 401,
                    "原因": "《鉴权规程》：凭据**格式坏**——载荷不是 JSON。"}
        who = str((pay or {}).get("谁") or "").strip()
        exp = str((pay or {}).get("到期") or "").strip()
        if not who or not exp:
            return {"拦": True, "码": 401,
                    "原因": "《鉴权规程》：凭据里**缺「谁」或「到期」**——"
                            "认不出人、也不知何时作废，不能受理。"}
        if exp < _dt2.date.today().isoformat():                   # ③ 有效期
            return {"拦": True, "码": 401,
                    "原因": f"《鉴权规程》：凭据**已于 {exp} 到期**——"
                            f"到期即作废，不看载荷里写得多漂亮。"}
        if who_req and str(who_req).strip() != who:               # ④ 身份相符
            return {"拦": True, "码": 403,
                    "原因": f"《鉴权规程》**越权**：凭据是「{who}」的，"
                            f"事却要办在「{who_req}」名下——403。"
                            f"（身份以凭据为准，请求里那个只用来对照。）"}
        return {"拦": False, "谁": who, "到期": exp}

    def _reload_after_learn(self) -> None:
        """自学写入后重载语料（v0.2.3）。

        **不重载就等于学了个寂寞**：文件在盘上、进程里的 `self.nodes` 还是旧的，
        于是刚学到的读不出来、查不到（实测：agent 学到 conc/422，回头 ldv_read
        说"没有这个节点"）。这里只重读节点与结构，不动 usage/配置。
        """
        from ldv.corpus.loader import load_edges as _le, load_nodes as _ln
        nd = self._nodes_dir()
        if nd is None:
            return
        self.nodes = _ln(nd)
        self.edges, _ = _le(self.nodes)
        try:
            if self.declared:
                self.declared_added = DECL.apply(self.nodes, self.declared,
                                                 self._decl_ledger)
        except Exception:      # noqa: BLE001 —— 声明重放失败不影响已读入的节点
            pass
        self._rebuild_structure(reload_usage=False)

    def _assemble(self, spec) -> dict:
        """装配路由（v0.2.8，用户 2026-11-08「教装配」）：把目标 + 零件装成一张装配单。

        **教的是形状，不是路径**：《装配规范》conc/439 只规定装配单长什么样
        （目标/判据/覆盖/缺口/状态），不规定某件事该怎么走。
        装配单回答的是「这条判据靠哪块砖顶着」，**不排先后**——
        先后是执行的事；这里越界写了顺序，就等于替人做了执行决定。

        判据从目标正文**逐条取**（①②③），一条都不许省；每条判据去库里找能管的
        零件，**相关性不够就标缺**，不拿最像的顶替。
        """
        import re as _re

        if isinstance(spec, str):
            spec = {"名称": spec}
        if not isinstance(spec, dict):
            return {"ok": False, "弃答": "装配要 {名称}——给一个教过的目标名。"}
        name = str(spec.get("名称") or spec.get("目标") or "").strip()
        if not name:
            return {"ok": False, "弃答": "装配要「名称」——装哪件事的目标。"}

        # 存在性直接扫节点（不走 require：键没声明就回 0 条，会把"有"当成"没有"）
        tid = next((i for i, n in self.nodes.items()
                    if str(n.fields.get("name") or "") == name), None)
        if not tid:
            return {"ok": False,
                    "弃答": (f"「{name}」没教过——装配的对象必须是教过的目标"
                             f"（没有目标就没有判据，装不出东西，也不发明）。")}
        tbody = str(getattr(self.nodes[tid], "body", "") or "")

        # ── 判据：逐条取（①②③ 开头；续行接到上一条）──
        crits = []
        for line in tbody.splitlines():
            s = line.strip()
            if not s:
                continue
            if _re.match(r"^[①-⑩]", s):
                crits.append(s)
            elif crits and not s.startswith(("**", "-", "（", "目标")):
                crits[-1] += s
        if not crits:
            return {"ok": False,
                    "弃答": (f"「{name}」正文里没有分条判据（①②③）——"
                             "装配靠判据，没有判据就装不了（先教判据，别教顺序）。")}

        rows = []
        for i0, c in enumerate(crits):
            body0 = _re.sub(r"^[①-⑩]\s*", "", c)
            head, _, why = body0.partition("——")
            rows.append({"序": "①②③④⑤⑥⑦⑧⑨⑩"[i0],
                         "判据": head.replace("*", "").strip(" 。；"),
                         "说明": why.replace("*", "").strip(" 。；"),
                         "覆盖": [], "状态": "缺"})

        def _grams(s0):
            t0 = _re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", str(s0 or ""))
            return {t0[i:i + 2] for i in range(len(t0) - 1)}

        def _grams3(s0):
            """三字片段（v0.2.18）：**入册资格用**。

            二字片段在中文长句里必然遍地命中（"三种/不许/必须/查询"）——
            拿它当资格，装配单就会把「没零件」报成「有零件」。三字起步才勉强
            算「说的是同一件事」。
            """
            t0 = _re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", str(s0 or ""))
            return {t0[i:i + 3] for i in range(len(t0) - 2)}

        # 不算零件的：目标自己、《装配规范》自己、元概念（目标/评估）、判错支
        _META = {"装配规范", "目标", "评估"}
        _skip = {tid}
        _skip |= {i for i, n in self.nodes.items()
                  if str(n.fields.get("name") or "") in _META | {name}}
        pool = {}
        for nid, n in self.nodes.items():
            if nid in _skip or str(n.fields.get("裁决") or "") == "判错":
                continue
            nm = str(n.fields.get("name") or "")
            if not nm:
                continue
            bd = str(getattr(n, "body", "") or "")
            pool[nid] = (nm, _grams(nm) | _grams(bd), _grams3(nm) | _grams3(bd),
                         bd[:400], str(n.fields.get("文档类") or ""))

        # 打分（v0.2.18）：**三字片段是资格，二字片段只是加分**。
        # 旧版把二字片段当资格，"三种/不许/必须/查询"这类词在谁身上都能命中 ⇒
        # 装配单把「库里没有这块砖」报成「有」（实测：空结果三分类被判"可交付"）。
        # 现在：判据名里至少命中 1 个三字片段（或说明里命中 1 个）才准进覆盖表。
        # ⚠️ 仍是字面覆盖，不是语义覆盖——所以只给三档（有/弱/缺），命中词摊开给人看。
        _HI = float(spec.get("高") or 6)
        _LO = float(spec.get("低") or 3)
        for row in rows:
            gname, gwhy = _grams(row["判据"]), _grams(row["说明"])
            gname3, gwhy3 = _grams3(row["判据"]), _grams3(row["说明"])
            if not gname and not gwhy:
                continue
            sc = []
            # 虚字/规范字：由它们构成的三字片段**不算资格**
            # ——"不允许""必须有""一段的"在每份规范里都有，命中它们等于没命中。
            _STOP = set("不没可以要须必应当的了是在和与或也就都还很这个那一三段种替解释"
                         "什么怎样哪如何吗呢吧啊")
            for nid, (nm, gp, gp3, bd, doc) in pool.items():
                raw3 = gname3 & gp3
                hit3 = {h for h in raw3 if not (set(h) & _STOP)}
                qw3 = len({h for h in (gwhy3 & gp3) if not (set(h) & _STOP)})
                if not (hit3 or qw3):
                    continue                    # 没一个**实义**三字片段对上 ⇒ 不够格当零件
                hn2, hw2 = len(gname & gp), len(gwhy & gp)
                score = 9 * len(hit3) + 3 * qw3 + 2 * hn2 + hw2
                # 「就是这块砖」vs「正文里提过一嘴」：三字片段要落在**零件名**
                # 或**首句**（开篇就说它是什么）才算主句；后半段的旁白不算。
                first = _re.split(r"[。；！？]", bd)[0]
                nhit3 = len(hit3 & _grams3(nm))
                fhit3 = any(h in first for h in hit3)
                # 规程类零件的规矩常写在末尾（"……也不许自动重试同一参数"）——
                # 落在**规范句**里同样算主句：那正是这块砖的用处。
                norm = [s for s in _re.split(r"[。；！？]", bd)
                        if _re.search(r"不许|不得|必须|一律|禁止|只认|只许|不允许|不该|不能|不进入|不重试|要", s)]
                nhit3s = any(h in s for s in norm for h in hit3)
                # 再者：命中落在正文**前半段**（开篇就说它是什么）——
                # 这是 v0.2.8 的老口径，保留（夹具与既有承诺都靠它）。
                _idx = min((bd.find(h) for h in hit3 if bd.find(h) >= 0), default=len(bd))
                fhalf = 1 if (hit3 and _idx <= max(20, len(bd) // 2)) else 0
                main = 1 if (nhit3 or fhit3 or nhit3s or fhalf) else 0
                sc.append((score, main, nid, nm, doc, sorted((hit3 | (gname & gp)) or (gwhy3 & gp3))[:5]))
            sc.sort(key=lambda x: (-x[0], -x[1], x[3]))
            for score, main, nid, nm, doc, hit in sc[:2]:
                if score < _LO:
                    continue        # 相关性不够 ⇒ 不是零件（宁缺勿替）
                row["覆盖"].append({"id": nid, "name": nm, "分": score,
                                    "主句": bool(main), "命中": hit[:5],
                                    "文档类": doc or None})
            # 外部摘录只能算「弱」：摘录是别人的话，当不了「这块砖」
            # v0.2.20：目标本身是规程/审计类时，**词汇节点不能判「有」**——
            # 词义只说明"说的是同一件事"，不说明"这条规矩管得住它"（最高「弱」）。
            _formal = str((self.nodes[tid].fields or {}).get("文档类") or "")
            _strict = _formal in {"审计目标", "审计规范", "交付规范", "调用规程",
                                  "接口规范", "查询语法", "流程规程", "流程禁止", "应用规范"}
            _ok_doc = {"审计目标", "审计规范", "交付规范", "调用规程", "接口规范",
                       "查询语法", "流程规程", "流程禁止", "应用规范"}
            if any(c["分"] >= _HI and c.get("主句")
                   and c.get("文档类") != "外部摘录"
                   and (not _strict or c.get("文档类") in _ok_doc)
                   for c in row["覆盖"]):
                row["状态"] = "有"
            elif row["覆盖"]:
                row["状态"] = "弱"
            else:
                row["状态"] = "缺"

        # 决策留痕（只记不改）：每条判据一次「在候选里挑了谁」
        for _r in rows:
            self._decision_log(
                "装配", "判据状态=" + _r["状态"],
                [{"名": c["name"], "分": c["分"]} for c in (_r["覆盖"] or [])],
                (_r["覆盖"][0]["name"] if _r["覆盖"] else None),
                _r["序"] + " " + _r["判据"])
        gaps = [{"序": r["序"], "判据": r["判据"],
                 "档": r["状态"],
                 "要什么": ("一条事（判据在，但库里没有零件管得住它）"
                            if r["状态"] == "缺"
                            else "只找到弱相关的候选——是不是这块砖，要人看一眼")}
                for r in rows if r["状态"] != "有"]
        _bad = sum(1 for r in rows if r["状态"] == "缺")
        _weak = sum(1 for r in rows if r["状态"] == "弱")
        if not gaps:
            _st = "可交付"
        elif _bad:
            _st = f"缺 {_bad} 条" + (f"、弱 {_weak} 条" if _weak else "") + "，交人"
        else:
            _st = f"弱 {_weak} 条，交人判"
        _out = {"ok": True, "路由": "装配",
                "装配单": {"目标": {"id": tid, "name": name},
                           "判据": rows,
                           "缺口": gaps,
                           "状态": _st},
                "不写的东西": ["先后顺序（执行的事，不是装配的事）",
                               "渠道/频次/阈值（这些值由人定，装配单里留「待定」）"],
                "局限": ("覆盖是**字面**匹配，资格线是**三字片段**（v0.2.18）——三字对不上就进不了表，"
                         "所以宁缺勿凑；但字面对上仍不等于语义对上（「提醒」与「预警」抓不到，"
                         "反过来「同一参数」也可能只是句子里顺带提到）。"
                         "看「命中」那几个片段，分低的当缺；拿不准就少装。"),
                "依据": [{"原文": "《装配规范》：五段（目标/判据/覆盖/缺口/状态），"
                                  "相关不够就标缺、不排先后（conc/439）",
                          "可复算": f"目标 {tid} 正文的分条判据 × 覆盖表的 相关度"}]}
        # 记账（v0.2.9）：把这一轮的装配快照落进台账——**过程是从这些快照长出来的**，
        # 不是一次算出来的。只在 `记账: true` 时写，避免每次查询都产账。
        if spec.get("记账"):
            _rec = self._assemble_log(name, "装配", None, {
                "判据": [{"序": r["序"], "判据": r["判据"], "状态": r["状态"],
                          "覆盖": [c["name"] for c in r["覆盖"]]} for r in rows],
                "状态": _st})
            _out["记账"] = _rec
        return _out

    def _assemble_log(self, target, event, gap, detail) -> dict:
        """装配台账（v0.2.9）：补齐史就是过程本身的原料。

        一条一行：目标 / 事件（装配|补齐|确认依赖）/ 缺口 / 补了什么 / 依赖谁 / 补后状态。
        ⚠️ **只记账，不改判据、不排顺序**——顺序是 `过程` 路由从这里读出来推的。
        """
        import datetime as _dt
        import json as _j
        root = (self._corpus_root if hasattr(self, "_corpus_root") else None)
        if root is None:
            return {"写成": False, "why": "内置语料不写账"}
        n = 0
        p = root / "assemble_ledger.jsonl"
        try:
            if p.is_file():
                with p.open(encoding="utf-8") as fh:
                    n = sum(1 for ln in fh
                            if ln.strip() and _j.loads(ln).get("目标") == target)
        except (OSError, ValueError):
            n = 0
        rec = {"ts": _dt.datetime.now().isoformat(timespec="seconds"),
               "目标": target, "事件": event, "轮次": n + 1}
        if gap:
            rec["缺口"] = gap
        rec.update(detail or {})
        try:
            with p.open("a", encoding="utf-8") as fh:
                fh.write(_j.dumps(rec, ensure_ascii=False) + "\n")
        except OSError as exc:
            return {"写成": False, "why": str(exc)[:60]}
        return {"写成": True, "轮次": rec["轮次"], "账": str(p.name)}

    def _process(self, spec) -> dict:
        """过程路由（v0.2.9，用户 2026-11-09：补一个缺口记一笔，从记录综合出过程）。

        **顺序不是设计出来的，是补齐史推出来的**：
        - 步骤 = 最新一版装配单里的判据（一条一步）；
        - 依赖 = 台账里「补齐/确认依赖」事件记的 `依赖: ["②"]`；
        - 拓扑排出先后；**没有依赖记录的步骤并列**（可以换序），如实标出来。

        局限照旧：依赖是人给的，给得少，排出来的序就残缺——**残缺要说出来，不许补全**。
        有环 ⇒ 报警（环通常意味着判据或依赖写错了，不是流程真有环）。
        """
        import json as _j
        if isinstance(spec, str):
            spec = {"名称": spec}
        if not isinstance(spec, dict):
            return {"ok": False, "弃答": "过程要 {名称}——给一个装配过的目标名。"}
        name = str(spec.get("名称") or spec.get("目标") or "").strip()
        if not name:
            return {"ok": False, "弃答": "过程要「名称」。"}
        root = (self._corpus_root if hasattr(self, "_corpus_root") else None)
        if root is None:
            return {"ok": False, "弃答": "内置语料不读账。"}
        p = root / "assemble_ledger.jsonl"
        if not p.is_file():
            return {"ok": False,
                    "弃答": "还没有装配台账——先跑一次带 `记账: true` 的装配。"}
        recs = []
        try:
            with p.open(encoding="utf-8") as fh:
                for ln in fh:
                    if not ln.strip():
                        continue
                    try:
                        r0 = _j.loads(ln)
                    except ValueError:
                        continue
                    if r0.get("目标") == name:
                        recs.append(r0)
        except OSError as exc:
            return {"ok": False, "弃答": f"台账读不出来：{str(exc)[:60]}"}
        snaps = [r0 for r0 in recs if r0.get("事件") == "装配"]
        if not snaps:
            return {"ok": False, "弃答": f"「{name}」还没有装配快照。"}
        snap = snaps[-1]
        deps = {}
        hist = []
        for r0 in recs:
            if r0.get("事件") in ("补齐", "确认依赖"):
                hist.append({"轮次": r0.get("轮次"), "事件": r0["事件"],
                             "缺口": r0.get("缺口"), "补": r0.get("补"),
                             "依赖": r0.get("依赖") or [],
                             "补后": r0.get("补后")})
                _g = str(r0.get("缺口") or "")
                _k = _g[:1]
                if _k:
                    deps[_k] = list(r0.get("依赖") or [])

        steps = [{"序": (r0.get("序") or ""), "做什么": r0.get("判据"),
                  "依据": r0.get("覆盖") or [], "状态": r0.get("状态")}
                 for r0 in (snap.get("判据") or [])]
        order, done, loose = [], set(), []
        left = [s for s in steps if s["序"]]
        while left:
            batch = [s for s in left
                     if all(d in done for d in deps.get(s["序"], []))]
            if not batch:
                return {"ok": False,
                        "弃答": ("依赖成环：" + "、".join(s["序"] for s in left)
                                + "——环通常是判据或依赖写错了，不是流程真有环。"),
                        "历史": hist}
            if len(batch) > 1:
                loose.append([s["序"] for s in batch])
            for s in batch:
                order.append(s)
                done.add(s["序"])
                left.remove(s)
        out_steps = []
        for i0, s in enumerate(order, 1):
            out_steps.append({"步": i0, "序": s["序"], "做什么": s["做什么"],
                              "依据": s["依据"], "依赖": deps.get(s["序"], []),
                              "覆盖状态": s["状态"]})
        # 「有记录」与「记录为空」要分开：确认过"这步没有前置"（依赖=[]）也是记录，
        # 用 `in deps` 判而不是用真值判——否则确认过的起点仍会被报成"没证据"。
        nodep = [s["序"] for s in steps if s["序"] not in deps]
        _used = {d for v in deps.values() for d in v}
        # 位置真的不固定的：**既没记依赖、也没有别的步依赖它**。
        # 只按"没有依赖记录"判会把起点也算进去——起点位置是定的（后面有人靠它），
        # 说它"可换序"就是错话。
        _loose = [s["序"] for s in steps
                  if s["序"] not in deps and s["序"] not in _used]
        _deg = (f"依赖记录覆盖 {len(steps) - len(nodep)}/{len(steps)} 步"
                + (f"；{len(_loose)} 步位置不固定（{'、'.join(_loose)}）"
                   if _loose else "；序由依赖唯一确定")
                + (f"；未记依赖的：{'、'.join(nodep)}（当起点用，但**没证据**）"
                   if nodep else ""))
        return {"ok": True, "路由": "过程",
                "过程": {"目标": name, "快照轮次": snap.get("轮次"),
                         "装配状态": snap.get("状态"), "步骤": out_steps},
                "序的完整度": _deg,
                "并列可换序": loose,
                "补齐史": hist,
                "不交的东西": ["可执行代码/脚本", "渠道频次阈值"],
                "依据": [{"原文": "顺序由**补齐史**推出来，不是设计出来的；"
                                  "依赖没记全，序就残缺，残缺要说出来",
                          "可复算": f"{p.name} 里 目标={name} 的记录"}],
                "账": str(p.name)}

    # ── 依赖的「提议 → 授予」（v0.2.10）──────────────────────────────────
    # 分工照 v0.2.5 的 review：**判断是人的，提议可以是 agent 的**。
    # agent 只能往待核队列里写，提议不进台账、不改状态、不动顺序；
    # 进台账（真正参与排顺序）只有人能落。

    def _snap_of(self, target):
        import json as _j
        root = (self._corpus_root if hasattr(self, "_corpus_root") else None)
        if root is None:
            return None, None
        p = root / "assemble_ledger.jsonl"
        if not p.is_file():
            return root, None
        snaps = []
        try:
            with p.open(encoding="utf-8") as fh:
                for ln in fh:
                    if not ln.strip():
                        continue
                    try:
                        r0 = _j.loads(ln)
                    except ValueError:
                        continue
                    if r0.get("目标") == target and r0.get("事件") == "装配":
                        snaps.append(r0)
        except OSError:
            return root, None
        return root, (snaps[-1] if snaps else None)

    def propose_dependency(self, target, gap, deps, *, 依据="", by="agent"):
        """提议一条依赖：**只写待核队列，不进台账**。

        agent 可以提议——但提议不改变任何顺序，也不改装配状态；
        进台账是 `confirm_dependency` 的事，那个只有人能做。
        """
        import datetime as _dt
        import json as _j
        target = str(target or "").strip()
        gap = str(gap or "").strip()
        why = str(依据 or "").strip()
        root, snap = self._snap_of(target)
        if root is None:
            return {"ok": False, "error": "内置语料不写账。"}
        if snap is None:
            return {"ok": False,
                    "error": f"「{target}」还没有装配快照——先装配一次（记账）。"}
        seqs = [str(r0.get("序") or "") for r0 in (snap.get("判据") or [])]
        k = gap[:1]
        if not gap or k not in seqs:
            return {"ok": False,
                    "error": (f"缺口 {gap!r} 不在快照的判据里（{'、'.join(seqs)}）——"
                              "不许提议没有的步。")}
        deps = [str(d).strip() for d in (deps or []) if str(d).strip()]
        if k in deps:
            return {"ok": False, "error": f"{k} 不能依赖自己。"}
        bad = [d for d in deps if d not in seqs]
        if bad:
            return {"ok": False,
                    "error": f"依赖里有快照没有的步：{bad}——不许发明步骤。"}
        if len(why) < 10:
            return {"ok": False,
                    "error": ("提议必须带依据（≥10 字，说清「这一步要用上一步的什么」）"
                              "——只凭「逻辑上应该」不算依据。")}
        rec = {"ts": _dt.datetime.now().isoformat(timespec="seconds"),
               "目标": target, "缺口": gap, "依赖": deps,
               "依据": why, "提议人": str(by or "agent"), "状态": "待核"}
        p = root / "dep_proposals.jsonl"
        # 幂等：同目标 + 同缺口 + 同依赖的**待核**提议不重复写
        # （实测 agent 连问两遍就写了两条一模一样的，待核队列会被自己灌满）。
        try:
            if p.is_file():
                with p.open(encoding="utf-8") as fh:
                    for ln in fh:
                        if not ln.strip():
                            continue
                        try:
                            r0 = _j.loads(ln)
                        except ValueError:
                            continue
                        if (r0.get("目标") == target and r0.get("缺口") == gap
                                and (r0.get("依赖") or []) == deps):
                            return {"ok": True, "已提议过": True, "提议": r0,
                                    "说明": "同样的提议已经在待核队列里，不重复写。",
                                    "边界": "仍只是**提议**——要落地得由人确认。"}
        except OSError:
            pass
        try:
            with p.open("a", encoding="utf-8") as fh:
                fh.write(_j.dumps(rec, ensure_ascii=False) + "\n")
        except OSError as exc:
            return {"ok": False, "error": str(exc)[:60]}
        return {"ok": True, "提议": rec,
                "边界": ("这只是**提议**：不进台账、不改装配状态、不动顺序；"
                         "要落地得由人调 confirm_dependency(by=\"human\")。"),
                "文件": p.name}

    def proposals(self, target=None):
        """列待核提议（只读）——人看这个点头。"""
        import json as _j
        root = (self._corpus_root if hasattr(self, "_corpus_root") else None)
        if root is None:
            return {"ok": False, "error": "内置语料不读账。"}
        p = root / "dep_proposals.jsonl"
        if not p.is_file():
            return {"ok": True, "待核": [], "空因": "还没有任何依赖提议"}
        out = []
        try:
            with p.open(encoding="utf-8") as fh:
                for ln in fh:
                    if not ln.strip():
                        continue
                    try:
                        r0 = _j.loads(ln)
                    except ValueError:
                        continue
                    if target and r0.get("目标") != target:
                        continue
                    out.append(r0)
        except OSError as exc:
            return {"ok": False, "error": str(exc)[:60]}
        return {"ok": True, "待核": out, "条数": len(out)}

    def confirm_dependency(self, target, gap, *, deps=None, by="", 依据=""):
        """把一条提议落进台账——**进台账才参与排顺序，所以只有人能调**。

        与 `review`、`drill(verdict="wrong")` 同一条守则：判断是外部授予。
        agent 可以提议、可以催，但不能自己把"我认为它依赖 ①"变成"它依赖 ①"。
        """
        import json as _j
        if by != "human":
            return {"ok": False,
                    "error": (f"确认依赖只能人做：by={by!r} 不是 human。"
                              "agent 可以提议，但不能自己落账。"),
                    "hint": "人看过提议后：confirm_dependency(target, gap, by=\"human\")"}
        target = str(target or "").strip()
        gap = str(gap or "").strip()
        root, snap = self._snap_of(target)
        if root is None:
            return {"ok": False, "error": "内置语料不写账。"}
        if snap is None:
            return {"ok": False, "error": f"「{target}」还没有装配快照。"}
        seqs = [str(r0.get("序") or "") for r0 in (snap.get("判据") or [])]
        k = gap[:1]
        if k not in seqs:
            return {"ok": False, "error": f"缺口 {gap!r} 不在快照的判据里。"}
        # 取最新一条匹配的提议（没提议也可以人直接给 deps）
        prop = None
        pp = root / "dep_proposals.jsonl"
        if pp.is_file():
            try:
                with pp.open(encoding="utf-8") as fh:
                    for ln in fh:
                        if not ln.strip():
                            continue
                        try:
                            r0 = _j.loads(ln)
                        except ValueError:
                            continue
                        if r0.get("目标") == target and r0.get("缺口") == gap:
                            prop = r0
            except OSError:
                pass
        if deps is None and prop is None:
            return {"ok": False,
                    "error": f"「{gap}」还没有待核的提议——先让 agent 提议，或直接传 deps。"}
        # ⚠️ 「依赖为空」不等于「没有提议」：确认"这一步没有前置"本身也是一个判断，
        #    要能落账（实测 agent 对头一步就是这么提的，旧写法会把它拒掉）。
        use = [str(d).strip() for d in (deps if deps is not None
                                       else (prop or {}).get("依赖") or [])]
        bad = [d for d in use if d not in seqs or d == k]
        if bad:
            return {"ok": False,
                    "error": f"依赖里有不存在/自己的步：{bad}"}
        # 幂等：台账里已有同缺口同依赖的确认，就不再写一条
        lp = root / "assemble_ledger.jsonl"
        already = False
        try:
            with lp.open(encoding="utf-8") as fh:
                for ln in fh:
                    if not ln.strip():
                        continue
                    try:
                        r0 = _j.loads(ln)
                    except ValueError:
                        continue
                    if (r0.get("目标") == target and r0.get("事件") == "确认依赖"
                            and str(r0.get("缺口") or "")[:1] == k
                            and (r0.get("依赖") or []) == use):
                        already = True
                        break
        except OSError:
            pass
        _why = str(依据 or (prop or {}).get("依据") or "").strip()
        if already:
            return {"ok": True, "已确认过": True, "目标": target, "缺口": gap,
                    "依赖": use,
                    "说明": "台账里已经有这一条（同缺口同依赖），不重复记账。"}
        rec = self._assemble_log(target, "确认依赖", gap,
                                 {"依赖": use, "依赖说明": _why,
                                  "采纳自": (prop or {}).get("提议人"),
                                  "确认人": "human"})
        return {"ok": True, "目标": target, "缺口": gap, "依赖": use,
                "记账": rec,
                "依据": [{"原文": "顺序由补齐史推出来；依赖是「这一步要用上一步的什么」",
                          "可复算": "assemble_ledger.jsonl 里该条 确认依赖"}]}

    def _gap(self, spec) -> dict:
        """差距路由（v0.2.12，用户 2026-11-09：创造 = 分清契约与需求的差距，在已定之外）。

        **需求必须保持原话**——一旦被人翻译成判据，差距就被抹平了（那时只剩"缺零件"，
        也就是执行，不是创造）。

        本层只做一件事：把需求按句拆开，逐句去库里问「有没有条文管这件事」，三态——
        - **契约内**：有规矩类节点（有 `文档类` 且不是"外部摘录"）管得住；
        - **只有词义**：只查到解释这个词的节点（门类=词汇/文字），**没有规矩**；
        - **契约外**：一条都查不到。

        ⚠️ **只量差距，不提建议**：建议是"已定之外"的东西，本层不发明
        （对抗测试里它压力下会编，这条边界不能松）。也不把诉求翻译成判据——那是人的活。
        """
        import re as _re

        if isinstance(spec, str):
            spec = {"需求": spec}
        q = str((spec or {}).get("需求") or "").strip()
        if not q:
            return {"ok": False,
                    "弃答": ("差距要「需求」——把原话给过来，**不要先翻译成判据**"
                             "（翻译了差距就没了，只剩缺零件）。")}

        def _grams(s0):
            t0 = _re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", str(s0 or ""))
            return {t0[i:i + 2] for i in range(len(t0) - 1)}

        # 分句：按标点切；短于 4 字的碎片并到前一句
        parts = [x.strip() for x in _re.split(r"[，。；！？,;!?\n]+", q) if x.strip()]
        merged = []
        for p0 in parts:
            if merged and len(p0) < 4:
                merged[-1] += p0
            else:
                merged.append(p0)
        parts = merged or [q]

        # 补齐记录是「需求 ↔ 条文」的桥（v0.2.14）：
        # 补的规矩措辞常和抱怨对不上（「毕业走了」 vs 《离校清欠》一个字都不沾），
        # 所以**补的时候要记一句「这条补的是哪段需求」**——靠记录认，不靠字面猜。
        _map = {}
        root = (self._corpus_root if hasattr(self, "_corpus_root") else None)
        if root is not None and (root / "demand_map.jsonl").is_file():
            import json as _j
            try:
                with (root / "demand_map.jsonl").open(encoding="utf-8") as fh:
                    for ln in fh:
                        if not ln.strip():
                            continue
                        try:
                            r0 = _j.loads(ln)
                        except ValueError:
                            continue
                        _map[str(r0.get("需求片段") or "").strip()] = r0
            except OSError:
                _map = {}

        _SKIP_NAMES = {"目标", "装配规范", "判据类型"}
        # 虚词片段不算命中（实测："还有人毕业走了" 靠"还有"命中了《并发控制规程》，
        # "打电话催也没用" 靠"电话"命中了《紧急出库规程》——都是噪声，不是条文管它）。
        _STOP_SEG = {"还有", "有人", "也不", "就还", "这个", "那个", "一个", "不了",
                     "没有", "就是", "都是", "可以", "我们", "他们", "什么", "怎么",
                     "时候", "现在", "已经", "还是", "或者", "但是", "所以", "因为",
                     "不用", "没用", "一趟", "一下", "的人", "的事"}
        rows = []
        for sent in parts:
            _hit_map = next((v for k, v in _map.items() if k and k in sent), None)
            if _hit_map:
                rows.append({"句": sent, "判定": "契约内（靠补齐记录）",
                             "管它的条文": [{"id": _hit_map.get("条文"),
                                            "name": _hit_map.get("条"),
                                            "分": None,
                                            "文档类": "补齐记录",
                                            "命中": ["记录"]}],
                             "邻近的条文（弱）": [], "只解释词的": []})
                continue
            gq = _grams(sent) - _STOP_SEG
            rules, words = [], []
            for nid, n in self.nodes.items():
                f = getattr(n, "fields", {}) or {}
                if str(f.get("裁决") or "") == "判错":
                    continue
                nm = str(f.get("name") or "")
                if not nm or nm in _SKIP_NAMES or nm.endswith("目标"):
                    continue
                body = str(getattr(n, "body", "") or "")
                gp = _grams(nm) | _grams(body)
                hit = gq & gp
                if not hit:
                    continue
                doc = str(f.get("文档类") or "")
                ent = {"id": nid, "name": nm,
                       "分": 3 * len(gq & _grams(nm)) + len(hit),
                       "文档类": doc or None,
                       "门类": f.get("门类") or None,
                       "命中": sorted(hit)[:4]}
                # 分桶：
                # ① 规矩：有文档类且不是未核的外部摘录；或名字就是「规程/规范/守则…」
                #    （早年节点没标文档类，但它是规矩——实测《紧急出库规程》漏标，
                #     被当成"只解释词"，把「契约外」判成了「只有词义」）
                # ② 词义：门类=词汇/文字（只解释词，没定规矩）
                # ③ 其他概念：既不是规矩也不是词义（目标、模型之类）
                _is_rule_nm = nm.endswith(("规程", "规范", "守则", "办法", "条例", "细则"))
                if (doc and doc != "外部摘录") or (not doc and _is_rule_nm):
                    rules.append(ent)
                elif str(f.get("门类") or "") in ("词汇", "文字"):
                    words.append(ent)
                else:
                    words.append(dict(ent, 说明="概念类"))
            rules.sort(key=lambda x: (-x["分"], x["name"]))
            words.sort(key=lambda x: (-x["分"], x["name"]))
            strong = [x for x in rules if x["分"] >= 4]
            # 分 1 基本是噪声（一个字/一个虚词撞上），不算「有弱相关条文」——
            # 实测「还有人毕业走了」靠《交接班规程》的"走了"拿 1 分，被当成"有弱相关"，
            # 于是「连词都没教过」被误报成「缺一条规程」。
            near = [x for x in rules if 2 <= x["分"] < 4]
            if strong:
                verdict = "契约内"
            elif near:
                verdict = "契约外（有弱相关条文）"
            elif [x for x in words if x["分"] >= 2]:
                # 「解释过这个词」也要有分量：分 1 多是擦边（"走了"命中《告别》），
                # 那不算解释过"毕业"，只能说连词都没有。
                verdict = "只有词义"
            else:
                verdict = "契约外"
            rows.append({"句": sent, "判定": verdict,
                         "管它的条文": strong[:2],
                         "邻近的条文（弱）": near[:2],
                         "只解释词的": words[:2]})

        out = [r for r in rows if r["判定"] != "契约内"]
        inner = len(rows) - len(out)
        return {"ok": True, "路由": "差距",
                "需求": q,
                "诉求": rows,
                "契约外": [{"句": r["句"], "判定": r["判定"]} for r in out],
                "状态": (f"{len(rows)} 条诉求：契约内 {inner}、契约外 {len(out)}"
                         if out else f"{len(rows)} 条诉求全部落在契约内"),
                "不做的": ["提建议（建议在已定之外，本层不发明）",
                           "把诉求翻译成判据（翻译了差距就没了）"],
                "依据": [{"原文": "创造 = 分清契约与需求的差距；先量清哪里空着，再谈补什么",
                          "可复算": "「诉求」每条的 管它的条文 / 只解释词的 两个清单"}],
                "局限": ("按句、按字面查；一问多义的句子会被拆粗。"
                         "「只有词义」= 库里只解释过这个词，没定过任何规矩。"
                         "**同一件事常有好几个词**（「催」在库里叫「提醒」「跟进」）——"
                         "判「契约外」时，换一个说法再跑一遍，别当成它没管。"),
                }

    def _advice(self, spec) -> dict:
        """建议路由 v0.2.17（用户拍板 B：**需求整条量，不切句**）。

        旧建议（对抱怨拆句挑刺）退役——抱怨不是来找建议的。
        现在建议 = **对一条需求的四份清单**，全是字面判定、可复核：

        - **覆盖**：库里有名字、且名字出现在整条需求里的（标 词义/规矩/外部）；
        - **新词候选**：需求里库里完全没有的片段——要教的词；
        - **要动的条文**：覆盖里是规矩的（需求碰到谁，谁可能要扩适用范围）；
        - **缺的口径**：约束词出现但库里没口径的（批量上限/顺序/粒度/时效…）——要人定。

        ⚠️ 判准与「差距」不同：差距问"有没有规矩管"，建议问"这件事要新增什么、
        动谁"。**需求不再判「契约内」**——"加导出功能"沾了系统名就判契约内，
        是把"要新增"当成"已经有了"（实测硬伤）。
        """
        import re as _re

        if isinstance(spec, str):
            spec = {"需求": spec}
        q = str((spec or {}).get("需求") or "").strip()
        intent = str((spec or {}).get("来意") or "").strip()
        if not q:
            return {"ok": False,
                    "弃答": "建议要「需求」——整条原话给过来，不切句。"}
        if intent != "需求":
            return {"ok": False,
                    "弃答": ("建议只对**需求**提（带 来意=\"需求\"）。"
                             "抱怨不是来找建议的——只量差距：{\"差距\": {\"需求\": 原话}}。")}

        def _grams(s0):
            t0 = _re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", str(s0 or ""))
            return {t0[i:i + 2] for i in range(len(t0) - 1)}

        qflat = _re.sub(r"[\s]", "", q)
        _SKIP = {"建议"}

        def _kind(nm, f):
            doc = str(f.get("文档类") or "")
            if (doc and doc != "外部摘录") or nm.endswith(
                    ("规程", "规范", "守则", "办法", "条例", "细则")):
                return "规矩"
            return ("词义" if str(f.get("门类")) in ("词汇", "文字") else "概念")

        # ① 覆盖（双向字面）：
        #    a) 库内名字出现在需求里（「借书」在「给借书加预约」里）；
        #    b) 需求的 2 字窗被库内名字沾着（「借阅」沾《图书借阅规程》）——
        #       名字是全称、需求说的是简称时，只查 a) 会漏（实测「图书借阅规程」
        #       对不上「图书借阅系统」，导出功能被误判已有）。
        covered = {}
        for nid, n in self.nodes.items():
            f = getattr(n, "fields", {}) or {}
            if str(f.get("裁决") or "") == "判错":
                continue
            nm = str(f.get("name") or "")
            if len(nm) < 2 or nm in _SKIP or nm.endswith("目标"):
                continue
            if nm in qflat:
                covered[nm] = {"名字": nm, "id": nid, "类": _kind(nm, f),
                               "沾": [nm], "沾度": "强",
                               "文档类": str(f.get("文档类")) or None}
        _CONSTRAINTS = {
            "批量": "一次多少个（分不分批、上限几条）",
            "上限": "最多允许多少",
            "顺序": "按什么排（先来后到/优先级）",
            "排队": "排队的先后和公平规则",
            "保留": "留多久（几天不动作废）",
            "恢复": "弄错了怎么撤销、谁批",
            "粒度": "记到多细（每笔还是每次会话）",
            "谁能": "谁有权用、谁不许",
            "时效": "有效多久",
            "几次": "试几次算超、超了怎么办",
            "导出": "这是要新增的功能——导哪些字段、什么格式、谁能导",
            "日志": "这是要新增的东西——记哪些动作、存多久、谁能看",
        }
        # ② 新词候选：2 字滑窗扫整条需求，相邻未命中窗合并成片段
        #    （命中 = 任一库内名字含这个窗；片段不得被覆盖名包含/包含）
        _STOP_CH = set("的地得了我想给要把在于是都有和与或但不能让他们看到个这那吗呢啊"
                       "们它他她你去来上中里就是会能可以先后再一次每些很挺还也又"
                       "就都只更太别准让完说问试拿着沿照按谁被系统加建改做")
        # 泛窗不算命中（实测「一次」把《NIST…一次性验证码》《第一次万灯夜》
        # 都拉进覆盖——它们跟需求毫无关系）。
        _STOP_WIN = {"一次", "别的", "其它", "其他", "这里", "那里", "什么"}
        new_words, seen_seg = [], set()
        for seg in _re.split(r"[，。；！？,;!?、\n]+", q):
            frags, lo, i, n = [], 0, 0, len(seg)
            while i <= n - 2:
                w2 = seg[i:i + 2]
                if w2 not in _STOP_WIN and any(w2 in nm for nm in self._name_index()):
                    # 命中窗：先收掉 lo..i 的未命中段，再记覆盖
                    if i > lo:
                        frags.append(seg[lo:i])
                    for nm in [nm for nm in self._name_index() if w2 in nm][:3]:
                        if nm in covered:
                            continue
                        n0 = next(nn for nn in self.nodes.values()
                                  if nn.fields.get("name") == nm)
                        covered[nm] = {"名字": nm,
                                       "id": str(getattr(n0, "id", "")) or None,
                                       "类": _kind(nm, getattr(n0, "fields", {}) or {}),
                                       "沾": sorted({w2} | set(
                                           covered.get(nm, {}).get("沾") or [])),
                                       "沾度": "弱",
                                       "文档类": str(n0.fields.get("文档类")) or None}
                    lo = i + 2
                    i += 2
                else:
                    i += 1
            if n > lo and n >= 2:
                frags.append(seg[lo:])
            for frag0 in frags:
                # 按停用字符切开取实词块（「加一个预约功能」→「预约功」），
                # 比整段 strip 干净——动词/量词粘在块首会把块拒掉（实测「加一」被拒）
                for frag in _re.split("[" + _re.escape("".join(_STOP_CH)) + "]",
                                      frag0):
                    if len(frag) < 2 or len(frag) > 6 or frag in seen_seg:
                        continue
                    if any(frag in nm or nm in frag for nm in covered):
                        continue     # 被覆盖名沾着，不算新词
                    seen_seg.add(frag)
                    new_words.append(frag)
        # 只滤**等于**约束词的块（「操作日志」含「日志」但信息更多，保留——
        # 口径条目说「要新增」，新词候选说「词都没有」，两件事）。
        new_words = [w for w in new_words if w not in _CONSTRAINTS]
        covered = list(covered.values())
        covered.sort(key=lambda x: (x["沾度"] != "强", -len(x["名字"]), x["名字"]))
        _cov_names = [c["名字"] for c in covered]
        # ③ 要动的条文：覆盖里的规矩（需求碰到谁，谁可能要扩适用范围）
        rules = sorted((c for c in covered if c["类"] == "规矩"),
                       key=lambda x: x["沾度"] != "强")
        # ④ 缺的口径：约束词出现 → 要人定的口径
        gaps = []
        for word, need in _CONSTRAINTS.items():
            if word in qflat:
                have = any(word in nm for nm in self._name_index())
                gaps.append({"约束词": word, "要定": need,
                             "库里": ("有沾边的，口径要人对" if have else "没有——要人定")})

        return {"ok": True, "路由": "建议", "来意": "需求",
                "需求": q,
                "覆盖": covered,
                "新词候选": new_words,
                "要动的条文": rules,
                "缺的口径": gaps,
                "状态": (f"覆盖 {len(covered)}、新词 {len(new_words)}、"
                         f"要动 {len(rules)} 条、缺口径 {len(gaps)}"),
                "不写的": ["功能怎么实现（那是设计与人的活）",
                           "口径的值（上限几条、留几天——由人定）"],
                "依据": [{"原文": "建议 = 对需求整条量：覆盖 / 新词候选 / 要动的条文 / 缺的口径，"
                                  "四份清单全可复核（v0.2.17，用户拍板 B）",
                          "可复算": "覆盖=名字出现在需求里；新词=滑窗合并后库内无此片段；"
                                    "口径=约束词表"}],
                "局限": ("覆盖按**字面**：需求里的词要和库里名字对得上才算——"
                         "换说法就漏（「催」对不上「提醒」）。新词候选有噪声"
                         "（载体词、修饰语会漏进来），是候选不是清单，人看着挑。"),
                }

    def _name_index(self):
        """所有节点名的缓存（建议路由滑窗用）。"""
        cache = getattr(self, "_nmidx", None)
        if cache is None:
            cache = [str(n.fields.get("name") or "") for n in self.nodes.values()
                     if n.fields.get("name")]
            self._nmidx = cache
        return cache

    def map_demand(self, 需求片段, node_id, *, by="", 依据="") -> dict:
        """补完缺口后记一句「这条补的是哪段需求」（v0.2.14，**只有人能调**）。

        为什么要这道记录：补的规矩措辞常和抱怨对不上——实测「还有人毕业走了」
        与《离校清欠》**一个字都不沾**（一个说"毕业走了"，一个写"离校前清欠"），
        字面永远命中不了。补的时候记一笔，下一轮靠**记录**认，不靠字面猜。

        与 review / confirm_dependency 同一条守则：判断归人，agent 不能自己落这条账。
        """
        import datetime as _dt
        import json as _j
        if by != "human":
            return {"ok": False,
                    "error": (f"记「补的是哪段需求」只能人做：by={by!r} 不是 human。"
                              "agent 可以提议补什么，但不能自己认定「这条补的是那段」。"),
                    "hint": "人确认后：map_demand(需求片段, node_id, by=\"human\")"}
        seg = str(需求片段 or "").strip()
        if len(seg) < 4:
            return {"ok": False,
                    "error": "需求片段太短（<4 字）——引一段原话，别只写个词。"}
        n = self.nodes.get(str(node_id or "").strip())
        if n is None:
            return {"ok": False, "error": f"没有这个节点 {node_id!r}"}
        root = (self._corpus_root if hasattr(self, "_corpus_root") else None)
        if root is None:
            return {"ok": False, "error": "内置语料不写账。"}
        rec = {"ts": _dt.datetime.now().isoformat(timespec="seconds"),
               "需求片段": seg, "条文": str(node_id), "条": n.fields.get("name"),
               "依据": str(依据 or ""), "记人": "human"}
        p = root / "demand_map.jsonl"
        try:
            with p.open("a", encoding="utf-8") as fh:
                fh.write(_j.dumps(rec, ensure_ascii=False) + "\n")
        except OSError as exc:
            return {"ok": False, "error": str(exc)[:60]}
        return {"ok": True, "记录": rec, "文件": p.name,
                "作用": "下一轮「差距」遇到包含这段文字的需求，直接按这条认"}

    def _decision_log(self, route, bucket, candidates, chosen, note="") -> None:
        """决策留痕（v0.2.15，用户 2026-11-09：先记账，再谈归纳）。

        **只记不改**——不参与任何排序、不改任何结论，写完就完。
        记的是将来「归纳」要用的四样：**情形桶、候选（含当时的分）、选了谁、依据**。
        没有反例就没有规律：没选中的候选也在记录里，它们就是反例的来源。
        """
        import datetime as _dt
        import json as _j
        root = (self._corpus_root if hasattr(self, "_corpus_root") else None)
        if root is None:
            return
        rec = {"ts": _dt.datetime.now().isoformat(timespec="seconds"),
               "路由": route, "桶": bucket,
               "候选": candidates, "选了": chosen, "依据": str(note or "")[:120]}
        try:
            with (root / "decision_ledger.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(_j.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _outward(self, spec) -> dict:
        """对外检索 + 自学（v0.2.3）——《外部检索规程》conc/417 与《自学规程》conc/418 的可执行形态。

        出去查一次花一份预算，学进来一条再花一份；带回来的必须有**出处 + 原文摘录 + 时间**，
        缺一不学；与已有教导同名的**不覆盖**（学新东西不许把旧的顶掉）；
        学进来的标 `来源=外部` / `采信=未核`，复核归人。
        """
        import datetime as _dt
        import json as _j
        import re as _re
        import urllib.request as _u
        from pathlib import Path as _P

        if isinstance(spec, str):
            spec = {"问": spec}
        if not isinstance(spec, dict):
            return {"ok": False, "弃答": "外查要 {问, 预算, 领域}——带着问句出去。"}
        q = str(spec.get("问") or "").strip()
        if not q:
            return {"ok": False,
                    "弃答": "外查要「问」——《外部检索规程》：带着问句出去，"
                            "不许\"随便看看有什么\"。"}

        nd = self._nodes_dir()
        base = (_P(self._corpus_root) if hasattr(self, "_corpus_root")
                else (nd.parent if nd else None))
        cfg_all = {}
        if base is not None and (base / "trigger.json").is_file():
            try:
                cfg_all = _j.loads((base / "trigger.json").read_text(encoding="utf-8") or "{}")
            except ValueError:
                cfg_all = {}
        cfg = cfg_all.get("outside") or {}
        url = str(cfg.get("url") or "").strip()
        if not url:
            return {"ok": False,
                    "弃答": "外部检索地址没配（配置里 outside.url）——"
                            "**地址是人给的，我不发明目标**，也不去猜哪儿能查。"}

        # 预算：读《自学规程》常量行（默认 5，上限 20）——教导即配置
        d_budget, d_cap = 5, 20
        try:
            r0 = self.query({"require": ["name=自学规程"]})
            ids0 = list(r0.get("answer_ids") or [])
            if ids0:
                b0 = str(getattr(self.nodes.get(ids0[0]), "body", "") or "")
                m1 = _re.search(r"单次外查预算\D{0,4}(\d+)", b0)
                d_budget = int(m1.group(1)) if m1 else d_budget
        except Exception:      # noqa: BLE001
            pass
        _rb = spec.get("预算")
        if _rb is None or str(_rb).strip() == "":
            _rb = d_budget
        try:
            budget = int(_rb)
        except (TypeError, ValueError):
            budget = d_budget
        budget = max(1, min(budget, d_cap))

        today = _dt.date.today().isoformat()
        need = ("做什么", "为什么", "请谁处理")
        payload = {"做什么": q, "为什么": str(spec.get("为什么") or "库里没有，出去查"),
                   "请谁处理": str(spec.get("请谁处理") or "外部资料源")}
        envelope = {"协议": "ldv-port/1",
                    "请求号": "req-" + _dt.datetime.now().strftime("%Y%m%d%H%M%S") + "-ext",
                    "时间戳": _dt.datetime.now().isoformat(timespec="seconds"),
                    "来源": "LDV", "动作": "外部检索", **payload}

        used, learned, rejected = 0, [], []
        _batch_names: set[str] = set()      # 本批已学的名（同批查重用）
        # ① 出去查一次（1 份）
        body = _j.dumps(envelope, ensure_ascii=False).encode("utf-8")
        try:
            req = _u.Request(url, data=body, headers={"Content-Type": "application/json"})
            with _u.build_opener(_u.ProxyHandler({})).open(req, timeout=8) as r:
                raw = r.read().decode("utf-8", "replace")
                code = int(getattr(r, "status", 200))
        except Exception as exc:      # noqa: BLE001 —— 按规程不重试
            self._outward_log(base, q, budget, 1, "外部没接上", None)
            return {"ok": False, "交人": True, "花费": 1, "预算": budget,
                    "弃答": (f"外部没接上（{type(exc).__name__}: {str(exc)[:80]}）——"
                             f"按规程**不重试、不反复敲门**，如实回报。"),
                    "依据": [{"原文": "《外部检索规程》：失败不重试，如实回报（conc/417）",
                              "可复算": f"外查端点 {url}"}]}
        used = 1
        try:
            got = _j.loads(raw)
        except ValueError:
            got = {}
        items = got.get("结果") or []
        if not isinstance(items, list) or not items:
            self._outward_log(base, q, budget, used, "外部返回 0 条", None)
            return {"ok": False, "交人": True, "花费": used, "预算": budget,
                    "弃答": (f"外部返回了、但一条可用候选都没有（status {code}）——"
                             f"**外部没有不等于没有**，可以换个问法再来，或教它这块。"),
                    "依据": [{"原文": "《外部检索规程》：带着问句出去，拿回原文摘录+出处（conc/417）",
                              "可复算": "外面回执里的 结果 数组"}]}

        # ①b 候选按**与问句的相关性**排队（2026-11-08 修）。
        #    实测：问「短信验证码怎么发怎么核」，源按自己的顺序返回，最相关的那条
        #    排在第五位 ⇒ 预算 3 只够学前两条（OAuth / NIST），**真正回答问句的
        #    那条永远轮不到**，还白白花掉两份。《自学规程》第③条「内容是不是真
        #    回答了问句」原来只是纸上的话——这里把它落成排序：重合的二字片段多者在前，
        #    同分保持源的原始顺序（稳定）。
        def _grams(s0):
            t0 = _re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", str(s0 or ""))
            return {t0[i:i + 2] for i in range(len(t0) - 1)}

        _qg = _grams(q)
        _order = sorted(range(len(items)),
                        key=lambda i0: (-len(_qg & _grams(
                            str((items[i0] or {}).get("标题") or "")
                            + str((items[i0] or {}).get("摘录") or ""))), i0))
        items = [items[i0] for i0 in _order]

        # ② 准入 + 学（每条 1 份）
        def _src_ok(s0):
            s0 = str(s0 or "").strip()
            if not s0:
                return False
            if _re.match(r"https?://", s0):
                return True
            return bool(_re.search(r"(版|页|出版社|标准|编号|GB)", s0))

        for it in items:
            if not isinstance(it, dict):
                continue
            title = str(it.get("标题") or "").strip()
            src0 = str(it.get("出处") or "").strip()
            excerpt = str(it.get("摘录") or it.get("正文") or "").strip()
            if not _src_ok(src0):
                rejected.append({"标题": title, "原因": "没有可回访的出处——听说的不进库"})
                continue
            if len(excerpt) < 20:
                rejected.append({"标题": title, "原因": "摘录太短（<20 字），不构成一段可引的话"})
                continue
            # 落盘的 name：去书名号、折叠空白（**保留单空格**）——
            # 原来一律去空格，实测把「OWASP 认证备忘清单 Authentication Cheat Sheet」
            # 粘成「OWASP认证备忘清单AuthenticationCheatSheet」，门牌没法读也没法照着敲。
            def _plain(x):
                s2 = str(x or "").replace("《", "").replace("》", "").strip()
                return _re.sub(r"\s+", " ", s2)

            # 比对的归一化：去全部空白（落盘带空格、旧的不带，两种得认成同一个名）
            def _nm(x):
                return _re.sub(r"\s+", "", _plain(x))

            # 指定学名只在**单条**候选时成立（2026-11-08 修）。
            # 实测：一次返回 6 条、指定「短信验证码发送与校验规则」，结果 RFC 6749 的
            # OAuth 条文和 NIST 的 OTP 条款**都顶着这个名字**落了盘——名字对不上内容，
            # 等于给门牌贴错门。多候选时一律用各自标题：一物一名。
            _want = str(spec.get("学名") or "").strip()
            _single = sum(1 for x0 in items if isinstance(x0, dict)) <= 1
            name = (_want if (_want and _single) else "") or _plain(title) or _plain(q)

            exist = [nid for nid, nd2 in self.nodes.items()
                     if _nm(nd2.fields.get("name")) == _nm(name)]
            # **同一批里也要查重**（2026-11-08 修）：新学的节点要等这批跑完
            # `_reload_after_learn()` 才进 self.nodes，于是同批第二个同名的
            # 「看不见」第一个 ⇒ 一次学出两份同名的（实测 429/430）。
            if exist or _nm(name) in _batch_names:
                rejected.append({"标题": title,
                                 "原因": (f"已有同名教导（{exist[0]}）" if exist
                                          else "这一批里已学过同名的一条")
                                          + "——**不覆盖**，要新增请人裁"})
                continue
            if used >= budget:
                rejected.append({"标题": title, "原因": "预算不够（学一条要一份）——留给人"})
                continue
            # 写节点：来源/出处/抓取/采信 四字段必写
            # 空语料也得能学：没有任何 conc 节点时 max() 会抛空序列
            # （2026-11-08 判据跑出来的——新语料上学第一条直接崩）。
            nums = [int(p2.stem.split("__")[1]) for p2 in (nd).glob("conc__*.md")
                    if p2.stem.split("__")[1].isdigit()]
            fid = f"conc/{((max(nums) + 1) if nums else 1):03d}"
            body_txt = (f"{excerpt}\n\n"
                        f"出处：{src0}（抓取于 {today}；**采信=未核**——待人复核，"
                        f"未核不许当规程用）\n")
            text = (f"---\nid: {fid}\ntype: 概念\nname: {name}\n门类: 事理\n"
                    f"领域: {str(spec.get('领域') or '外部资料')}\n文档类: 外部摘录\n"
                    f"来源: 外部\n出处: {src0}\n抓取: {today}\n采信: 未核\n---\n\n"
                    + body_txt)
            (nd / f"conc__{fid.split('/')[1]}.md").write_text(text, encoding="utf-8", newline="\n")
            used += 1
            _batch_names.add(_nm(name))
            learned.append({"id": fid, "name": name, "出处": src0,
                            "采信": "未核", "字数": len(excerpt)})

        # 学完一次统一重载 + 开词 + 记教学账——**学到的必须当场可用**
        if learned:
            self._reload_after_learn()
            for x in learned:
                # 新节点带进来的**每个维度键都要开词**：只开 name 不够——
                # 实测只开了 name，按 领域=财务行政 查还是 0 条（键不在词汇表）。
                try:
                    node0 = self.nodes.get(str(x["id"]))
                    keys0 = ["name=" + str(x["name"])]
                    if node0 is not None:
                        # 来源/采信 必须开词（2026-11-08 加）：不开的话
                        # 「拉一份待复核清单」这件正事恰恰查不出来——实测
                        # `采信=未核` 回 0 条，而「复核归人」全靠这个键点人。
                        for dim in ("领域", "文档类", "门类", "量度类",
                                    "来源", "采信", "出处"):
                            v0 = node0.fields.get(dim)
                            if v0:
                                keys0.append(f"{dim}={v0}")
                    for k0 in keys0:
                        self.declare(k0, why="自学入账后开词（键不声明就检索不到）",
                                     by="owner")
                    self.taught([x["id"]])
                except Exception:      # noqa: BLE001
                    pass
            self._rebuild_structure(reload_usage=False)

        self._outward_log(base, q, budget, used,
                          "学到 %d 条" % len(learned),
                          (learned[0]["id"] if learned else None))
        return {"ok": bool(learned), "花费": used, "预算": budget,
                "交人": True,          # 学到也要人复核（采信=未核）
                "学到": learned, "没学": rejected,
                "要人做": ([f"复核 {x['id']}《{x['name']}》：核对出处后把 采信 改成已核"
                            for x in learned] or ["给它一个能查的地方，或直接教"]),
                "依据": [{"原文": "《自学规程》：有出处才学、不覆盖旧教导、"
                                  "学进来标 来源=外部/采信=未核、复核归人（conc/418）",
                          "可复算": "新节点的 front matter：来源/出处/抓取/采信 四字段"}]}

    def _outward_log(self, base, q, budget, used, verdict, node):
        """外查留账：查了什么、花了多少、学到哪条（学习也要留痕）。"""
        import datetime as _dt
        import json as _j
        if base is None:
            return
        try:
            with (base / "outward_ledger.jsonl").open("a", encoding="utf-8") as f:
                f.write(_j.dumps({"ts": _dt.datetime.now().isoformat(timespec="seconds"),
                                  "问": q, "预算": budget, "花费": used,
                                  "结论": verdict, "学到节点": node},
                                 ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _lookup(self, spec) -> dict:
        """预算受限的检索（v0.2.2）——《查询规程》conc/416 的可执行形态。

        阶梯一步一份预算：**按名字 → 按线索 → 收窄 → 交人**。
        精确 = 恰好一条 + 取到正文 + 不是判错支；**多命中不算、只有条目名不算**。
        预算花完就停，停下的唯一出口是交人——不拿"最像的一条"顶替。
        """
        import datetime as _dt
        import json as _j
        from pathlib import Path as _P

        if isinstance(spec, str):
            spec = {"问": spec}
        if not isinstance(spec, dict):
            return {"ok": False, "弃答": "查询要 {问, 线索, 预算}——我不猜你要查什么。"}
        # 预算对账：{"查询": {"账": true}} —— 预算不是感觉，是能对账的数
        if spec.get("账") or spec.get("ledger"):
            import json as _jl
            from pathlib import Path as _Pl
            nd0 = self._nodes_dir()
            base0 = (_Pl(self._corpus_root) if hasattr(self, "_corpus_root")
                     else (nd0.parent if nd0 else None))
            rows = []
            if base0 is not None:
                lg0 = base0 / "lookup_ledger.jsonl"
                if lg0.is_file():
                    for line in lg0.read_text(encoding="utf-8").splitlines():
                        try:
                            rows.append(_jl.loads(line))
                        except ValueError:
                            continue
            by = {}
            for r0 in rows:
                by[str(r0.get("结论"))] = by.get(str(r0.get("结论")), 0) + 1
            return {"ok": True, "总查询": len(rows),
                    "总花费": sum(int(r0.get("花费") or 0) for r0 in rows),
                    "精确命中": sum(1 for r0 in rows if r0.get("精确")),
                    "按结论": by,
                    "最近": rows[-5:]}

        q = str(spec.get("问") or "").strip()
        if not q:
            return {"ok": False, "弃答": "查询要「问」——没有问句我不猜。"}
        clues = spec.get("线索") or spec.get("keys") or []
        if isinstance(clues, str):
            clues = [clues]
        clues = [str(c).strip() for c in clues if str(c).strip()]

        # 预算与上限：读《查询规程》的常量行（读不到就 5/20）——**教导即配置**
        d_budget, d_cap = 5, 20
        try:
            r0 = self.query({"require": ["name=查询规程"]})
            ids0 = list(r0.get("answer_ids") or [])
            if ids0:
                body0 = str(getattr(self.nodes.get(ids0[0]), "body", "") or "")
                import re as _r0
                m1 = _r0.search(r"默认预算\D{0,4}(\d+)", body0)
                m2 = _r0.search(r"单次上限\D{0,4}(\d+)", body0)
                d_budget = int(m1.group(1)) if m1 else d_budget
                d_cap = int(m2.group(1)) if m2 else d_cap
        except Exception:      # noqa: BLE001 —— 常量读不到不影响查询本身
            pass
        # 「没给」用默认，「给了 0」要当 0 处理（`0 or 5` 会把 0 吞成默认值——
        # 那等于让"预算给 0"偷偷变成 5 份，预算阀就漏了）
        _raw_b = spec.get("预算")
        if _raw_b is None or str(_raw_b).strip() == "":
            _raw_b = d_budget
        try:
            budget = int(_raw_b)
        except (TypeError, ValueError):
            budget = d_budget
        budget = max(1, min(budget, d_cap))

        names = sorted({str(n.fields.get("name")) for n in self.nodes.values()
                        if n.fields.get("name")}, key=len, reverse=True)
        tried, used = [], 0

        _bad_names = {str(n.fields.get("name")) for n in self.nodes.values()
                      if str(n.fields.get("裁决") or "") == "判错"}

        def _near_names(limit: int = 5) -> list[str]:
            """库里名字与问句**字面**挨得上的条目（只报名字，不替谁挑）。

            2026-11-08 实测出来的缺口：调用方线索猜错、预算花完，就把
            「没查到」说成「LDV 没教过」——**两种情况的差别在这一层看不见**。
            这里把「词不对」显性化：回几个相近的名字，让它再查一轮。
            """
            g1 = {q[i:i + 2] for i in range(max(0, len(q) - 1))}
            if not g1:
                return []
            sc = []
            for nm in names:
                if len(nm) < 3 or nm in _bad_names:
                    continue
                g2 = {nm[i:i + 2] for i in range(len(nm) - 1)}
                k = len(g1 & g2)
                if k:
                    sc.append((k, nm))
            sc.sort(key=lambda x: (-x[0], x[1]))
            return [nm for _, nm in sc[:limit]]

        def _try(require, step, why):
            """试一次；预算不够返回 None（不花、也不装成试过）。"""
            nonlocal used
            if used >= budget:
                return None
            r = self.query({"require": require})
            n = int(r.get("n_answer") or 0)
            used += 1
            tried.append({"步": step, "条件": require, "为什么": why,
                          "命中": n, "花费": 1})
            return {"n": n, "ids": list(r.get("answer_ids") or []),
                    "require": list(require)}

        def _relevant(node) -> bool:
            """相关性闸：问句里得有东西真出现在这条的**名字或正文**里。

            只靠"收窄到只剩一条"不算精确——收窄是比条件，**不保证切出来的那条
            与问的是同一件事**（实测：问"成绩怎么算"，按 领域=系统接口 收窄会
            剩下《API 定义规范》，一条命中但不是答案）。所以再加一道词上的对照。
            """
            text = (str(node.fields.get("name") or "") + " "
                    + str(getattr(node, "body", "") or ""))
            for n in (4, 3, 2):
                for i in range(max(0, len(q) - n + 1)):
                    g = q[i:i + n]
                    if g.strip() and g in text:
                        return True
            return False

        def _precise(hit):
            """精确判定：恰好一条 + 有正文 + 非判错支 + **问句与正文对得上**。"""
            if not hit or hit["n"] != 1:
                return None
            nid = hit["ids"][0]
            node = self.nodes.get(nid)
            if node is None or str(node.fields.get("裁决") or "") == "判错":
                return None
            body = str(getattr(node, "body", "") or "").strip()
            if not body or not _relevant(node):
                return None
            return {"id": nid, "name": node.fields.get("name"), "正文": body[:900],
                    "字段": {k: node.fields.get(k) for k in
                             ("门类", "领域", "文档类", "来源", "出处", "采信")
                             if node.fields.get(k)},
                    # 外部学来且未核的，引用时必须标出来——不许冒充自己的教导
                    "需标注": ("外部/未核" if str(node.fields.get("采信") or "") == "未核"
                               else None)}

        def _const(key, default):
            return default

        last_multi = None
        hit = None

        # ① 按名字查：问句里出现过的已教名字（长的先试，最多 3 个）
        #    **只认 3 字以上的名字**：两字名太容易撞——实测问"差旅报销标准是多少"，
        #    因为语料里有个名字叫「多少」（反义对），第①步就"命中"了它，
        #    看似精确、其实答的不是那件事。宁可不中，不许假中。
        # ⚠️ 问法词不算名字（2026-11-08，目标驱动测试当场抓到）：问句
        #    「到期的书怎么催还，催几次，不还怎么办」按名字命中了「怎么办」这个
        #    **词义节点**（门类=词汇），于是精确命中、答案变成「怎么办：问接下来
        #    该做什么」——问题被一个词吃了。两字门槛挡得住「多少」，挡不住三字的
        #    「怎么办」。这里按名字排掉问法词：它们是**问法**，不是被问的东西。
        _QWORDS = {"怎么办", "为什么", "什么意思", "是什么", "怎样", "如何",
                   "什么时候", "在哪里"}
        cand_names = [nm for nm in names
                      if len(nm) >= 3 and nm in q and nm not in _QWORDS][:3]
        for nm in cand_names:
            h = _try(["name=" + nm], "①按名字", f"问句里出现「{nm}」")
            if h is None:
                break
            p0 = _precise(h)
            if p0:
                hit = (p0, h)
                break
            if h["n"] > 1:
                last_multi = h

        # ② 按线索查：逐个试（命中唯一就停；多命中也记下来备收窄）
        if not hit:
            for c in clues:
                h = _try([c], "②按线索", f"线索「{c}」")
                if h is None:
                    break
                p0 = _precise(h)
                if p0:
                    hit = (p0, h)
                    break
                if h["n"] > 1:
                    last_multi = h
                if h["n"] == 0:
                    continue

        # ③ 多命中就收窄：本地数一数哪个维度能切出唯一，再去真查确认
        if not hit and last_multi:
            # ③ 收窄：本地先数出"哪个维度值能切出唯一"，再逐个真查确认。
            #    一次只认一条候选是不行的——实测第一个候选是未声明的键，查出 0 条就
            #    整轮放弃了（收窄白花一份预算）。所以候选排成队，命中就停，
            #    查空就换下一个，**每个候选都花一份预算**。
            cands = []
            for dim in ("领域", "文档类", "门类", "类别"):
                groups = {}
                for nid in last_multi["ids"]:
                    v = self.nodes[nid].fields.get(dim)
                    if v is None:
                        continue
                    groups.setdefault(str(v), []).append(nid)
                if len(groups) <= 1:
                    continue
                for v, ids in groups.items():
                    if len(ids) == 1:
                        cands.append(f"{dim}={v}")
            for key in cands:
                h = _try(last_multi["require"] + [key], "③收窄",
                         f"多命中收窄：加「{key}」")
                if h is None:
                    break
                p0 = _precise(h)
                if p0:
                    hit = (p0, h)
                    break

        # ── 结论：三种出口，判据只有"有没有精确命中" ──
        base_ev = [{"原文": "《查询规程》：精确=恰好一条+有正文+非判错；"
                            "一步一份预算，花完就停；多命中不算、只有名字不算（conc/416）",
                    "可复算": "看「已试」每步的命中数与花费，加总＝花费"}]
        if hit:
            node, h = hit
            out = {"ok": True, "结论": "精确命中", "精确": True, "交人": False,
                   "结果": node, "花费": used, "预算": budget,
                   "已试": tried, "依据": base_ev + [
                       {"原文": f"恰好一条命中（{h['n']}）、有正文、非判错 ⇒ 可以用，不必问人",
                        "可复算": f"节点 {node['id']} 的正文"}],
                   "可以就直接用": True}
        elif used >= budget:
            # 预算耗尽优先于"多命中"：钱花完了就是没找到，不能只报"收不窄"糊过去
            top = ([{"id": nid, "name": self.nodes[nid].fields.get("name")}
                    for nid in last_multi["ids"][:3]] if last_multi else [])
            out = {"ok": False, "结论": "预算用完，没找到", "精确": False, "交人": True,
                   "花费": used, "预算": budget, "已试": tried,
                   "候选": top, "相近的名字": _near_names(),
                   "原因": (f"预算 {budget} 份用尽，仍未精确命中"
                            + (f"（命中 {last_multi['n']} 条但没余额收窄）" if last_multi else "")
                            + "——**不拿最像的一条顶替**，交给能定的人。"
                            + ("（「相近的名字」是库里名字挨得上的条目："
                               "**没有不等于空**，先照这几个名字再查一轮）"
                               if _near_names() else "")),
                   "依据": base_ev}
        elif last_multi:
            top = [{"id": nid, "name": self.nodes[nid].fields.get("name")}
                   for nid in last_multi["ids"][:3]]
            out = {"ok": False, "结论": "多命中，收不窄", "精确": False, "交人": True,
                   "候选": top, "花费": used, "预算": budget, "已试": tried,
                   "原因": (f"命中 {last_multi['n']} 条，收窄也没收敛到唯一——"
                            f"**多命中不算查到**，不替你挑。"),
                   "依据": base_ev}
        else:
            _near = _near_names()
            out = {"ok": False, "结论": "查不到（预算内）", "精确": False, "交人": True,
                   "花费": used, "预算": budget, "已试": tried,
                   "相近的名字": _near,
                   "原因": ("按名字和线索都没命中——**没查到不等于没有**："
                            "可能是词不对、维度不对，要么改说法，要么教它这块。"
                            + (f"库里名字挨得上的有：{'、'.join(_near)}——"
                               f"按这些名字再查一轮，别当成「它没教过」。"
                               if _near else "")),
                   "依据": base_ev}

        # 预算账：花了多少、试了什么、结论如何——预算不是感觉，是能对账的数
        nd = self._nodes_dir()
        base = (_P(self._corpus_root) if hasattr(self, "_corpus_root")
                else (nd.parent if nd else None))
        if base is not None:
            try:
                with (base / "lookup_ledger.jsonl").open("a", encoding="utf-8") as f:
                    f.write(_j.dumps({"ts": _dt.datetime.now().isoformat(timespec="seconds"),
                                      "问": q, "线索": clues, "预算": budget,
                                      "花费": used, "结论": out["结论"],
                                      "精确": out["精确"],
                                      "命中节点": (out.get("结果") or {}).get("id")},
                                     ensure_ascii=False) + "\n")
            except OSError:
                pass
        return out

    def _lending(self, spec) -> dict:
        """图书借阅路由（v0.1.99）：按教过的规程做**判定**，自己不落库。

        判定与执行分开：这里只回答「允不允许 / 为什么 / 要写哪几条」，
        写库由调用方按 `待写` 执行——**判定权在内核，执行在手**（同 agent 的分工）。
        上限、借期、滞纳金从《图书借阅规程》的 `常量:` 行读，**不写死**。
        """
        import datetime as _dt
        import json as _j
        import re as _re
        from pathlib import Path as _P
        from ldv.core import mongowire as MW

        if not isinstance(spec, dict):
            return {"ok": False, "弃答": "借阅操作要 {操作, …}——操作：借书/还书/查库存。"}
        op = str(spec.get("操作") or "").strip()
        if op not in ("借书", "还书", "查库存"):
            return {"ok": False, "弃答": f"不认识的操作「{op}」——只认 借书/还书/查库存。"}

        nd = self._nodes_dir()
        base = (_P(self._corpus_root) if hasattr(self, "_corpus_root")
                else (nd.parent if nd else _P(".")))
        cfg_all = {}
        cfg_p = base / "trigger.json"
        if cfg_p.is_file():
            try:
                cfg_all = (_j.loads(cfg_p.read_text(encoding="utf-8") or "{}") or {})
            except ValueError:
                cfg_all = {}
        cfg_m = cfg_all.get("mongo") or {}
        host = str(cfg_m.get("host") or "127.0.0.1")
        port = int(cfg_m.get("port") or 27017)
        db = str(cfg_m.get("db") or "lamp_oil")
        today = _dt.date.today()

        # ── 常量：从规程正文的 常量: 行读 ──
        body = ""
        r0 = self.query({"require": ["name=图书借阅规程"]})
        ids = list(r0.get("answer_ids") or [])
        if ids:
            body = str(getattr(self.nodes.get(ids[0]), "body", "") or "")
        consts = {}
        for line in body.splitlines():
            s = line.strip()
            if s.startswith("常量:") or s.startswith("常量："):
                for kv in s[3:].split("；"):
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        consts[k.strip()] = v.strip()

        def _num(key, default, cast=float):
            m = _re.search(r"[\d.]+", consts.get(key, "") or "")
            try:
                return cast(m.group()) if m else default
            except (TypeError, ValueError):
                return default

        cap = int(_num("借阅上限", 3))
        loan_days = int(_num("借期", 30))
        rate = _num("滞纳金", 0.2)

        def _find(coll, flt, limit=200):
            r = self.query({"mongo": {"op": "find", "集合": coll,
                                      "过滤": flt, "条数": limit}})
            return r.get("结果") or []

        ev = [{"原文": f"《图书借阅规程》常量：上限 {cap} 本、借期 {loan_days} 天、"
                      f"滞纳金 {rate} 元/天（conc/408）",
               "可复算": "常量解析自规程正文 常量: 行，未写死在代码里"}]

        if op == "查库存":
            flt = {}
            if spec.get("书名"):
                flt["title"] = {"$regex": str(spec["书名"])}
            if spec.get("作者"):
                flt["author"] = {"$regex": str(spec["作者"])}
            if spec.get("分类"):
                flt["category"] = str(spec["分类"])
            rows = _find("books", flt, int(spec.get("条数") or 50))
            if spec.get("只要有可借"):
                rows = [b for b in rows if int(b.get("available") or 0) > 0]
            out = [{"bookId": b.get("bookId"), "title": b.get("title"),
                    "author": b.get("author"), "category": b.get("category"),
                    "total": b.get("total"), "available": b.get("available"),
                    "可借": int(b.get("available") or 0) > 0} for b in rows]
            return {"ok": True, "操作": op, "判定": "查询完成",
                    "结果": out, "条数": len(out),
                    "解读": {"结论": "有结果" if out else "空结果",
                             "空结果不算故障": "没有匹配的书 = 库存里就是没有"},
                    "依据": ev}

        bid = str(spec.get("书号") or "").strip()
        who = str(spec.get("借书人") or "").strip()
        if not bid:
            return {"ok": False, "弃答": "借书/还书都要「书号」。"}
        # ── 鉴权在先（conc/411）：借还都要认人，身份只来自凭据 ──
        auth_ev = []
        if op in ("借书", "还书"):
            au = self._auth_check(spec.get("凭据"), who,
                                  secret=str(cfg_all.get("auth_secret") or ""))
            if au.get("拦"):
                return {"ok": False, "操作": op, "判定": "拒绝", "状态码": au["码"],
                        "原因": au["原因"],
                        "依据": [{"原文": "《鉴权规程》conc/411：先认人再办事；"
                                          "无凭据/验不过 ⇒ 401，人和事对不上 ⇒ 403；"
                                          "身份只来自凭据，不采信自报",
                                  "可复算": "凭据 = 载荷(谁+到期).HMAC 签名；"
                                            "密钥在 trigger.json 的 auth_secret，LDV 自己验"}]}
            who = au.get("谁") or who
            auth_ev = [{"原文": f"《鉴权规程》验签通过：凭据属「{who}」，"
                                f"到期 {au.get('到期')}（conc/411）",
                        "可复算": "签名=HMAC-SHA256(载荷,密钥)；比对本机算出的签名"}]
            ev = ev + auth_ev
        if not who:
            return {"ok": False, "弃答": "借书/还书要「借书人」——"
                                        "没给就凭凭据里的身份，两个都没有就不办。"}

        books = _find("books", {"bookId": bid}, 1)
        if not books:
            return {"ok": False, "操作": op, "判定": "拒绝", "状态码": 404,
                    "原因": f"没有这本书：{bid}", "依据": ev}
        book = books[0]
        open_loans = _find("loans", {"borrowerId": who, "status": "借出"}, 200)

        if op == "还书":
            hit = [x for x in open_loans if x.get("bookId") == bid]
            if not hit:
                return {"ok": False, "操作": op, "判定": "拒绝", "状态码": 404,
                        "原因": f"{who} 名下没有这本书的未还记录（{bid}）", "依据": ev}
            loan = sorted(hit, key=lambda x: str(x.get("borrowedAt") or ""))[0]
            due = str(loan.get("dueDate") or "")
            overdue = 0
            if due:
                try:
                    overdue = max(0, (today - _dt.date.fromisoformat(due)).days)
                except ValueError:
                    overdue = 0
            fine = round(overdue * rate, 2)
            # 还书的写也要原子（conc/410）：先抢「这条未还记录」，抢到才加回库存——
            # 两人同时还同一条，只有一个改得到（条件 status=借出），另一个不会重复加。
            w = MW.find_and_modify(
                "loans", {"loanId": loan.get("loanId"), "status": "借出"},
                {"$set": {"status": "已还", "returnedAt": today.isoformat(), "fine": fine}})
            if not (w.get("文档") or {}).get("value"):
                return {"ok": False, "操作": op, "判定": "拒绝", "状态码": 409,
                        "原因": f"这条借阅已经还过了（{loan.get('loanId')} 状态不是「借出」）——"
                                f"并发时两人同时还，只有一个算数，另一个不重复加库存。",
                        "依据": ev + [{"原文": "《并发控制规程》抢资源三律②："
                                              "改到 0 条 = 没抢到，不许当成功（conc/410）",
                                      "可复算": "findAndModify(loans, {loanId, status:借出}, "
                                                "{$set:{status:已还}}) 返回 value=null 即没抢到"}]}
            MW.find_and_modify("books", {"bookId": bid}, {"$inc": {"available": 1}})
            MW.find_and_modify("borrower_stat", {"borrowerId": who}, {"$inc": {"openLoans": -1}})
            return {"ok": True, "操作": op, "判定": "允许", "状态码": 200,
                    "结果": {"loanId": loan.get("loanId"), "bookId": bid,
                             "borrowerId": who, "borrowedAt": loan.get("borrowedAt"),
                             "dueDate": due, "returnedAt": today.isoformat(),
                             "fine": fine, "逾期天数": overdue},
                    "已写": [{"集合": "loans", "op": "cas改状态(借出→已还)"},
                             {"集合": "books", "op": "自增 available 1"},
                             {"集合": "borrower_stat", "op": "自减 openLoans 1"}],
                    "待写": [],
                    "依据": ev + [{"原文": f"逾期 {overdue} 天 × {rate} 元/天 = {fine} 元",
                                   "可复算": "还书日 − 应还日，负数记 0"},
                                  {"原文": "《并发控制规程》：条件与改动一条报文（conc/410）",
                                   "可复算": "还书是一次 findAndModify，不是「先查再改」"}]}

        # ── 借书：先闸后抢（conc/410 抢资源三律）──
        # 快闸：明显不合规的先挡掉（省一次原子报文）；**但真正的闸在下面两条 CAS 里**
        if int(book.get("available") or 0) <= 0:
            return {"ok": False, "操作": op, "判定": "拒绝", "状态码": 409,
                    "原因": f"《图书借阅规程》库存为 0 不许借：{bid} 在馆 0 本", "依据": ev}
        if len(open_loans) >= cap:
            return {"ok": False, "操作": op, "判定": "拒绝", "状态码": 409,
                    "原因": f"《图书借阅规程》同一借书人未还最多 {cap} 本："
                            f"{who} 已借 {len(open_loans)} 本", "依据": ev}
        overdue = [x for x in open_loans
                   if str(x.get("dueDate") or "") and str(x.get("dueDate")) < today.isoformat()]
        if overdue:
            first = sorted(overdue, key=lambda x: str(x.get("dueDate")))[0]
            return {"ok": False, "操作": op, "判定": "拒绝", "状态码": 409,
                    "原因": f"《图书借阅规程》有逾期未还不许再借："
                            f"{who} 有 {len(overdue)} 本逾期（最早应还 {first.get('dueDate')}）",
                    "依据": ev}

        race_ev = [{"原文": "《并发控制规程》抢资源三律：①扣减带余量条件 "
                            "②改到 0 条=没抢到 ③先占后写、失败回补（conc/410）",
                    "可复算": "两条 findAndModify：books 条件 {available:{$gt:0}} + $inc -1；"
                              "borrower_stat 条件 {openLoans:{$lt:%d}} + $inc +1" % cap}]

        # ① 原子预占书册：「还有余量」和「减一本」是同一条报文，服务端一次判一次做
        res = MW.find_and_modify("books", {"bookId": bid, "available": {"$gt": 0}},
                                 {"$inc": {"available": -1}})
        if not (res.get("文档") or {}).get("value"):
            return {"ok": False, "操作": op, "判定": "拒绝", "状态码": 409,
                    "原因": f"没抢到：{bid} 的在馆余量已被别人借空"
                            f"（条件「在馆 > 0」没命中，改到 0 条）——这不叫失败，叫没抢到。",
                    "依据": ev + race_ev}

        # ② 额度计数器也要原子（计数型资源）
        #   先保证有计数账：$setOnInsert + upsert 是**一次报文**（不是"先查再插"）；
        #   一人一条由库上的唯一索引保证——没有唯一键，并发 upsert 会建出两条账，
        #   各记各的数（实测踩过：4 条借出、计数账 [3,1]）。
        try:
            MW.find_and_modify("borrower_stat", {"borrowerId": who},
                               {"$setOnInsert": {"openLoans": len(open_loans)}},
                               upsert=True)
        except Exception:      # noqa: BLE001 —— 别人抢先建了账（唯一键冲突），不算错
            pass
        quota = MW.find_and_modify("borrower_stat",
                                   {"borrowerId": who, "openLoans": {"$lt": cap}},
                                   {"$inc": {"openLoans": 1}})
        if not (quota.get("文档") or {}).get("value"):
            # 回补（三律③）：书册退回去，不留虚占
            MW.find_and_modify("books", {"bookId": bid}, {"$inc": {"available": 1}})
            return {"ok": False, "操作": op, "判定": "拒绝", "状态码": 409,
                    "原因": f"《图书借阅规程》未还最多 {cap} 本：{who} 的额度已满"
                            f"（计数器条件 openLoans < {cap} 没命中，已回补书册）。",
                    "依据": ev + race_ev + [
                        {"原文": "《并发控制规程》：计数器的前提是唯一——"
                                 "一人一条计数账，靠唯一索引保证（conc/410）",
                         "可复算": "db.borrower_stat.getIndexes() 里有 borrowerId 唯一索引"}]}

        # ③ 写明细；写不成要回补占用的两样
        import random as _rn
        days = int(spec.get("天数") or loan_days)
        loan = {"loanId": f"L{today.strftime('%Y%m%d')}"
                          f"{_rn.randint(100000, 999999)}",
                "bookId": bid, "borrowerId": who,
                "borrowedAt": today.isoformat(),
                "dueDate": (today + _dt.timedelta(days=days)).isoformat(),
                "returnedAt": None, "fine": 0, "status": "借出"}
        w = self.query({"mongo": {"op": "insert", "集合": "loans", "文档": [loan]}})
        if not (w.get("ok") and int((w.get("结果") or {}).get("写入") or 0) == 1):
            MW.find_and_modify("books", {"bookId": bid}, {"$inc": {"available": 1}})
            MW.find_and_modify("borrower_stat", {"borrowerId": who},
                               {"$inc": {"openLoans": -1}})
            return {"ok": False, "操作": op, "判定": "失败", "状态码": 500,
                    "原因": f"明细没写成（{w.get('弃答') or '写入 0 条'}）——"
                            f"已把占的册和额度回补，账上不留虚占。",
                    "依据": ev + race_ev}

        return {"ok": True, "操作": op, "判定": "允许", "状态码": 201,
                "结果": {k: loan[k] for k in ("loanId", "bookId", "borrowerId",
                                              "borrowedAt", "dueDate", "returnedAt", "fine")},
                "已写": [{"集合": "books", "op": "cas扣减(条件 在馆>0)"},
                         {"集合": "borrower_stat", "op": "cas自增(条件 未还<%d)" % cap},
                         {"集合": "loans", "op": "insert"}],
                "待写": [],
                "依据": ev + race_ev + [
                    {"原文": f"三条快闸都过（在馆 {book.get('available')} > 0、"
                             f"未还 {len(open_loans)} < {cap}、无逾期），且两条 CAS 都抢到",
                     "可复算": "抢没抢到看 findAndModify 的 value 是否为 null，"
                               "不看本地查到的旧值"}]}

    def _mongo(self, spec) -> dict:
        """MongoDB 端口调用（v0.1.93）：LDV **自己按线协议开 27017**。

        不借驱动——BSON 编解码与 OP_MSG 落在 ldv/core/mongowire.py（只用标准库），
        协议要点教在《MongoDB 端口协议》（conc/396）。
        这一步的意义：LDV 对数据库不再是"发指令等 agent 执行"，而是**自己连**。
        地址仍是人配的（trigger.json 的 mongo 段，默认 127.0.0.1:27017）。
        """
        import json as _j
        import time as _t
        from pathlib import Path as _P
        from ldv.core import mongowire as MW

        ops = ("hello", "ping", "count", "find", "aggregate", "insert", "update", "结存")
        if not isinstance(spec, dict):
            return {"ok": False,
                    "弃答": "mongo 调用要 {op, …}——op 只认 " + "/".join(ops) + "。"}
        op = str(spec.get("op") or "").strip().lower()
        if op not in ops:
            return {"ok": False,
                    "弃答": f"不认识的 op「{op}」——《MongoDB 端口协议》只认 " + "/".join(ops) + "。"}
        nd = self._nodes_dir()
        base = (_P(self._corpus_root) if hasattr(self, "_corpus_root")
                else (nd.parent if nd else _P(".")))
        cfg_m = {}
        cfg_p = base / "trigger.json"
        if cfg_p.is_file():
            try:
                cfg_m = (_j.loads(cfg_p.read_text(encoding="utf-8") or "{}").get("mongo") or {})
            except ValueError:
                cfg_m = {}
        host = str(cfg_m.get("host") or "127.0.0.1")
        port = int(cfg_m.get("port") or 27017)
        db = str(spec.get("库") or cfg_m.get("db") or "lamp_oil")
        coll = str(spec.get("集合") or "")
        flt = spec.get("过滤") or spec.get("filter") or {}
        t0 = _t.time()
        try:
            if op == "hello":
                raw = MW.hello(host, port)
            elif op == "ping":
                raw = MW.ping(host, port)
            elif op == "count":
                if not coll:
                    return {"ok": False, "弃答": "count 要「集合」。"}
                raw = MW.count(coll, flt, db, host, port)
            elif op == "find":
                if not coll:
                    return {"ok": False, "弃答": "find 要「集合」。"}
                raw = MW.find(coll, flt, int(spec.get("条数") or 10), db, host, port,
                              projection=spec.get("投影") or spec.get("projection"),
                              sort=spec.get("排序") or spec.get("sort"))
            elif op == "结存":
                # 账面结存（v0.1.95）：**期初 + 流水净变动**。
                # 期初只在批次台账记一次、不进流水（不然重复计数），所以结存
                # 必须两处相加——只加流水会把期初整批漏掉（实测：JY-01 主库
                # 流水 5、真实结存 45，差的就是 40 的期初）。
                def _n(x):
                    return int(x) if float(x) == int(x) else round(float(x), 3)

                lots_c = str(spec.get("批次集合") or "lots")
                led_c = str(spec.get("流水集合") or "ledger_2611")
                _rl = MW.find(lots_c, {}, 500, db, host, port)
                _lb = (((_rl.get("文档") or {}).get("cursor") or {}).get("firstBatch")) or []
                _rg = MW.find(led_c, {}, 2000, db, host, port)
                _gl = (((_rg.get("文档") or {}).get("cursor") or {}).get("firstBatch")) or []
                acc = {}
                for d in _lb:
                    k = (str(d.get("物料")), str(d.get("库房")))
                    row = acc.setdefault(k, {"期初": 0, "入库": 0, "调入": 0,
                                             "出库": 0, "调出": 0, "损耗": 0, "减": 0})
                    row["期初"] += float(d.get("期初") or 0)
                for d in _gl:
                    if d.get("裁决") == "判错":
                        continue          # 判错支不计入结存
                    k = (str(d.get("物料")), str(d.get("库房")))
                    row = acc.setdefault(k, {"期初": 0, "入库": 0, "调入": 0,
                                             "出库": 0, "调出": 0, "损耗": 0, "减": 0})
                    f = str(d.get("方向") or "")
                    if f in row:
                        row[f] += float(d.get("数量") or 0)
                rows = []
                for (mat, loc), v in sorted(acc.items()):
                    bal = (v["期初"] + v["入库"] + v["调入"] - v["出库"]
                           - v["调出"] - v["损耗"] - v["减"])
                    item = {"物料": mat, "库房": loc, "期初": _n(v["期初"]),
                            "入库": _n(v["入库"]), "调入": _n(v["调入"]),
                            "出库": _n(v["出库"]), "调出": _n(v["调出"]),
                            "损耗": _n(v["损耗"]), "减": _n(v["减"]),
                            "结存": _n(bal)}
                    rows.append({k: vv for k, vv in item.items() if vv})
                return {"ok": True, "耗时毫秒": int((_t.time() - t0) * 1000),
                        "解读": {"结论": "有结果" if rows else "空结果",
                                 "条数": len(rows),
                                 "口径": "结存 = 期初 + 入库 + 调入 − 出库 − 调出 − 损耗 − 减（判错支不计）"},
                        "结果": rows,
                        "依据": [{"原文": "《批次管理规程》：期初只记批次台账、不进流水"
                                          "（两处都记就是重复计数）（conc/398）",
                                  "可复算": "lots.期初 求和 + ledger_2611 按方向加权"}]}
            elif op == "aggregate":
                if not coll:
                    return {"ok": False, "弃答": "aggregate 要「集合」。"}
                pipe = spec.get("管道") or spec.get("pipeline") or []
                if not isinstance(pipe, list) or not pipe:
                    return {"ok": False,
                            "弃答": "aggregate 要「管道」——阶段数组；空管道我不发。"}
                raw = MW.aggregate(coll, pipe, db, host, port)
            elif op == "update":
                # 只许改状态类字段（conc/364 同款规矩）：业务字段禁改，错账走判错支
                ok_keys = ("available", "status", "returnedAt", "fine", "updatedAt",
                           "状态", "备注", "裁决")
                sets = spec.get("set") or spec.get("改") or {}
                bad = [k for k in sets if k not in ok_keys]
                if bad:
                    return {"ok": False,
                            "弃答": f"业务字段不许 update：{'、'.join(bad)}——"
                                    f"错账走判错支，状态类字段才可改（conc/364）"}
                if not sets:
                    return {"ok": False, "弃答": "update 要「set」——改什么、改成什么。"}
                raw = MW.update(coll, flt, sets, db, host, port,
                                many=bool(spec.get("多条")))
            else:  # insert
                if not coll:
                    return {"ok": False, "弃答": "insert 要「集合」。"}
                docs = spec.get("文档") or spec.get("documents") or []
                if isinstance(docs, dict):
                    docs = [docs]
                if not docs:
                    return {"ok": False, "弃答": "insert 要「文档」——空的我不发。"}
                raw = MW.insert(coll, docs, db, host, port)
                # 写入错误不许静默（v0.2.0）：库拒了（唯一键冲突等）时 n 会少于
                # 提交条数、writeErrors 里带原因——以前这里只看 n，把"一条没写进去"
                # 报成了"写入成功 0 条"（实测踩过：重复 borrowerId 被库拒，
                # 路由却回 ok=True）。
                _doc = raw.get("文档") or {}
                _werr = _doc.get("writeErrors") or []
                _n = int(_doc.get("n") or 0)
                if _werr or _n < len(docs):
                    _first = (_werr[0] if _werr else {})
                    _code = _first.get("code")
                    _msg = str(_first.get("errmsg") or "未知原因")
                    _is_val = (_code == 121 or "validation" in _msg.lower())
                    _tag = "参数校验失败" if _is_val else "查询失败"
                    # 允许值（v0.2.19）：validator 就写在集合元数据里，
                    # 失败时去问一句就有——不问，交付方拿不到"允许值 Y"。
                    _allow = {}
                    if _is_val:
                        try:
                            _lc = MW.command({"listCollections": 1, "$db": db})
                            _b = (((_lc.get("文档") or {}).get("cursor") or {})
                                  .get("firstBatch") or [])
                            _opt = (((_b[0] or {}).get("options") or {}).get("validator") or {})
                            _prop = ((_opt.get("$jsonSchema") or {}).get("properties") or {})
                            for _d in docs:
                                for _k, _v in (_d or {}).items():
                                    _en = ((_prop.get(_k) or {}).get("enum"))
                                    if _en:
                                        _allow[str(_k)] = list(_en)
                        except Exception:  # noqa: BLE001
                            _allow = {}
                    # 失败挂账（v0.2.19）：不落账的失败，下一个人要重新踩一遍
                    _rec = {"op": "insert", "库": db, "集合": coll,
                            "步骤": spec.get("第几步"), "已尝试": docs,
                            "允许值": _allow or None, "错号": _code,
                            "errmsg": _msg[:200]}
                    _lead = ""
                    _root = getattr(self, "_corpus_root", None)
                    if _root is not None:
                        try:
                            import datetime as _dt2
                            import json as _j2
                            _p2 = _root / "mongo_ledger.jsonl"
                            with _p2.open("a", encoding="utf-8") as _f2:
                                _f2.write(_j2.dumps({"ts": _dt2.datetime.now()
                                                     .isoformat(timespec="seconds"), **_rec},
                                                    ensure_ascii=False) + "\n")
                            _lead = "已挂账 mongo_ledger.jsonl"
                        except OSError:
                            _lead = "挂账失败（写不进去）"
                    return {"ok": False, "耗时毫秒": int((_t.time() - t0) * 1000),
                            "解读": {"结论": "写入被拒", "任务标签": _tag,
                                     "条数": len(docs), "写入": _n,
                                     "已尝试": docs, "允许值": _allow or None,
                                     "错号": _code, "挂账": _lead or None},
                            "弃答": (f"库拒了这次写入：提交 {len(docs)} 条、写进 {_n} 条——"
                                     f"{_msg[:110]}"
                                     + (f"｜允许值：{_allow}" if _allow else "")),
                            "依据": [{"原文": "写进去才算写了：n 少于提交条数或有 "
                                              "writeErrors，就必须如实报失败；"
                                              "允许值从集合 validator 现取（conc/364 同款）",
                                      "可复算": "listCollections 的 validator.enum 与回执的 n/writeErrors"}]}
        except (ConnectionRefusedError, TimeoutError, OSError) as exc:
            return {"ok": False, "耗时毫秒": int((_t.time() - t0) * 1000),
                    "解读": {"结论": "连不上", "任务标签": "查询失败",
                             "可重试": "否——报人"},
                    "弃答": f"27017 没接上：{type(exc).__name__}: {str(exc)[:80]}"
                            f"——连不上是故障，不重试、如实报人。",
                    "依据": [{"原文": "《MongoDB 端口协议》：连不上（拒绝/超时）才是故障（conc/396）",
                              "可复算": "对 127.0.0.1:27017 做一次 TCP connect 即可复现"}]}
        doc = raw.get("文档") or {}
        ms = int((_t.time() - t0) * 1000)
        ev = [{"原文": "《MongoDB 端口协议》OP_MSG/2013 + BSON 命令文档（conc/396）",
               "可复算": "ldv/core/mongowire.py 只用 socket/struct，报文可 dump 后逐字节复算"}]

        if not doc.get("ok"):
            code = doc.get("code")
            verdict = "未授权" if code in (13, 18) else "命令错"
            # 任务标签（v0.2.19）：交付方要的是「无此记录/无权限/查询失败」三词，
            # 本层自己说的是「空结果/未授权/命令错」——两套词并存，别让人再猜。
            task_tag = "无权限" if verdict == "未授权" else "查询失败"
            return {"ok": False, "耗时毫秒": ms,
                    "解读": {"结论": verdict, "任务标签": task_tag,
                             "错误码": code,
                             "原因": str(doc.get("errmsg") or "")[:200]},
                    "弃答": f"{verdict}（code {code}）：{str(doc.get('errmsg') or '')[:120]}"
                            f"——回执 ok=0 就是没办成，不装作办成了。",
                    "依据": ev}

        if op in ("find", "aggregate"):
            cur = doc.get("cursor") or {}
            batch = list(cur.get("firstBatch") or [])
            for sec in (raw.get("序列") or []):     # 大结果集走 kind 1 文档序列
                if str(sec.get("字段") or "").endswith("firstBatch"):
                    batch += list(sec.get("文档") or [])
            out = {"ok": True, "耗时毫秒": ms,
                   "解读": {"结论": "有结果" if batch else "空结果",
                            "任务标签": "有结果" if batch else "无此记录",
                            "条数": len(batch),
                            "空结果不算故障": "ok=1 但没有文档 = 账面就是这样，不是连不上"
                                             if not batch else None},
                   "结果": batch, "依据": ev}
            # 空结果要能自证（v0.1.94）：协议层分不出"字段名写错导致的空"和
            # "确实没有"——两种都是 ok=1 + 零文档。所以空的时候顺手取一条样本，
            # 把这个集合**真实的字段名**报出来，让人/agent 一眼看出是不是谓词错了。
            if not batch:
                try:
                    probe = MW.find(coll, {}, 1, db, host, port)
                    pd = ((probe.get("文档") or {}).get("cursor") or {}).get("firstBatch") or []
                    chaves = sorted(pd[0].keys()) if pd else []
                except Exception:  # noqa: BLE001
                    chaves = []
                out["该集合字段"] = (chaves if chaves
                                    else "（集合里一条文档都没有，字段也看不到）")
                out["空结果两种可能"] = ("①这个条件下真没有；②过滤用的字段名不是本集合的"
                                       "——先对「该集合字段」，名字不对就是谓词写错，不是没账")
            # 账面汇总（v0.1.94）：分组结果的**加权和**——规则由规程给
            # （入库 +1、出库/减/损耗 −1，见《数据库查询规程》conc/361），
            # 这里只做乘加，不硬编码业务；**agent 也就不必心算了**。
            rule = spec.get("汇总规则") or spec.get("rule")
            if isinstance(rule, dict) and batch:
                vk = str(spec.get("汇总字段") or "合计")
                parts, acc = {}, 0
                for row in batch:
                    g = str(row.get("_id"))
                    v = row.get(vk)
                    if g in rule and isinstance(v, (int, float)):
                        parts[g] = rule[g] * v
                        acc += rule[g] * v
                out["汇总"] = {"各项": parts, "合计": acc, "字段": vk}
            # 判错支提醒（v0.1.94）：聚合里没排判错时如实提示——**不替人过滤**，
            # 但必须让人知道"这个数里含错账"。实测：agent 漏排判错得 12、
            # 却误诊成"日期条件失效"，有了这条提示它才不会再猜错原因。
            if op == "aggregate":
                try:
                    pipe_st = [st for st in (spec.get("管道") or []) if isinstance(st, dict)]
                    has_rule = any("裁决" in (st.get("$match") or {}) for st in pipe_st)
                    if not has_rule:
                        rc = MW.count(coll, {"裁决": "判错"}, db, host, port)
                        bad_n = int((rc.get("文档") or {}).get("n") or 0)
                        if bad_n:
                            out["判错未排"] = (
                                f"本集合有 {bad_n} 条「裁决=判错」的账，这次聚合没排它们"
                                f"——数字里含错账；要净数请在管道里加 "
                                f"{{\"$match\": {{\"裁决\": {{\"$ne\": \"判错\"}}}}}}")
                except Exception:  # noqa: BLE001
                    pass
            return out

        if op == "count":
            n = doc.get("n")
            return {"ok": True, "耗时毫秒": ms,
                    "解读": {"结论": "有结果", "计数": n},
                    "结果": {"集合": coll, "库": db, "计数": n}, "依据": ev}

        if op == "insert":
            return {"ok": True, "耗时毫秒": ms,
                    "解读": {"结论": "写入成功", "写入": doc.get("n")},
                    "结果": {"集合": coll, "库": db, "写入": doc.get("n"),
                             "写入错误": doc.get("writeErrors") or None}, "依据": ev}

        keep = {k: doc.get(k) for k in
                ("ok", "maxWireVersion", "minWireVersion", "maxBsonObjectSize",
                 "maxMessageSizeBytes", "maxWriteBatchSize", "isWritablePrimary",
                 "readOnly", "connectionId") if k in doc}
        return {"ok": True, "耗时毫秒": ms, "解读": {"结论": "通了"},
                "结果": keep, "依据": ev}

    def _self_audit(self) -> dict:
        """评估路由（v0.1.87，用户裁定：路由代替机制——需要的时候表现出
        评估）。三条自查内建在此：①判错支 ②三方对账 ③场面单清点。
        conc/369《评估》是它的文档——教导写的是路由行为的样子。
        产出：平/悬/要人拍板，全部带依据。"""
        k = self.ensure("keyset")
        _q = self.query
        report = {"ok": True, "路由": "评估", "三项": [], "要人拍板": []}

        # ① 判错支：裁决=判错的账，逐条列出并核对收口状态
        r_bad = _q({"require": ["裁决=判错"]})
        bad_ids = list(r_bad.get("answer_ids") or [])
        report["三项"].append({"自查": "①判错支", "条数": len(bad_ids),
                               "清单": bad_ids,
                               "状态": "留档在案（删除禁止），逐条核对收口"})

        # ② 三方对账（v0.1.87 修正）：stock_ledger / credit_ledger 在 MongoDB，
        # 数据在 agent 的执行器里——LDV **不出库查数**，出**对账指令**：
        # agent 按《多集合对账规程》聚合后把数报回来，LDV 算平不平。
        report["三项"].append({
            "自查": "②三方对账",
            "状态": "待 agent 提供",
            "对账指令": ("agent 按《多集合对账规程》（conc/365）聚合："
                         "stock_ledger 入库合计 / 出库+损耗合计 / 「减」向合计；"
                         "stock_take 最新实盘；credit_ledger 未还合计（排除裁决=判错）。"
                         "把这几个数报回来，LDV 用表达式树算平不平。")})
        report["要人拍板"].append("②需 agent 先跑 Mongo 聚合——LDV 等数算平")

        # ③ 场面单清点：学过的社交场面逐个验证定义句路由
        soc = sorted({str(n.fields.get("name")) for n in self.nodes.values()
                      if str(n.fields.get("门类备注") or "") == "社交"})
        dead = []
        for nm in soc:
            sh = self._scene_hit(f"来一句{nm}的话该怎么说", [nm])
            if sh is None or sh[0] != nm:
                dead.append(nm)
        report["三项"].append({"自查": "③场面单清点", "场面数": len(soc),
                               "路由失效": dead or "无——全部可达",
                               "状态": "平" if not dead else "悬"})

        # ⑤ 语义冲突清点（v0.1.88，用户裁定：教导的矛盾要随时间自发现形）：
        # 同名多值——同一名字下数值不同的教导组（符号可检的语义冲突）。
        # 结构矛盾（树）管不到语义层；评估路由把每一组列出来，持续在案——
        # 每次评估都提，这就是教导矛盾随时间的自我动力。
        groups = {}
        for n in self.nodes.values():
            nm = str(n.fields.get("name") or "")
            v = n.fields.get("数值")
            if nm and v is not None:
                groups.setdefault(nm, []).append((n.id, self._float(v)))
        conflicts = []
        for nm, members in sorted(groups.items()):
            vals = sorted({v for _, v in members})
            if len(vals) > 1:
                conflicts.append({"名": nm, "值": vals,
                                  "节点": [nid for nid, _ in members]})
        report["三项"].append({"自查": "⑤同名多值冲突",
                               "冲突组": conflicts or "无",
                               "状态": "悬" if conflicts else "平",
                               "说明": "同一名字下教过不同数值——教导矛盾在此"
                                       "持续在案，每次评估都会重新提出，直到人裁决"})
        if conflicts:
            report["要人拍板"].append(
                f"⑤同名多值 {len(conflicts)} 组（如「{conflicts[0]['名']}」"
                f"{conflicts[0]['值']}）——教导互相打架，需人裁决哪条对")

        # ④ diff 未清数
        try:
            r_diff = _q({"require": ["状态=未清"]})
            report["diff 未清"] = r_diff.get("n_answer")
            if (r_diff.get("n_answer") or 0) > 0:
                report["要人拍板"].append("diff 有未清差异——查明前不出库")
        except Exception:
            pass
        report["说明"] = ("评估路由内建三条自查（conc/369《评估》是它的文档）："
                          "评估照账，不改账——悬着的要人拍板。")
        return report

    def _converse(self, spec):
        """对话通道（v0.1.71）：听一句 → 认词 → 判意图 → 路由到已学通道 → 答所问。

        规矩（对话课教的）：听完再答、答所问、不知道就说不知道、轮着说。
        每轮落 usage/dialog.jsonl（对话账本）——上下文在账里，不在猜里。
        """
        said = str(spec.get("说") if isinstance(spec, dict) else spec or "").strip()
        if not said:
            return {"ok": False, "弃答": "对话缺「说」——你说一句，我答一句。"}
        # 克制路由（v0.1.87，用户裁定：路由代替机制——需要的时候表现出克制）：
        # 危险意图（删除/撤销/改账）不进执行器，路由直接以克制应答——
        # 引《数据库记账规程》条文，给出合规替代（判错支留档），零副作用。
        import re as _re_c
        if _re_c.search(r"删[掉除去]|撤销|撤回|抹掉|清掉|去掉那条|改掉那条", said):
            return {"ok": True, "路由": "克制应答",
                    "答": ("删除禁止——这条是规程，不是工具的 Limit。《数据库记账规程》"
                           "原话：**删除禁止——错账不删，挂「裁决: 判错」分支留档，"
                           "再教正确的一条。**要改错账的合规走法：把错的那条挂判错支"
                           "留档，另教一条正确的——留档与正确账都在，账史完整可查。"
                           "你要改的那条是哪笔？把单据号或内容给我，我按规程挂档。")}
        # ── 认词：出现在句里的已教名字（长词优先）──
        names = sorted({str(n.fields.get("name")) for n in self.nodes.values()
                        if n.fields.get("name")}, key=len, reverse=True)
        heard = [w for w in names if w and w in said][:6]
        # ── 社交场面名（门类备注=社交）——教到哪，路由认到哪（v0.1.73）──
        soc_names = sorted((str(n.fields.get("name")) for n in self.nodes.values()
                            if n.fields.get("门类备注") == "社交" and n.fields.get("name")),
                           key=len, reverse=True)
        # 语义延伸（v0.1.74）：场面是语义，词是名字——全名或语义连通都算指向
        _sh = self._scene_hit(said, soc_names)
        soc_hit, soc_word, soc_basis = _sh if _sh else (None, None, None)
        # 算术句判定（v0.1.77/v0.1.81）：带数值的数名 + 运算字 ⇒ 社交让位——
        # "重力加速度 乘 五"里只有"五"认识，也要让位给缺料反问（不数名，
        # 社交误指会把反问抢掉——实测"等于"沾被夸）
        _num_hits = sum(1 for n in self.nodes.values()
                        if n.fields.get("数值") is not None
                        and str(n.fields.get("name") or "") in said)
        _is_arith = _num_hits >= 1 and any(c in said for c in self._CONV_OPS)
        # ── 意图路由 ──
        route, ans, extra = None, None, {}
        dic = self._dict_load()
        # ① 造句：句中有「造句」
        if "造句" in said or ("造" in said and "句" in said):
            route = "造句"
            tgt = None
            for w in sorted(cards := {str(f.get("词")) for n in self.nodes.values()
                                      if (f := getattr(n, "fields", {})) and f.get("词")},
                            key=len, reverse=True):
                if w and w in said:
                    tgt = w
                    break
            if tgt:
                r = self.query({"造句": {"词": tgt, "式": "比喻句"}})
                ans = (f"用「{tgt}」造句：{r.get('句子')}" if r.get("ok")
                       else str(r.get("弃答"))[:80])
                extra["造句"] = r.get("句子")
            else:
                ans = "你想用哪个词造句？我没在句子里认出有词卡的词。"
        # ② 查字：什么意思 / 怎么读 / 查
        elif any(k in said for k in ("什么意思", "怎么读", "查一下", "查字")):
            # ★ 先认**教过的词**再退到查单字（2026-11-08 修）：
            #   教了「登录」，问「登录是什么意思」就该答登录；
            #   原来的写法一律走单字路，取了句子里最后一个字 ⇒ 答成「录」的字典义。
            _whit = None
            for _nid, _n in self.nodes.items():
                _nmx = str(getattr(_n, "fields", {}).get("name") or "")
                if len(_nmx) >= 2 and _nmx in said and not any(
                        _c in _nmx for _c in "什么意思怎么读查字一下"):
                    if _whit is None or len(_nmx) > len(_whit[1]):
                        _whit = (_nid, _nmx)
            if _whit:
                route = "问词义"
                # 直接取节点，**不走 require**：require 依赖词汇表，name 键没声明
                # 就回 0 条（判据 D-Route1 当场把这个坑抓出来了：路由对了、答是空的）。
                _bod = str(getattr(self.nodes.get(_whit[0]), "body", "") or "")
                ans = f"「{_whit[1]}」——{_bod.strip()[:220]}"
                extra["词义"] = _whit[0]
            else:
                route = "查字典"
                _stop = set("什么意思怎读查字一下的了不这那呢吧吗个是有在和就我你他她它们")
                cand = [c for c in said if c in dic["字"] and c not in _stop]
                if cand:
                    c = cand[-1]
                    r = self.query({"查字典": {"字": c}})
                    ans = (f"「{c}」读 {r.get('拼音')}，{r.get('部首')}部 {r.get('笔画')} 画——"
                           f"{str(r.get('释义'))[:80]}")
                    extra["查字"] = c
                else:
                    ans = "你想查哪个字？句子里我没认出书架上有字头的字。"
        # ③ 道理：道理 / 寓意 / 告诉 + 教过的故事
        elif any(k in said for k in ("道理", "寓意", "告诉我们", "说明什么")):
            route = "讲道理"
            story = next((w for w in heard
                          if self.nodes.get(next((nid for nid, n in self.nodes.items()
                                                  if n.fields.get("name") == w), ""),
                                            None)
                          and self.nodes[next(nid for nid, n in self.nodes.items()
                                              if n.fields.get("name") == w)].fields.get("教训")), None)
            if story:
                nid = next(nid for nid, n in self.nodes.items() if n.fields.get("name") == story)
                les = self.nodes[nid].fields.get("教训")
                ans = f"《{story}》的教训：{les}"
                extra["故事"] = nid
            else:
                ans = "你说的故事我还没教过（或没写教训）——教了我才能讲它的道理。"
        # ④ 怎么办：怎么办 / 应该 + 教过的事（带应照）
        elif any(k in said for k in ("怎么办", "应该", "该怎么做")):
            route = "问该怎么做"
            hit = None
            for nid, n in self.nodes.items():
                f = getattr(n, "fields", {})
                nm = str(f.get("name") or "")
                if f.get("应照") and (nm in said or any(w and w in str(f.get("行为") or "") for w in heard)):
                    hit = (nid, n)
                    break
            if hit:
                nid, n = hit
                r = self.query({"分析": f"name={n.fields.get('name')}"})
                ans = (f"照反查出的「{r.get('应照的道德')}」：{str(r.get('反应'))[:80]}"
                       if r.get("ok") else str(r.get("弃答"))[:80])
                extra["事"] = nid
            else:
                if soc_hit and not _is_arith:   # 教过的是场面不是事 ⇒ 引社交正文
                    ans, nid = self._quote_scene(soc_hit, soc_word, soc_basis)
                    route = "社交问法"
                    extra["社交"] = nid
                elif soc_names:             # 问"怎么说"但没点名场面 ⇒ 报学过的场面
                    route = "社交问法"
                    ans = ("你想问哪种场面的话？我学过：" + "、".join(soc_names)
                           + "——点名一个，我照学的答。")
                else:
                    ans = "这件事我还没教成一件事（人物/行为/应照）——要不要教，由你定。"
        # ④′ 社交问法（v0.1.73）：场面名在场或问"该怎么说" ⇒ 引那场面的正文
        #    引正文：场面名在场即可（提到场面 = 问场面）；没点名则报学过的场面单
        #    （v0.1.77）算术句（两个带数值的数名 + 运算字）优先走算术，社交让位
        elif (soc_hit or any(k in said for k in ("怎么说", "怎么问", "该怎么", "该说什么"))) \
                and not _is_arith:
            route = "社交问法"
            if soc_hit:
                ans, nid = self._quote_scene(soc_hit, soc_word, soc_basis)
                extra["社交"] = nid
            else:
                ans = ("你想问哪种场面的话？我学过：" + "、".join(soc_names)
                       + "——点名一个，我照学的答。")
        # ⑤ 算术：教过的数 + 加减乘除
        elif any(op in said for op in self._CONV_OPS):
            # 收集带数值的数名（v0.1.78）：
            #   **最长匹配**——短名位置被更长的命中名完全罩住 ⇒ 剔除
            #   （"二十"里的"二"、"一百"里的"一"不是独立的数）；
            #   **同名重复可用**——同一数名在句中出现两次，可当两个操作数
            #   （"一百除一百" = 100 ÷ 100）。
            pool = []
            for nid, n in self.nodes.items():
                nm = str(n.fields.get("name") or "")
                v = n.fields.get("数值")
                # 判错支默认隐匿（v0.1.82）：标了 裁决: 判错 的节点不进操作数——
                # 与 query 的 forbid 注入同一规矩（agent 引入前的行为保持）
                if (v is None or not nm or nm not in said
                        or n.fields.get("裁决") == "判错"):
                    continue
                for k in range(max(1, min(said.count(nm), 2))):
                    pos = said.index(nm) if k == 0 \
                        else said.index(nm, said.index(nm) + len(nm))
                    pool.append((pos, len(nm), nm, float(v)))
            nums = [(ln, nm, v) for pos, ln, nm, v in pool
                    if not any(opos <= pos and pos + ln <= opos + oln and oln > ln
                               for opos, oln, _onm, _ov in pool)]
            # 组合自查（v0.1.79）：**相邻**数名拼接成复合中文数，过数的组成
            # 自查（_split_name 拆零件、零件全在词汇表）⇒ 合并成一个操作数——
            # "四十" + "二" 相邻 ⇒ "四十二" = 42。拆法复用 v0.1.60 的组成自查，
            # 不新造；拼不出（"二三"、"十二十"）就保持原样，绝不硬凑。
            # ⚠️ 组合前先剔被罩短名（"四十二"里的"十"），否则会从"十"起步
            #    拼出交叠的"十二"，和"四十"抢位置（实测 28 的来源）。
            from ldv.core import compose as _CP
            pool.sort(key=lambda t: (t[0], -t[1]))
            core = [(pos, ln, nm, v) for pos, ln, nm, v in pool
                    if not any(opos <= pos and pos + ln <= opos + oln and oln > ln
                               for opos, oln, _onm, _ov in pool)]
            merged: list[tuple[int, int, str, float]] = []
            i = 0
            while i < len(core):
                pos, ln, nm, v = core[i]
                j = i
                while j + 1 < len(core) and core[j + 1][0] == pos + ln:
                    cand = nm + core[j + 1][2]
                    parts = _CP._split_name(cand)
                    if not parts or len(parts) < 2:
                        break
                    total, ok_parts = 0.0, True
                    for p in parts:
                        ids = [nid for nid, n in self.nodes.items()
                               if n.fields.get("name") == p]
                        pv = (self.nodes[ids[0]].fields.get("数值")
                              if len(ids) == 1 else None)
                        if pv is None:
                            ok_parts = False
                            break
                        total += float(pv)
                    if not ok_parts:
                        break
                    nm, v, ln = cand, total, len(cand)
                    j += 1
                merged.append((pos, ln, nm, v))
                i = j + 1
            nums = [(ln, nm, v) for pos, ln, nm, v in merged]
            op_ch = next((c for c in said if c in self._CONV_OPS), None)
            if len(nums) >= 2 and op_ch:
                nums.sort(key=lambda t: said.index(t[1]))
                (l_len, l_nm, l_v), (r_len, r_nm, r_v) = nums[0], nums[1]
                r = self.query({"derive": {"运算": self._CONV_OPS[op_ch],
                                           "左": f"name={l_nm}", "右": f"name={r_nm}",
                                           "字段": "数值"}})
                hits = [h["name"] for h in (r.get("语料中算得值的节点") or [])]
                tail = (f"，这个数叫「{hits[0]}」" if hits
                        else "——这个数还没有名字，要不要教")
                if r.get("ok"):
                    ans = f"{l_nm} {op_ch} {r_nm}，算得 {r.get('算得')}{tail}"
                    route = f"算术（{self._CONV_OPS[op_ch]}）"
                    extra["算式"] = r.get("算式")
                else:
                    # derive 弃答透传（v0.1.81）：多命中/缺证据是**矛盾被发现的
                    # 时刻**——原样转达，不化妆成"算得 None"（误导）
                    ans = (f"{l_nm} {op_ch} {r_nm}，算不下去——"
                           f"{str(r.get('弃答'))[:120]}")
                    route = f"算术（弃答）"
            else:
                # 缺料反问（v0.1.81，用户裁定）：**问名字的含义，不报词表**——
                # 内部先找（组成自查已在上面试过，零件不在词汇表 ⇒ 找不出），
                # 找不出就**点名**第一个不认识的块，问外部"它相当于多少"；
                # 外部答了教成新节点入方向树（教导闭环）——下次就认识了，
                # 不需要再问。遮掉已知名与数字，剩下的第一个中文块即缺口。
                from ldv.core import compose as _CP2
                import re as _re3
                known = set()
                for nid, n in self.nodes.items():
                    nm = str(n.fields.get("name") or "")
                    if nm:
                        known.add(nm)
                tmp = said
                for nm in sorted(known, key=len, reverse=True):
                    if nm:
                        tmp = tmp.replace(nm, "□")
                tmp = _re3.sub(r"[0-9.]+", "□", tmp)
                _stop2 = "的了着呢吧吗啊呀哦是把是将就也很都还没在有人我你他她它们这个那什么怎该要与和跟向上下中里个说等于多少几试试问"
                asks = [b for b in _re3.findall(r"[\u4e00-\u9fff]{2,}", tmp)
                        if not any(c in _stop2 for c in b)]
                if asks:
                    ans = (f"「{asks[0]}」我不认识——它相当于多少？告诉我，"
                           "我记进方向树，下次就认识了。")
                else:
                    qn = sorted({str(n.fields.get("name")) for n in self.nodes.values()
                                 if n.fields.get("数值") is not None
                                 and n.fields.get("name")})
                    ans = ("想算的话，句子里得有**两个我认识的数**和一个运算（加减乘除）。"
                           "我认识的数：" + "、".join(qn) + "。")
                route = "算术（缺料）"
        # ⑤′ 无运算符但问"等于几/多少"且有两个数 → 提示
        # ⑥ 兜底：听完了，如实说
        if route is None:
            route = "没听出意图"
            if heard:
                ans = (f"我听到了：{'、'.join(heard)}。但我没听出你想让我做什么——"
                       "可以让我：查字（X什么意思）、造句（用X造句）、"
                       "算一算（X加Y等于几）、讲道理（X告诉我们什么道理）。")
            else:
                ans = ("这句话里的词我都不认识——要不要教，由你定。"
                       "或者试试：查字、造句、算一算、讲道理。")
        # ── 对话账本（v0.1.76：一轮记全）——说了什么、听到什么、指向哪、
        #    凭什么、答了什么。"说完让人接"落在账上：轮号等下一轮来读。
        import json as _json
        from datetime import datetime as _dt
        ud = getattr(self, "_usage_dir", None)
        turn = None
        if ud:
            try:
                from pathlib import Path as _P
                fp = _P(ud) / "dialog.jsonl"
                n_prev = sum(1 for _ in fp.open(encoding="utf-8")) if fp.exists() else 0
                turn = n_prev + 1
                socrec = None
                if route == "社交问法" and soc_hit:
                    socrec = {"词": soc_word, "场面": soc_hit,
                              "凭据": (soc_basis or ["全名在场"])}
                with fp.open("a", encoding="utf-8") as f:
                    f.write(_json.dumps({"at": (self._usage_now or clock.now()).isoformat(timespec="seconds")
                                         if hasattr(self._usage_now or clock.now(), "isoformat")
                                         else str(self._usage_now or clock.now()),
                                         "轮": turn, "说": said, "听到": heard,
                                         "指向": socrec,
                                         "路由": route, "答": ans},
                                        ensure_ascii=False) + "\n")
            except OSError:
                pass
        return {"ok": True, "轮": turn, "听到": heard, "路由": route,
                "答": ans, "extra": extra or None,
                "长说明": "对话 = 听完再答、答所问、不知道就说不知道。"
                          "答里的每个本事（查字/造句/算术/反查）都是已学的通道。"}

    def _semnet(self):
        """语义网（v0.1.72）：从字典释义的构词衍生——语义不是猜的，是从词形长出来的。

        边 = 构词 compound 内相邻两字（电灯光 ⇒ 电-灯、灯-光），
        每条边带出处（哪个条目的哪个构词）。
        """
        if getattr(self, "_semnet_cache", None) is not None:
            return self._semnet_cache
        import re as _re
        dic = self._dict_load()
        edges: dict[frozenset, list[str]] = {}
        for w0, e in dic["字"].items():
            exp = str(e.get("explanation") or "")
            for m in _re.finditer(r"[^，。；：、！？（）()\s\u2460-\u2473]*～[^，。；：、！？（）()\s]*", exp):
                run = m.group(0)
                if len(run) > 6:
                    continue
                comp = run.replace("～", w0)
                comp = comp.strip("的了着")
                for a, b in zip(comp, comp[1:]):
                    if a == b:
                        continue
                    key = frozenset((a, b))
                    src = f"「{w0}」条释义构词「{comp}」"
                    if src not in edges.setdefault(key, []):
                        edges[key].append(src)
        self._semnet_cache = edges
        return edges

    def _sem_path(self, chars_a, chars_b, max_depth: int = 2):
        """两词的语义路径：任一字到任一字，BFS ≤ max_depth。返回 (路径, 依据) 或 None。"""
        edges = self._semnet()
        # 共字 = 0 步（最强）
        common = set(chars_a) & set(chars_b)
        if common:
            return [f"共字「{sorted(common)[0]}」"], ["两词共字——同源"]
        adj = {}
        for key, srcs in edges.items():
            a, b = tuple(key)
            adj.setdefault(a, []).append((b, srcs))
            adj.setdefault(b, []).append((a, srcs))
        from collections import deque
        frontier = deque((c, [c]) for c in set(chars_a))
        seen = set(chars_a)
        for _ in range(max_depth):
            nxt = deque()
            for node, path in frontier:
                for nb, srcs in adj.get(node, []):
                    if nb in seen:
                        continue
                    np = path + [nb]
                    if nb in set(chars_b):
                        basis = []
                        for i in range(len(np) - 1):
                            key = frozenset((np[i], np[i + 1]))
                            basis.append(f"{np[i]}—{np[i+1]}（{'；'.join(edges[key][:1])}）")
                        return np, basis
                    seen.add(nb)
                    nxt.append((nb, np))
            frontier = nxt
        return None

    def _scene_hit(self, said: str, soc_names: list[str]):
        """场面命中（v0.1.74 语义延伸）：多个词指向同一个语义——
        场面是语义节点，词只是指向它的名字。两级识别，每级都要**凭据**：
        ① 全名在场（词形原样）；
        ② 句中词（去虚字后的 2-3 字窗）出现在场面节点**正文**里——
           正文是教过的：场面收哪些词，是教学长出来的，不是编的同义表
           （教正文时提到「道谢」「对不起」「再见」，这些词从此指向该场面）。
        依据原样进答案——指向谁、凭什么，全部亮出来，裁决可复算。
        多命中取名短的场面（总目让位专条）。连通不上就如实连通不上。
        （2026-11-05 用户裁定：不做同义层，做语义延伸。字典构词门试过
        弃用——本字典"又如X(x)"体不产"～"构词，词形证据对道谢类不成立；
        BFS 多步链实测噪声大。正文门是语料原生证据，随教学自动生长。）
        """
        for nm in soc_names:
            if nm in said:
                return nm, nm, None
        _stop = set("的了着呢吧吗啊呀哦是把是将就也很都还没在有人我你他她它们"
                    "这个那什么怎该要与和跟向上下中里个说")
        # 功能词类型位（v0.1.84，用户裁定第③步）：量词/虚词短语**占位不占义**——
        # 「一句/一次/一下/一条」这类"一+量词"结构与纯虚字串不进语义候选。
        # 它已经第二次发作（「一下」→「一次」→「一句」），说明前两次修的是
        # 症状；这次给量词一个 function_word 的位置，一次根治。
        import re as _re
        _FUNC = _re.compile(r"一[句次下个份批位条趟笔场回步道种样套圈样趟]"
                            r"|两[句次下个桶份批位条趟笔]"
                            r"|[几三]个?得?不行?|等[于于]+")
        clean, raw = [], []
        for n in (4, 3, 2):                 # 4 字窗：4 字短引（「这活漂亮」）也要够得着
            for i in range(len(said) - n + 1):
                g = said[i:i + n]
                raw.append(g)
                if any(c in _stop for c in g):
                    continue
                if _FUNC.search(g):          # 功能词：占位不占义
                    continue
                clean.append(g)
        clean = sorted(set(clean), key=len, reverse=True)
        cands = []
        for nm in soc_names:
            nid = next((nid for nid, n in self.nodes.items()
                        if n.fields.get("name") == nm), None)
            if nid is None:
                continue
            body = str(getattr(self.nodes[nid], "body", "") or "")
            # 长引句（>4 字）是例句——剥掉，例句里的日常词（"今天不行…"的
            # 今天）不算场面词汇；短引（「谢谢」「对不起」）是教的词——留
            body = _re.sub(r"「[^」]{7,}」", "", body)
            # 定义句（v0.1.84，用户裁定第②步，主线）：散文判据收紧——
            # "正文出现过" → "定义性特征"：命中词必须出现在场面**定义句**
            # （正文第一句）或教过的短引里；正文深处的偶现词（"等于"散见
            # 三场面深处、"一句"散见六场面）不再指向。定义句是场面被教时
            # 的定义性表述——词在那里，才是场面收的词。
            # 定义句（v0.1.84，用户裁定第②步，主线）：散文判据收紧——
            # "正文出现过" → "定义性特征"：命中词必须落在场面**定义句**
            # （正文第一句）或教过的短引里；正文深处的偶现词（"等于"散见
            # 三场面深处、"一句"散见六场面）不再指向。定义句是场面被教时
            # 的定义性表述——词在那里，才是场面收的词。
            defn = _re.split(r"[，。；]", body)[0] + "。"
            hit = None
            for g in clean:                 # 净词：须落在定义句内
                pos = defn.find(g)
                if pos >= 0:
                    hit = (pos, g)
                    break
            if hit is None:
                # 含虚字的串组不成词——除非教它的时候引过号：
                # 「你好」「早上好」是教的词，杀 gram 的停用字不该杀它
                quotes = set(_re.findall(r"「([^」]{1,7})」", body))
                # 子串匹配（v0.1.86）：引串是教的整句话术（可达 7 字），窗最多
                # 4 字——窗串是某引串的**子串**即命中；整串精确匹配会漏掉
                # 长话术的任何部分引用（实测「字写得真漂亮」7 字引漏检）。
                for g in sorted(set(raw), key=len, reverse=True):
                    if _FUNC.search(g):
                        continue          # 功能词同规：量词短语占位不占义
                    if any(g in q for q in quotes):
                        hit = (body.find(g), g)
                        break
            if hit:
                # 依据文案随命中路径记录（v0.1.86）：定义小句命中=定义句；
                # 引串命中=教过的话术——按 cands[0] 对应的路径说，不串线
                kind = ("定义句里" if _re.split(r"[，。；]", body)[0] and hit[1] in
                        _re.split(r"[，。；]", body)[0] else "教过的话术里")
                # 越早出现越核心（"谢谢"定义感谢，也散见于请求的收尾）
                cands.append((hit[0], len(nm), nm, hit[1], kind))
        if not cands:
            return None
        cands.sort(key=lambda t: (t[0], t[1]))
        _p, _l, nm, g, _kind = cands[0]
        return nm, g, [f"这个词在学过的「{nm}」{_kind}"]

    def _quote_scene(self, nm: str, word: str | None, basis):
        """引场面正文（v0.1.74）：全名命中照旧引；经语义连通命中的，
        先亮指向——你说的哪个词、经哪条语义边、指向哪个场面——再引正文。
        """
        nid = next(nid for nid, n in self.nodes.items()
                   if n.fields.get("name") == nm)
        body = str(getattr(self.nodes[nid], "body", "") or "").strip()
        if body.startswith(nm + "："):
            body = body.split("：", 1)[1].strip()
        if word and word != nm:
            how = "；".join(basis) if basis else "全名在场"
            ans = f"你说的「{word}」——{how}——照学过的「{nm}」：{body[:140]}"
        else:
            ans = f"照学过的「{nm}」：{body[:140]}"
        return ans, nid

    def _dict_associate(self, word: str) -> dict | None:
        """字典自问：查得到 ⇒ {释义头, 联想词}；查不到 ⇒ None。"""
        dic = self._dict_load()
        e = dic["字"].get(word)
        if not e:
            return None
        from ldv.core.compose import _dict_associate as _asso
        return {"释义头": str(e.get("explanation") or "")[:120],
                "联想": _asso(e, word),
                "拼音": e.get("pinyin"), "部首": e.get("radicals"),
                "笔画": e.get("strokes")}

    def _lookup_dict(self, spec):
        """查字典通道（v0.1.68）：把「部首门/音序门/直查」三课落成可用工具。

        spec = {"字": "光"} | {"部首": "火"} | {"音": "guāng"} | {"歇后语": "芝麻开花"}
        一律逐字引字典条目；查不到如实报——字典没收录，不现场编义。
        """
        if not isinstance(spec, dict):
            return {"ok": False, "弃答": "查字典的参数得是字典形状（如 {'字': '光'}）"}
        bad = sorted(set(spec) - {"字", "部首", "音", "歇后语"})
        if bad:
            return {"ok": False,
                    "弃答": f"查字典里不认识的键 {bad} —— 只认 ['字', '部首', '音', '歇后语']"}
        dic = self._dict_load()
        if not dic["字"] and not dic["歇后语"]:
            return {"ok": False, "弃答": "书架上还没有字典文件（refs/ 里没找到）——先给它一部字典"}
        ref = "refs/xinhua_word.json"
        if "字" in spec:
            w = str(spec["字"]).strip()
            e = dic["字"].get(w)
            if not e:
                return {"ok": False, "弃答": f"字典里没有「{w}」这个字头——如实报，不现场编义。"}
            return {"ok": True, "字": w,
                    "拼音": e.get("pinyin"), "部首": e.get("radicals"),
                    "笔画": e.get("strokes"),
                    "释义": str(e.get("explanation") or "")[:400],
                    "依据": [{"原文": f"字典条目「{w}」（{ref}）",
                              "可复算": f"读 {ref} 中 word=={w} 的条目即得"}]}
        if "部首" in spec:
            rad = str(spec["部首"]).strip()
            lst = dic["部首"].get(rad, [])
            if not lst:
                return {"ok": False, "弃答": f"字典里没有收部首「{rad}」的字——如实报。"}
            return {"ok": True, "部首": rad, "共": len(lst),
                    "字": lst[:8], "依据": [{"原文": f"部首门：{ref} 中 radicals=={rad} 的字头",
                                              "可复算": f"按部首索引重排即得（共 {len(lst)} 字）"}]}
        if "音" in spec:
            py = str(spec["音"]).strip().replace("\u0261", "g")   # IPA ɡ 归一为 g
            lst = dic["音"].get(py, [])
            if not lst:
                close = [k for k in dic["音"] if k.startswith(py[:2])][:5]
                return {"ok": False,
                        "弃答": f"字典里没有读音「{py}」的字头" + (f"——相近的音：{close}" if close else "——如实报。")}
            return {"ok": True, "音": py, "共": len(lst), "字": lst[:8],
                    "依据": [{"原文": f"音序门：{ref} 中 pinyin 含「{py}」的字头",
                              "可复算": f"按读音索引重排即得（共 {len(lst)} 字）"}]}
        if "歇后语" in spec:
            q = str(spec["歇后语"]).strip()
            hits = [(e.get("riddle"), e.get("answer"))
                    for e in dic["歇后语"] if q in str(e.get("riddle") or "")][:5]
            if not hits:
                return {"ok": False, "弃答": f"歇后语库里没有含「{q}」的条目——如实报。"}
            return {"ok": True, "歇后语": [{"谜面": a, "底": b} for a, b in hits],
                    "依据": [{"原文": f"歇后语库 refs/xiehouyu.json（{len(dic['歇后语'])} 条）",
                              "可复算": "按谜面包含匹配重跑即得"}]}
        return {"ok": False, "弃答": "查字典缺键——只认 ['字', '部首', '音', '歇后语']"}

    def _answers_path(self):
        from pathlib import Path as _P
        ud = getattr(self, "_usage_dir", None)
        return _P(ud) / "answers.jsonl" if ud else None

    def _recall_answer(self, key: str):
        """答题账本回忆：同题上次作答（时间 + 要点）。账坏 ⇒ 无回忆，不碍作答。"""
        from datetime import datetime as _DT
        p = self._answers_path()
        if p is None or not p.exists():
            return None
        hit = None
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("键") == key:
                    hit = r
        except OSError:
            return None
        if not hit:
            return None
        return {"时间": hit.get("at"), "要点": hit.get("要点"),
                "可复算": "重跑同题应得同一结果——对不上就是把账记错了"}

    def _record_answer(self, key: str, result: dict) -> None:
        p = self._answers_path()
        if p is None:
            return
        try:
            要点 = None
            if "算得" in result:
                要点 = {"算式": result.get("算式"), "算得": result.get("算得"),
                        "命中": [h.get("name") for h in (result.get("语料中算得值的节点") or [])]}
            elif "解得" in result:
                要点 = {"算式": result.get("算式"), "解得": result.get("解得"),
                        "命中": [h.get("name") for h in (result.get("语料中解得值的节点") or [])]}
            elif "应照的道德" in result:
                要点 = {"应照的道德": result.get("应照的道德"),
                        "反应": str(result.get("反应"))[:80]}
            if 要点 is None:
                return
            _now = self._usage_now or clock.now()
            _at = _now.isoformat(timespec="seconds") if hasattr(_now, "isoformat") else str(_now)
            line = json.dumps({"at": _at, "键": key, "要点": 要点}, ensure_ascii=False)
            with p.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

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
                "need_predicate": "键不在词汇表时给：逐条证据（拼错 / 缺谓词 / 缺维度），"
                                  "`可声明=true` 的可以当场 `POST /api/declare` 或带 "
                                  "`auto_declare:true` 重发",
            },
            "declare": {
                "POST /api/declare": '{"key":"金额>=3000","why":"…","by":"agent"}',
                "边界": "只追加不覆盖；0 命中拒收；只对 `_data/` 下的用户语料开放",
                "生效": "响应里的 `生效` 才是关键 —— 写进文件 ≠ 进得了 `all_keys`",
            },
            "boundaries": ["只读（`/api/declare` 是唯一例外，且只写用户语料）",
                           "单进程串行（query 会 expand）", "只绑 127.0.0.1，无鉴权"],
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
            elif u.path == "/api/declare":     # 值谓词声明（agent 填内容的那一格）
                self._json(APP.declare(
                    str(body.get("key") or ""),
                    why=str(body.get("why") or ""),
                    by=str(body.get("by") or "agent"),
                    allow_empty=bool(body.get("allow_empty"))))
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
                    help="语料名（默认：优先 ext-wiki-vote，"
                         "**它的 nodes/ 不在就回落到内置语料** —— 见 default_corpus()）")
    ap.add_argument("--batch", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    global APP
    # 默认用**真实分类表**，不用仓库那 36 项 demo —— 入口是给人用的，得先有内容。
    # ⚠️ 这里是「换材料」，不是「造材料」：155 项全部来自 arXiv 官方分类表，
    #    `filled_by` 逐项写着出处。**不代填任何内容**（立项红线）。
    corpus = a.corpus or os.environ.get("LDV_CORPUS") or default_corpus()
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
