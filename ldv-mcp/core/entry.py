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
import hashlib
import io
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
import traceback
from collections import Counter
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import urllib.error
import urllib.parse
import urllib.request

from ldv import protocol as PROTO
from ldv.checks._fixtures import build_keyset, build_reach
from ldv.core import clock
from ldv.core import declare as DECL
from ldv.core.interfaces import WeightedRecord
from ldv.corpus.loader import load_edges, load_nodes, keys_of
from ldv.corpus.loader import Node
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


# ══ 原话级路由算子（v0.2.49 正位）══════════════════════════════════════════
# 2026-11-14 用户：「工具应该放在路由组合，你先看以往做法」——这批算子此前错放在
# 桥 ldv-mcp/server.py（v0.2.43–48），绕开了内核的路由组合机制。本块按
# docs/路由的教法.md 的授权形态搬回内核：规矩写语料（七段节点），算子（怎么认
# 地址、怎么发一次）放代码——节点在才走（名字是唯一钥匙），没教 ⇒ 走旧路（I5）。

# ── 《闲聊交外》（conc/972）触发词 ──────────────────────────────────────────
_CHAT_TRIGGERS = ("你好", "您好", "哈喽", "嗨", "在吗", "在不在",
                  "谢谢", "多谢", "辛苦了", "再见", "拜拜", "晚安", "早安",
                  "早上好", "下午好", "晚上好", "吃了吗", "吃饭了吗", "喝了吗",
                  "睡了吗", "无聊", "陪我聊")
# ★ v0.2.48 补：英文触发词按**词边界**认——"chinawriter" 里的 hi 不是打招呼
#   （D-RelayE2E 抓到：中文句里的英文域名被子串误命中 ⇒ 好好的爬网页任务被判成交外）。
_CHAT_ASCII = ("hello", "hi")
_CHAT_FACT_HEADS = ("为什么", "怎么", "怎样", "如何", "什么", "多少", "几",
                    "谁", "哪", "多大", "多久", "什么时候", "讲讲", "说说",
                    "介绍", "查查", "查一下")

# ── 《直连端口》（conc/1023）地址解析 ──────────────────────────────────────
_HTTP_ADDR = re.compile(
    r"(?P<scheme>https?://)?(?P<host>[\w.\-]+):(?P<port>\d{1,5})(?P<path>/[^\s，。；、？!！]*)?",
    re.IGNORECASE)
# ★ v0.2.48 补：带 http(s):// 前缀的地址**端口缺省也认**（http→80、https→443）。
#   路径只认 RFC3986 常用字符——「…/forum/内容到本地」里的中文不并进路径。
_HTTP_ADDR2 = re.compile(
    r"(?P<scheme2>https?)://(?P<host2>[\w.\-]+)(?P<path2>/[A-Za-z0-9._~!$&'()*+,;=:@%/\-]*)?",
    re.IGNORECASE)


def _parse_addr(text: str):
    """地址解析：`主机:端口[/路径]`（老口径，端口必带）优先；`http(s)://host[/path]`
    端口缺省也认。返回 (scheme, host, port, path, 是否显式给了scheme)；没地址 None。"""
    m = _HTTP_ADDR.search(text)
    if m:
        return ((m.group("scheme") or "http://").lower(), m.group("host"),
                m.group("port"), (m.group("path") or "/"), bool(m.group("scheme")))
    m2 = _HTTP_ADDR2.search(text)
    if m2:
        scheme = m2.group("scheme2").lower() + "://"
        return (scheme, m2.group("host2"),
                "443" if scheme == "https://" else "80",
                (m2.group("path2") or "/"), True)
    return None


_HTTP_INTENT = ("查", "调", "发", "探", "请求", "访问", "ping", "测")
_HTTP_WRITE = {"POST", "PUT", "DELETE", "PATCH"}

# ── 《无头浏览器直取》（conc/1024）载体 ────────────────────────────────────
_HEADLESS_INTENT = ("无头", "headless", "浏览器", "渲染", "截图", "快照")
_BROWSER_PATHS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)


def _find_browser():
    for p in _BROWSER_PATHS:
        if os.path.isfile(p):
            return p, ("Edge" if "msedge" in p.lower() else "Chrome")
    return None, None


# ── 《爬到落盘》（conc/1028）/《限速连调》（conc/1025）触发词 ────────────────
_SAVE_WORDS = ("存", "保存", "落盘", "到本地")


def _has_save_intent(text: str) -> bool:
    """存词在场判定（v0.2.77）：排除「存储」复合词——「存储组磁盘告警」的
    存不是要落盘；「保存/落盘/到本地」仍整词算。"""
    if any(w in text for w in ("保存", "落盘", "到本地")):
        return True
    return any(text[i:i + 2] != "存储"
               for i, ch in enumerate(text) if ch == "存")
_RATE_WORDS = ("限速", "每秒", "qps", "速率")

# ── 《JA4自探》（conc/1026）拆解 ──────────────────────────────────────────
_JA4_VER = {0x0304: "13", 0x0303: "12", 0x0302: "11", 0x0301: "10"}


def _is_grease(v: int) -> bool:
    return (v >> 8) == (v & 0xFF) and (v & 0x0F) == 0x0A and ((v >> 4) & 0x0F) == 0x0A


def _parse_client_hello(raw: bytes):
    """拆 TLS 记录层里的 ClientHello：版本/密码套件/扩展（剔 GREASE）。"""
    if len(raw) < 5 or raw[0] != 0x16:
        return None
    rl = int.from_bytes(raw[3:5], "big")
    b = raw[5:5 + rl]
    if len(b) < 4 or b[0] != 0x01:
        return None
    i = 4
    legacy = int.from_bytes(b[i:i + 2], "big"); i += 2 + 32      # 版本+random
    sid = b[i]; i += 1 + sid
    cs_len = int.from_bytes(b[i:i + 2], "big"); i += 2
    ciphers = []
    for j in range(0, cs_len - 1, 2):
        v = int.from_bytes(b[i + j:i + j + 2], "big")
        if not _is_grease(v):
            ciphers.append(v)
    i += cs_len
    comp = b[i]; i += 1 + comp
    ext_end = i + 2 + int.from_bytes(b[i:i + 2], "big"); i += 2
    exts = {}
    while i + 4 <= ext_end:
        et = int.from_bytes(b[i:i + 2], "big")
        el = int.from_bytes(b[i + 2:i + 4], "big")
        if not _is_grease(et):
            exts[et] = b[i + 4:i + 4 + el]
        i += 4 + el
    return {"legacy": legacy, "ciphers": ciphers, "exts": exts}


def _ja4_of(hello: dict) -> dict:
    """按 JA4 口径（FoxIO-LLC/ja4）拼指纹——哈希=sha256 前 12 位；组成件全带出。"""
    exts = hello["exts"]
    sv = exts.get(0x002B)
    if sv and len(sv) >= 3:
        vers = [int.from_bytes(sv[1 + j:3 + j], "big")
                for j in range(0, len(sv) - 1, 2)
                if not _is_grease(int.from_bytes(sv[1 + j:3 + j], "big"))]
        ver = _JA4_VER.get(max(vers), "?") if vers else "?"
    else:
        ver = _JA4_VER.get(hello["legacy"], "?")
    sni = 0x0000 in exts
    alpn_raw = exts.get(0x0010)
    alpn = "00"
    if alpn_raw and len(alpn_raw) >= 4:
        n = alpn_raw[1] if len(alpn_raw) > 1 else 0
        name = alpn_raw[2:2 + n]
        alpn = ("h2" if name == b"h2" else "1.1" if name == b"http/1.1"
                else name.decode("ascii", "replace")[:4] or "00")
    # 密码：按发出顺序；扩展：字典序排、SNI 挪尾、签名算法接尾（口径自算）
    c_str = ",".join("%04x" % c for c in hello["ciphers"])
    ext_hex = sorted("%04x" % e for e in exts if e != 0x0000)
    if sni:
        ext_hex.append("0000")
    sig = exts.get(0x000D)
    sig_hex = []
    if sig and len(sig) >= 2:
        for j in range(0, len(sig) - 1, 2):
            sig_hex.append("%04x" % int.from_bytes(sig[j:j + 2], "big"))
    e_str = ",".join(ext_hex + sig_hex)
    return {"指纹": "t%s%s%02d%02d%s_%s_%s" % (
                ver, "d" if sni else "i", len(ext_hex),
                len(hello["ciphers"]), alpn,
                hashlib.sha256(c_str.encode()).hexdigest()[:12],
                hashlib.sha256(e_str.encode()).hexdigest()[:12]),
            "版本": "TLS1." + ver[1:], "SNI": sni,
            "扩展数": len(ext_hex), "密码数": len(hello["ciphers"]), "alpn": alpn,
            "密码套件": ["0x%04x" % c for c in hello["ciphers"]],
            "扩展": ["0x%04x" % e for e in sorted(exts)],
            "签名算法": sig_hex}


# ── 《拉起服务》（conc/1058）《Mongo存取》（conc/1059）《对账执行》（conc/1060）载体 ──
import datetime as _dtm
import decimal as _dec
import socket as _socket
import subprocess as _subproc

# 「创建」并进拉起（2026-11-16 用户口径：「对服务来说创建和拉起不是差不多吗」）
_SERVE_TRIGGERS = ("拉起", "启动", "起服务", "跑起来", "创建")
# 动作动词报缺（conc/1072）：这三个动词落在非服务物上 ⇒ 如实声明动作没有
_VERB_GAPS = ("创建", "搭建", "实现", "做", "搞", "弄")
# 口语动词并入（2026-11-16 用户口径：「递归自进化是一个新名词，这些口语词加进去」）。
# 做/搞/弄 面宽 ⇒ verb_gap 在链上排在专属路由（mongo/对账/直连…）**之后**兜底：
# 「做…对账」「做JA4」先被各自的路由接走，只有没专属路由的宾语才落到报缺。
# 疑问头在场 = 问方法不是创建意图（「怎么实现X」是查方法，不是要系统）
_VERB_QUESTION_HEADS = ("怎么", "怎样", "如何", "什么", "为什么", "哪些", "哪个", "吗", "呢")
# 组合器（conc/1075，v0.2.53）：目标句拆步的动词候选表——动词到路由，
# 参数从原话里拿，拿不到标缺口不编。词取多字复合形，压误命中。
# v0.2.78 扩容（用户拍板）：仍是已有三条执行路由（http/mongo/对账）的
# 复合动词，不新增路由形状；门槛 ≥2 动词才开候选，单动词误命中进不来。
_PLAN_VERBS = ("抓下来", "抓取", "爬下来", "下载", "拉取",
               "存进", "存入", "存到", "写入", "入库", "对账",
               "取下来", "访问", "调用", "调接口", "拉数据", "取数据",
               "落库", "写进", "记进", "记录到", "登记到",
               "核账", "对平")
_PLAN_ROUTE_OF = {"抓下来": "http", "抓取": "http", "爬下来": "http",
                  "下载": "http", "拉取": "http",
                  "取下来": "http", "访问": "http", "调用": "http",
                  "调接口": "http", "拉数据": "http", "取数据": "http",
                  "存进": "mongo", "存入": "mongo", "存到": "mongo",
                  "写入": "mongo", "入库": "mongo",
                  "落库": "mongo", "写进": "mongo", "记进": "mongo",
                  "记录到": "mongo", "登记到": "mongo",
                  "对账": "对账", "核账": "对账", "对平": "对账"}
# 候选确认口气：人拍板才执行（判定权在人；候选不自动跑）
_PLAN_CONFIRM = ("按候选执行", "按这个执行", "按步单执行", "按方案执行",
                 "执行候选", "执行步单")
# 研发任务执行（conc/1074）：九段 × 语料节点名——任务卡正文从语料查，规矩在语料不在算子
_DEV_STAGES = [
    ("接任务与澄清需求", "conc/1062"),
    ("技术方案与拆解", "conc/1063"),
    ("拉分支开发", "conc/1064"),
    ("编码与自测", "conc/1065"),
    ("提交PR", "conc/1066"),
    ("代码评审", "conc/1067"),
    ("合并与部署", "conc/1068"),
    ("线上验证与监控", "conc/1069"),
    ("收尾", "conc/1070"),
]
_DEV_DB = "ldv_dev"
_DEV_COLL = "dev_tasks"
_DETACHED = 0x0008 | 0x0200 | 0x08000000   # DETACHED_PROCESS|NEW_GROUP|NO_WINDOW

# 币种小数位（conc/1043）：JPY 0 位、KWD 3 位、默认 2 位——差一位就是 100 倍
_RECON_DECIMALS = {"JPY": 0, "KWD": 3, "BHD": 3, "TND": 3, "CLP": 0, "ISK": 0, "VND": 0}
# 币种表示（conc/1044）：代码/符号/英文全称/ISO 数字码 ⇒ 归一
_CUR_ALIASES = {"USD": "USD", "$": "USD", "US$": "USD", "US DOLLAR": "USD",
                "840": "USD", "EUR": "EUR", "€": "EUR", "978": "EUR",
                "JPY": "JPY", "YEN": "JPY", "円": "JPY", "392": "JPY",
                "KWD": "KWD", "414": "KWD", "CNY": "CNY", "RMB": "CNY",
                "￥": "CNY", "156": "CNY", "GBP": "GBP", "£": "GBP", "826": "GBP"}
_CUR_TOKEN_RE = re.compile(r"(?i)us\s*dollar|usd|eur|jpy|kwd|cny|gbp|[¥$€£円元\s]")


def _parse_amount(raw):
    """金额四形态（conc/1042）：'1,234.56' / '-12.00' / '12.00-'（尾负号）/
    '1.234,56'（欧式：点=千分位、逗=小数）。解析不出 ⇒ None，不猜。"""
    if raw is None:
        return None
    s = _CUR_TOKEN_RE.sub("", str(raw)).strip()
    if not s:
        return None
    neg = False
    if s.endswith("-"):                          # 尾负号（银行流水常见）
        neg, s = True, s[:-1].strip()
    if s.startswith("-"):
        neg, s = True, s[1:]
    if not s:
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):          # 欧式 1.234,56
            s = s.replace(".", "").replace(",", ".")
        else:                                     # 美式 1,234.56
            s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        if len(parts) > 1 and all(len(p) == 3 for p in parts[1:]) and len(parts[0]) <= 3:
            s = s.replace(",", "")                # 1,234 千分位
        else:
            s = s.replace(",", ".")               # 617,28 欧式小数
    try:
        v = _dec.Decimal(s)
    except (_dec.InvalidOperation, ValueError):
        return None
    return -v if neg else v


def _norm_currency(raw):
    """币种归一（conc/1044）：USD/$/US Dollar/840 ⇒ USD。认不出 ⇒ None（不猜）。"""
    if raw is None:
        return None
    return _CUR_ALIASES.get(str(raw).strip().upper()) or _CUR_ALIASES.get(str(raw).strip())


def _minor(amount, cur):
    """主单位金额 → 最小单位整数（按币种小数位，conc/1043）。"""
    d = _RECON_DECIMALS.get(cur, 2)
    return int((amount * (_dec.Decimal(10) ** d)).quantize(
        _dec.Decimal(1), rounding=_dec.ROUND_HALF_UP))


def _tolerance_minor(cur):
    """容差 0.01（主单位）换算成该币种最小单位（conc/1037 容差级）。"""
    return max(1, int(round(_dec.Decimal("0.01") * (_dec.Decimal(10)
                                                    ** _RECON_DECIMALS.get(cur, 2)))))


def _bank_utc_date(row):
    """银行行 → UTC 日期（conc/1045）：本地时间带时区偏移换 UTC；只有结算日按当日。"""
    bt = str(row.get("booking_time") or row.get("booking_time_local") or "").strip()
    if bt:
        try:
            dt = _dtm.datetime.fromisoformat(bt.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                tz = str(row.get("bank_tz") or "+00:00")
                sign = 1 if tz.startswith("+") else -1
                try:
                    hh, mm = int(tz[1:3]), int(tz[4:6])
                except (ValueError, IndexError):
                    hh, mm = 0, 0
                dt = dt.replace(tzinfo=_dtm.timezone(
                    _dtm.timedelta(hours=sign * hh, minutes=sign * mm)))
            return dt.astimezone(_dtm.timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, IndexError):
            pass
    return str(row.get("value_date") or "")


def _tcp_up(host, port, timeout=2.0):
    try:
        with _socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


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

    def usage_health(self) -> dict:
        """死账读数（v0.2.66，用户拍板「读侧把树上不存在的 did 过滤出来」，
        docs/历史与排序_20261111.md §3.2/§6-B 的轻量半步）。

        ★ 背景：did 是内容派生（父+键集），树每加材料就重建 ⇒ 老账大量指向
          已不存在的方向（活语料实测 92%）。**只报数，不删、不重指**：
          - 删 = 账本只追加的纪律破掉（可审计/可回退同时丢）；
          - 重指 = 把老账硬安到新树上，就是「静默错位」—— 正是 2026-11-02
            改内容派生修掉的病（指错比丢失更糟）。
        ★ 将来若要重指，数据**已经在**：每条记录落盘时 `snapshot`（§I7）
          带着该方向当时的 require/forbid 键集（keyset.record）——
          本方法只报现状，不做任何写。
        ⚠️ 死账无害化的另一半在排序（v0.2.66）：触发收窄成「本题的支带倾向」，
           树上不存在的 did 根本对不上任何支。
        """
        if self._usage_dir is None:
            return {"ok": False, "why": "内置语料不启用使用记录"}
        rows = usage_store.load(self._usage_dir)
        live = {d.did for d in self.ensure("keyset").all_directions()}
        # ★ 节点粒度账单独归类（v0.2.74 合取口径）：did 带 `K=` 前缀（路由
        #   确认的节点合取）或含 `/`（节点 id 形状）的不是方向账——
        #   混进死账读数会把「稳定钥匙的账」误报成「树上不存在的方向」。
        #   只报数不删的纪律不动。
        per: dict[str, int] = {}
        per_node: dict[str, int] = {}
        for r in rows:
            did = str(r["did"])
            if did.startswith("K=") or "/" in did:
                per_node[did] = per_node.get(did, 0) + 1
            else:
                per[did] = per.get(did, 0) + 1
        alive = {d for d in per if d in live}
        n_alive = sum(per[d] for d in alive)
        return {"ok": True, "记录数": len(rows),
                "涉及方向数": len(per),
                "指向现树": n_alive, "指向现树的方向": len(alive),
                "死账": len(rows) - n_alive - sum(per_node.values()),
                "死账方向": len(per) - len(alive),
                "节点粒度账": {"条数": sum(per_node.values()),
                                "账头数": len(per_node),
                                "说明": "路由确认的 K= 合取账（v0.2.74）与"
                                        "v0.2.73 逐节点旧账——节点 id 稳定钥匙，"
                                        "不参与方向死账统计"},
                "说明": "死账只报数不删（账本只追加）；每条记录的 snapshot 带"
                        "当时的键集留痕，重指等语料低频修改期再拍板"}
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
        #   ★ 实减口径（v0.2.65，2026-11-18 用户拍板补小账）：`lost` 是被推翻支的
        #     **全部成员**，可能含本就不在这次答案集里的项——报总数会把账报大。
        #     实减数 = 这次答案集里**实际被剔掉的**项数（`ans ∩ lost`）。
        rej_dids, lost = self._rejected_dirs(k, req)
        n_lost = len(ans & lost) if lost else 0
        if lost:
            ans = set(ans) - lost
            if not ans:
                return ([self._node(k, k.root, ans,
                                    "满足约束的项都在被推翻的支里 —— 要看得把 "
                                    "require 说全（含该支的 must），或调 reject_below")],
                        [], "rejected", ans, sorted(rej_dids), n_lost)
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
        return nodes, path, why, ans, sorted(rej_dids), n_lost

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

    # ── ③ 合取 0 命中 ⇒ 析取回退（v0.2.64，2026-11-18 用户拍板）：──────
    #    多词问句走合取，教学语料天然单挂（一门课一个节点，交叉关系写在
    #    正文挂链里）⇒ 「Python 的判断语句怎么写」切两键合取必 0 命中。
    #    口径：合取没中时**按每个词分别查一遍**给并集，如实标「任一命中，
    #    不是推荐」——只算不改：answer_ids/n_answer 不动，回退数据单独
    #    一格，判定权在调用方。单键不回退（单键没中就是没中）。
    def _disjunctive(self, req: frozenset[str], forb: frozenset[str]) -> dict:
        per: list[dict] = []
        union: list[str] = []
        seen: set[str] = set()
        # ⚠️ 只认 require 键：forb 里可能有透镜插的内部键（如「合取=已归并」），
        #    并进去就是把全库都翻出来——多词问句的病灶只在 require 侧。
        #    并集按逐键顺序排（不按 id 全局排序）：先命中的键的项先出——
        #    「你好 循环」里循环的正文要排在问候前头，别让寒暄条目带偏调用方。
        for k in sorted(req):
            sub = self.answer_set(frozenset([k]), frozenset())
            if sub:
                per.append({"键": k, "命中": len(sub), "ids": sorted(sub)})
                for i in sorted(sub):
                    if i not in seen:
                        seen.add(i)
                        union.append(i)
        return {"逐键": per,
                "answer_ids": union,
                "answer_titles": [self.titles.get(i, i) for i in union],
                "为什么": ("合取没中——按每个词分别查给你：任一命中，"
                           "不是推荐，要不要采信由人。")}

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
        _combo = [k for k in ("derive", "solve", "moral", "分析", "成文", "变形", "反义", "主题", "造句", "评估", "端口", "借算", "mongo", "接口定义", "借阅", "查询", "外查", "装配", "过程", "差距", "建议", "推演", "组合", "后续", "结算", "取", "验收", "http", "对账", "小说分析", "对比分析") if k in spec]
        if _combo:
            if len(spec) > 1 or len(_combo) > 1:
                return {"ok": False,
                        "error": "derive / solve / moral / 成文 / 变形 / 反义 / 评估 / 端口"
                                 " 是独立的组合问法——别和检索字段（require/forbid/…）"
                                 "混在一个 spec 里，也别多个一起发"}
            if _combo[0] == "后续":
                # 结束之后的后续动作（v0.2.32，用户 2026-11-10「给结束的后续动作也通过
                # 教学做成一个路由」）：三态各一条出口，都是交出去，不是自己再跑一遍。
                # 规矩在 conc/547《结束之后》；这里只分派与摘录。
                return self._after_route(spec["后续"])
            if _combo[0] == "组合":
                # 组合路由（v0.2.29，用户 2026-11-10「教它怎么组合路由，把上一步结论
                # 带回到入口继续找下一步」）：一步一段，段段重新过 query 入口。
                # 规矩在 conc/545《结论回入口》；这里只放**怎么填、怎么停**的算子。
                return self._compose(spec["组合"])
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
                # v0.2.35：教过《接口目录》且给了「接口」名 ⇒ 走目录（多端点/GET/分页）。
                _ps = spec["端口"]
                if isinstance(_ps, dict) and str(_ps.get("接口") or "").strip() and self._api_table():
                    return self._api_call(_ps)
                return self._port_call(_ps)
            if _combo[0] == "小说分析":
                # 小说分析管线路由（v0.2.41，用户拍板「教 ldv 怎么调接口让它自己爬」）：
                # 规矩在语料《小说分析管线》（conc/918）——发起、等就绪、按目录序逐段调，
                # 一段一调全走《接口目录》（conc/553），地址人配、路径不猜。
                # 没教过这个节点 ⇒ 弃答（I5：找不到路由走旧路）。
                return self._novel_pipeline(spec["小说分析"])
            if _combo[0] == "对比分析":
                # 对比分析管线路由（v0.2.42，用户拍板「换一个同等级学生题目重新做；
                # 不能光换爬源，结构要不同」）：双分支汇合——两本书各一条支线、
                # 两个任务号，支线段各走各的，汇合段参数=任务号A+任务号B 两把一起带。
                # 规矩在语料《对比分析管线》（conc/971）；没教过 ⇒ 弃答（I5）。
                return self._duibi_pipeline(spec["对比分析"])
            if _combo[0] == "结算":
                # 结算路由（v0.2.35）：一批行 + 一张汇率 + 一份费率 ⇒ 一张表加总。
                # 公式全在 conc/554；这里只做代入、求和、核对。
                return self._settle(spec["结算"])
            if _combo[0] == "取":
                # 取数路由（v0.2.35）：从一整块里取一个值。规矩在 conc/555。
                return self._pick(spec["取"])
            if _combo[0] == "验收":
                # 回单验收（v0.2.35）：跑完没有 ≠ 要的兑现没有。规矩在 conc/557。
                return self._accept(spec["验收"])
            if _combo[0] == "http":
                # http 步进路由（v0.2.53，组合器步单用）：{方法, 地址, 体?}——
                # 契约同《直连端口》：地址不成不猜、环回绕代理、一次不重发。
                # 没教过《直连端口》⇒ 弃答（I5：找不到路由走旧路）。
                if not self._route_node("直连端口"):
                    return {"ok": False,
                            "弃答": "没教过《直连端口》——http 这步走不了。"}
                return self._http_spec(spec["http"])
            if _combo[0] == "对账":
                # 对账步进路由（v0.2.53，组合器步单用）：{日期}——真对账走
                # 《对账执行》同一条 core（_recon_rule），不另起第二条路。
                # 没教过《对账执行》⇒ 弃答（I5）；没日期不猜默认日。
                if not self._route_node("对账执行"):
                    return {"ok": False,
                            "弃答": "没教过《对账执行》——对账这步走不了。"}
                _ds = spec["对账"]
                _d = str((_ds or {}).get("日期") or "").strip() if isinstance(_ds, dict) else ""
                if not re.match(r"\d{4}-\d{2}-\d{2}$", _d):
                    return {"ok": False,
                            "弃答": "对账步要「日期」（YYYY-MM-DD）——不猜默认日。"}
                return self._recon_rule("执行一次对账，日期 " + _d)
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
            # ── v0.2.66（用户拍板，docs/历史与排序_20261111.md §3.1/§6-A）：
            #    触发收窄成「**本题的支**至少一支带倾向」。原判据是「账本非空」——
            #    一条与本题无关的历史就把整表翻成 usage，零倾向支从**切分序**
            #    翻成 did 字典序（实测 23 题里 16 道是这种假翻转：标签变了、
            #    内容没变）。排序改三档稳定 key：
            #      正倾向抬前（按 -倾向，原序破并列）→
            #      零倾向居中、**保原切分序** →
            #      负倾向压最末（保 §2.5 实测「判错的支掉到最末」）。
            #    `ordered_by=usage` 恢复字面义「本题的次序被历史动过」。
            #    ⚠️ 只改次序不改集合：answer_ids/n_answer 排序前后逐字相同
            #       （判据 `[接入] ③` 仍守）。
            _tended = ({n["did"] for n in nodes} & set(self._tendency)
                       if self._tendency else set())
            if _tended and len(nodes) > 1:
                _orig = {n["did"]: i for i, n in enumerate(nodes)}  # 原切分序
                def _order_key(n):
                    t = self._tendency.get(n["did"])
                    i = _orig[n["did"]]
                    if t is None:
                        return (1, 0.0, i)      # 没历史：居中，保原切分序
                    if t > 0:
                        return (0, -t, i)       # 正倾向：抬前
                    return (2, t, i)            # 负倾向：压最末
                nodes = sorted(nodes, key=_order_key)
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
                # ★ 本题带倾向的支数（v0.2.66，用户拍板修 §3.3）：**只报计数、
                #    不报分数**（分数属协议禁词那类）。单支题没有「先看哪支」，
                #    `ordered_by` 仍是 default，但这一格能看出「这题有历史」——
                #    读数字面与事实对齐。I5：删掉它不影响上面任何读数。
                "tended_branches": len(_tended),
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
                # ③ 合取 0 命中 ⇒ 析取回退数据（v0.2.64，只算不改：answer_ids
                #    不动，回退并集单独一格；只认 require 键，≥2 键才回退）
                "析取回退": (self._disjunctive(req, forb)
                             if (not ans and len(req) >= 2) else None),
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

    def _write_rule(self) -> str:
        """语料里教过《写操作不重发》没有（v0.2.36，用户 2026-11-10「不重发」）。

        空串 = 没教过 ⇒ 写操作也照《对方故障有界重试》重发（v0.2.35 行为，I5）。
        教过 ⇒ 非 GET 一律只发一次：对方可能已经受理，重发就是重复提交。
        ★ 这条与「读能不能重发」分开授权：读无副作用，写一次就够。
        """
        for nid, n in self.nodes.items():
            if str((n.fields or {}).get("name") or "") == "写操作不重发":
                return nid
        return ""

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

    def _fill_rule(self) -> str:
        """语料里教过《值入形态》没有。空串 = 没教过 ⇒ `填` 不生效（与上一版逐字相同）。"""
        for nid, n in self.nodes.items():
            if str((n.fields or {}).get("name") or "") == "值入形态":
                return nid
        return ""

    # ── 不知道（v0.2.34）─────────────────────────────────────────────────
    # 不验真假是设计，**不是可以不说**的理由：值从外面来，真假我说不知道；
    # 路只走教过的，没教过的说「不知道有没有」，不说「没有」。
    # 规矩全在 conc/552《不知道》；这里是算子：把三种不知道摊成条目。
    def _unknown_rule(self) -> str:
        """语料里教过《不知道》没有。空串 = 没教过 ⇒ 两个字段都不出现（与上一版逐字相同）。"""
        for nid, n in self.nodes.items():
            if str((n.fields or {}).get("name") or "") == "不知道":
                return nid
        return ""

    @staticmethod
    def _unknown_item(kind: str, what: str, why: str, who: str) -> dict:
        """一条「不知道」：不知道什么 / 哪一类（值·路·判）/ 凭什么说到这 / 谁能补。

        四件缺一件就成了「不知道」三个字——那是把判断推回给人，却什么都没帮他。
        """
        return {"不知道什么": what, "哪一类": kind,
                "我凭什么说到这": why, "谁能补": who}

    _SLOT_PAT = (
        ("编号", r"[A-Z]{2,6}-\d{3,}"),
        ("日期", r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2})?"),
        ("百分数", r"\d+(?:\.\d+)?\s*%"),
        ("量纲值", r"\d+(?:\.\d+)?\s*(?:σ|km/s/Mpc|e-11|e-12)"),
        ("小数", r"\d+\.\d+"),
    )

    @classmethod
    def _shape_slots(cls, text) -> list:
        """把边的前提里**认得出的值位**摊出来（只认形态，不改句子）。

        认不出类型的那部分不列——宁可少列，不替人把一句话拆错。
        """
        import re as _re
        out, taken = [], []
        for name, pat in cls._SLOT_PAT:
            for m in _re.finditer(pat, str(text or "")):
                a, b = m.span()
                if any(not (b <= x or a >= y) for x, y in taken):
                    continue
                taken.append((a, b))
                out.append({"类": name, "原值": m.group(0).strip()})
        return out

    def _apply_fill(self, edges, fill):
        """把外面给的值**填进我自己的边**——只换值，措辞一个字不动。

        返回 (edges, 填入记录, 错误说明)。错误非空 ⇒ 这种填法不认识，不猜。
        """
        pairs = []
        if isinstance(fill, dict):
            fill = [fill]
        for it in (fill or []):
            if isinstance(it, dict) and str(it.get("原") or "") and "成" in it:
                pairs.append((str(it["原"]), str(it["成"])))
            elif isinstance(it, (list, tuple)) and len(it) == 2:
                pairs.append((str(it[0]), str(it[1])))
        if not pairs:
            return edges, [], ('`填` 要写成 [{"原": 旧值, "成": 新值}] —— '
                               '只给值，不给句子；句子我这边有')
        rec = []
        for old, new in pairs:
            hits = []
            for e in edges:
                for side in ("前提", "结论"):
                    if old and old in e[side]:
                        e[side] = e[side].replace(old, new)
                        hits.append(f"{e['出自']}·{side}")
            rec.append({"原值": old, "新值": new, "命中": sorted(set(hits)),
                        "几处": len(hits)})
        return edges, rec, ""

    # ── 起点来源（v0.2.38）──────────────────────────────────────────────
    # 边是我教过的**接法**，起点是外面递进来的**观测**——两件事，可信度不同。
    # 上一版 `诚实` 写「全部由语料原文逐跳得来」，把起点也算成了语料原文，是过头话。
    # 规矩全在 conc/560《起点来源》；这里是算子：一条起点一行，只标，不打分。
    def _source_rule(self) -> str:
        """语料里教过《起点来源》没有。空串 = 没教过 ⇒ 字段不出现（与上一版逐字相同）。"""
        for nid, n in self.nodes.items():
            if str((n.fields or {}).get("name") or "") == "起点来源":
                return nid
        return ""

    def _start_source(self, starts, *, by_x, into, hint="") -> list:
        """每条起点标四件：谁给的 / 形对不对得上 / 真不真（对不上时加一句说明）。

        只交两件查得动的事：这句话是谁递进来的（任务书，还是上一段跑出来的），
        以及它在我教过的边里出没出现过。真假不归我判。
        """
        keys = set(by_x) | set(into)
        out = []
        for s in starts:
            fit = "对得上" if self._infer_key(s) in keys else "对不上"
            item = {"起点": s, "谁给的": str(hint or "调用方"),
                    "形对不对得上": fit, "真不真": "我不知道"}
            if fit == "对不上":
                item["说明"] = ("语料里没有这句话——我只按整句接住，"
                                "不拆、不补、不猜它是不是真的")
            out.append(item)
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
            out = {"ok": False, "依据": [{"原文": f"《推演的路》（{rule_id}）",
                                          "可复算": "语料里 形态: 推演 的节点为零"}],
                   "报缺": [{"缺什么": "推演的边", "哪一类": "缺内容",
                             "对方原话": "语料里没有 形态: 推演 的节点",
                             "要谁给": "教导方：按《推演的路》四条字段写跳",
                             "给了怎么用": "落成 形态: 推演 节点后本条即可走"}],
                   "弃答": "路是空的：一条边都没教过，走不了。"}
            _u = self._unknown_rule()
            if _u:
                out["不知道"] = [self._unknown_item(
                    "路", "这条语料之外有没有这样的边",
                    "我这里一条 形态: 推演 的节点都没有——不是「不存在」，是「没教过」",
                    "教导方按《推演的路》（conc/538）写跳；写了才有")]
                out["诚实"] = "我一条边都没有，所以什么都没推；不是推了没结果。"
            return out

        # ── 值入形态（v0.2.33）：外面只给值，措辞由我这边出 ──────────────
        # 没教过《值入形态》⇒ 这段整块不执行，行为与 v0.2.32 逐字相同。
        # ⚠️ 但**教过《不知道》**时，"收下没用"这话必须说出口（v0.2.34）：
        #    静默丢掉外面给的值，等于拿旧值冒充新值——比答错更坏。
        fill_id = self._fill_rule()
        unk_id = self._unknown_rule()
        filled, fill_said, unknown = [], "", []
        src_id = self._source_rule()          # v0.2.38：教过才标（I5）
        _hint = str(spec.get("_从来源") or "")  # 组合回填时由 _compose 打上，指明第几段
        given = spec.get("填")
        if given and not fill_id and unk_id:
            _n_given = len(given) if isinstance(given, list) else 1
            return {"ok": False,
                    "不知道": [self._unknown_item(
                        "值", f"你给的这 {_n_given} 个值该填到哪一位",
                        "《值入形态》没教过——我不会把值换进句子，也没学过该换哪一侧",
                        "教导方：教一遍《值入形态》（conc/551），或直接把整句起点给我")],
                    "诚实": "你给了值，我一条都没用上；这一条不会拿旧值冒充你给的值。",
                    "弃答": "值我收下了，可我不会填——不拿原值冒充新值，先停在这儿。"}
        if fill_id and given:
            edges, filled, fill_err = self._apply_fill(edges, given)
            if fill_err:
                return {"ok": False, "弃答": fill_err}
            if not any(r["几处"] for r in filled):
                fill_said = ("（给的值在我教过的边里**找不到位**——不新建位、不硬塞，"
                             "下面照旧报缺）")
            else:
                fill_said = "（值已按你说的填进边里：措辞一个字没动，只换了值）"
                if unk_id:
                    for r in filled:
                        if not r["几处"]:
                            continue
                        unknown.append(self._unknown_item(
                            "值",
                            f"「{r['新值']}」是否成立（它是不是真的、对方账上是不是这个数）",
                            f"我只按《值入形态》把它换进 {r['几处']} 处句子，没查过它的真假",
                            "观测方：给这个值的凭据；我不验真假，只保证换了值、没动措辞"))
                    for s in starts:
                        for r in filled:
                            if r["几处"] and r["原值"] in s and r["新值"] not in s:
                                unknown.append(self._unknown_item(
                                    "值", "起点要不要跟着换",
                                    f"你给的起点还是旧值「{r['原值']}」，边里已换成"
                                    f"「{r['新值']}」——两边对不上，所以下面走不动",
                                    "你说一句：起点也换，我就一起换；不换就照旧报缺"))

        by_x, into = {}, {}
        for e in edges:
            by_x.setdefault(self._infer_key(e["前提"]), []).append(e)
            into.setdefault(self._infer_key(e["结论"]), []).append(e)

        srcs = (self._start_source(starts, by_x=by_x, into=into, hint=_hint)
                if src_id else [])

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
            out = {"ok": True, "从": starts, "到": goal, "跳数": len(path),
                   "路径": [{"前提": e["前提"], "规则": e["规则"], "结论": e["结论"],
                             "依据": e["依据"], "链": e["链"], "出自": e["出自"]}
                            for e in path],
                   "可达": sorted(seen.values())[:20],
                   "依据": base + [{"原文": f"{e['出自']}：{e['依据']}", "可复算": e["规则"]}
                                   for e in path],
                   "note": "逐跳可复核；哪一跳最弱由依据决定，不由我打分"}
            if filled:
                out["填入"] = filled
                out["填值来源"] = (f"由《值入形态》（{fill_id}）代入：**值来自外部，"
                                   f"这一条起点不是语料原文**；措辞原样，只换了值")
                out["note"] += "；填进来的值不是推出来的，读的人要看得见"
            if unk_id:
                if unknown:
                    out["不知道"] = unknown
                _hit = sum(r["几处"] for r in filled)
                _pairs = "、".join(f"{r['原值']}→{r['新值']}" for r in filled if r["几处"])
                out["诚实"] = (f"这条结论里有 {_hit} 处不是语料原文（{_pairs}）："
                               f"值是我换进去的，真假我没验——它成立不成立，我不说知道。"
                               if _hit else
                               "这条结论全部由语料原文逐跳得来，值没有外部代入。")
            if srcs:
                # v0.2.38：起点不是推出来的，是外面递进来的——单独摊开，不算进「语料原文」。
                out["起点来源"] = srcs
                if unk_id:
                    _n = len(srcs)
                    _bad = [x for x in srcs if x["形对不对得上"] == "对不上"]
                    _tail = (f"起点 {_n} 条都不是我推出来的（是递进来的观测），"
                             f"真不真我不知道"
                             + (f"；其中 {len(_bad)} 条连形都对不上"
                                f"（{'、'.join(str(x['起点'])[:12] for x in _bad)}）"
                                if _bad else ""))
                    out["诚实"] = ((out["诚实"].rstrip("。") + "。" + _tail + "。")
                                   if _hit else
                                   ("这条结论的边是语料原文逐跳来的；" + _tail + "——"
                                    "边是接法，起点是观测，这两件事不共用一句话。"))
            return out

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
            item = {"缺什么": e["前提"], "哪一类": "缺内容",
                    "对方原话": f"要「{goal}」，得先有它，这是《{e['规则']}》的前提",
                    "要谁给": "观测方或教导方（补这一跳）",
                    "缺的形态": kind, "给了怎么用": how,
                    "依据": e["依据"], "出自": e["出自"]}
            # 这条缺口是不是**我自己换值换出来的**（v0.2.34）：
            # 起点还是旧值、边已换新值 ⇒ 不是外面欠我一个值，不许报成外部缺口。
            if unk_id and any(r["几处"] and r["新值"] and r["新值"] in item["缺什么"]
                              for r in filled):
                item["哪一类"] = "自填不一致"
                item["本人换的"] = True
                item["给了怎么用"] = ("这条缺口是我自己换值换出来的：起点还是旧值，边已换新值。"
                                      "不是你欠我一个值——要接上，把 `从` 也换成新值")
            if fill_id:
                # 教过《值入形态》才摊：**这条缺口的入边长什么样**、哪个位置能填值。
                # 缺口本身是起点观测（没有入边）时，摊的就是它自己那句话。
                feed = into.get(self._infer_key(e["前提"])) or []
                shapes = [x["前提"] for x in feed] or [e["前提"]]
                slots, seen_slot = [], set()
                for s in shapes:
                    for sl in self._shape_slots(s):
                        k = (sl["类"], sl["原值"])
                        if k not in seen_slot:
                            seen_slot.add(k)
                            slots.append(sl)
                item["入边形态"] = shapes[0] if len(shapes) == 1 else shapes
                item["值位"] = slots
                item["怎么填"] = ('把「值位」里的原值换成你手上那个值：'
                                  '{"填": [{"原": <原值>, "成": <新值>}]}——句子不用你写')
            miss.append(item)
        if not need:
            out = {"ok": False, "从": starts, "到": goal,
                   "可达": sorted(seen.values())[:20], "依据": base,
                   "报缺": [{"缺什么": f"到「{goal}」的任何一条边", "哪一类": "缺内容",
                             "对方原话": "语料里没有以它为结论的跳",
                             "要谁给": "教导方（要么补这一跳，要么承认推不过去）",
                             "给了怎么用": "补边后再问；不补就只能说『没有这条路』"}],
                   "弃答": f"到不了「{goal}」：没有哪条教过的边以它作结论。"
                           f"上面这些是我从起点走得到的，不多不少。" + fill_said}
            if unk_id:
                out["不知道"] = [self._unknown_item(
                    "路", f"「{goal}」在别处有没有来路",
                    "我只知道我教过的边；这条语料里没有哪条边以它作结论",
                    "教导方补这一跳，或观测方换个说法问")]
                out["诚实"] = ("说「到不了」，是因为我这里没有这条边；"
                               "不等于世上没有这条路——没教过的路，我说不知道。")
            if filled:
                out["填入"] = filled
            if srcs:
                out["起点来源"] = srcs   # v0.2.38：报缺时也标——起点是收下的，不算对方欠账
            return out
        out = {"ok": False, "从": starts, "到": goal,
               "可达": sorted(seen.values())[:20], "报缺": miss, "依据": base,
                "弃答": f"到不了「{goal}」：还得再有几个前提之一（共 {len(miss)} 条），"
                        f"缺的那几条在报缺单里——不猜、不跳。" + fill_said}
        if unk_id:
            _self = [m for m in miss if m.get("本人换的")]
            _real = [m for m in miss if not m.get("本人换的")]
            _unk = list(unknown)
            for m in _real:
                if m.get("缺的形态") == "缺一个观测值":
                    _unk.append(self._unknown_item(
                        "值", f"「{m['缺什么']}」是不是真的",
                        "它在我教过的边里没有来路，我只能当它是一次观测",
                        "观测方给凭据；我不验真假，只把它当起点用"))
            if _real:
                _unk.append(self._unknown_item(
                    "路", f"「{goal}」还有没有别的来路",
                    f"我只找到 {len(need)} 条以它作结论的边，其余不知道",
                    "教导方补边；不补就只能走到报缺单为止"))
            if _unk:
                out["不知道"] = _unk
            if _self and not _real:
                out["弃答"] = ("走不动，但不是缺东西：我按你给的值把边换了，起点还是旧值，"
                               "两边对不上——要不要连起点一起换，你说一句。" + fill_said)
            out["诚实"] = (
                ("这条缺口是我自己换值换出来的：起点没换、边换了，两边对不上——"
                 "不是外面少给我一个值。") if _self and not _real else
                (f"报缺单里有 {len(_real)} 条是真缺口；其中标「缺一个观测值」的那几条，"
                 f"值真不真我不知道，我只知道它们没有来路。")
                if _real else "没有缺口：这一段我按教过的边走完了。")
        if filled:
            out["填入"] = filled
        if srcs:
            out["起点来源"] = srcs       # v0.2.38：报缺时也标
        return out

    # ── 组合路由（v0.2.29）──────────────────────────────────────────────
    # 「把上一步的结论带回入口，继续找下一步」：一步一段，**每段都重新过 query 入口**。
    # 规矩在 conc/545《结论回入口》；这里是**算子**：填占位符、过入口、取结论、停手。
    # ⚠️ 结论按**原文**回填，一个词不改——结论里带成立范围，删掉限定语就变成另一个主张。
    _STEP_MARK = "«上一步»"
    _COMBO_MAX = 5
    _STEP_ROUTES = ("derive", "solve", "moral", "分析", "成文", "变形", "反义", "主题",
                    "造句", "评估", "端口", "借算", "mongo", "接口定义", "借阅",
                    "查询", "外查", "装配", "过程", "差距", "建议", "推演", "结算", "取",
                    "验收", "http", "对账")

    def _combo_rule(self) -> str:
        """语料里教过《结论回入口》没有。空串 = 没教过 ⇒ 组合路由不成立。"""
        for nid, n in self.nodes.items():
            if str((n.fields or {}).get("name") or "") == "结论回入口":
                return nid
        return ""

    def _step_conclusion(self, r) -> tuple:
        """这一段跑出来的**那一次结论**，返回 (原文, 取自哪里)。

        路由的结果就是一次结论——推演的结论、端口的回执、装配的单子、
        差距的清单，**都是这一步跑出来的东西，不按形状分等级**。所以只在
        「弃答」（ok=False）时才算没结论；跑成了，返回什么就带走什么：

        - 有「结论」字段（或路径末跳的「结论」）⇒ 取它，逐字；
        - 没有 ⇒ **整个返回原样序列化**，不摘要、不挑字段、不省键。

        带走了接不接得上，由下一段自己的规矩判——我不预筛。
        """
        if not isinstance(r, dict) or r.get("ok") is False:
            return "", ""
        path = r.get("路径")
        if isinstance(path, list) and path and isinstance(path[-1], dict):
            c = str(path[-1].get("结论") or "").strip()
            if c:
                return c, "路径末跳的「结论」"
        c = r.get("结论")
        if isinstance(c, str) and c.strip():
            return c.strip(), "「结论」字段"
        return json.dumps(r, ensure_ascii=False), "整个返回（原样序列化）"

    def _step_has_mark(self, obj) -> bool:
        if isinstance(obj, str):
            return self._STEP_MARK in obj
        if isinstance(obj, (list, tuple)):
            return any(self._step_has_mark(x) for x in obj)
        if isinstance(obj, dict):
            return any(self._step_has_mark(v) for v in obj.values())
        return False

    def _step_fill(self, obj, prev: str, segs=None):
        """把占位符换成结论原文（逐字替换，不做任何改写）。

        v0.2.35：教过《分段引用》⇒ 另认 `«第N段»`（第 N 段跑出来的那一块），
        仍然**整段原文**搬过去，不取字段、不摘要。
        """
        if isinstance(obj, str):
            s = obj.replace(self._STEP_MARK, prev)
            if segs is not None and self._ref_rule():
                for i, d in enumerate(segs, 1):
                    s = s.replace(f"«第{i}段»", str(d.get("得到") or ""))
            return s
        if isinstance(obj, (list, tuple)):
            return [self._step_fill(x, prev, segs) for x in obj]
        if isinstance(obj, dict):
            return {k: self._step_fill(v, prev, segs) for k, v in obj.items()}
        return obj

    _REF_MARK = "«第{}段»"

    @classmethod
    def _step_refs(cls, obj) -> set:
        """这一段引用了前面第几段（`«第N段»`）。"""
        import re as _re
        out = set()

        def walk(x):
            if isinstance(x, str):
                for m in _re.finditer(r"«第(\d+)段»", x):
                    out.add(int(m.group(1)))
            elif isinstance(x, (list, tuple)):
                for y in x:
                    walk(y)
            elif isinstance(x, dict):
                for y in x.values():
                    walk(y)
        walk(obj)
        return out

    def _ref_rule(self) -> str:
        """语料里教过《分段引用》没有。空串 = 没教过 ⇒ 只认 `«上一步»`（与上一版逐字相同）。"""
        for nid, n in self.nodes.items():
            if str((n.fields or {}).get("name") or "") == "分段引用":
                return nid
        return ""

    def _finish_rule(self) -> str:
        """语料里教过《什么叫结束》没有。空串 = 没教过 ⇒ 组合照 v0.2.30 跑。"""
        for nid, n in self.nodes.items():
            if str((n.fields or {}).get("name") or "") == "什么叫结束":
                return nid
        return ""

    def _finish(self, yao: str, done: list, *, stopped_at=None, 验收=None) -> dict:
        """收尾三样：任务书要什么 / 手里现在是什么 / 凭什么这么判。

        只判两件**确定**的事，不替人判语义（语义归人，见《什么叫结束》conc/546）：

        - 末段弃答 ⇒ **没到**——这是确定的，不用看内容；
        - 走满 5 段 ⇒ **用完了**，不是完成——段数用完是过程，不是结果；
        - 其余 ⇒ **判不了**：三样摊开，拿任务书那一句对。

        跑完 ≠ 做成了。这里是「跑完了」与「做成了」分开写的地方。

        v0.2.35：任务书另给了「验收」条目时，教过《回单验收》就**逐条核回单**，
        `到了没` 以核对结果为准（段数用完不再是终点）——条目是任务书给的，不是我挑的。
        """
        last = (done or [{}])[-1]
        hand = str(last.get("得到") or "")
        # v0.2.39：`手里是` 仍按《什么叫结束》= **末段交出的东西**，口径不动；但末段弃答时，
        # 前面段真拿到的结论不能跟着消失（N1：第 1 段开出 RF-0001，第 2 段弃答后只剩弃答句，
        # 读的人以为啥也没办成）。另记一份 `之前交出的`，只搬不改。
        held = [{"序": d.get("序"), "路由": d.get("路由"), "得到": d.get("得到")}
                for d in (done or []) if d.get("交出结论")]

        def _mark(o):
            if not last.get("交出结论") and held:
                o["之前交出的"] = held
            return o

        if stopped_at is not None:
            return _mark({"任务书要": yao, "手里是": hand, "到了没": "没到",
                          "凭什么": (f"第 {stopped_at} 段弃答，后面的段没走——"
                                     f"任务书要的东西没有到手")})
        if len(done) >= self._COMBO_MAX:
            out = {"任务书要": yao, "手里是": hand, "到了没": "用完了",
                   "凭什么": (f"走满 {self._COMBO_MAX} 段是段数用完，不是完成；"
                              f"要的东西兑没兑现，得拿任务书那一句对")}
            if 验收 is not None and self._accept_rule():
                v = self._accept({"在": hand, "核": 验收})
                out["验收"] = v
                if v.get("ok"):
                    out["到了没"] = v["到了没"] + "（段数用完了，但回单逐条核过）"
                    out["凭什么"] = (f"照《回单验收》逐条核回单：共 {len(v['逐条'])} 条，"
                                     f"没过 {len(v['没过的'])} 条、"
                                     f"回单没写 {len(v['回单没写的'])} 条")
            return _mark(out)
        out = {"任务书要": yao, "手里是": hand, "到了没": "判不了",
               "凭什么": ("末段交出来的东西兑没兑现任务书那一句，我不替你拍；"
                          "三样摊在这儿，对不上就按《什么叫结束》报缺")}
        if 验收 is not None and self._accept_rule():
            v = self._accept({"在": hand, "核": 验收})
            out["验收"] = v
            if v.get("ok"):
                out["到了没"] = v["到了没"]
                out["凭什么"] = (f"照《回单验收》逐条核回单：共 {len(v['逐条'])} 条，"
                                 f"没过 {len(v['没过的'])} 条、回单没写 {len(v['回单没写的'])} 条")
            else:
                out["到了没"] = "核不了"
                out["凭什么"] = str(v.get("弃答") or "回单不像结构化的，核不了")
        return _mark(out)

    def _after_rule(self) -> str:
        """语料里教过《结束之后》没有。空串 = 没教过 ⇒ 结束只出三样（与 v0.2.31 逐字同）。"""
        for nid, n in self.nodes.items():
            if str((n.fields or {}).get("name") or "") == "结束之后":
                return nid
        return ""

    def _after(self, fin, *, stopped_at=None, done=None) -> dict:
        """结束之后：三态各一条出口，都是**把问题交出去**，不是自己再跑一遍。

        规矩在《结束之后》conc/547，这里只做**分派与摘录**：按「到了没」选出口、
        把已有的原话填进单子，不补内容、不判语义、不给新路。
        """
        fin = fin if isinstance(fin, dict) else {}
        state = str(fin.get("到了没") or "").strip()
        yao = str(fin.get("任务书要") or "")
        hand = str(fin.get("手里是") or "")
        why = str(fin.get("凭什么") or "")
        last = (done or [{}])[-1]
        out = {"态": state, "任务书要": yao, "手里是": hand, "凭什么": why}
        # v0.2.39：结束里那份「之前交出的」照搬过来——下一步要接的东西，往往正是它。
        if fin.get("之前交出的"):
            out["之前交出的"] = fin["之前交出的"]

        if state == "没到":
            # 弃答原话优先取那一段的「得到」；单独问「后续」时没有段，就取手里的东西
            #（停下的那一段交出来的就是它）。
            said = str(last.get("得到") or "") or hand
            at = stopped_at if stopped_at is not None else last.get("序")
            # 弃答卷里已经写明类别时**照抄**（「缺 N 条客观事实（缺X：Y）」）——
            # 摘录不是推理；抄不到才写「说不清哪一类」（conc/536 的规矩）。
            kind = "说不清哪一类"
            import re as _re_after
            _m = _re_after.search(r"（(缺[^：:）]{1,12})[：:]", said)
            if _m:
                kind = _m.group(1).strip()
            out.update({
                "动作": "报缺",
                "谁接": "补得上这一条的人",
                "拿什么接": [{
                    "缺什么": said or "对方没说（弃答只说没走通）",
                    "哪一类": kind,
                    "对方原话": said or "对方没说",
                    "要谁给": "补得上这一条的人",
                    "给了怎么用": (f"从第 {at} 段原样重问，起点换成补上的那条" if at is not None
                                   else "从停下的那一段原样重问，起点换成补上的那条"),
                }],
                "接完从哪进": (f"第 {at} 段" if at is not None else "停下的那一段"),
                "不许": ["自己补一条", "换个起点再跑一遍", "跳过这一段往下走"],
            })
        elif state == "用完了":
            out.update({
                "动作": "停手交人",
                "谁接": "拿着任务书的那个人",
                "拿什么接": {"任务书要": yao, "手里是": hand},
                "接完从哪进": ("不从这里进——段数已经用完；要接着走，得人先补东西或者改任务书"),
                "不许": ["为了凑完成再加段", "把「换条路再跑一遍」当后续",
                         "把手里的东西摘要成一句好话"],
            })
        elif state == "判不了":
            out.update({
                "动作": "问一句人话",
                "谁接": "拿着任务书的那个人",
                "拿什么接": {"任务书要": yao, "手里是": hand},
                "要人答": f"「{yao}」这一句，手里这份东西兑没兑现？",
                "接完从哪进": "人答「兑现」算到了；答「没兑现」按没到报缺",
                "不许": ["自己给「差不多」的判", "人没答就自己再跑一遍换一个结果",
                         "替人把任务书改宽", "拿手里的东西当答案"],
            })
        else:
            out["弃答"] = ("「到了没」不认识——结束只出 没到 / 用完了 / 判不了 三种；"
                          "不认识就不猜下一步。")
        return out

    def _after_route(self, spec) -> dict:
        """独立路由 `{"后续": …}`：结束已经判过，问下一步做什么。"""
        rule_id = self._after_rule()
        if not rule_id:
            return {"ok": False,
                    "弃答": "没有《结束之后》——结束了下一步做什么，我没学过，不自己编。"}
        if not isinstance(spec, dict):
            return {"ok": False, "弃答": "「后续」要给结束那三样，或者整块「结束」。"}
        fin = spec.get("结束") if isinstance(spec.get("结束"), dict) else spec
        r = self._after(fin)
        if not r.get("动作"):
            return {"ok": False, **r}
        return {"ok": True, **r,
                "依据": [{"原文": f"《结束之后》（{rule_id}）",
                          "可复算": "三态各一条出口，都在「动作 / 谁接 / 拿什么接 / 不许」里"}]}

    def _compose(self, spec) -> dict:
        """把一串步骤首尾相接：上一段的结论 → 下一段的入口。

        - 每段都必须是一条路由的 spec（一个键）；`组合` 不套 `组合`；
        - 上一段的结论**原文**替换下一段里的 `«上一步»`，别的一个字不动；
        - 路由的结果就是一次结论：推演的结论、回执、单子、清单都带走，不挑形状；
        - 哪一段弃答，就停在那一段——不带着空值往下，也不拿「我知道的」填空；
        - 最多 5 段（与《推演的路》5 跳同一上限），超了报「分段给」。
        """
        rule_id = self._combo_rule()
        if not rule_id:
            return {"ok": False,
                    "弃答": "没有《结论回入口》——上一步的结论怎么带回入口，我没学过，不硬串。"}
        steps = spec.get("步") if isinstance(spec, dict) else None
        if isinstance(steps, dict):
            steps = [steps]
        if not isinstance(steps, list) or not steps:
            return {"ok": False,
                    "弃答": "组合要「步」——一段一条路由，按先后排好。起点的名字由你给，不由我编。"}
        if len(steps) > self._COMBO_MAX:
            return {"ok": False,
                    "弃答": (f"要 {len(steps)} 段，超过 {self._COMBO_MAX} 段——路太长，分段给，"
                             f"不硬并。")}

        # ── 先整串验形状，一段都不许跑：端口/外查那些段有真副作用，
        #    「第 3 段才不合格」不该让前两段先发出去。
        for i, st in enumerate(steps, 1):
            if not isinstance(st, dict) or len(st) != 1:
                return {"ok": False, "段": [], "停在哪": i,
                        "弃答": (f"第 {i} 段要一条路由的 spec（只一个键），"
                                 f"现在给的是 {list(st) if isinstance(st, dict) else type(st).__name__}。")}
            route = next(iter(st))
            if route not in self._STEP_ROUTES:
                return {"ok": False, "段": [], "停在哪": i,
                        "弃答": (f"第 {i} 段的路由「{route}」不认识——组合只串已有的路由，"
                                 f"不发明新路由。可用：{list(self._STEP_ROUTES)}")}
            if i == 1 and self._step_has_mark(st):
                return {"ok": False, "段": [], "停在哪": i,
                        "弃答": (f"第 {i} 段用了 {self._STEP_MARK}，但它上面没有结论可取——"
                                 f"占位符只在第 2 段起才成立。")}
            # v0.2.35：`«第N段»` 也只许指**已经跑过的**段；没教过《分段引用》就不认这个写法。
            _refs = self._step_refs(st)
            if _refs:
                if not self._ref_rule():
                    return {"ok": False, "段": [], "停在哪": i,
                            "弃答": (f"第 {i} 段用了「第{max(_refs)}段」——这个写法我没学过"
                                     f"（只有《结论回入口》的 {self._STEP_MARK}）。")}
                if max(_refs) >= i:
                    return {"ok": False, "段": [], "停在哪": i,
                            "弃答": (f"第 {i} 段引用了第 {max(_refs)} 段——它还没跑到，"
                                     f"占位符不成立。")}

        # ── 什么叫结束（v0.2.31）：教过才要求「要」——不给就不开跑。
        #    这是「结束」能被判的前提：不知道要什么，谈不上结束。
        fin_id = self._finish_rule()
        yao = str(spec.get("要") or "").strip() if isinstance(spec, dict) else ""
        if fin_id and not yao:
            return {"ok": False, "段": [],
                    "弃答": ("《什么叫结束》：任务书要先说「要」什么——不知道要什么，"
                             "我没法说结束，也不替你编一个。")}

        done, prev = [], ""
        last = len(steps)
        for i, st in enumerate(steps, 1):
            route = next(iter(st))
            spec_i = self._step_fill(st, prev, done)
            # v0.2.38：这一段起点是上一段跑出来的，不是外面新递的——标上是第几段、什么路由
            if (route == "推演" and done and self._step_has_mark(st) and self._source_rule()
                    and isinstance(spec_i.get(route), dict)):
                spec_i[route]["_从来源"] = (f"组合上一步（第{done[-1]['序']}段 "
                                            f"{done[-1]['路由']}）")
            r = self.query(spec_i)
            concl, where = self._step_conclusion(r)
            ran = isinstance(r, dict) and r.get("ok") is not False
            got = concl or (r.get("弃答") if isinstance(r, dict) else "")
            done.append({"序": i, "路由": route,
                         "填入": spec_i.get(route),
                         "得到": got,
                         "走通": ran, "交出结论": bool(concl),
                         "结论取自": where,
                         "依据": (r.get("依据") if isinstance(r, dict) else None)})
            # v0.2.39：端口那一路的留痕原样带走——`改动`（按回执改了哪个写法，旧 → 新）与
            # `尝试`（发了几次、每次的 status 与对方原话）。原先只留在 _port_call 的返回里，
            # 段记录不带 ⇒ 长线里最值钱的一步（它自己按样例改格式重发）在报告里是隐形的。
            if isinstance(r, dict):
                if r.get("改动"):
                    done[-1]["改动"] = r["改动"]
                if r.get("尝试"):
                    done[-1]["尝试"] = r["尝试"]
                # v0.2.39：`查过`（挂了先查的三态与判据）也带走。不加这一条，
                # 写失败那一段交出来的是弃答句，「在/不在/认不准」和它的判据在组合里全看不见。
                if r.get("查过"):
                    done[-1]["查过"] = r["查过"]
            # v0.2.38：每段各标各的起点来源，不汇总——汇总就把「哪一段是外来的」抹掉了
            if isinstance(r, dict) and r.get("起点来源"):
                done[-1]["起点来源"] = r["起点来源"]
            if not ran:
                r_stop = {"ok": False, "段数": len(done), "段": done, "停在哪": i,
                          "停手原因": (f"第 {i} 段（{route}）弃答了——后面 {last - i} 段没走，"
                                       f"不带着空值往下。"),
                          "上一段结论": prev}
                if fin_id:
                    r_stop["结束"] = self._finish(yao, done, stopped_at=i)
                    _aft = self._after_rule()
                    if _aft:      # v0.2.32：结束之后做什么，也由教导授权
                        r_stop["结束"]["后续"] = self._after(r_stop["结束"],
                                                             stopped_at=i, done=done)
                        r_stop["依据"] = [{"原文": f"《结束之后》（{_aft}）",
                                           "可复算": "后续五样：态 / 动作 / 谁接 / 拿什么接 / 接完从哪进"}]
                return r_stop
            prev = concl

        _last_has = bool(done and done[-1]["交出结论"])
        out = {"ok": True, "段数": len(done), "段": done,
               "结论": prev if _last_has else "",
               "最后交出的结论": prev,
               "末段交出结论": _last_has,
               "依据": [{"原文": f"《结论回入口》（{rule_id}）",
                         "可复算": "逐段回它的路由口，按「填入」原样再问一次"}],
               "note": ("结论按原文回填，一字未改；哪一段最弱由该段依据决定，不由我打分。"
                        "推演的结论、端口的回执、装配的单子都一样算一次结论——"
                        "接不接得上由下一段自己的规矩判，我不预筛")}
        if fin_id:
            out["结束"] = self._finish(yao, done, 验收=(spec.get("验收") if isinstance(spec, dict) else None))
            out["依据"].append({"原文": f"《什么叫结束》（{fin_id}）",
                                "可复算": "「结束」三样：任务书要 / 手里是 / 凭什么"})
            out["note"] = (f"跑完不等于做成了——结束判的是「{out['结束']['到了没']}」"
                           f"（{out['结束']['凭什么']}）。" + out["note"])
            _aft = self._after_rule()
            if _aft:          # v0.2.32：结束之后做什么，也由教导授权（三态各一条出口）
                out["结束"]["后续"] = self._after(out["结束"], done=done)
                out["依据"].append({"原文": f"《结束之后》（{_aft}）",
                                    "可复算": "后续五样：态 / 动作 / 谁接 / 拿什么接 / 接完从哪进"})
                out["note"] = f"下一步是「{out['结束']['后续'].get('动作')}」。" + out["note"]
        return out

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
                  **rec_a, "改动": list(fix_log), "解读": read, "ok": ok_a})
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

    # ── 接口目录（v0.2.35）──────────────────────────────────────────────
    # 一个 url 装不下多端点：目录按**名字**给 方法/路径/参数/是否分页；
    # 地址 = 人配的 base + 目录里的路径。规矩在 conc/553《接口目录》。
    # 没教过目录 ⇒ 端口只认 trigger.json 的 url（与上一版逐字相同）。
    def _api_rule(self) -> str:
        for nid, n in self.nodes.items():
            if str((n.fields or {}).get("name") or "") == "接口目录":
                return nid
        return ""

    def _api_table(self) -> dict:
        """把《接口目录》正文里的端点行读成表：名字 → 方法/路径/参数/分页。"""
        import re as _re
        rid = self._api_rule()
        if not rid:
            return {}
        body = str(getattr(self.nodes[rid], "body", "") or "")
        pat = _re.compile(
            r"^-\s*(?P<name>[^｜|]+?)\s*[｜|]\s*(?P<m>GET|POST|PUT|PATCH|DELETE)\s+(?P<path>/\S*)"
            r"(?:\s*[｜|]\s*参数[:：](?P<args>[^｜|]*))?"
            r"(?:\s*[｜|]\s*分页[:：](?P<page>\S+))?\s*$")
        out = {}
        for ln in body.splitlines():
            m = pat.match(ln.strip())
            if not m:
                continue
            out[m.group("name").strip()] = {
                "方法": m.group("m"), "路径": m.group("path"),
                "参数": [x.strip() for x in (m.group("args") or "").split(",") if x.strip()],
                "分页": str(m.group("page") or "").strip()}
        return out

    def _api_cfg(self) -> dict:
        import json as _j
        from pathlib import Path as _P
        nd = self._nodes_dir()
        base = (_P(self._corpus_root) if hasattr(self, "_corpus_root")
                else (nd.parent if nd else _P(".")))
        p = base / "trigger.json"
        try:
            return _j.loads(p.read_text(encoding="utf-8") or "{}") if p.is_file() else {}
        except ValueError:
            return {}

    @staticmethod
    def _http_json(method: str, url: str, body=None, *, timeout: float = 5.0):
        """发一次请求，返回 (status, obj|text, err)。4xx/5xx 也是回执，当回执收。"""
        import json as _j
        import urllib.error as _ue
        import urllib.request as _u
        data = None
        headers = {}
        if body is not None:
            data = _j.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        try:
            _op = _u.build_opener(_u.ProxyHandler({}))
            req = _u.Request(url, data=data, headers=headers, method=method)
            with _op.open(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8", "replace")
                code = int(getattr(r, "status", 200))
        except _ue.HTTPError as he:
            try:
                raw = he.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                raw = ""
            code = int(getattr(he, "code", 0) or 0)
            err = ""
        except Exception as exc:  # noqa: BLE001
            return 0, "", f"{type(exc).__name__}: {str(exc)[:100]}"
        try:
            return code, _j.loads(raw), ""
        except ValueError:
            return code, raw, ""

    def _verify_rule(self) -> str:
        """语料里教过《挂了先查再定》没有。空串 = 没教过 ⇒ 写失败不查（与 v0.2.36 逐字相同）。"""
        for nid, n in self.nodes.items():
            if str((n.fields or {}).get("name") or "") == "挂了先查再定":
                return nid
        return ""

    def _api_fail(self, name, ent, code, obj, err, attempts, read, *,
                  write=False, write_rule="", retry_ok=True) -> dict:
        """把「没办成」说成人话（v0.2.36）：措辞按结论分，缺客观事实就出报缺单。

        4xx 是我方报文的问题（「哪一项不对由它说」）；
        5xx/连接失败是对方自己挂了（「不是报文的问题」）——两者不能混用一句话。
        v0.2.35 把这两句并成了一句，503 也在问「哪一项不对」，等于替对方认了个它没犯的错。
        """
        out = {"ok": False, "接口": name, "方法": ent.get("方法")}
        if code:
            out["状态"] = code
        if err:
            out["发不出去"] = err
        else:
            out["回执"] = obj
        out["尝试"] = attempts
        n = len(attempts)
        concl = str((read or {}).get("结论") or "")
        tail = ""
        if write and write_rule and n == 1 and (err or int(code or 0) >= 500):
            tail = "；写操作只发一次，没重发（《写操作不重发》）"
        elif n > 1:
            tail = f"；试了 {n} 次（见「尝试」）"
        code = int(code or 0)
        if concl == "我方格式错" or (400 <= code < 500):
            out["弃答"] = (f"{name} 回了 {code}——回执原样收着，是哪一项不对由它说，"
                           f"我不替它改口径。")
        elif concl == "读不懂":
            out["弃答"] = (f"{name} 回了 {code}，可回执我读不懂——办成了没，我不知道。")
        elif concl == "驳回":
            out["弃答"] = (f"{name} 收到了但明确不收："
                           f"{(read or {}).get('原因') or '它没给原因'}——按它给的原因改。")
        else:
            out["弃答"] = (f"{name} 那边自己挂了（{err or ('status ' + str(code))}）——"
                           f"不是报文的问题，我没动它{tail}。")
        # 报缺路由（v0.2.27 的表 + v0.2.36 挂到这里）：缺客观事实就出单，不猜
        _mr = self._missing_rule()
        if _mr["单"]:
            kind, what = self._missing_kind(read)
            if kind:
                _row = self._missing_table(_mr["分类"]).get(kind)
                _say = ""
                if isinstance(obj, dict):
                    _say = str(obj.get("detail") or obj.get("message")
                               or obj.get("reason") or obj.get("error") or "")
                if not _say and err:
                    _say = err
                item = {"缺什么": what, "哪一类": kind,
                        "对方原话": (_say[:200] if _say else "对方没说"),
                        "要谁给": _row[1] if _row else "",
                        "给了之后怎么用": _row[2] if _row else ""}
                if _row is None:
                    item["note"] = "语料那张表里没有这一类——谁给，我不替你定"
                if write and write_rule and n == 1:
                    # 表里那句「原样再发一次」只对读成立；写操作照它做就是重复提交。
                    # 这里换成《写操作不重发》自己写的那条动作（换一条路查），不是替语料编。
                    item["给了之后怎么用"] = ("对方恢复后**先查这一单在不在**，"
                                              "别原样再发（《写操作不重发》）")
                    item["备注"] = "写操作没重发：重发会重复提交"
                out["报缺"] = [item]
        return out

    # 三态各自的可行动作（v0.2.39，口径：「把三态的可行动作写成判据，仍不代发、仍由人拍」）。
    # 动作只有两种形状：**往下走**（在）、**交人拍**（其余四态）——没有一条是「自己再发一次」，
    # 重发仍归《写操作不重发》。规矩在 conc/559，这里只是把它的动作摊成字段。
    _VER_ACT = {
        "在": {"能做什么": "拿「数据」当这次的回执往下走——这一单对方已经收下，不用再发",
               "不许": ["为了「再确认一下」重发一次", "把查到的那条当新单再提交一遍"]},
        "不在": {"能做什么": "可以按原报文再发一次——发不发由人说；我不自己发",
                 "不许": ["自己重发", "把「不在」说成「已失败」写进结论"]},
        "认不准": {"能做什么": "把「查过」整块交人：查的接口、参数、几条、核项差在哪都在里面",
                   "不许": ["挑一条最像的当这一单", "再查第二次碰运气", "拿条数凑成「在」"]},
        "查不了": {"能做什么": "维持报缺，等对方恢复后再查一次——查不通不等于没落账",
                   "不许": ["拿查不通当「不在」", "换着参数乱试"]},
        "没查": {"能做什么": "要外面补查法：查哪个接口、带哪些参数、拿哪几项认",
                 "不许": ["自己编一个查询接口", "拿参数里的期间冒充单号"]},
    }

    @classmethod
    def _verdict(cls, state: str, 判据: list, **extra) -> dict:
        """三态一块：`三态` + `判据`（凭什么定成这样，一条一行）+ 该态能做什么/不许做什么。"""
        out = {"三态": state, "判据": 判据}
        act = cls._VER_ACT.get(state) or {}
        if act:
            out["能做什么"] = act["能做什么"]
            out["不许"] = list(act["不许"])
        out.update(extra)
        return out

    def _api_verify(self, spec, out, name) -> dict:
        """挂了先查再定（v0.2.37）：**只在写发了却没回执时**查一次，把「成没成」定下来。

        需要才调用——写成功不查、读失败不查、没教过不查、没给查法不猜。
        查来的结论只用来定性，**不替我重发**（《写操作不重发》仍然有效）。
        「认不准」「查不了」都不许折成「不在」：查不通不等于没落账。

        v0.2.39：三态各自带 `判据`（查法给了 / 查通了 / 返回是空的·核项全对上，一条一行）
        与 `能做什么`／`不许`；「认不准」另带 `看过的`（查到的那几条各自差在哪）——
        说不准也要说准是**卡在哪一步**。
        """
        rid = self._verify_rule()
        if not rid:
            return out
        chk = spec.get("查") if isinstance(spec, dict) else None
        if not isinstance(chk, dict) or not str(chk.get("接口") or "").strip():
            out["查过"] = self._verdict(
                "没查",
                [{"项": "查法给了没有", "值": "没有——任务书没给「查」（接口/参数/核）",
                  "算数": "否"}],
                凭什么 = (f"任务书没给「查」——《挂了先查再定》（{rid}）要几样："
                          f"查哪个接口、带什么参数、拿哪几项认；缺一样我不猜。"))
            if out.get("弃答"):
                out["弃答"] += "；我没法查（没给查法，见「查过」）——成没成，只能你去问它。"
            return out
        need = dict(chk.get("核") or {}) if isinstance(chk.get("核"), dict) else {}
        info = {"查的接口": chk.get("接口"), "查的参数": chk.get("参数") or {}}
        got = self._api_call({k: v for k, v in chk.items() if k != "核"},
                            source="挂了先查")
        if not got.get("ok"):
            _sent = bool(got.get("尝试") or got.get("状态"))
            _v = self._verdict(
                "查不了",
                [{"项": "查法给了", "值": f"接口={info['查的接口']}", "算数": "是"},
                 {"项": "查通了", "值": f"没通：{str(got.get('弃答') or '没拿到回执')[:80]}",
                  "算数": "否"}],
                **info,
                凭什么 = (f"查也挂了：{str(got.get('弃答') or '没拿到回执')[:120]}"
                          f"——查不通不等于没落账，我不拿它当「不在」。"))
            if not _sent:
                # 查这一次**根本没发出去**（接口名不在目录、没配地址、参数不成）：
                # 那不是「对方挂了」，是查法本身不成立——等对方恢复没用，得人改查法。
                _v["能做什么"] = ("先改查法（接口名 / 地址 / 参数）——这一次根本没发出去；"
                                  "改完再查，不改等于没查")
                _v["不许"] = ["拿「查法不成立」当「没落账」", "换着参数乱试"]
                _v["凭什么"] = (f"查法不成立：{str(got.get('弃答') or '')[:120]}"
                                f"——不是对方挂了，等它恢复也没用。")
            out["查过"] = _v
            if out.get("弃答"):
                out["弃答"] += "；我想查，可查也不通（见「查过」）——成没成，还是不知道。"
            return out
        data = got.get("数据") or {}
        rows = data.get("items") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            rows = [data] if isinstance(data, dict) and data else []
        rows = [r for r in rows if isinstance(r, dict)]
        info["查到条数"] = len(rows)
        info["查的状态"] = got.get("状态")
        if not need:
            out["查过"] = self._verdict(
                "认不准",
                [{"项": "查法给了", "值": f"接口={info['查的接口']}", "算数": "是"},
                 {"项": "查通了", "值": f"status {info['查的状态']}，{len(rows)} 条", "算数": "是"},
                 {"项": "核项给了", "值": "没给——哪一项算「这一单」我不知道", "算数": "否"}],
                **info,
                凭什么 = (f"查到 {len(rows)} 条，可任务书没给「核」——"
                          f"哪一条是这次的不敢认，认错比不认坏。"))
            return out
        from decimal import Decimal as _D, InvalidOperation as _IE

        def _same(a, b):
            try:
                return _D(str(a)) == _D(str(b))
            except (_IE, ValueError, TypeError):
                return str(a).strip() == str(b).strip()

        hit = [r for r in rows
               if all(k in r and _same(r[k], v) for k, v in need.items())]
        _need_txt = "、".join(f"{k}={v}" for k, v in need.items())
        if hit:
            r0 = hit[0]
            sid = str(r0.get("statement_id") or r0.get("id")
                      or r0.get("编号") or "").strip()
            out["ok"] = True
            out["数据"] = r0
            out.pop("弃答", None)
            out.pop("报缺", None)     # 缺的是「成没成」，查明了成，就不再挂这个账
            out["查过"] = self._verdict(
                "在",
                [{"项": "查法给了", "值": f"接口={info['查的接口']}", "算数": "是"},
                 {"项": "查通了", "值": f"status {info['查的状态']}，{len(rows)} 条", "算数": "是"},
                 {"项": "核项全对上", "值": _need_txt, "算数": "是"}],
                **info, 凭据 = sid or r0,
                凭什么 = (f"照《挂了先查再定》（{rid}）在对方那边查到一条核项全对的："
                          + _need_txt))
            out["说明"] = (f"{name} 的忙没回执，可这一单对方**已经收下**"
                           f"（{sid or '见「查过」'}）——凭据是查出来的，不是推的；不重发。")
            return out
        if not rows:
            out["查过"] = self._verdict(
                "不在",
                [{"项": "查法给了", "值": f"接口={info['查的接口']}", "算数": "是"},
                 {"项": "查通了", "值": f"status {info['查的状态']}，回执读得懂", "算数": "是"},
                 {"项": "返回是空的", "值": "0 条", "算数": "是"}],
                **info, 凭什么 = "查通了，对方那一期间一张单都没有")
            out["弃答"] = (f"{name} 那边挂了，我按你说的查过：**那一期间没有这一单**，"
                           f"它没收下。要不要再发一次，你说——我不自己重发。")
            if out.get("报缺"):
                out["报缺"][0]["给了之后怎么用"] = (
                    "已查明没落账：可以按原报文再发一次，发不发由人说（《挂了先查再定》）")
            return out

        # 认不准：把「看过的那几条各自差在哪」摊出来——说不准也要说准卡在哪一步
        def _look(r, n):
            same_k, diff_k = [], []
            for k, v in need.items():
                if k not in r:
                    diff_k.append(f"{k}=对方没这个字段")
                elif _same(r[k], v):
                    same_k.append(f"{k}={v}")
                else:
                    diff_k.append(f"{k}={r[k]}≠{v}")
            return {"条": n, "对上的": same_k, "差在": diff_k}

        看过 = [_look(r, n) for n, r in enumerate(rows[:3], 1)]
        extra = {"看过的": 看过}
        if len(rows) > 3:
            extra["还有几条没看"] = len(rows) - 3
        out["查过"] = self._verdict(
            "认不准",
            [{"项": "查法给了", "值": f"接口={info['查的接口']}", "算数": "是"},
             {"项": "查通了", "值": f"status {info['查的状态']}，{len(rows)} 条", "算数": "是"},
             {"项": "核项全对上", "值": f"没有一条全对（核项：{_need_txt}）", "算数": "否"}],
            **info, **extra,
            凭什么 = (f"查到 {len(rows)} 条，没有一条核项全对——"
                      f"可能是别的单，也可能是我算错，认不准。"))
        out["弃答"] = (f"{name} 那边挂了，我查了，可查到的东西认不出是不是这一单"
                       f"——成没成，我说不准。")
        return out
    def _api_call(self, spec, *, timeout: float = 5.0, source: str = "接口目录") -> dict:
        """按目录调一个接口：GET 参数进查询串、POST 参数进报文；分页按 next_page 取全。"""
        import urllib.parse as _up
        if not isinstance(spec, dict):
            return {"ok": False, "弃答": "接口调用要 {接口: 名字, 参数: {...}}。"}
        name = str(spec.get("接口") or "").strip()
        tab = self._api_table()
        if name not in tab:
            return {"ok": False,
                    "弃答": f"《接口目录》里没有「{name}」——名字是唯一钥匙，我不猜路径。"
                            f"目录里有：{list(tab)}"}
        ent = tab[name]
        cfg = self._api_cfg()
        base = str(cfg.get("base") or "").rstrip("/")
        if not base:
            return {"ok": False,
                    "弃答": "没配 base——端口地址是人配的（trigger.json 的 base），我不发明。"}
        args = dict(spec.get("参数") or {}) if isinstance(spec.get("参数"), dict) else {}
        if isinstance(spec.get("参数"), str):
            # 上一步的结论原样递进来（一整块 JSON）：只挑目录里列的参数，多的一律不带。
            import json as _j0
            try:
                blob = _j0.loads(spec["参数"])
            except ValueError:
                return {"ok": False, "弃答": "「参数」是一整块文本，但不是 JSON——不拆自由文本。"}
            if isinstance(blob, dict):
                args = {k: blob[k] for k in ent["参数"] if k in blob}
            else:
                return {"ok": False, "弃答": "「参数」要一个对象，给的是列表或值。"}

        path = str(ent["路径"])
        for k in list(args):                      # 路径里的 {id} 从参数取，不再进查询串
            if "{" + k + "}" in path:
                path = path.replace("{" + k + "}", str(args.pop(k)))
        url0 = base + path
        # ── 重发路（v0.2.36 把授权接上）：能不能重发**由语料说**，代码只执行 ──
        #   读：`故障重试` ← conc/534（5xx 原样重发，最多 2 次）
        #   写：`写不重发` ← conc/558（非 GET 只发一次，重发=重复提交）
        #   两个都没教过 ⇒ 一次都不重发（I5：与 v0.2.35 之前逐字相同）。
        #   ★ v0.2.35 这里写的是 bool(self._port_retry_rule())——非空 dict 恒真，
        #     等于重试恒开、跟语料无关。删掉 534 也照发 3 次，授权链是断的。
        _rt = self._port_retry_rule()
        retry_ok = bool(str(_rt.get("故障重试") or ""))
        write = str(ent["方法"]).upper() != "GET"
        write_rule = self._write_rule()
        if write and write_rule:
            retry_ok = False       # 写操作：一次都不重发
        want_pages = bool(spec.get("翻页")) or bool(ent["分页"])

        import datetime as _dt
        import json as _j2
        from pathlib import Path as _P2
        _nd = self._nodes_dir()
        croot = (_P2(self._corpus_root) if getattr(self, "_corpus_root", None)
                 else (_nd.parent if _nd else None))
        now = _dt.datetime.now()
        attempts, seq = [], 0
        box = {"read": {}}

        def _log(row):
            """每次尝试都落一条（v0.2.36）：只留最后一次 = 事后看不出重发过。"""
            if croot is None:
                return
            try:
                with (croot / "port_calls.jsonl").open("a", encoding="utf-8") as f:
                    f.write(_j2.dumps(row, ensure_ascii=False) + "\n")
            except (OSError, AttributeError):
                pass

        def _once(params):
            nonlocal seq
            tries, last = 0, None
            while True:
                seq += 1
                reqno = f"req-{now.strftime('%Y%m%d%H%M%S%f')[:17]}-api{seq:04d}"
                if ent["方法"] == "GET":
                    q = _up.urlencode({k: str(v) for k, v in params.items()})
                    url = url0 + (("?" + q) if q else "")
                    code, obj, err = self._http_json("GET", url, None, timeout=timeout)
                else:
                    url = url0
                    code, obj, err = self._http_json("POST", url, params, timeout=timeout)
                last = (code, obj, err)
                raw = (obj if isinstance(obj, str)
                       else _j2.dumps(obj, ensure_ascii=False))
                read = (self._port_read(code, raw) if not err else
                        {"结论": "对方故障", "原文": err,
                         "依据": "连接失败/超时属对方故障，不是报文问题（conc/395）"})
                box["read"] = read
                attempts.append({"尝试": len(attempts) + 1, "请求号": reqno,
                                 "status": code, "结论": read.get("结论"),
                                 "对方返回": (read.get("对方返回")
                                              if read.get("对方返回") is not None
                                              else (read.get("原文") or "")[:200])})
                _log({"ts": now.isoformat(timespec="seconds"), "来源": source,
                      "接口": name, "请求号": reqno, "尝试": len(attempts),
                      "url": url, "status": code, "解读": read,
                      "ok": read.get("结论") == "受理"})
                bad = (err) or (code >= 500)
                if not bad or not retry_ok or tries >= 2:
                    return last
                tries += 1

        code, obj, err = _once(args)
        if err or code >= 400:
            out = self._api_fail(name, ent, code, obj, err, attempts, box["read"],
                                 write=write, write_rule=write_rule, retry_ok=retry_ok)
            # 挂了先查再定（v0.2.37）：**只在写、且只挂在这两种上**才走这一步。
            #   4xx 是明确拒收（账一定没记）⇒ 不查；写成功、读失败、没教过 559 都不查
            #   （I5：没教过时与 v0.2.36 逐字相同）。
            if write and self._verify_rule() and (err or int(code or 0) >= 500):
                return self._api_verify(spec, out, name)
            return out
        pages, items, guard = 1, list(((obj or {}).get("items") or []) if isinstance(obj, dict) else []), 1
        while want_pages and isinstance(obj, dict) and obj.get("next_page") and guard < 20:
            guard += 1
            p = dict(args)
            p["page"] = obj["next_page"]
            # 分页参数名沿用目录（订单列表这里叫 page）
            code2, obj, err2 = _once(p)
            if err2 or code2 >= 400:
                out2 = self._api_fail(name, ent, code2, obj, err2, attempts,
                                      box["read"], write=write,
                                      write_rule=write_rule, retry_ok=retry_ok)
                out2["弃答"] = (f"第 {guard} 页没取到——半张表我不汇总。"
                                + str(out2.get("弃答") or ""))
                out2["已取"] = items
                return out2
            pages += 1
            items += list((obj or {}).get("items") or [])
        data = dict(obj or {}) if isinstance(obj, dict) else {"原文": obj}
        if want_pages:
            data["items"] = items
            data["pages"] = pages
            data.pop("next_page", None)
        out = {"ok": True, "接口": name, "方法": ent["方法"], "状态": code,
               "页数": pages, "取到": len(items) if want_pages else 1,
               "数据": data,
               "依据": [{"原文": f"《接口目录》（{self._api_rule()}）：{name}",
                         "可复算": f"{ent['方法']} {ent['路径']}"}]}
        if len(attempts) > 1:
            out["尝试"] = attempts     # 重发过就留痕，不让人以为一次就成
        return out

    # ── 小说分析管线（v0.2.41）────────────────────────────────────────
    # 规矩在 conc/918《小说分析管线》：发起→等就绪→按目录序逐段调。
    # 算子只做「按《接口目录》一个一个发」，每段一发、回执全收——路径不猜、
    # 参数不编；就绪判定看「任务列表」的状态，轮询有界，没就绪就停手交回。
    def _novel_pipeline_rule(self) -> str:
        for nid, n in self.nodes.items():
            if str((n.fields or {}).get("name") or "") == "小说分析管线":
                return nid
        return ""

    def _duibi_pipeline_rule(self) -> str:
        for nid, n in self.nodes.items():
            if str((n.fields or {}).get("name") or "") == "对比分析管线":
                return nid
        return ""

    def _duibi_pipeline(self, spec) -> dict:
        """对比分析管线（v0.2.42）：双分支汇合。形状与 _novel_pipeline（单任务线性链）
        不同处：① 发起两次、两个任务号；② 轮询每轮 A/B 各一次、两条都就绪才走；
        ③ 支线段各带各的任务号；④ 汇合段参数=任务号A+任务号B 两把一起带。"""
        import time as _t3
        if not isinstance(spec, dict):
            return {"ok": False, "弃答": "「对比分析」要 {书名A: 甲书, 书名B: 乙书}。"}
        if not self._duibi_pipeline_rule():
            return {"ok": False,
                    "弃答": "没教过《对比分析管线》——长线活不走，要一次发一个接口，按《接口目录》来。"}
        nameA = str(spec.get("书名A") or "").strip()
        nameB = str(spec.get("书名B") or "").strip()
        if not nameA or not nameB:
            return {"ok": False,
                    "弃答": "「对比分析」要 {书名A: 甲书, 书名B: 乙书}——两把书名是钥匙，我不猜。"}
        if nameA == nameB:
            return {"ok": False,
                    "弃答": "两本书同名 ⇒ 没有可比性，我不拿同一条支线冒充两侧。"}
        rounds = max(1, int(spec.get("轮数") or 30))
        gap = max(0.0, float(spec.get("间隔") or 20))

        trace = []

        def call(iname, params):
            r = self._api_call({"接口": iname, "参数": params})
            trace.append({"接口": iname, "参数": params, "ok": bool(r.get("ok")),
                          "状态码": r.get("状态"),
                          "弃答": (str(r.get("弃答"))[:120] if r.get("弃答") else None)})
            return r

        # ① 双发起（写：各一次，按《写操作不重发》conc/558）
        tids = {}
        for side, book in (("A", nameA), ("B", nameB)):
            r0 = call("发起采集", {"书名": book})
            if not r0.get("ok"):
                return {"ok": False, "管线": "「发起采集」(%s=%s) 发起失败" % (side, book),
                        "留痕": trace}
            tids[side] = str((r0.get("数据") or {}).get("任务号") or "")

        # ② 双轮询（读：有界；每轮两条支线各查一次，两条都就绪才往下）
        prog = {}
        ready = False
        for _ in range(rounds):
            all_ready = True
            for side, book in (("A", nameA), ("B", nameB)):
                r1 = call("采集任务列表", {"书名": book})
                items = (r1.get("数据") or {}).get("items") or []
                me = next((x for x in items if str(x.get("任务号") or "") == tids[side]), None)
                prog[side] = me
                if not (me and "就绪" in str(me.get("状态") or "")):
                    all_ready = False
            if all_ready:
                ready = True
                break
            if _ < rounds - 1:
                _t3.sleep(gap)
        if not ready:
            pending = ["%s=%s" % (s, b) for s, b in (("A", nameA), ("B", nameB))
                       if not (prog.get(s) and "就绪" in str(prog[s].get("状态") or ""))]
            return {"ok": True, "管线": "没爬完，停手交回", "任务号A": tids["A"], "任务号B": tids["B"],
                    "未就绪的支线": pending, "进度": prog,
                    "轮询": "%d次×间隔%gs（每轮A/B各一次）" % (rounds, gap),
                    "说明": "按《对比分析管线》停手线：任一支线到轮询上限没就绪就交人——"
                            "报明是哪一本；爬取在服务那边继续，再发一次「对比分析」接着办（章节缓存可续）。",
                    "留痕": trace}

        # ③ 支线段：A、B 各走一遍目录序，参数各带各的任务号
        branch = [("章节预处理", {"任务号": None}),
                  ("人物出场统计", {"任务号": None, "前": 10}),
                  ("高频词统计", {"任务号": None, "前": 20})]
        results, done, ledger = {}, [], {"A": [], "B": []}
        for side in ("A", "B"):
            for iname, proto in branch:
                params = dict(proto)
                params["任务号"] = tids[side]
                r = call(iname, params)
                key = "%s支线:%s" % (side, iname)
                if r.get("ok"):
                    results[key] = r.get("数据")
                    done.append(key)
                    ledger[side].append(key)
                else:
                    return {"ok": False, "管线": "%s支线「%s」没成，停手交回" % (side, iname),
                            "任务号A": tids["A"], "任务号B": tids["B"],
                            "已完成的段": done, "状态码": r.get("状态"),
                            "弃答": r.get("弃答"), "留痕": trace}

        # ④ 汇合段：两把钥匙一起带
        joins = [("对比分析", {"任务号A": tids["A"], "任务号B": tids["B"]}),
                 ("对比图表", {"任务号A": tids["A"], "任务号B": tids["B"]}),
                 ("对比汇总", {"任务号A": tids["A"], "任务号B": tids["B"]})]
        for iname, params in joins:
            r = call(iname, params)
            if r.get("ok"):
                results[iname] = r.get("数据")
                done.append(iname)
            else:
                return {"ok": False, "管线": "汇合段「%s」没成，停手交回" % iname,
                        "任务号A": tids["A"], "任务号B": tids["B"],
                        "已完成的段": done, "状态码": r.get("状态"),
                        "弃答": r.get("弃答"), "留痕": trace}
        return {"ok": True, "管线": "完成", "任务号A": tids["A"], "任务号B": tids["B"],
                "已完成的段": done, "支线台账": ledger,
                "结果": results, "留痕": trace}

    def _novel_pipeline(self, spec) -> dict:
        import time as _t2
        if not isinstance(spec, dict):
            return {"ok": False, "弃答": "「小说分析」要 {小说名: 名字}。"}
        if not self._novel_pipeline_rule():
            return {"ok": False,
                    "弃答": "没教过《小说分析管线》——长线活不走，要一次发一个接口，按《接口目录》来。"}
        name = str(spec.get("小说名") or "").strip()
        if not name:
            return {"ok": False, "弃答": "「小说分析」要 {小说名: 名字}——书名是钥匙，我不猜。"}
        rounds = max(1, int(spec.get("轮数") or 30))
        gap = max(0.0, float(spec.get("间隔") or 20))

        trace = []

        def call(iname, params):
            r = self._api_call({"接口": iname, "参数": params})
            trace.append({"接口": iname, "参数": params, "ok": bool(r.get("ok")),
                          "状态码": r.get("状态"),
                          "弃答": (str(r.get("弃答"))[:120] if r.get("弃答") else None)})
            return r

        # ① 发起抓取（写：按《写操作不重发》conc/558，一次）
        r0 = call("抓取小说", {"小说名": name})
        if not r0.get("ok"):
            return {"ok": False, "管线": "抓取小说发起失败", "留痕": trace}
        tid = str((r0.get("数据") or {}).get("任务号") or "")

        # ② 轮询等就绪（读：有界，没就绪停手交回——进度在服务那边不断）
        me, ready = None, False
        for _ in range(rounds):
            r1 = call("任务列表", {"小说名": name})
            items = (r1.get("数据") or {}).get("items") or []
            me = next((x for x in items if str(x.get("任务号") or "") == tid), None)
            if me and "就绪" in str(me.get("状态") or ""):
                ready = True
                break
            if _ < rounds - 1:
                _t2.sleep(gap)
        if not ready:
            return {"ok": True, "管线": "没爬完，停手交回", "任务号": tid,
                    "进度": me, "轮询": "%d次×间隔%gs" % (rounds, gap),
                    "说明": "按《小说分析管线》停手线：到轮询上限没就绪就交人——爬取在服务那边继续，"
                            "再发一次「小说分析」接着办（章节缓存可续）。",
                    "留痕": trace}

        # ③ 就绪：按目录序逐段调，一段一发；某段挂了停手交回（重试归各段自己的规矩）
        chain = [("文本预处理", {"任务号": tid}),
                 ("词频统计", {"任务号": tid, "前": 20}),
                 ("关键词提取", {"任务号": tid, "前": 10, "算法": "TF-IDF"}),
                 ("关键词提取", {"任务号": tid, "前": 10, "算法": "TextRank"}),
                 ("主题建模", {"任务号": tid, "主题数": 3}),
                 ("生成图表", {"任务号": tid}),
                 ("分析汇总", {"任务号": tid})]
        results, done = {}, []
        for iname, params in chain:
            r = call(iname, params)
            key = iname if params.get("算法") in (None, "TF-IDF") else iname + "(TextRank)"
            if r.get("ok"):
                results[key] = r.get("数据")
                done.append(key)
            else:
                return {"ok": False, "管线": "「%s」没成，停手交回" % key,
                        "任务号": tid, "已完成的段": done, "状态码": r.get("状态"),
                        "弃答": r.get("弃答"), "留痕": trace}
        return {"ok": True, "管线": "完成", "任务号": tid, "已完成的段": done,
                "结果": results, "留痕": trace}

    # ── 结算（v0.2.35）──────────────────────────────────────────────────
    # 公式全在 conc/554：行/退款/汇总/费率四类行，逐行代入、逐行舍入、再加总。
    # 这里是算子：解析公式、安全求值、求和、核对——口径一句都不写。
    def _settle_rule(self) -> str:
        for nid, n in self.nodes.items():
            if str((n.fields or {}).get("name") or "") == "结算算法":
                return nid
        return ""

    def _settle_alg(self) -> dict:
        import re as _re
        rid = self._settle_rule()
        if not rid:
            return {}
        body = str(getattr(self.nodes[rid], "body", "") or "")
        pat = _re.compile(r"^-\s*(?P<scope>行|退款|汇总)\s*[:：]\s*(?P<k>[^=＝]+?)\s*[=＝]\s*(?P<v>.+?)\s*$")
        rate = _re.compile(r"^-\s*费率\s*[:：]\s*(?P<tier>\S+)\s+(?P<r>[\d.]+)\s*$")
        alg = {"行": [], "退款": [], "汇总": [], "费率": {}}
        for ln in body.splitlines():
            m = pat.match(ln.strip())
            if m:
                alg[m.group("scope")].append((m.group("k").strip(), m.group("v").strip()))
                continue
            m = rate.match(ln.strip())
            if m:
                alg["费率"][m.group("tier")] = m.group("r")
        return alg

    @staticmethod
    def _safe_eval(expr: str, env: dict, tables: tuple = ("汇率", "费率")):
        """只认四则、括号、字段名、round()、以及表查（汇率[currency]）。

        认不出的一律抛错——宁可不填，也不拿 eval 去跑外面递进来的字符串。
        """
        import ast as _ast
        node = _ast.parse(str(expr), mode="eval")

        def ev(n):
            if isinstance(n, _ast.Expression):
                return ev(n.body)
            if isinstance(n, _ast.Constant):
                return n.value
            if isinstance(n, _ast.Name):
                if n.id in env:
                    return env[n.id]
                raise KeyError(n.id)
            if isinstance(n, _ast.BinOp):
                a, b = ev(n.left), ev(n.right)
                op = n.op
                if isinstance(op, _ast.Add):
                    return a + b
                if isinstance(op, _ast.Sub):
                    return a - b
                if isinstance(op, _ast.Mult):
                    return a * b
                if isinstance(op, _ast.Div):
                    return a / b
                raise ValueError("只认四则")
            if isinstance(n, _ast.UnaryOp) and isinstance(n.op, _ast.USub):
                return -ev(n.operand)
            if isinstance(n, _ast.Call):
                if not isinstance(n.func, _ast.Name) or n.func.id != "round" or len(n.args) != 2:
                    raise ValueError("只认 round(x, n)")
                # ⚠️ 舍入用 half-up（Decimal），不用 float 的 round ——
                #   Python 的 round 是银行家舍入，2.675 会舍成 2.67，跟对方差一分就核不过。
                from decimal import Decimal as _D, ROUND_HALF_UP as _HU
                nd = int(ev(n.args[1]))
                q = _D(1).scaleb(-nd)
                val = ev(n.args[0])
                if isinstance(val, _D):
                    return val.quantize(q, rounding=_HU)
                return _D(str(val)).quantize(q, rounding=_HU)
            if isinstance(n, _ast.Subscript):
                if not isinstance(n.value, _ast.Name) or n.value.id not in tables:
                    raise ValueError("只认表查")
                t = env.get(n.value.id)
                if not isinstance(t, dict):
                    raise KeyError(n.value.id)
                k = ev(n.slice)
                if k not in t:
                    raise KeyError(f"{n.value.id}[{k}]")
                v = t[k]
                from decimal import Decimal as _D
                try:
                    return _D(str(v))      # 汇率表里是 "7.12" 这种字符串，转成 Decimal 再算
                except Exception:  # noqa: BLE001
                    return v
            raise ValueError(f"不认识的写法：{type(n).__name__}")

        return ev(node)

    def _settle(self, spec) -> dict:
        """按《结算算法》把行算成表：逐行落列 → 逐行舍入 → 汇总 → 核对。"""
        import json as _j
        alg = self._settle_alg()
        if not alg:
            return {"ok": False,
                    "弃答": "没有《结算算法》——公式没教过，我不凭空算。"
                            "（与上一版逐字相同：没有算法就不算）"}
        if not isinstance(spec, dict):
            return {"ok": False, "弃答": "结算要 {订单: [...], 退款: [...], 汇率: {...}}。"}

        def _as_list(v):
            # 端口交回来的是一整块 `{"数据": {...}}`：先剥一层「数据」，再看里面有没有 items
            if isinstance(v, dict) and isinstance(v.get("数据"), (dict, list)):
                v = v["数据"]
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                return list(v.get("items") or [])
            if isinstance(v, str):
                try:
                    d = _j.loads(v)
                except ValueError:
                    return []
                if isinstance(d, dict):
                    return _as_list(d)
                if isinstance(d, list):
                    return d
            return []

        rows = _as_list(spec.get("订单"))
        refs = _as_list(spec.get("退款"))
        fx = spec.get("汇率")
        if isinstance(fx, str):
            try:
                fx = _j.loads(fx)
            except ValueError:
                fx = {}
        if isinstance(fx, dict) and isinstance(fx.get("数据"), dict):
            fx = fx["数据"]
        fx = dict((fx or {}).get("rates") or fx or {}) if isinstance(fx, dict) else {}
        if not rows:
            return {"ok": False, "弃答": "没有订单行——不拿空表算总额。"}
        if not fx:
            return {"ok": False, "弃答": "没有汇率——跨币种的本币额算不出来，不猜。"}
        rate = dict(alg["费率"])
        if not rate:
            return {"ok": False, "弃答": "没有费率——手续费不默认 0。"}

        kept = [r for r in rows if str(r.get("state") or "confirmed") == "confirmed"]
        dropped = len(rows) - len(kept)

        def _row(v, formulas, env0):
            from decimal import Decimal as _D
            env = dict(env0)
            for k, vv in v.items():
                # 金额一律走 Decimal：float 的 547.485 会变成 547.4849999…，舍入翻一分。
                try:
                    env[k] = _D(str(vv))
                except Exception:  # noqa: BLE001
                    env[k] = vv
            env["汇率"], env["费率"] = fx, {k: _D(x) for k, x in rate.items()}
            out = dict(env)
            for col, expr in formulas:
                out[col] = self._safe_eval(expr, out)
            return out

        lines = []
        try:
            for r in kept:
                lines.append(_row(r, alg["行"], {"tier": str(r.get("tier") or ""),
                                                 "currency": str(r.get("currency") or "")}))
        except (KeyError, ValueError, TypeError, ZeroDivisionError) as exc:
            return {"ok": False, "弃答": f"行算不下去：{exc}——缺字段或缺表，我不补默认值。"}

        ref_lines = []
        ref_kept = [r for r in refs if str(r.get("state") or "") == "settled"]
        try:
            for r in ref_kept:
                ref_lines.append(_row(r, alg["退款"],
                                      {"currency": str(r.get("currency") or "")}))
        except (KeyError, ValueError, TypeError, ZeroDivisionError) as exc:
            return {"ok": False, "弃答": f"退款算不下去：{exc}——不补默认值。"}

        agg = {}
        try:
            for k, expr in alg["汇总"]:
                if expr.endswith("求和"):
                    col = expr[:-2].strip()
                    pool = ref_lines if col.startswith("退款") else lines
                    if not pool or col not in pool[0]:
                        return {"ok": False, "弃答": f"汇总要的列「{col}」没算出来——不默认 0。"}
                    from decimal import Decimal as _D
                    agg[k] = float(sum((_D(str(x[col])) for x in pool), _D(0)))
                else:
                    env = dict(agg)
                    from decimal import Decimal as _D
                    for c in (lines[0] if lines else {}):
                        vals = [x.get(c) for x in lines]
                        if not vals or not all(isinstance(v, (int, float)) for v in vals):
                            continue
                        env[c] = float(sum((_D(str(v)) for v in vals), _D(0)))
                    agg[k] = self._safe_eval(expr, env)
        except (KeyError, ValueError, TypeError, ZeroDivisionError) as exc:
            return {"ok": False, "弃答": f"汇总算不下去：{exc}——不凑数。"}


        chk = None
        if "营收" in agg and "应付" in agg and "退款" in agg and "手续费" in agg:
            from decimal import Decimal as _D
            chk = float(_D(str(agg["营收"])) - _D(str(agg["退款"]))
                        - _D(str(agg["手续费"])) - _D(str(agg["应付"])))
        # 交出去的「提交」：字段名照对方目录（lines_count/gross_cny/…），
        # 值是我算出来的——**算的是我，验的是它**，所以核对不平就带着「平=False」交出去。
        period = str(spec.get("期间") or "").strip()
        sub = {}
        if period and "营收" in agg:
            sub = {"period": period, "lines_count": len(lines),
                   "gross_cny": float(agg["营收"]),
                   "refund_cny": float(agg["退款"]),
                   "fee_cny": float(agg["手续费"]),
                   "payable_cny": float(agg["应付"]),
                   "checksum": float(agg["营收"])}

        return {"ok": True, "行数": len(lines), "剔除": dropped,
                "退款笔数": len(ref_lines), **sub,
                "表": [{"订单": x.get("order_id"),
                        "本币": float(x.get("行本币")),
                        "手续费": float(x.get("行手续费") or 0)} for x in lines],
                "汇总": {k: (float(v) if hasattr(v, "quantize") else v)
                         for k, v in agg.items()},
                "核对": {"左 - 右": chk, "平": (chk == 0) if chk is not None else None},
                "依据": [{"原文": f"《结算算法》（{self._settle_rule()}）",
                          "可复算": "逐行代入后相加；每行先舍入到分"},
                         {"原文": f"汇率表 {spec.get('汇率日') or ''}".strip(),
                          "可复算": "汇率由外面给，我不发明"}]}

    # ── 取数（v0.2.35）──────────────────────────────────────────────────
    def _pick_rule(self) -> str:
        for nid, n in self.nodes.items():
            if str((n.fields or {}).get("name") or "") == "取数":
                return nid
        return ""

    def _pick(self, spec) -> dict:
        """从一整块里取一个值：键路径 `a.b`、`items[0].id`；取不到就说这层有哪些键。"""
        import json as _j
        import re as _re
        if not self._pick_rule():
            return {"ok": False, "弃答": "没有《取数》——取一个值这件事我没学过。"}
        if not isinstance(spec, dict):
            return {"ok": False, "弃答": "取数要 {自: 一整块, 键: 'a.b'}。"}
        obj = spec.get("自")
        if isinstance(obj, str):
            try:
                obj = _j.loads(obj)
            except ValueError:
                return {"ok": False, "弃答": "「自」不是 JSON——取数不解析自由文本。"}
        key = str(spec.get("键") or "").strip()
        if not key:
            return {"ok": False, "弃答": "要一个「键」——取哪一个值，你说了算，我不挑。"}
        cur = obj
        for part in [x for x in key.split(".") if x]:
            m = _re.fullmatch(r"([^\[]+)\[(\d+)\]", part)
            name, idx = (m.group(1), int(m.group(2))) if m else (part, None)
            if isinstance(cur, dict):
                if name not in cur:
                    return {"ok": False, "取不到": key, "这一层有": sorted(cur)[:12],
                            "弃答": f"「{name}」不在这一层——键名不对就是不对，我不猜同义键。"}
                cur = cur[name]
            elif isinstance(cur, list) and name.isdigit():
                i = int(name)
                if i >= len(cur):
                    return {"ok": False, "弃答": f"下标 {i} 越界（共 {len(cur)} 条）。"}
                cur = cur[i]
            else:
                return {"ok": False, "弃答": f"「{key}」走不过去：这一层是 {type(cur).__name__}。"}
            if idx is not None:
                if not isinstance(cur, list) or idx >= len(cur):
                    return {"ok": False, "弃答": f"「{part}」下标 {idx} 越界。"}
                cur = cur[idx]
        return {"ok": True, "键": key, "值": cur, "类型": type(cur).__name__,
                "依据": [{"原文": f"《取数》（{self._pick_rule()}）",
                          "可复算": "按层取，原样交出，不做计算"}]}

    # ── 回单验收（v0.2.35）──────────────────────────────────────────────
    # 「跑完没有」与「要的兑现没有」是两件事：逐条在回单里指到值、再比对。
    # 规矩在 conc/557《回单验收》；这里只做指路与比对，不替任务书补条目。
    def _accept_rule(self) -> str:
        for nid, n in self.nodes.items():
            if str((n.fields or {}).get("name") or "") == "回单验收":
                return nid
        return ""

    def _accept(self, spec) -> dict:
        import json as _j
        if not self._accept_rule():
            return {"ok": False, "弃答": "没有《回单验收》——「跑完」是不是「做成」，我没学过怎么判。"}
        if not isinstance(spec, dict):
            return {"ok": False, "弃答": "验收要 {在: 回单, 核: {字段: 应有值}}。"}
        got = spec.get("在")
        if isinstance(got, str):
            try:
                got = _j.loads(got)
            except ValueError:
                return {"ok": False, "弃答": "「在」不是 JSON——验收只认结构性回单。"}
        if isinstance(got, dict) and isinstance(got.get("数据"), dict):
            got = got["数据"]
        want = spec.get("核")
        if not isinstance(want, dict) or not want:
            return {"ok": False, "弃答": "要「核」——核哪几条，任务书说了算，我不替你挑。"}
        items, bad, unable = [], [], []
        for k, v in list(want.items())[:8]:
            if not isinstance(got, dict) or k not in got:
                items.append({"条目": k, "回单上的字段": "回单里没有这一项",
                              "值": None, "过没过": False})
                unable.append(k)
                continue
            same = str(got[k]) == str(v)
            try:
                same = same or float(got[k]) == float(v)
            except (TypeError, ValueError):
                pass
            items.append({"条目": k, "回单上的字段": k, "值": got[k],
                          "过没过": same, "要的是": v})
            if not same:
                bad.append(k)
        verdict = "到了" if not bad and not unable else ("核不了" if unable and not bad else "没到")
        return {"ok": True, "到了没": verdict, "逐条": items,
                "没过的": bad, "回单没写的": unable,
                "依据": [{"原文": f"《回单验收》（{self._accept_rule()}）",
                          "可复算": "逐条在回单里指到值再比对；指不到算核不了"}]}

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
            # ④按正文（v0.2.40，用户「教吧」）：键=值够不着正文——《查询规程》
            #    三步全落空时，「正文里提过」和「没有」是两种情况，交人把这两种
            #    混成了一种。教过《查不到按正文》且预算有余 ⇒ 拿**人给的词**搜
            #    非判错条目的正文；没教过走旧路（I5，与 v0.2.39 逐字相同）。
            _rule_nid = (self._text_search_rule()
                         if used < budget else None)
            _bh = None
            if _rule_nid:
                # 词只有两个来源，不发明第三个：人给的线索（剥掉 键= 取值——
                # 键已经失败了，搜的是值）+ 问句里出现过的已教名字（**含两字名**
                # ——①按名字只认三字以上，两字名在这步接住：「负数」就是这么漏的）
                _cw, _nw = [], []
                for c in clues:
                    v = str(c).rsplit("=", 1)[-1].strip()
                    if v and v not in _cw:
                        _cw.append(v)
                _n_taken = 0
                for nm in names:
                    if _n_taken >= 3:
                        break
                    if 2 <= len(nm) <= 8 and nm in q and nm not in _QWORDS \
                            and nm not in _cw:
                        _nw.append(nm)
                        _n_taken += 1
                if _cw or _nw:
                    used += 1
                    _bh = self._body_hits(_cw, _nw)
                    tried.append({"步": "④按正文",
                                  "条件": {"线索词": _cw, "问句里的名字": _nw},
                                  "为什么": "键=值三步全落空——教过《查不到按正文》，拿人给的词搜正文",
                                  "命中": _bh["共"], "花费": 1})
            if _bh and _bh["共"]:
                out = {"ok": True, "结论": "正文里提过（非精确）", "精确": False,
                       "交人": False, "正文命中": _bh["条"],
                       "正文命中共": _bh["共"],
                       "花费": used, "预算": budget, "已试": tried,
                       "原因": ("按名字和线索（键=值）都没命中，但上面这些条目的"
                                "**正文**里出现过问的词——正文是材料不是字段，"
                                "引文交给你自己看，别当成精确命中交差。"),
                       "依据": base_ev + [
                           {"原文": "《查不到按正文》：键=值三步全落空且预算有余 ⇒ "
                                    "拿人给的词搜非判错条目的正文；正文命中是线索"
                                    "不是答案，精确=False",
                            "可复算": f"每条命中的「词」都能在其「引文」里逐字找到"
                                      f"（共 {_bh['共']} 条）"}],
                       "判据": [
                           "按名字与线索（键=值）都 0 命中——已试 各步 命中=0——算数=是",
                           f"教过《查不到按正文》（{_rule_nid}）且第四步花了余下的预算"
                           f"（花费 {used}≤{budget}）——算数=是",
                           f"每个「词」都在该条正文里出现（{_bh['共']} 条逐条可对）——算数=是",
                       ]}
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

    # ══ 原话级路由（v0.2.49 正位）——规矩在语料七段节点，算子在这 ════════════
    # 桥（ask 工具）只递原话；触发与执行全在内核。节点在才走（名字是唯一钥匙），
    # 没教 ⇒ None ⇒ 桥走原路（I5）。判据：exp/直连端口_20261114.py 等。

    def _route_node(self, name: str) -> str:
        """按 name= 找路由节点（名字是唯一钥匙）。空串 = 没教过 ⇒ 走旧路（I5）。"""
        for nid, n in self.nodes.items():
            if str((n.fields or {}).get("name") or "") == name:
                return nid
        return ""

    def _route_log(self, row: dict):
        """每次尝试落一条 port_calls.jsonl（v0.2.36 口径，与 _api_call 同文件同根）。"""
        try:
            croot = (Path(self._corpus_root)
                     if getattr(self, "_corpus_root", None) else None)
            if croot is None:
                return
            import datetime as _dt
            _row = {"ts": _dt.datetime.now().isoformat(timespec="seconds"), **row}
            with (croot / "port_calls.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(_row, ensure_ascii=False) + "\n")
        except (OSError, AttributeError, TypeError, ValueError):
            pass

    # ── 多轮（conc/1098，v0.2.62）：动作前参考记录与缓冲区 ─────────────

    def _turn_path(self):
        """轮次记录：语料根 buffer/turns.jsonl（env LDV_TURNS_PATH 可覆，判据用）。"""
        from pathlib import Path as _P
        env = os.environ.get("LDV_TURNS_PATH")
        if env:
            return _P(env)
        nd = self._nodes_dir()
        base = (_P(self._corpus_root) if hasattr(self, "_corpus_root")
                else (nd.parent if nd else _P(".")))
        return base / "buffer" / "turns.jsonl"

    def _turn_record(self, text, hit):
        """每轮追加一条轮次记录（只追加、不改写）。记录失败不拦主流程。"""
        if not self._route_node("多轮"):
            return
        try:
            import datetime as _dt
            row = {"ts": _dt.datetime.now().isoformat(timespec="seconds"),
                   "问": str(text)[:200]}
            if isinstance(hit, dict):
                row["路由"] = str(hit.get("节点") or "")
                row["ok"] = hit.get("ok")
                for k in ("任务名", "候选号", "交段"):
                    if hit.get(k) is not None:
                        row[k] = hit[k]
            else:
                row["路由"] = "原路"          # 链全不中走了旧查询——也如实记
            p = self._turn_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            pass                       # 轮次记录是参考不是闸门——记不上不停手

    def _turn_last_task(self):
        """轮次记录里最近一笔带任务名且没挂的账名；没有 ⇒ None（不编）。"""
        try:
            p = self._turn_path()
            if not p.is_file():
                return None
            lines = p.read_text(encoding="utf-8").splitlines()[-50:]
            for line in reversed(lines):
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                acc = row.get("任务名")
                if acc and row.get("ok") is not False:
                    return str(acc)
            return None
        except OSError:
            return None

    def _multi_turn_rule(self, text: str, max_chars: int = 600):
        """《多轮》（conc/1098）：动作前参考记录——LDV 天然带历史。
        ① 带任务名的段内话术（回执/第 N 段）⇒ 优先交账路由（conc/1074），
           不被闲聊/执行/写代码这些前位路由劫走；
        ② 不带任务名的接续句（接着做/继续/然后/下一步）⇒ 从轮次记录还原
           最近一笔账，按进度接上；
        ③ 记录里没有 ⇒ 走原路，不硬扯、不编历史（缺才要）。
        只在节点教了时走（名字唯一钥匙，遮住即回旧行为）。"""
        if not self._route_node("多轮"):
            return None
        # ① 段内话术（带任务名）：账路由优先
        if ("回执" in text and "任务" in text) \
                or re.search(r"任务.{0,30}第\s*\d+\s*段", text):
            hit = self._dev_task_rule(text)
            if hit:
                hit = dict(hit)
                hit["多轮"] = {"参考": "带任务名的段内话术——优先按《研发任务执行》"
                                  "路由，不让前位路由劫走。"}
                return hit
            return None                      # 账路由自己不要 ⇒ 让链继续判
        # ② 接续句不带任务名：从轮次记录还原账
        if re.match(r"^(接着做?|继续[做干吧干下去]*|然后呢?|下一步(怎么办|怎样|怎么走)?"
                    r"|进行得?怎么样[了吗?？]?|进度(怎么样|如何|到哪了)?)"
                    r"\s*[?？。!！]?\s*$", text.strip()):
            acc = self._turn_last_task()
            if not acc:
                return None                  # 记录里没账 ⇒ 走原路，不硬扯
            recon = "%s 任务 的进度" % acc
            hit = self._dev_task_rule(recon)
            if hit:
                hit = dict(hit)
                hit["多轮"] = {"参考": "轮次记录最近一笔账「%s」" % acc,
                               "还原句": recon,
                               "为什么": "单句接续参考历史接上账——LDV 天然带历史。"}
                return hit
            return {"ok": False, "多轮": True, "节点": "conc/1098",
                    "还给人": True,
                    "为什么": "轮次记录里最近在做账「%s」，但这句接不上账路由"
                              "（账可能已关或查不了）——把要什么说清，不猜。" % acc}
        return None

    # ── 固定组合注册（conc/1099，v0.2.63）：缓冲区晋升的出口 ────────────

    _FIXED_COMBO_ASK = ("是什么", "什么是", "怎么用", "怎么写", "怎么定义",
                        "是什么意思", "举个例子", "干嘛的", "怎么选")

    def _fixed_combo_nodes(self):
        """树里所有 type=固定组合 的节点（沉淀进树的才在此列）。"""
        return [(nid, n) for nid, n in self.nodes.items()
                if str((n.fields or {}).get("type") or "") == "固定组合"]

    def _fixed_combo_rule(self, text: str, max_chars: int = 600):
        """《固定组合注册》（conc/1099，v0.2.63）——缓冲区晋升的出口：
        步单同序列（步路由序列，v0.2.67）成功满 3 次获提名后，人拍板把它
        **沉淀进方向树**（type=固定组合的语料节点）。口径（用户 2026-11-17）：
        记录、缓冲区、方向树就是历史，不单独列历史；选择倾向由方向树决定，
        **沉淀进树的节点才影响倾向**——所以出口只有一个：教它，落成节点。
        ① 注册：话带「开成固定组合」+ 名字 + 候选号 ⇒ 校验提名（同序列
           成功 ≥3 且提名过）⇒ 建节点（步单原文照搬不改写；内存+落盘双写，
           当场可点名）⇒ 回执；
        ② 点名执行：话命中某固定组合名且不是问句 ⇒ 取步单原文 ⇒ 组合执行
           器跑 ⇒ 回执（不落缓冲区——它已是树的成员）。
        停手线：没提名不开；名字人给不自己编；同名不覆盖；步单原文不改写；
        一句话命中多个组合名 ⇒ 还给人挑；问句不执行走查询。
        可移除：删节点+算子，缓冲区照旧只当历史。"""
        if not self._route_node("固定组合注册"):
            return None
        # ① 注册（人拍板才走到这——判定权在人）
        if "开成固定组合" in text:
            m = re.search(r"名字(?:叫|为|是)?[「'\"]?([^\s「」'\",，。]+)", text)
            cid = re.search(r"P-\d{8}-\d{6}-\d{3}", text)
            if not (m and cid):
                return {"ok": False, "固定组合注册": True, "节点": "conc/1099",
                        "还给人": True,
                        "为什么": "开成固定组合要说清：名字叫什么、取哪个候选号"
                                  "（P-xxxxxxxx-xxxxxx-xxx）——不猜。"}
            recs = self._buffer_load()
            rec = next((x for x in recs if x.get("候选号") == cid.group(0)), None)
            if rec is None:
                return {"ok": False, "固定组合注册": True, "节点": "conc/1099",
                        "还给人": True,
                        "为什么": "缓冲区里没有 %s——不猜。" % cid.group(0)}
            # v0.2.67：校验按步路由序列认（用户拍板「步路由序列计数」）——
            # 参数微差的同序列步单合取累计；指纹留档备查
            _seq = self._plan_seq(rec)
            total = sum(1 for x in recs if _seq and self._plan_seq(x) == _seq
                        and x.get("状态") == "已执行")
            if total < 3 or not rec.get("提名"):
                return {"ok": False, "固定组合注册": True, "节点": "conc/1099",
                        "还给人": True, "候选号": rec.get("候选号"),
                        "为什么": ("同序列（%s）成功 %d 次、提名%s——不满 3 次"
                                   "没提名不开（conc/1076 缓冲区晋升规矩）。"
                                   % ("→".join(_seq) if _seq else "?", total,
                                      "过" if rec.get("提名") else "没有"))}
            name = m.group(1)
            if self._route_node(name):
                return {"ok": False, "固定组合注册": True, "节点": "conc/1099",
                        "还给人": True,
                        "为什么": "树里已有《%s》这个名字——换一个名，不覆盖。" % name}
            if not isinstance(rec.get("步单"), list) or not rec["步单"]:
                return {"ok": False, "固定组合注册": True, "节点": "conc/1099",
                        "还给人": True,
                        "为什么": "候选里没有步单——没法沉淀，不编。"}
            _mx = 0
            for nid0 in self.nodes:
                mm = re.match(r"conc__(\d+)$", str(nid0))
                if mm:
                    _mx = max(_mx, int(mm.group(1)))
            # ★ 编号以**盘上文件**为准（2026-11-20 晋升判据暴露的真缺陷）：
            #   真语料节点的键是 conc/N 形状、文件名才是 conc__N.md——只看
            #   内存键，语料长过注册号段后新注册必撞真文件（1101 撞 1101.md），
            #   且撞了只会报「换名重试」，人换一百个名也没用。盘上并查，取最大。
            _nd0 = self._nodes_dir()
            if _nd0:
                for _f0 in Path(_nd0).glob("conc__*.md"):
                    _mm = re.match(r"conc__(\d+)\.md$", _f0.name)
                    if _mm:
                        _mx = max(_mx, int(_mm.group(1)))
            nid_new = "conc__%d" % (_mx + 1)
            nd = self._nodes_dir()
            if not nd:
                return {"ok": False, "固定组合注册": True, "节点": "conc/1099",
                        "查不了": "内置语料", "error": "内置语料没法写节点文件。"}
            fields = {"name": name, "type": "固定组合",
                      "出处": "缓冲区晋升 %s（同序列成功 %d 次，人拍板）"
                              % (rec.get("候选号"), total),
                      "来源候选号": rec.get("候选号"),
                      "来源指纹": rec.get("指纹"),
                      "来源序列": "→".join(_seq) if _seq else "",
                      "goal": rec.get("目标")}
            body = ("\n# 固定组合·%s\n\n步单原文（照搬自缓冲区候选 %s，不改写；"
                    "执行时目标句原样带回）：\n\n```json\n%s\n```\n"
                    % (name, rec.get("候选号"),
                       json.dumps(rec["步单"], ensure_ascii=False)))
            try:
                p = Path(nd) / ("%s.md" % nid_new)
                if p.exists():
                    return {"ok": False, "固定组合注册": True, "节点": "conc/1099",
                            "还给人": True,
                            "为什么": "节点号 %s 已被占用——换名重试。" % nid_new}
                import datetime as _dt
                fm = ("---\nname: %s\ntype: 固定组合\n出处: %s\n来源候选号: %s\n"
                      "来源指纹: %s\n来源序列: %s\n---\n"
                      % (name, fields["出处"], rec.get("候选号"), rec.get("指纹"),
                         fields["来源序列"]))
                p.write_text(fm + body, encoding="utf-8")
                self.nodes[nid_new] = Node(id=nid_new, fields=fields,
                                           body=fm + body,
                                           keys=keys_of({"name": name,
                                                         "type": "固定组合",
                                                         "goal": rec.get("目标")}))
            except (OSError, ValueError) as exc:
                return {"ok": False, "固定组合注册": True, "节点": "conc/1099",
                        "查不了": "写盘挂了", "error": str(exc)[:160]}
            self._route_log({"来源": "固定组合注册 conc/1099", "名字": name,
                             "落成节点": nid_new, "候选号": rec.get("候选号"),
                             "同序列成功": total, "序列": fields["来源序列"],
                             "ts": _dt.datetime.now().isoformat(timespec="seconds")})
            return {"ok": True, "固定组合注册": True, "节点": "conc/1099",
                    "注册了": name, "落成节点": nid_new,
                    "候选号": rec.get("候选号"), "同序列成功次数": total,
                    "序列": fields["来源序列"],
                    "步单": rec["步单"],
                    "为什么": ("步单沉淀进方向树（type=固定组合）——沉淀进树的"
                               "节点才影响倾向；缓冲区记录保持原样不动。"),
                    "问句": "以后直接点名「跑 %s」就按这条固定步单执行。" % name}

        # ② 点名执行：话命中某固定组合名（且不是问句）
        if any(h in text for h in self._FIXED_COMBO_ASK):
            return None
        hits = [(nid, n) for nid, n in self._fixed_combo_nodes()
                if str((n.fields or {}).get("name") or "") in text]
        if not hits:
            return None
        if len(hits) > 1:
            return {"ok": False, "固定组合注册": True, "节点": "conc/1099",
                    "还给人": True,
                    "为什么": "一句话命中了 %d 个固定组合（%s）——点名一个，"
                              "不猜跑哪个。" % (len(hits), "、".join(
                                  str((n.fields or {}).get("name"))
                                  for _, n in hits))}
        nid, n = hits[0]
        steps = None
        m2 = re.search(r"```json\s*(\[.*?\])\s*```", n.body or "", re.S)
        if m2:
            try:
                steps = json.loads(m2.group(1))
            except ValueError:
                steps = None
        if not steps:
            return {"ok": False, "固定组合注册": True, "节点": "conc/1099",
                    "还给人": True,
                    "为什么": "《%s》节点里没有合式的步单——如实报不编。"
                              % str((n.fields or {}).get("name"))}
        goal = str((n.fields or {}).get("goal") or text)
        r = self._compose({"要": goal, "步": steps})
        if isinstance(r, dict):
            r = dict(r)
            r["节点"] = "conc/1099"
            r["固定组合"] = str((n.fields or {}).get("name"))
            r["落成节点"] = nid
        return r

    def _confirm_usable(self, nids: list, why: str) -> int:
        """路由确认进账（v0.2.74 合取口径，2026-11-19 用户拍板）：
        「一次成功的使用就是一次确认」——成功=**能够使用**（节点能力被
        消费），不掺结果判断；结果好坏仍归人工下钻的 verdict。
        ★ 一次消费=**一笔账**：did=本轮必要节点 id 的合取
          `K=<id 字典序 | 连接>`（单节点也带 K= 前缀，单一形状好判据）——
          节点 id 是**稳定钥匙**，不随树重建换号（避开方向账 92% 死账的坑）；
          这是账本 `_safe` 注释「did 形如 D0 / K=a|b」预留的形状，从此启用。
          v0.2.73 的逐节点账（did=conc/xxx）只追加不删，新账走合取。
        qfp 带 `route:` 前缀+事件戳 ⇒ 每次消费一条（巩固语义不变）；
        snapshot 留来源节点清单备查；正文 body_note 如实写来历，
        不冒充「被下钻」。记账失败不拦主流程。"""
        if self._usage_dir is None or not nids:
            return 0
        now = self._usage_now or clock.now()
        ids = sorted(dict.fromkeys(str(x) for x in nids))
        try:
            did = "K=" + "|".join(ids)
            qfp = "route:%s@%s" % (did, now.strftime("%Y%m%dT%H%M%S%f"))
            out = usage_store.append(
                self._usage_dir, did=did, outcome=1.0, propensity=1.0,
                qfp=qfp,
                note="路由确认（%s）：能够使用=确认，非结果判断" % why,
                at=now.isoformat(timespec="microseconds"),
                snapshot=json.dumps({"kind": "route-confirm",
                                     "nodes": ids},
                                    ensure_ascii=False, sort_keys=True),
                body_note="路由确认：节点 %s 的能力在这次路由/回答里被消费"
                          "（%s）——可用性确认，结果判断不在本条。"
                          % ("、".join(ids), why))
            return 1 if out.get("written") else 0
        except Exception:
            return 0                      # 记账挂了不拦主流程（与轮次记录同策）

    def _ask_routes(self, text: str, req: list, forb: list, max_chars: int = 600):
        """《多轮》（conc/1098）包装：跑路由链，每轮落一条轮次记录
        （buffer/turns.jsonl，只追加）。记录失败不拦主流程。"""
        hit = self._ask_routes_chain(text, req, forb, max_chars)
        self._turn_record(text, hit)
        # ── 路由确认进账（v0.2.74 合取口径）：路由命中=节点能力被消费
        #    ⇒ 一次确认一笔账（did=K=节点合取）。报缺/还给人（没能用上）
        #    与链全不中（原路）不记。
        if isinstance(hit, dict) and hit.get("ok") and not hit.get("还给人"):
            _nid = str(hit.get("节点") or "")
            if _nid.startswith("conc/"):
                _n = self._confirm_usable([_nid], "路由命中")
                if _n:
                    hit["路由确认"] = _n
        return hit

    def _ask_routes_chain(self, text: str, req: list, forb: list,
                          max_chars: int = 600):
        """原话级路由链（桥只递原话，顺序在这里）：多轮 → 闲聊交外 → …
        → 直连端口。命中即返回回执；全不中 ⇒ None（走原路）。"""
        hit = self._multi_turn_rule(text, max_chars=max_chars)  # 多轮（conc/1098，参考记录）
        if hit:
            return hit
        hit = self._fixed_combo_rule(text, max_chars=max_chars)  # 固定组合注册/点名（conc/1099）
        if hit:
            return hit
        hit = self._chat_rule(text, req, forb)
        if hit:
            return hit
        hit = self._serve_rule(text)             # 拉起服务（conc/1058，创建≈拉起）
        if hit:
            return hit
        hit = self._exec_rule(text, max_chars=max_chars)   # 调用本机程序（conc/1087，白名单）
        if hit:
            return hit
        hit = self._write_code_rule(text, max_chars=max_chars)  # 写代码（conc/1089，外层LLM出码+落盘自测）
        if hit:
            return hit
        hit = self._rework_rule(text)            # 返工账派生（conc/1081，缺陷反馈⇒关联返工账）
        if hit:
            return hit
        hit = self._dev_task_rule(text)          # 研发任务执行（conc/1074）
        if hit:
            return hit
        hit = self._planner_rule(text, max_chars=max_chars)  # 组合器（conc/1075，报缺候选）
        if hit:
            return hit
        hit = self._mongo_rule(text)             # Mongo存取（conc/1059）
        if hit:
            return hit
        hit = self._recon_rule(text)             # 对账执行（conc/1060）
        if hit:
            return hit
        hit = self._procedure_rule(text, max_chars=max_chars)  # 规程执行（conc/1127）
        if hit:
            return hit
        hit = self._verb_gap_rule(text)          # 动作动词报缺（conc/1072，口语动词兜底）
        if hit:
            return hit
        hit = self._ja4_rule(text)
        if hit:
            return hit
        hit = self._save_rule(text)
        if hit:
            return hit
        hit = self._headless_rule(text, max_chars=max_chars)
        if hit:
            return hit
        hit = self._rate_rule(text)
        if hit:
            return hit
        return self._http_rule(text, max_chars=max_chars)

    def _chat_rule(self, text: str, req: list, forb: list):
        """《闲聊交外》：话要的是回应不是事实 ⇒ 不查，交回外层 agent。
        只在**节点教了**时判（名字是唯一钥匙，遮住节点即回旧行为）。
        停手线：话里带事实问头就不交——宁可查了再说。
        ★ 专业词命中不跳外（v0.2.65，2026-11-18 用户口径）：整句对词表
        切出了键 ⇒ 这句里有教过的专业性词汇，照常查词表，不许被日常
        话术截走跳到别的领域（「你好，讲讲循环」答循环，不交外）。"""
        if not self._route_node("闲聊交外"):
            return None                          # 没教 ⇒ 没这条路由（可移除）
        if any(h in text for h in _CHAT_FACT_HEADS):
            return None                          # 事实问头在场 ⇒ 照常查
        _sg = self._seg_rule(text)
        if _sg is not None and _sg["切出"]:
            return None                          # 专业词命中 ⇒ 走查询不跳外
        _hit = (any(t in text for t in _CHAT_TRIGGERS)
                or any(re.search(r"(?<![A-Za-z])%s(?![A-Za-z])" % re.escape(t), text,
                                 re.IGNORECASE) for t in _CHAT_ASCII))
        if not _hit:
            return None                          # 不是寒暄 ⇒ 走原路
        return {"ok": True, "交外": True, "节点": "conc/972",
                "为什么": "这句话是寒暄/闲聊——要的是回应，不是事实。按《闲聊交外》不查，交回外层像人一样答。",
                "停手线": "话里带事实问头（为什么/怎么/什么/多少…）就不走这条，宁可查。",
                "回答方式": "外层 agent 正常聊天；按留痕规矩，回答里标一句「这句没查资料」。"}

    @staticmethod
    def _squash_ws(s: str):
        """去空白并留索引映射：返回 (去空白串, 逐字符对应的原串下标)。
        seg 空白归一化用（v0.2.68：「C 语言」要切得中词表里的「C语言」）。"""
        squashed, idx = [], []
        for i, ch in enumerate(s):
            if not ch.isspace():
                squashed.append(ch)
                idx.append(i)
        return "".join(squashed), idx

    def _seg_rule(self, text: str):
        """《整句对词表》（conc/1018）：键里有词表外的词（或整句没翻出键）时，
        拿**原文**对照词表切一次——词表是 LDV 自己的（all_keys ∪ 已登记的声明，
        含开了词还没正文的），最长优先、不重叠。近似词不替换（只当拼错提示）。
        只在**节点教了**时走（名字是唯一钥匙，遮住节点即回旧行为）。
        返回 {"切出": [{"词","键"}…], "为什么": …}；没教 ⇒ None。"""
        if not self._route_node("整句对词表"):
            return None                          # 没教 ⇒ 没这条路由（可移除）
        keys = set(self.all_keys)
        for d in (DECL.load(Path(self._corpus_root)) or []):
            k = str(d.get("名") or "")
            if k:
                keys.add(k)                      # 开过词还没正文的也在词表里
        best: dict = {}
        for k in sorted(keys):
            if "=" in k:
                f, _, v = k.partition("=")
            else:
                f, v = "", k
            v = v.strip()
            if not v or len(v) < 2 or v == f:
                continue                         # 一字值与字段名本身不进切分（噪声）
            if v not in best or (f == "name" and best[v].partition("=")[0] != "name"):
                best[v] = k                      # 同形多字段时优先 name=（最具体）
        # ★ 对照词表**不区分大小写**（2026-11-14）：IP/UA/JA4 这类词天然带大小写，
        #    人随手打 `user-agent`、`ja4指纹` 也要切得中；键留痕仍取原句原样。
        # ★ **空白归一化**（v0.2.68，2026-11-18 用户拍板）：人随手打「C 语言」
        #    带空格，词表词是「C语言」——按去空白后的串对照切，索引映射回原文；
        #    切出的「词」仍取原句原样（连内部空格一起留痕），键不翻译不改写。
        tl, _tmap = self._squash_ws(text)
        tl = tl.lower()
        hits, consumed = [], [False] * len(text)
        for v, k in sorted(best.items(), key=lambda x: -len(x[0])):
            vl = self._squash_ws(v)[0].lower()
            if not vl:
                continue
            start = 0
            while True:
                i = tl.find(vl, start)
                if i < 0:
                    break
                o0 = _tmap[i]                       # 去空白下标 → 原文下标
                o1 = _tmap[i + len(vl) - 1]
                if not any(consumed[o0:o1 + 1]):
                    for j in range(o0, o1 + 1):
                        consumed[j] = True
                    hits.append({"词": text[o0:o1 + 1], "键": k})
                start = i + 1
        hits.sort(key=lambda h: text.find(h["词"]))
        return {"切出": hits,
                "为什么": "键里有词表外的词（或整句没翻出键）——按《整句对词表》，"
                          "整句当需求，LDV 拿原文对照自己的词表切了一次"
                          "（最长优先、不重叠；近似词不替换）。"}

    def _http_rule(self, text: str, max_chars: int = 600):
        """《直连端口》（conc/1023，v0.2.45）：原文里带 `主机:端口[/路径]`（或 http(s)
        前缀）且是让调的口气 ⇒ 真的发一次 HTTP/HTTPS，回执原样带回来。
        只在**节点教了**时走（名字是唯一钥匙，遮住节点即回旧行为）。
        停手线（语料 conc/1023）：地址不成不猜；连不上报「对方挂了」、查法不成报
        「查法不成」；每地址一次不自动重发；写方法（POST/PUT/DELETE）没点名不发。"""
        if not self._route_node("直连端口"):
            return None                          # 没教 ⇒ 没这条路由（可移除）
        if not any(w in text for w in _HTTP_INTENT):
            return None                          # 没有让调的口气 ⇒ 走原路
        addr = _parse_addr(text)
        if not addr:
            return None
        scheme, host, port, path, _exp = addr
        if port.startswith("0"):
            return None                          # 端口形态不对 ⇒ 不猜，走原路
        if not _exp and "." not in host and host != "localhost":
            return None                          # host 既没点也不是 localhost ⇒ 多半不是地址
        if not (1 <= int(port) <= 65535):
            return None
        url = f"{scheme}{host}:{port}{path}"
        # ★ 路径/查询里的非 ASCII 按 RFC 3986 百分号编码（v0.2.76）：
        #   中文参数直接塞进 URL 会在 http.client 的 ascii 编码上炸
        #   （v0.2.69 端到端实锤过「中文城市名 URL 未编码」，规程步单
        #   user=中文 再次撞上——这里统一修掉）；已有转义不动。
        if re.search(r"[^\x00-\x7f]", url):
            url = urllib.parse.quote(url, safe=":/?#[]@,$&'=+;()!*~-")

        # 方法：原文点名才发写；不点名一律 GET（停手线）。
        # ⚠ 边界用 (?<![A-Za-z])/(?![A-Za-z]) 而不是 \b —— 「发POST到」里 CJK 与
        #   ASCII 都是 \w，\b 切不出边界（D-Http4 抓到）。
        method = "GET"
        _mm = re.search(r"(?<![A-Za-z])(GET|POST|PUT|DELETE|PATCH|HEAD)(?![A-Za-z])",
                        text, re.IGNORECASE)
        if _mm:
            method = _mm.group(1).upper()
        body = None
        if method in _HTTP_WRITE:
            bj = re.search(r"\{.*\}", text, re.DOTALL)   # 请求体：原文给了 JSON 才带
            if bj:
                body = bj.group(0)

        # ★ UA 定制（conc/1023 动作补课）：原文给 `UA=xxx` 就用它发——
        #    UA 技术的直接使用（conc/1022：客户端自报可伪造，这里正是「伪造」这步）。
        _ua_m = re.search(r"(?<![A-Za-z])(?:User-Agent|UA)(?![A-Za-z])\s*[=:：]\s*([^\s，。；]+)",
                          text, re.IGNORECASE)
        _ua = _ua_m.group(1) if _ua_m else "LDV-direct/0.2"
        req = urllib.request.Request(url, method=method,
                                     data=body.encode("utf-8") if body else None,
                                     headers={"User-Agent": _ua})
        # ★ 环回地址绕开系统代理直连（代理会替环回收发，连不上的真话就被它盖了）；
        #   外部主机仍走环境代理（爬源那套口径不变）。
        if host in ("localhost", "::1") or host.startswith("127."):
            _opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        else:
            _opener = urllib.request.build_opener()
        t0 = time.perf_counter()
        try:
            with _opener.open(req, timeout=10) as resp:
                raw = resp.read(1024 * 256)
                code, ctype = resp.status, resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:  # HTTP 4xx/5xx 也是「调成了，对方这么答」
            raw, code, ctype = (e.read(1024 * 256) if e.fp else b""), e.code, ""
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            why = str(getattr(e, "reason", e) or e)
            self._route_log({"来源": "直连端口 conc/1023", "方法": method,
                             "地址": url, "结果": "对方挂了", "error": why[:160]})
            _fail = {"ok": False, "直连端口": True, "节点": "conc/1023",
                     "查不了": "对方挂了", "调了": {"方法": method, "地址": url},
                     "error": f"{method} {url} 没调成：{why[:160]}",
                     "为什么": "按《直连端口》真的发了，但对方连不上/超时——如实报，"
                               "不自动重发（每地址一次；要不要再发由人说）。"}
            # ★ 《直连挂了换无头》（conc/1027，v0.2.47）：连接层挂了且原文没点名
            #   浏览器 ⇒ 对同一地址换无头真试一次（接力只此一步，conc/1024 口径）。
            _rl = self._relay_headless(text, url, method, why, max_chars)
            return _rl if _rl else _fail
        ms = int((time.perf_counter() - t0) * 1000)
        self._route_log({"来源": "直连端口 conc/1023", "方法": method,
                         "地址": url, "结果": str(code), "耗时ms": ms})
        body_txt = raw.decode("utf-8", errors="replace")
        trunc = None
        if len(body_txt) > max_chars:
            trunc = f"正文 {len(body_txt)} 字符，截前 {max_chars}"
            body_txt = body_txt[:max_chars]
        return {"ok": True, "直连端口": True, "节点": "conc/1023",
                "调了": {"方法": method, "地址": url, "状态码": code,
                         "耗时ms": ms, "类型": ctype, "UA": _ua,
                         **({"正文": body_txt} if body_txt else {}),
                         **({"留痕": trunc} if trunc else
                            ({"留痕": "正文全量带出"} if body_txt else {}))},
                "为什么": "原文里带主机:端口地址——按《直连端口》真的发了 "
                          f"{method} {url}，回执原样带回来。",
                "下一步": "要不要再调、换路径调，由人说；本条只发了一次，不自动重发。"}

    def _headless_rule(self, text: str, max_chars: int = 600):
        """《无头浏览器直取》（conc/1024，v0.2.46）：地址 + 无头/浏览器/截图 词在场
        ⇒ 真驱动一次本机无头浏览器（--dump-dom 取渲染后 DOM / --screenshot 截图）。
        只在节点教了时走；没地址不猜；没装浏览器如实报。"""
        if not self._route_node("无头浏览器直取"):
            return None
        tl = text.lower()
        if not any(w in tl for w in _HEADLESS_INTENT):
            return None                          # 没说要用浏览器 ⇒ 走原路
        addr = _parse_addr(text)
        if not addr:
            return None                          # 没地址 ⇒ 不猜，走原路
        scheme, host, port, path, _exp = addr
        url = scheme + host + ":" + port + path
        exe, bname = _find_browser()
        if not exe:
            return {"ok": False, "无头直取": True, "节点": "conc/1024",
                    "查不了": "查法不成",
                    "error": "本机没找到 Edge/Chrome（查了 Program Files 标准路径）"
                             "——按《无头浏览器直取》没内核就不猜不装。",
                    "为什么": "无头直取的载体是本机 Edge/Chrome 的 --headless 模式；"
                              "没有载体就如实报，不冒充取到了。"}
        shot = ("截图" in tl or "快照" in tl)
        t0 = time.perf_counter()
        tmp_png = None
        if shot:
            import tempfile
            fd, tmp_png = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            argv = [exe, "--headless", "--disable-gpu",
                    "--screenshot=" + tmp_png, "--window-size=1280,800", url]
        else:
            argv = [exe, "--headless", "--disable-gpu", "--dump-dom",
                    "--virtual-time-budget=5000", url]
        try:
            cp = subprocess.run(argv, capture_output=True, timeout=30,
                                creationflags=0x08000000)      # CREATE_NO_WINDOW
        except subprocess.TimeoutExpired:
            self._route_log({"来源": "无头直取 conc/1024", "浏览器": bname,
                             "地址": url, "结果": "对方挂了", "error": "30 秒没回来"})
            return {"ok": False, "无头直取": True, "节点": "conc/1024",
                    "查不了": "对方挂了",
                    "调了": {"浏览器": bname, "地址": url, "模式": "截图" if shot else "取DOM"},
                    "error": "无头浏览器 30 秒没回来——按《无头浏览器直取》停手，不猜不重发。"}
        ms = int((time.perf_counter() - t0) * 1000)
        if shot:
            size = os.path.getsize(tmp_png) if os.path.isfile(tmp_png) else 0
            if not size:
                self._route_log({"来源": "无头直取 conc/1024", "浏览器": bname,
                                 "地址": url, "结果": "截图没生成"})
                return {"ok": False, "无头直取": True, "节点": "conc/1024",
                        "查不了": "对方挂了",
                        "调了": {"浏览器": bname, "地址": url, "模式": "截图", "耗时ms": ms},
                        "error": "截图没生成（退出码 %s）：%s——原样报，不猜页面内容。"
                                 % (cp.returncode, (cp.stderr or b"").decode("utf-8", "replace")[:140])}
            self._route_log({"来源": "无头直取 conc/1024", "浏览器": bname,
                             "地址": url, "结果": "截图 %d 字节" % size, "耗时ms": ms})
            return {"ok": True, "无头直取": True, "节点": "conc/1024",
                    "调了": {"浏览器": bname, "地址": url, "模式": "截图",
                             "截图": tmp_png, "字节数": size, "耗时ms": ms},
                    "为什么": "按《无头浏览器直取》真驱动了 %s 无头模式，渲染并截了图。" % bname,
                    "下一步": "截图文件留在上面那个路径；要不要再截/换尺寸由人说。"}
        dom = cp.stdout.decode("utf-8", errors="replace")
        if not dom.strip():
            self._route_log({"来源": "无头直取 conc/1024", "浏览器": bname,
                             "地址": url, "结果": "没取到 DOM"})
            return {"ok": False, "无头直取": True, "节点": "conc/1024",
                    "查不了": "对方挂了",
                    "调了": {"浏览器": bname, "地址": url, "模式": "取DOM", "耗时ms": ms},
                    "error": "无头浏览器跑了但没取到 DOM（退出码 %s）：%s——原样报，不猜。"
                             % (cp.returncode, (cp.stderr or b"").decode("utf-8", "replace")[:140])}
        self._route_log({"来源": "无头直取 conc/1024", "浏览器": bname,
                         "地址": url, "结果": "DOM %d 字节" % len(dom), "耗时ms": ms})
        trunc = None
        if len(dom) > max_chars:
            trunc = f"DOM {len(dom)} 字符，截前 {max_chars}"
            dom = dom[:max_chars]
        return {"ok": True, "无头直取": True, "节点": "conc/1024",
                "调了": {"浏览器": bname, "地址": url, "模式": "取DOM",
                         "DOM字节数": len(dom), "耗时ms": ms,
                         "正文": dom,
                         "留痕": trunc or "DOM 截到上限内全量带出"},
                "为什么": "按《无头浏览器直取》真驱动了 %s --headless 取渲染后 DOM——"
                          "这是无头浏览器技术的直接使用（检测面见 conc/1020）。" % bname}

    def _relay_headless(self, text: str, url: str, method: str,
                        why: str, max_chars: int):
        """《直连挂了换无头》（conc/1027，v0.2.47）：直连连接层挂了 ⇒ 对同一地址
        换无头浏览器真试一次（conc/1024 口径）。名字是唯一钥匙：conc/1027 或
        conc/1024 任一没教就不接（直连挂了照旧停手交回）；原文点名了浏览器词的
        本来就该无头路由先走，这里不再接力。接力只此一步：无头也没拿到，两次
        各报各的错，不猜不冒充、不发第三次（4xx/5xx 是对方活着这么答，不接力）。"""
        if not self._route_node("直连挂了换无头"):
            return None
        if not self._route_node("无头浏览器直取"):
            return None                       # 无头本体没教 ⇒ 接力无从走
        if any(w in text.lower() for w in _HEADLESS_INTENT):
            return None                       # 点名浏览器 ⇒ 不经这条（保险）
        hl = self._headless_rule("无头浏览器抓取 " + url, max_chars=max_chars)
        if hl is None:
            return None                       # 理论到不了（地址在场）；保险回旧行为
        if hl.get("ok"):
            return {"ok": True, "直连端口": True, "换了无头": True,
                    "节点": "conc/1023 → conc/1027 → conc/1024",
                    "直连挂了": {"方法": method, "地址": url,
                                 "error": f"{method} {url} 没调成：{why[:160]}"},
                    "调了": hl.get("调了"),
                    "为什么": "直连挂了（%s），按《直连挂了换无头》对同一地址换无头"
                              "浏览器真试了一次——这次拿到了。" % why[:100],
                    "下一步": "直连一次＋无头一次，共两次；要不要再调由人说。"}
        return {"ok": False, "直连端口": True, "换了无头": True,
                "节点": "conc/1023 → conc/1027 → conc/1024",
                "直连挂了": {"方法": method, "地址": url,
                             "error": f"{method} {url} 没调成：{why[:160]}"},
                "无头也没拿到": hl.get("调了"),
                "查不了": "对方挂了",
                "error": hl.get("error"),
                "为什么": "直连挂了（%s），按《直连挂了换无头》换无头又真试了一次"
                          "——两次都没拿到，如实报，不猜不第三次。" % why[:100],
                "下一步": "两次都停手了；换地址/换法/放弃由人说。"}

    def _save_rule(self, text: str):
        """《爬到落盘》（conc/1028，v0.2.48）：地址 + 存/保存/落盘/到本地 词在场
        ⇒ 真取一次（原文点名浏览器/无头走《无头浏览器直取》；否则直连，直连挂了
        自走 conc/1027 接力），取到了把**完整正文**写进语料根 `_crawled/`。
        停手线：没地址不猜；取不到不落盘——拿不到的东西不冒充存了；正文空的
        不落盘；只写语料根 `_crawled/`，写别处由人说；文件写失败如实报。
        只在**节点教了**时走（名字是唯一钥匙，遮住节点即回旧行为）。"""
        if not self._route_node("爬到落盘"):
            return None
        if not _has_save_intent(text):
            return None                          # 没说要存 ⇒ 走原路
        addr = _parse_addr(text)
        if not addr:
            return None                          # 没地址 ⇒ 不猜
        scheme, host, port, path, _exp = addr
        url = scheme + host + ":" + port + path
        _head = {"方法": "GET", "地址": url}
        if any(w in text.lower() for w in _HEADLESS_INTENT):
            fr = self._headless_rule("无头浏览器抓取 " + url, max_chars=50_000_000)
            if fr is not None:
                _head = {k: v for k, v in (fr.get("调了") or {}).items() if k != "正文"}
                _head["来源"] = "无头直取"
        else:
            fr = self._http_rule("访问 " + url, max_chars=50_000_000)
            if fr is None:                       # 理论到不了（地址+访问都是意图）；保险
                return None
            _head = {k: v for k, v in (fr.get("调了") or {}).items() if k != "正文"}
            _head["来源"] = ("直连→换无头" if fr.get("换了无头") else "直连")
        if not fr.get("ok"):
            return {"ok": False, "爬到落盘": True, "节点": "conc/1028",
                    "调了": _head, "查不了": fr.get("查不了"),
                    "error": fr.get("error"),
                    "为什么": "《爬到落盘》先要取到才存——这单没取到（"
                              + str(fr.get("为什么") or "")[:110] + "），"
                              "不落盘、不冒充存了。"}
        body = str((fr.get("调了") or {}).get("正文") or "")
        if not body.strip():
            return {"ok": False, "爬到落盘": True, "节点": "conc/1028",
                    "调了": _head,
                    "error": "取到了但正文是空的——按《爬到落盘》空的不落盘。",
                    "为什么": "空正文落盘就是冒充存了东西，不做。"}
        _root = Path(self._corpus_root).parent.parent       # 语料工作根（ldv-work）
        _dir = _root / "_crawled"
        try:
            _dir.mkdir(parents=True, exist_ok=True)
            _ct = str(_head.get("类型") or "")
            _ext = ".html" if ("html" in _ct or fr.get("无头直取")) else ".txt"
            _slug = re.sub(r"[^\w.\-]+", "_", "%s:%s%s" % (host, port, path))[:60] or "page"
            _fp = _dir / ("%s_%s%s" % (_slug, time.strftime("%H%M%S"), _ext))
            _fp.write_text(body, encoding="utf-8")
        except OSError as e:
            return {"ok": False, "爬到落盘": True, "节点": "conc/1028",
                    "调了": _head,
                    "error": "文件没写成：%s——原样报，不猜。" % e,
                    "为什么": "按《爬到落盘》写盘失败就如实报，不冒充存了。"}
        return {"ok": True, "爬到落盘": True, "节点": "conc/1028",
                "调了": _head,
                "落盘": {"文件": str(_fp), "字节数": _fp.stat().st_size,
                         "预览": body[:300]},
                "为什么": "按《爬到落盘》真的取了一次、把完整正文存进了本地"
                          "（%s 口径）——只取一次存一次，不重发。" % _head.get("来源"),
                "下一步": "文件在「落盘.文件」那个路径；要不要再爬、换页、挪文件由人说。"}

    def _rate_rule(self, text: str):
        """《限速连调》（conc/1025，v0.2.46）：地址 + 速率 + 次数三样齐 ⇒ 按节奏真发
        多次 GET；429/503 原样记，不自动退避重试。缺一样就还给人问补全。"""
        if not self._route_node("限速连调"):
            return None
        tl = text.lower()
        if not any(w in tl for w in _RATE_WORDS) or not _parse_addr(text):
            return None
        m = (re.search(r"每秒\s*(\d+)\s*次", text) or re.search(r"(\d+)\s*qps", tl)
             or re.search(r"限速\s*(\d+)", text) or re.search(r"速率\s*(\d+)", text))
        rate = int(m.group(1)) if m else None
        mt = re.search(r"(\d+)\s*次", text)
        times = int(mt.group(1)) if mt else None
        if not rate or not times:
            return {"ok": False, "限速连调": True, "节点": "conc/1025", "还给人": True,
                    "error": "说了限速，但速率和次数没给全——按《限速连调》不猜。"
                             "说成「按每秒N次调 地址 M次」就能跑。",
                    "为什么": "限速连调三样齐才走：地址、速率、次数；缺一样问人。"}
        if times > 20 or rate > 50:
            return {"ok": False, "限速连调": True, "节点": "conc/1025", "还给人": True,
                    "error": "次数 %d / 速率 %d 超了界限（次数≤20、速率≤50）——"
                             "超界不猜，人来确认再跑。" % (times, rate),
                    "为什么": "按《限速连调》停手线：超界不自动跑。"}
        ma = _parse_addr(text)
        scheme, host, port, path, _exp = ma
        url = scheme + host + ":" + port + path
        _opener = (urllib.request.build_opener(urllib.request.ProxyHandler({}))
                   if (host in ("localhost", "::1") or host.startswith("127."))
                   else urllib.request.build_opener())
        interval = 1.0 / rate
        results, t0 = [], time.perf_counter()
        for i in range(times):
            wait = t0 + i * interval - time.perf_counter()
            if wait > 0:
                time.sleep(wait)
            c0 = time.perf_counter()
            try:
                rq = urllib.request.Request(url, headers={"User-Agent": "LDV-rate/0.2"})
                with _opener.open(rq, timeout=10) as resp:
                    code = resp.status
            except urllib.error.HTTPError as e:   # 429/503 也是对方答了——原样记
                code = e.code
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                self._route_log({"来源": "限速连调 conc/1025", "地址": url,
                                 "结果": "第 %d 次没调成" % (i + 1),
                                 "error": str(getattr(e, "reason", e) or e)[:100]})
                return {"ok": False, "限速连调": True, "节点": "conc/1025",
                        "查不了": "对方挂了",
                        "调了": {"地址": url, "速率": "每秒%d次" % rate,
                                 "次数": times, "已完成": results},
                        "error": "第 %d 次没调成（%s）——按《限速连调》停手，"
                                 "不自动退避重试，退不退由人说。"
                                 % (i + 1, str(getattr(e, "reason", e) or e)[:100])}
            results.append({"次": i + 1, "状态码": code,
                            "耗时ms": int((time.perf_counter() - c0) * 1000)})
        total = int((time.perf_counter() - t0) * 1000)
        limited = [x for x in results if x["状态码"] in (429, 503)]
        avg_gap = (int((total - sum(x["耗时ms"] for x in results)) / (times - 1))
                   if times > 1 else 0)
        self._route_log({"来源": "限速连调 conc/1025", "地址": url,
                         "结果": "%d 次全发完" % times, "总耗时ms": total})
        return {"ok": True, "限速连调": True, "节点": "conc/1025",
                "调了": {"地址": url, "速率": "每秒%d次" % rate, "次数": times,
                         "各次": results, "总耗时ms": total,
                         **({"被限流的次数": len(limited)} if limited else {}),
                         "节奏": ("实际平均间隔≈%dms（目标 %dms）" % (avg_gap, int(interval * 1000))
                                  if times > 1 else "单次")},
                "为什么": "按《限速连调》真的按节奏发了 %d 次、每次间隔≈%.0fms"
                          "——客户端守节奏，别把对方打挂。" % (times, interval * 1000)}

    def _ja4_rule(self, text: str):
        """《JA4自探》（conc/1026，v0.2.46）：原文说算/探 JA4 ⇒ 自起本地 TLS 捕获端，
        让自己的 ssl 客户端去连，抓它发出的 ClientHello 原始字节，按 JA4 口径算指纹。
        只能探自己发出的；算对方的要对方给或抓包，不猜。"""
        if not self._route_node("JA4自探"):
            return None
        tl = text.lower()
        if "ja4" not in tl:
            return None
        if not any(w in tl for w in ("算", "探", "测", "自探")) or "什么" in tl:
            return None                          # 问「什么是JA4」⇒ 查词条，不自探
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        holder = {"raw": b""}

        def _cap():
            try:
                conn, _ = srv.accept()
                conn.settimeout(5)
                data = b""
                while len(data) < 5:
                    chunk = conn.recv(5 - len(data))
                    if not chunk:
                        break
                    data += chunk
                if len(data) >= 5 and data[0] == 0x16:
                    need = 5 + int.from_bytes(data[3:5], "big")
                    while len(data) < need:
                        chunk = conn.recv(need - len(data))
                        if not chunk:
                            break
                        data += chunk
                holder["raw"] = data
                conn.close()
            except OSError:
                pass

        def _probe():
            try:
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                s = socket.create_connection(("127.0.0.1", port), timeout=5)
                ss = ctx.wrap_socket(s)          # 无 server_hostname ⇒ 不发 SNI
                ss.close()
            except Exception:
                pass                             # 对方（捕获端）不完成握手是设计如此
        th = threading.Thread(target=_cap, daemon=True); th.start()
        threading.Thread(target=_probe, daemon=True).start()
        th.join(timeout=10)
        srv.close()
        if not holder["raw"]:
            return {"ok": False, "JA4自探": True, "节点": "conc/1026",
                    "查不了": "查法不成",
                    "error": "没抓到 ClientHello 原始字节——按《JA4自探》如实报拆解失败，不编指纹。"}
        hello = _parse_client_hello(holder["raw"])
        if not hello:
            return {"ok": False, "JA4自探": True, "节点": "conc/1026",
                    "查不了": "查法不成",
                    "error": "抓到了 %d 字节但拆不成 ClientHello——原样报，不编。" % len(holder["raw"])}
        comp = _ja4_of(hello)
        self._route_log({"来源": "JA4自探 conc/1026", "结果": comp["指纹"]})
        return {"ok": True, "JA4自探": True, "节点": "conc/1026",
                "指纹": comp["指纹"],
                "调了": {"探的是": "LDV 自己的 TLS 客户端（本机 Python ssl）",
                         "组成": {k: comp[k] for k in
                                  ("版本", "SNI", "扩展数", "密码数", "alpn")}},
                "密码套件": comp["密码套件"], "扩展": comp["扩展"],
                "签名算法": comp["签名算法"],
                "依据": "按 JA4 规范口径（FoxIO-LLC/ja4）自算：哈希=sha256 前 12 位，"
                        "扩展按字典序排、SNI 挪尾、签名算法接尾；**未与官方工具对拍**"
                        "（无外网依赖）——组成件全摆出，可人工审计。",
                "为什么": "按《JA4自探》真的起了一个本地 TLS 捕获端，抓到了自己发出的 "
                          "ClientHello 并逐字段拆解算出指纹——JA4 技术的直接使用。"}

    # ── 《拉起服务》（conc/1058）/《Mongo存取》（conc/1059）/《对账执行》（conc/1060）──
    # 2026-11-15 对账职位考试：服务真拉起、数据真取、对账真跑。算子在这（v0.2.49 正位）。

    def _services_registry(self) -> dict:
        """服务注册表：语料根 `_services.json`（环境里的文件，服务名→exe/参数/端口）。
        没有/坏了 ⇒ {}——注册表是环境的，我不编。"""
        try:
            base = (Path(self._corpus_root) if getattr(self, "_corpus_root", None)
                    else None)
            if base is not None:
                p = base / "_services.json"
                if p.is_file():
                    return json.loads(p.read_text(encoding="utf-8") or "{}")
        except (OSError, ValueError):
            pass
        return {}

    def _service_start(self, name: str, reg: dict) -> dict:
        """按注册表拉起一个服务：先探健康（已在跑不重拉），没在真起，
        健康有界轮询（30×1s）。起不来停手交回，不改参数重试。"""
        import time as _t
        e = reg.get(name) or {}
        exe = str(e.get("exe") or "")
        args = [str(a) for a in (e.get("args") or [])]
        port = e.get("port")
        host = str(e.get("host") or "127.0.0.1")
        if not exe or not os.path.isfile(exe):
            return {"ok": False, "拉起服务": True, "服务": name, "查不了": "查法不成",
                    "error": f"注册表里「{name}」的 exe 不存在：{exe}——如实报，不猜路径。"}
        t0 = _t.time()
        if port and _tcp_up(host, port, timeout=1.5):
            self._route_log({"来源": "拉起服务 conc/1058", "服务": name,
                             "结果": "已在跑", "端口": port})
            return {"ok": True, "拉起服务": True, "节点": "conc/1058", "服务": name,
                    "已在跑": True, "端口": port, "耗时毫秒": int((_t.time() - t0) * 1000),
                    "为什么": "健康探到端口已通——服务已在跑，按《拉起服务》不重拉。"}
        logp = Path(self._corpus_root).parent.parent / "_services_logs" / f"{name}.log"
        try:
            logp.parent.mkdir(parents=True, exist_ok=True)
            lf = logp.open("ab")
            proc = _subproc.Popen([exe] + args, stdout=lf, stderr=lf,
                                  creationflags=_DETACHED)
            pid = proc.pid
        except OSError as exc:
            self._route_log({"来源": "拉起服务 conc/1058", "服务": name,
                             "结果": "启动失败", "error": str(exc)[:100]})
            return {"ok": False, "拉起服务": True, "节点": "conc/1058", "服务": name,
                    "查不了": "查法不成",
                    "error": f"启动失败：{exc}——不改参数重试，改参数是人的事。"}
        healthy = False
        for _ in range(30):
            if port is None or _tcp_up(host, port, timeout=1.0):
                healthy = True
                break
            _t.sleep(1.0)
        ms = int((_t.time() - t0) * 1000)
        if healthy:
            self._route_log({"来源": "拉起服务 conc/1058", "服务": name, "结果": "起了",
                             "pid": pid, "端口": port, "耗时毫秒": ms})
            return {"ok": True, "拉起服务": True, "节点": "conc/1058", "服务": name,
                    "端口": port, "pid": pid, "健康": True, "耗时毫秒": ms,
                    "日志": str(logp),
                    "为什么": "按注册表真起了进程（独立驻留），健康探到端口通——"
                              "这是《拉起服务》的直接使用。"}
        tail = ""
        try:
            tail = logp.read_text(encoding="utf-8", errors="replace")[-400:]
        except OSError:
            pass
        self._route_log({"来源": "拉起服务 conc/1058", "服务": name, "结果": "起不来"})
        return {"ok": False, "拉起服务": True, "节点": "conc/1058", "服务": name,
                "pid": pid, "查不了": "起不来", "日志尾": tail,
                "error": "健康探到上限（30×1s）还没通——停手交回，日志尾原样带，"
                         "不改参数重试。"}

    def _verb_gap_rule(self, text: str, max_chars: int = 600):
        """《动作动词报缺》（conc/1072）：问句带创建类动词（创建/搭建/实现）
        且不是问方法 ⇒ 「创建这个动作我没有」要如实声明，能给的给规程。
        服务类创建已归《拉起服务》（创建≈拉起，2026-11-16 用户口径）。
        宾语词表切得出 ⇒ 查出规程当设计资料 + 声明；切不出 ⇒ 还给人报缺。
        节点没教走旧路（可移除）。"""
        if not self._route_node("动作动词报缺"):
            return None
        verbs = [w for w in _VERB_GAPS if w in text]
        if not verbs and re.search(r"写.{0,12}?(系统|程序|脚本|工具|模块|代码)",
                                   text):
            verbs = ["写"]               # 弱动词带造物宾语才算（2026-11-17 教编码课程：
                                         # 「写语料/写回执/写报告」不算创建，「写…系统/程序/代码」才算）
        if not verbs:
            return None                          # 没有创建类动词 ⇒ 不归这条路由
        if any(h in text for h in _VERB_QUESTION_HEADS):
            return None                          # 问方法（怎么实现X）⇒ 走旧路照查
        sg = self._seg_rule(text)
        if sg is None:
            return None
        keys = [h["键"] for h in sg["切出"]]
        if not keys:
            return {"ok": False, "动词落空": True, "节点": "conc/1072",
                    "还给人": True, "动词": verbs,
                    "error": "这句话带着创建类动词（%s），但宾语在我的词表里切不出来。"
                             % "、".join(verbs),
                    "为什么": "我没有创建东西的能力，宾语又没有教过的词条——"
                              "不猜不编：要么说清要什么，要么先教词条。"}
        r = self.query({"require": keys})
        if not r or not r.get("ok"):
            return {"ok": False, "动词落空": True, "节点": "conc/1072",
                    "还给人": True, "动词": verbs,
                    "error": "词表里切出了键（%s）但没查到正文。" % "、".join(keys),
                    "为什么": "开了词还没教内容，规程没教——教了才查得到。"}
        # 条目组装同 server.py::_tool_ask 口径（query 只给 ids，正文在这组）
        _items = []
        for _nid in sorted(r.get("answer_ids") or []):
            _nd = self.nodes.get(_nid)
            if _nd is None:
                continue
            _f = _nd.fields or {}
            _body = (_nd.body or "").strip()
            _items.append({"n": len(_items) + 1,
                           "title": str(_f.get("title", "") or ""),
                           "text": _body[:max_chars],
                           "source": _f.get("source", ""),
                           "filled_by": str(_f.get("filled_by", "") or ""),
                           "truncated": bool(max_chars > 0
                                             and len(_body) > max_chars)})
        self._route_log({"来源": "动作动词报缺 conc/1072", "动词": verbs,
                         "切出": keys, "命中": r.get("n_answer")})
        r["items"] = _items
        # ★ 《正式开发创建流程》（conc/1073，2026-11-16 用户口径：「正式的开发
        #   创建流程应该和研发流程一样」）：创建类动词落在要正式开发的东西上
        #   ⇒ 回执**附「该走的流程」**（九阶段）——声明「动作我没有」必须带着
        #   「该走哪条路」，不是光拒。节点在才附（名字唯一钥匙，可移除）。
        if self._route_node("正式开发创建流程"):
            _rp = self.query({"require": ["name=正式开发创建流程"]})
            if _rp and _rp.get("ok") and _rp.get("n_answer"):
                _rnid = sorted(_rp.get("answer_ids") or [])
                _rn = self.nodes.get(_rnid[0]) if _rnid else None
                if _rn is not None:
                    _rf = _rn.fields or {}
                    _rbody = (_rn.body or "").strip()
                    r["该走的流程"] = {
                        "title": str(_rf.get("title", "") or ""),
                        "text": _rbody[:max_chars],
                        "truncated": bool(max_chars > 0
                                          and len(_rbody) > max_chars),
                        "为什么": ("创建类动词落在要正式开发的东西上——流程就是"
                                   "《研发全流程》九阶段；声明动作没有的同时把"
                                   "该走的路一并给。")}
        r["动词落空"] = {"动词": verbs,
                        "声明": ("创建这个动作我没有——%s 在我的词表里是概念不是能力，"
                                 "能给的是它的规程。" % "、".join(keys))}
        r["对词表切分"] = {"节点": "conc/1072", "切出": sg["切出"],
                          "为什么": sg["为什么"]}
        r["节点"] = "conc/1072"
        # ★ 报缺不停在规程（v0.2.55，2026-11-16 用户口径：「让它自己报缺词，
        #   然后完成这个系统」）：声明的同时按《研发任务执行》直接开账，回执带
        #   账本状态/第 1 段任务卡；已有账幂等如实报（不覆盖）。遮 1074 ⇒ 只报缺。
        if self._route_node("研发任务执行"):
            _name = self._verb_gap_taskname(text, sg)
            if _name:
                r["研发任务"] = self._dev_task_rule(
                    "开研发任务 %s：需求是%s" % (_name, text)) or {}
        return r

    def _verb_gap_taskname(self, text: str, sg=None):
        """任务名=**词表命中的造物词**优先（v0.2.68 账名收紧，2026-11-18
        用户拍板）：先拿词表切分结果（调用方已切的 sg，判据直调时现切），
        切出里带造物后缀（系统/程序/脚本/工具/模块）的最长命中直接当账名——
        词表里的叫法是唯一钥匙，不靠剔表猜（「用 C 语言写一个登录系统」旧法
        剔出「语言写一个登录系统」——歪名源头）。词表没中造物词 ⇒ 造物宾语
        优先（v0.2.64）：找「X系统/X程序/X脚本/X工具/X模块」词组
        （如「一个登录系统」），剔掉量词/创建动词前缀取「登录系统」；
        再没有 ⇒ 退回旧法（动词后宾语段）。
        名字是唯一钥匙——原话里的叫法原样进账，不翻译不改写。"""
        if sg is None:
            sg = self._seg_rule(text)
        for h in (sg or {}).get("切出") or []:
            _w = re.sub(r"^(?:一个|个|的|做|写|开发|创建|搭|搭建|实现|搞|弄)+",
                        "", str(h.get("词") or "").strip())
            if (_w and _w.endswith(("系统", "程序", "脚本", "工具", "模块"))
                    and _w not in _VERB_GAPS):
                return _w[:40]                   # 切出按原句序（先到先得）
        m = re.search(r"[^\s，。,：:；;！!？?]{1,8}?(?:系统|程序|脚本|工具|模块)",
                      text)
        if m:
            _w = re.sub(r"^(?:一个|个|的|做|写|开发|创建|搭|搭建|实现|搞|弄)+",
                        "", m.group(0))
            if _w and _w not in _VERB_GAPS:
                return _w[:40]
        name = text
        for w in _VERB_GAPS:
            name = name.replace(w, " ", 1)
        name = re.sub(r"^[一个\s]+", "", name.strip())
        name = re.split(r"[，。,：:；;！!？?\s]", name)[0].strip("的 ")
        return name[:40] if name else None

    # ── 组合器 + 缓冲区（v0.2.53，conc/1075/1076）──────────────────────
    # 用户口径（2026-11-16）：「允许报缺候选，但是它自己的组合步单不允许直接
    # 成为固定路由，任务完成后只能先作为历史记录，在缓冲区域建树，然后依照
    # 缓冲区域的晋升规则离开缓冲区。」规矩在语料；这里只放算子：
    # 拆步、亮候选、人拍板才执行、落缓冲树、满 3 次提名。

    def _buffer_path(self):
        """缓冲区位置：语料根 buffer/plans.jsonl（不是 nodes 语料、不是组合点名表）。"""
        from pathlib import Path as _P
        nd = self._nodes_dir()
        base = (_P(self._corpus_root) if hasattr(self, "_corpus_root")
                else (nd.parent if nd else _P(".")))
        return base / "buffer" / "plans.jsonl"

    def _buffer_load(self) -> list:
        try:
            p = self._buffer_path()
            if not p.is_file():
                return []
            out = []
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    out.append(json.loads(line))
            return out
        except (OSError, ValueError):
            return []

    def _buffer_save(self, recs: list):
        """整文件重写（条目量小）；先写 .tmp 再替换，写坏了不毁旧账。"""
        try:
            p = self._buffer_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                for rec in recs:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            tmp.replace(p)
            return str(p)
        except OSError as e:
            return "buffer 写不进去：%s" % e

    @staticmethod
    def _plan_fp(steps) -> str:
        """步单指纹：同一条步单认这个（参数也算——改了参数就是另一条步单）。
        ⚠️ v0.2.67（2026-11-18 用户拍板「步路由序列计数」）起，晋升计数
        改按 _plan_seq 认——指纹只留档备查，不再是计数的钥匙。"""
        import hashlib as _h
        return _h.sha1(json.dumps(steps, ensure_ascii=False, sort_keys=True)
                       .encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _plan_seq(rec) -> list:
        """步路由序列（v0.2.67）：晋升计数按这个认「同一条路」——
        序列相同即同款合取累计，**参数不算**（参数变序列不变）。
        从步单 [{路由: spec}, …] 提取路由键；旧记录没存序列也现算得出。"""
        out = []
        for s in (rec.get("步单") if isinstance(rec, dict) else None) or []:
            if isinstance(s, dict) and s:
                out.append(next(iter(s)))
        return out

    def _plan_steps(self, text: str):
        """动词→步单候选。参数从原话里拿，拿不到标缺口——不编。
        每步的 spec 形状 = 组合执行器认的形状：{路由: 参数} 只一个键。"""
        steps, gaps = [], []
        used_routes = set()
        addr = _parse_addr(text)
        for w in _PLAN_VERBS:
            if w not in text:
                continue
            route = _PLAN_ROUTE_OF.get(w)
            if route is None:
                gaps.append("动词「%s」拼不出路由——这步如实说缺，不硬拼" % w)
                continue
            if route in used_routes:
                continue                     # 同一路由不重复拼（抓下来+抓取=一步）
            used_routes.add(route)
            if route == "http":
                if not addr:
                    gaps.append("「%s」缺地址（主机:端口）——原话里没给，不编" % w)
                    steps.append({"序": len(steps) + 1, "路由": "http",
                              "spec": {"http": {"方法": "GET"}}, "缺口": ["地址"],
                              "零件来源": "动词表 + 《直连端口》（conc/1023）"})
                    continue
                _scheme, _h0, _p0, _path, _e = addr
                steps.append({"序": len(steps) + 1, "路由": "http",
                              "spec": {"http": {"方法": "GET",
                                                "地址": _scheme + _h0 + ":" + _p0 + _path}},
                              "零件来源": "原话地址 + 《直连端口》（conc/1023）"})
            elif route == "mongo":
                # 地址跟注册表走（_mongo_addr：注册表 mongodb 段优先，退 trigger.json）
                # ——_mongo(spec) 自己只看 spec 主机/端口与 trigger.json，不读注册表，
                #    步单不带地址就会写错实例。
                _mh, _mp, _mc = self._mongo_addr()
                mdb = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*库", text)
                mcol = (re.search(r"的\s*([A-Za-z_][A-Za-z0-9_]*)\s*集合", text)
                        or re.search(r"集合\s*[:：=]\s*([A-Za-z_][A-Za-z0-9_]*)", text))
                _g = []
                if not mdb:
                    _g.append("库")
                if not mcol:
                    _g.append("集合")
                if not used_routes - {"mongo"}:
                    _g.append("存的内容没来路（前面没有抓取步）")
                spec_m = {"op": "insert", "主机": _mh, "端口": _mp}
                if mdb:
                    spec_m["库"] = mdb.group(1)
                if mcol:
                    spec_m["集合"] = mcol.group(1)
                if not (used_routes - {"mongo"}):
                    spec_m["文档"] = {"来源": "原话"}
                else:
                    spec_m["文档"] = {"来源": "«上一步»"}
                steps.append({"序": len(steps) + 1, "路由": "mongo",
                              "spec": {"mongo": spec_m},
                              **({"缺口": _g} if _g else {}),
                              "零件来源": "原话 + 《Mongo存取》（conc/1059）"})
                if _g:
                    gaps.append("「%s」缺 %s——不编" % (w, "、".join(_g)))
            elif route == "对账":
                md = re.search(r"(\d{4}-\d{2}-\d{2})", text)
                if not md:
                    gaps.append("「%s」缺日期（YYYY-MM-DD）——不猜默认日" % w)
                    steps.append({"序": len(steps) + 1, "路由": "对账",
                              "spec": {"对账": {}}, "缺口": ["日期"],
                              "零件来源": "动词表 + 《对账执行》（conc/1060）"})
                else:
                    steps.append({"序": len(steps) + 1, "路由": "对账",
                                  "spec": {"对账": {"日期": md.group(1)}},
                                  "零件来源": "原话日期 + 《对账执行》（conc/1060）"})
        return steps, gaps

    def _buffer_after_run(self, rec: dict, r: dict):
        """执行完落缓冲区建树 + 计数 +（教过《缓冲区晋升》才）提名。返回提名问句或 None。"""
        import datetime as _dt
        ok = bool(isinstance(r, dict) and r.get("ok"))
        rec["状态"] = "已执行" if ok else "执行失败"
        _end = r.get("结束") if isinstance(r, dict) else None
        rec["结果要点"] = {"走通": ok,
                       "停在哪": r.get("停在哪") if isinstance(r, dict) else None,
                       "到了没": (_end or {}).get("到了没") if isinstance(_end, dict) else None}
        # 成功次数按**步路由序列**累计（v0.2.67 用户拍板）：序列相同即同款
        # 合取——参数不同也算同一条路走通；同候选号重跑不涨（防刷口径不变），
        # 指纹留档备查不再当钥匙
        _seq = self._plan_seq(rec)
        _prior = sum(1 for x in self._buffer_load()
                     if _seq and self._plan_seq(x) == _seq
                     and x.get("状态") == "已执行"
                     and x.get("候选号") != rec.get("候选号"))
        if ok:
            rec["成功次数"] = _prior + 1
        rec["路由序列"] = _seq
        rec["ts最后"] = _dt.datetime.now().isoformat(timespec="seconds")
        # 建树：根=目标，孩子=每步执行结果（原样搬，不改写）
        rec["树"] = {"根": {"目标": rec.get("目标"), "候选号": rec.get("候选号")},
                     "孩子": [{"序": d.get("序"), "路由": d.get("路由"),
                               "得到": (str(d.get("得到") or "")[:200]),
                               "走通": d.get("走通")}
                              for d in (r.get("段") or []) if isinstance(r, dict)]}
        提名 = None
        if ok and self._route_node("缓冲区晋升"):
            # 提名一次（序列口径）：该序列在缓冲区里提名过就不再提——
            # 「同一条步单提名一次」在 v0.2.67 序列认法下的含义
            _nominated = any(x.get("提名") and _seq and self._plan_seq(x) == _seq
                             for x in self._buffer_load()
                             if x.get("候选号") != rec.get("候选号"))
            if _prior + 1 >= 3 and not _nominated and not rec.get("提名"):
                rec["提名"] = True
                提名 = ("这个步路由序列（%s）成功走通满 3 次——参数不同也算同款"
                        "（步路由序列计数，v0.2.67）。缓冲区指纹 %s——要不要把它"
                        "开成固定组合？开成固定组合要你拍板教它，我不自己落。"
                        % ("→".join(_seq), rec.get("指纹")))
        recs = self._buffer_load()
        for i, x in enumerate(recs):
            if x.get("候选号") == rec.get("候选号"):
                recs[i] = rec
                break
        else:
            recs.append(rec)
        self._buffer_save(recs)
        return 提名

    def _buffer_report(self, text: str):
        """《缓冲区晋升》的「读」出口：缓冲区里有什么，照实报。"""
        recs = self._buffer_load()
        cid = re.search(r"P-\d{8}-\d{6}-\d{3}", text)
        sel = [r for r in recs if (not cid or r.get("候选号") == cid.group(0))]
        return {"ok": True, "缓冲区": True, "节点": "conc/1076",
                "路径": str(self._buffer_path()), "条数": len(sel),
                "树": [{"候选号": r.get("候选号"), "目标": r.get("目标"),
                        "状态": r.get("状态"), "成功次数": r.get("成功次数"),
                        "提名": bool(r.get("提名")),
                        "步": [(s.get("路由") or "?") for s in (r.get("步单") or [])]}
                       for r in sel[-10:]],
                "为什么": "按《缓冲区晋升》：步单的历史都在缓冲区，照实报——"
                          "离开缓冲区只有一条路：成功满 3 次提名 + 你拍板。"}

    def _planner_rule(self, text: str, max_chars: int = 600):
        """《组合器》（conc/1075）：目标句没命中路由 ⇒ 拆步单候选报缺，
        **人拍板才执行**；《缓冲区晋升》（conc/1076）：步单不落固定路由，
        执行完落缓冲区建树当历史，同一步单成功满 3 次 ⇒ 回执提名。
        节点没教走旧路（可移除）。"""
        if not self._route_node("组合器"):
            return None
        # 读缓冲区（读问句带疑问头是自然说法——「缓冲区里有什么」，先于闸门）
        if "缓冲区" in text and self._route_node("缓冲区晋升"):
            return self._buffer_report(text)
        if any(h in text for h in _VERB_QUESTION_HEADS):
            return None                          # 问方法 ⇒ 走旧路照查
        # ── 人拍板确认（判定权在人：候选不自动跑）────────────────────
        _cid = re.search(r"P-\d{8}-\d{6}-\d{3}", text)
        if _cid or any(c in text for c in _PLAN_CONFIRM):
            recs = self._buffer_load()
            rec = None
            if _cid:
                rec = next((x for x in recs
                            if x.get("候选号") == _cid.group(0)), None)
            else:
                pend = [x for x in recs if x.get("状态") == "待拍板"]
                rec = pend[-1] if pend else None
            if rec is None:
                return {"ok": False, "组合器": True, "节点": "conc/1075",
                        "还给人": True,
                        "为什么": "说执行，但对不上候选号，缓冲区里也没有待拍板的"
                                  "步单——不猜。"}
            # v0.2.63：已执行的候选**原话带「重跑」才放行**（补 v0.2.53 口径承诺）。
            # 防刷口径不变：同一候选号重跑成功不涨计数（计数按指纹跨候选累计），
            # 回执如实说明。
            if rec.get("状态") == "已执行" and "重跑" not in text:
                return {"ok": True, "组合器": True, "节点": "conc/1075",
                        "候选号": rec.get("候选号"),
                        "不重跑": ("这条步单已执行过（成功次数 %s）——要重跑在原话里"
                                   "带上「重跑」二字。" % rec.get("成功次数")),
                        "结果要点": rec.get("结果要点")}
            if rec.get("缺口"):
                return {"ok": False, "组合器": True, "节点": "conc/1075",
                        "还给人": True, "候选号": rec.get("候选号"),
                        "为什么": "步单还有缺口没补（%s）——补上才跑，不猜。"
                                  % "；".join(rec["缺口"])}
            # 步单里有 mongo 步 ⇒ 先按《拉起服务》拉起（conc/1074/1060 同款预检）
            if any(isinstance(s, dict) and "mongo" in s for s in rec.get("步单") or []):
                _mh, _mp, _mc = self._mongo_addr()
                if not _tcp_up(_mh, _mp, timeout=1.5):
                    up = self._service_start("mongodb", self._services_registry())
                    if not up.get("ok"):
                        return {"ok": False, "组合器": True, "节点": "conc/1075",
                                "查不了": "对方挂了", "候选号": rec.get("候选号"),
                                "error": "MongoDB 拉不起来，存数这步走不了——"
                                         + str(up.get("error") or "")[:120]}
            r = self._compose({"要": rec.get("目标") or "", "步": rec.get("步单") or []})
            _tn = self._buffer_after_run(rec, r)
            if isinstance(r, dict):
                r = dict(r)
                r["节点"] = "conc/1075"
                r["组合器"] = True
                r["候选号"] = rec.get("候选号")
                r["缓冲区"] = {"落了": str(self._buffer_path()),
                               "状态": rec.get("状态"),
                               "成功次数": rec.get("成功次数"),
                               "说明": "按《缓冲区晋升》：步单只落历史，不落固定路由。"}
                if _tn:
                    r["提名"] = _tn
            return r
        # ── 读缓冲区（《缓冲区晋升》的读出口；节点没教走旧路）──────────
        if "缓冲区" in text and self._route_node("缓冲区晋升"):
            return self._buffer_report(text)
        # ── 目标句拆候选：≥2 个动作动词才轮到我（单动作各归各路由）────
        verbs = [w for w in _PLAN_VERBS if w in text]
        if len(verbs) < 2:
            return None
        import datetime as _dt
        sg = self._seg_rule(text)
        steps, gaps = self._plan_steps(text)
        _used = {s["路由"] for s in steps}
        _unmapped = []
        if sg:
            for h in sg.get("切出") or []:
                _w = str(h.get("词") or "")
                if _w not in verbs and _w not in "".join(verbs):
                    _unmapped.append(_w)
        # ── 动作前参考缓冲区（《多轮》conc/1098）：同目标/同指纹有历史 ⇒
        #    引用旧候选不重复开；判定权在人，不自动执行。原话带「重跑」豁免
        #    （v0.2.63）：重跑/改参数都是开新候选的口令——晋升计数按步路由
        #    序列合取（v0.2.67），同候选重跑防刷不变 ──
        _fp_new = self._plan_fp([s["spec"] for s in steps]) if steps else ""
        _recs = self._buffer_load()
        _same = [x for x in _recs if x.get("目标") == text]
        if not _same and _fp_new:
            _same = [x for x in _recs if x.get("指纹") == _fp_new]
        if _same and "重跑" not in text:
            _ex = _same[-1]
            if _ex.get("状态") == "待拍板" or (_ex.get("成功次数") or 0) >= 1:
                _st = _ex.get("状态")
                return {"ok": True, "组合器": True, "节点": "conc/1075",
                        "参考缓冲区": True, "候选号": _ex.get("候选号"),
                        "目标": text, "状态": _st,
                        "成功次数": _ex.get("成功次数"),
                        "说明": ("缓冲区已有同款步单（候选号 %s，状态 %s，"
                                 "成功 %s 次）——不重复开候选。"
                                 % (_ex.get("候选号"), _st, _ex.get("成功次数"))),
                        "问句": ("要执行说「按候选执行」或报候选号 %s；"
                                 "要改步单把话改了再说。" % _ex.get("候选号")),
                        "多轮": "按《多轮》（conc/1098）：动作前先参考缓冲区历史。"}
        cid = ("P-" + _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
               + "-%03d" % (_dt.datetime.now().microsecond // 1000))
        _plan = [s["spec"] for s in steps]     # 组合执行器认的形状：一段一个键
        rec = {"候选号": cid,
               "ts": _dt.datetime.now().isoformat(timespec="seconds"),
               "目标": text, "状态": "待拍板",
               "步单": _plan, "步单明细": steps, "缺口": gaps,
               "指纹": self._plan_fp(_plan),
               "成功次数": 0, "提名": False,
               "树": {"根": {"目标": text, "候选号": cid},
                      "孩子": [{"序": s["序"], "路由": s["路由"],
                                "零件来源": s.get("零件来源"),
                                **({"缺口": s["缺口"]} if s.get("缺口") else {})}
                               for s in steps]}}
        saved = self._buffer_save(self._buffer_load() + [rec])
        self._route_log({"来源": "组合器 conc/1075", "候选号": cid,
                         "动词": verbs, "步": [s["路由"] for s in steps],
                         "缺口": gaps or None})
        return {"ok": True, "组合器": True, "节点": "conc/1075",
                "报缺候选": True, "候选号": cid,
                "目标": text,
                "动词": verbs,
                "步单候选": steps,
                **({"缺口": gaps} if gaps else {}),
                **({"没拼的切出词": _unmapped} if _unmapped else {}),
                "缓冲区": {"落了": str(saved), "状态": "待拍板",
                           "说明": "候选只是候选——执行要你拍板；步单不落固定路由。"},
                "问句": ("要不要按这个执行？说「按候选执行」或报候选号 %s。"
                         % cid) + ("补上缺口再执行。" if gaps else ""),
                "停手线": "候选不自动执行；参数缺了不编；执行完步单只落缓冲区"
                          "当历史（conc/1076），不写进固定组合、不自己教路由。",
                "对词表切分": sg}

    def _http_spec(self, spec, max_chars: int = 600):
        """http 步进（组合器步单用）：{方法, 地址, 体?}。契约同《直连端口》：
        地址不成不猜；环回绕系统代理；一次不重发；4xx/5xx 也算调成（对方这么答）。"""
        import urllib.request as _u
        import urllib.error as _ue
        if not isinstance(spec, dict):
            return {"ok": False, "弃答": "http 步要 {方法, 地址}——地址不成不猜。"}
        parsed = _parse_addr(str(spec.get("地址") or "").strip())
        if not parsed:
            return {"ok": False,
                    "弃答": "http 步的地址不成（%s）——不猜。"
                            % str(spec.get("地址") or "")[:80]}
        scheme, host, port, path, _e = parsed
        method = str(spec.get("方法") or "GET").upper()
        if method not in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"):
            return {"ok": False,
                    "弃答": "不认识的方法 %s——GET/POST/PUT/DELETE/PATCH/HEAD。" % method}
        body_b = None
        if method != "GET" and spec.get("体") is not None:
            body_b = json.dumps(spec["体"], ensure_ascii=False).encode("utf-8")
        url = scheme + host + ":" + port + path
        # ★ 路径/查询非 ASCII 百分号编码（v0.2.76，同 _http 直连路由）：
        #   中文参数塞 URL 会在 http.client 的 ascii 编码上炸（v0.2.69 实锤）。
        if re.search(r"[^\x00-\x7f]", url):
            import urllib.parse as _up
            url = _up.quote(url, safe=":/?#[]@,$&'=+;()!*~-")
        req = _u.Request(url, method=method, data=body_b,
                         headers={"User-Agent": "LDV-combo/0.2"})
        if host in ("localhost", "::1") or host.startswith("127."):
            opener = _u.build_opener(_u.ProxyHandler({}))
        else:
            opener = _u.build_opener()
        t0 = time.perf_counter()
        try:
            with opener.open(req, timeout=10) as resp:
                raw = resp.read(1024 * 256)
                code = resp.status
        except _ue.HTTPError as e:
            raw, code = (e.read(1024 * 256) if e.fp else b""), e.code
        except (_ue.URLError, TimeoutError, ConnectionError, OSError) as e:
            why = str(getattr(e, "reason", e) or e)
            self._route_log({"来源": "组合器http步 conc/1075", "地址": url,
                             "结果": "对方挂了", "error": why[:160]})
            return {"ok": False, "查不了": "对方挂了",
                    "调了": {"方法": method, "地址": url},
                    "弃答": "%s %s 没调成：%s——不自动重发。" % (method, url, why[:120])}
        ms = int((time.perf_counter() - t0) * 1000)
        txt = raw.decode("utf-8", errors="replace")
        self._route_log({"来源": "组合器http步 conc/1075", "地址": url,
                         "结果": str(code), "耗时ms": ms})
        return {"ok": True, "http": True,
                "调了": {"方法": method, "地址": url, "状态码": code, "耗时ms": ms},
                "结论": txt[:max_chars] if len(txt) > max_chars else txt,
                "正文截断": len(txt) > max_chars}

    def _dev_task_rule(self, text: str, max_chars: int = 400):
        """《研发任务执行》（conc/1074）：研发全流程的可执行长线管线——
        LDV 当流程编排者：开任务建账（Mongo ldv_dev.dev_tasks）、按段发
        任务卡（任务卡正文从语料节点查，规矩在语料不在算子）、收人交的
        回执、判段完成推进下一段，九段走完任务才关。
        人的活不代做；段没回执就问推进⇒停手交回；可移除（节点不在走旧路）。"""
        if not self._route_node("研发任务执行"):
            return None
        # 触发：显式「研发任务」，或 任务+段/回执/进度 的推进类说法
        # （人交回执自然说「X 任务第 N 段完成」——不含「研发任务」也要接）
        if "研发任务" not in text and not (
                ("任务" in text and re.search(r"任务.{0,30}第\s*\d+\s*段", text))
                or ("回执" in text and "任务" in text)
                or ("进度" in text and "任务" in text)):
            return None
        m = re.search(r"研发任务[：:\s]*([^\s：:,，。]{1,40})", text)
        if m:
            task = m.group(1).strip()
        else:
            # 推进类句式（「X 任务第 N 段完成」「X 任务的进度」）：任务名=
            # 以「任务」开头的 token 的前一个 token（X 自己可能含「任务」）
            task = ""
            _toks = text.split()
            for _i, _tk in enumerate(_toks):
                if _tk.startswith("任务") and _i > 0:
                    task = _toks[_i - 1].strip("：:,，。")
                    break
        if not task:
            return {"ok": False, "研发任务执行": True, "节点": "conc/1074",
                    "还给人": True,
                    "为什么": "说了研发任务但没给任务名——不猜。"}
        import time as _t
        from ldv.core import mongowire as MW
        host, port, _cfg = self._mongo_addr()
        # ① Mongo 没在跑先按《拉起服务》拉起（对账同款）
        if not _tcp_up(host, port, timeout=1.5):
            up = self._service_start("mongodb", self._services_registry())
            if not up.get("ok"):
                return {"ok": False, "研发任务执行": True, "节点": "conc/1074",
                        "查不了": "对方挂了",
                        "error": "MongoDB 拉不起来，任务账记不了——"
                                 + str(up.get("error") or "")[:120]}

        def _load(name):
            try:
                raw = MW.find(_DEV_COLL, {"任务名": name}, 1, _DEV_DB,
                              host, port)
            except (ConnectionRefusedError, TimeoutError, OSError) as exc:
                return None, "%s:%s 连不上（%s）" % (host, port,
                                                      type(exc).__name__)
            doc = raw.get("文档") or {}
            if not doc.get("ok"):
                return None, str(doc.get("errmsg") or "命令错")
            cur = doc.get("cursor") or {}
            batch = list(cur.get("firstBatch") or [])
            for sec in (raw.get("序列") or []):
                if str(sec.get("字段") or "").endswith("firstBatch"):
                    batch += list(sec.get("文档") or [])
            return (batch[0] if batch else None), None

        def _card(idx):
            sname, sid = _DEV_STAGES[idx]
            _nid = self._route_node(sname)
            nd = self.nodes.get(_nid) if _nid else None
            body = (nd.body or "").strip() if nd is not None else ""
            return {"段": idx + 1, "段名": sname, "依据": sid,
                    "要做什么": body[:max_chars] if body
                    else "（语料里没查到《%s》正文）" % sname,
                    "truncated": bool(body and len(body) > max_chars)}

        # ── 意图分派：交回执 / 进度 / 开任务 ──────────────────────────
        if re.search(r"第\s*(\d+)\s*段", text) and \
                any(w in text for w in ("完成", "回执")):
            md = re.search(r"第\s*(\d+)\s*段", text)
            n = int(md.group(1))
            if n < 1 or n > len(_DEV_STAGES):
                return {"ok": False, "研发任务执行": True, "节点": "conc/1074",
                        "还给人": True, "任务名": task,
                        "为什么": "段号 %d 越界（一共 %d 段）——不猜。"
                                  % (n, len(_DEV_STAGES))}
            mr = re.search(r"回执[：:]?\s*(.+)$", text, re.S)
            receipt = mr.group(1).strip() if mr else ""
            if not receipt:
                return {"ok": False, "研发任务执行": True, "节点": "conc/1074",
                        "还给人": True, "任务名": task, "段": n,
                        "为什么": "交段回执要带内容（做了什么、产出在哪）——"
                                  "空回执不算完成。"}
            doc, err = _load(task)
            if err:
                return {"ok": False, "研发任务执行": True, "节点": "conc/1074",
                        "查不了": "对方挂了", "error": err}
            if doc is None:
                return {"ok": False, "研发任务执行": True, "节点": "conc/1074",
                        "还给人": True, "任务名": task,
                        "为什么": "没有这个任务——先开任务再交回执，不猜。"}
            stages = doc.get("段账") or []
            if doc.get("状态") == "已关闭":
                return {"ok": True, "研发任务执行": True, "节点": "conc/1074",
                        "任务名": task, "任务状态": "已关闭",
                        "为什么": "这个任务九段已经走完关账了，不再收回执。"}
            if n > 1 and (stages[n - 2] or {}).get("状态") != "完成":
                return {"ok": False, "研发任务执行": True, "节点": "conc/1074",
                        "还给人": True, "任务名": task, "段": n,
                        "为什么": "第 %d 段（%s）还没完成——流程按段走，"
                                  "先把前一段做完了再交这段的。"
                                  % (n - 1, _DEV_STAGES[n - 2][0])}
            if (stages[n - 1] or {}).get("状态") == "完成":
                return {"ok": False, "研发任务执行": True, "节点": "conc/1074",
                        "还给人": True, "任务名": task, "段": n,
                        "为什么": "第 %d 段在 %s 已经交过回执了——要重交请说"
                                  "「重交」，不覆盖旧账。"
                                  % (n, (stages[n - 1] or {}).get("完成于") or "")}
            try:
                sets = {"段账.%d.状态" % (n - 1): "完成",
                        "段账.%d.回执" % (n - 1): receipt[:2000],
                        "段账.%d.完成于" % (n - 1): _t.strftime("%Y-%m-%d %H:%M:%S")}
                if n < len(_DEV_STAGES):
                    sets["当前段"] = n + 1
                MW.update(_DEV_COLL, {"任务名": task}, sets, _DEV_DB,
                          host, port)
            except (ConnectionRefusedError, TimeoutError, OSError) as exc:
                return {"ok": False, "研发任务执行": True, "节点": "conc/1074",
                        "查不了": "对方挂了",
                        "error": "写账失败（%s）——第 %d 段没记上，别往下走。"
                                 % (type(exc).__name__, n)}
            self._route_log({"来源": "研发任务执行 conc/1074", "任务名": task,
                             "交段": n, "结果": "完成"})
            if n == len(_DEV_STAGES):
                try:
                    MW.update(_DEV_COLL, {"任务名": task},
                              {"状态": "已关闭"}, _DEV_DB, host, port)
                except (ConnectionRefusedError, TimeoutError, OSError):
                    pass
                _fresh, _err2 = _load(task)      # 关账小结读最新账，不用交账前快照
                _st = (_fresh or doc).get("段账") or stages
                return {"ok": True, "研发任务执行": True, "节点": "conc/1074",
                        "任务名": task, "任务状态": "已关闭",
                        "全流程小结": {
                            "九段": [{"段": i + 1,
                                       "段名": (_st[i] or {}).get("段名"),
                                       "完成": (_st[i] or {}).get("状态") == "完成",
                                       "回执要点": str((_st[i] or {})
                                                       .get("回执") or "")[:120]}
                                      for i in range(len(_DEV_STAGES))],
                            "为什么": "九段全完成——任务关账。"},
                        "为什么": "第 9 段（收尾）回执收到——全流程走完，任务关闭。"}
            return {"ok": True, "研发任务执行": True, "节点": "conc/1074",
                    "任务名": task, "交段": n, "段状态": "完成",
                    "当前段": n + 1,
                    "下一段任务卡": _card(n),
                    "为什么": "第 %d 段回执收到、账记上了——按《研发全流程》"
                              "推进下一段。" % n}

        if "进度" in text or "到哪" in text or "推进" in text:
            doc, err = _load(task)
            if err:
                return {"ok": False, "研发任务执行": True, "节点": "conc/1074",
                        "查不了": "对方挂了", "error": err}
            if doc is None:
                return {"ok": False, "研发任务执行": True, "节点": "conc/1074",
                        "还给人": True, "任务名": task,
                        "为什么": "没有这个任务——先开任务再问进度，不猜。"}
            stages = doc.get("段账") or []
            done = sum(1 for s in stages if (s or {}).get("状态") == "完成")
            if doc.get("状态") == "已关闭":
                return {"ok": True, "研发任务执行": True, "节点": "conc/1074",
                        "任务名": task, "任务状态": "已关闭",
                        "已完成段数": len(_DEV_STAGES),
                        "各段": [{"段": s.get("段"), "段名": s.get("段名"),
                                  "状态": s.get("状态")} for s in stages],
                        "为什么": "九段全完成，任务已关账。"}
            cur = int(doc.get("当前段") or 1)
            return {"ok": True, "研发任务执行": True, "节点": "conc/1074",
                    "任务名": task, "需求": doc.get("需求") or "",
                    "已完成段数": done, "当前段": cur,
                    "当前段任务卡": _card(cur - 1),
                    "在等谁": ("人做第 %d 段（%s）——交「%s 任务第 %d 段完成，"
                               "回执：……」我就推进。"
                               % (cur, _DEV_STAGES[cur - 1][0], task, cur)),
                    "各段": [{"段": s.get("段"), "段名": s.get("段名"),
                              "状态": s.get("状态"),
                              "回执要点": str(s.get("回执") or "")[:80]}
                             for s in stages]}

        # 开任务（默认分支：开/建/立项）
        mq = re.search(r"需求[是为：:]+\s*(.+)$", text, re.S)
        req = (mq.group(1).strip()[:1500]) if mq else ""
        if not req:
            return {"ok": False, "研发任务执行": True, "节点": "conc/1074",
                    "还给人": True, "任务名": task,
                    "为什么": "开任务要带需求（「开研发任务 X：需求是……」）——"
                              "根据需求执行是这条管线的起点，没需求不建账。"}
        doc, err = _load(task)
        if err:
            return {"ok": False, "研发任务执行": True, "节点": "conc/1074",
                    "查不了": "对方挂了", "error": err}
        if doc is not None:
            return {"ok": False, "研发任务执行": True, "节点": "conc/1074",
                    "还给人": True, "任务名": task,
                    "任务状态": doc.get("状态"), "当前段": doc.get("当前段"),
                    "为什么": "这个任务名已经有一本账（%s，当前第 %s 段）——"
                              "要重开请换个名或说清怎么处理旧账，不覆盖。"
                              % (doc.get("状态"), doc.get("当前段"))}
        now = _t.strftime("%Y-%m-%d %H:%M:%S")
        doc = {"任务名": task, "需求": req, "状态": "进行中", "当前段": 1,
               "开于": now,
               "段账": [{"段": i + 1, "段名": _DEV_STAGES[i][0],
                         "依据": _DEV_STAGES[i][1], "状态": "未开始",
                         "回执": None, "完成于": None}
                        for i in range(len(_DEV_STAGES))]}
        try:
            MW.insert(_DEV_COLL, [doc], _DEV_DB, host, port)
        except (ConnectionRefusedError, TimeoutError, OSError) as exc:
            return {"ok": False, "研发任务执行": True, "节点": "conc/1074",
                    "查不了": "对方挂了",
                    "error": "建账失败（%s）——任务没开成。" % type(exc).__name__}
        self._route_log({"来源": "研发任务执行 conc/1074", "任务名": task,
                         "结果": "开任务"})
        return {"ok": True, "研发任务执行": True, "节点": "conc/1074",
                "任务名": task, "需求": req, "任务状态": "进行中",
                "总段数": len(_DEV_STAGES), "当前段": 1,
                "第一段任务卡": _card(0),
                "怎么推进": ("人做完一段就交「%s 任务第 N 段完成，回执：……」"
                             "——我记上账、发下一段任务卡；问「%s 任务的进度」"
                             "随时看账。" % (task, task)),
                "为什么": "按《研发全流程》开了任务账（Mongo ldv_dev）——"
                          "九段逐段走，走完才关。"}

    def _rework_rule(self, text: str):
        """《返工账派生》（conc/1081）：对已关任务账报缺陷 ⇒ 自动开关联返工账
        （任务名=原任务名+「返工」，账上记关联原账+缺陷反馈原话，旧账不改）。
        规矩在语料；名字是唯一钥匙，遮节点走旧路=只靠口令带「返工」显式开。
        停手线：任务名对不上不猜；账没关不另开；同名返工账在⇒报进度不覆盖。"""
        if not self._route_node("返工账派生"):
            return None
        if "研发任务" in text:
            return None                      # 显式口令归 1074，这条不抢
        # 1074 推进类句式也让位：交回执/问进度不是缺陷反馈——
        # 「X返工 任务第 N 段完成」自带「返工」二字，回执里也常提
        # 「缺陷」，不让位会拦死九段推进（2026-11-17 端到端抓到）。
        if re.search(r"第\s*\d+\s*段", text) and ("完成" in text or "回执" in text):
            return None
        if "回执" in text and "任务" in text:
            return None
        if "进度" in text and "任务" in text:
            return None
        if not ("缺陷" in text or "返工" in text or "bug" in text.lower()):
            return None
        import time as _t
        from ldv.core import mongowire as MW
        host, port, _cfg = self._mongo_addr()
        # Mongo 没在跑先按《拉起服务》拉起（1074 同款）
        if not _tcp_up(host, port, timeout=1.5):
            up = self._service_start("mongodb", self._services_registry())
            if not up.get("ok"):
                return {"ok": False, "返工账派生": True, "节点": "conc/1081",
                        "查不了": "对方挂了",
                        "error": "MongoDB 拉不起来，任务账对不了——"
                                 + str(up.get("error") or "")[:120]}
        try:
            raw = MW.find(_DEV_COLL, {}, 200, _DEV_DB, host, port)
        except (ConnectionRefusedError, TimeoutError, OSError) as exc:
            return {"ok": False, "返工账派生": True, "节点": "conc/1081",
                    "查不了": "对方挂了",
                    "error": "%s:%s 连不上（%s）"
                             % (host, port, type(exc).__name__)}
        doc0 = raw.get("文档") or {}
        if not doc0.get("ok"):
            return {"ok": False, "返工账派生": True, "节点": "conc/1081",
                    "查不了": "对方挂了",
                    "error": str(doc0.get("errmsg") or "命令错")}
        batch = list((doc0.get("cursor") or {}).get("firstBatch") or [])
        for sec in (raw.get("序列") or []):
            if str(sec.get("字段") or "").endswith("firstBatch"):
                batch += list(sec.get("文档") or [])
        # 任务名不猜：账里已有的名字在话里出现的才算点名（最长优先）
        hits = sorted({str(b.get("任务名") or "") for b in batch
                       if b.get("任务名") and str(b.get("任务名")) in text},
                      key=len, reverse=True)
        if not hits:
            # 不猜也不拦路：名字对不上 ⇒ 可能压根不是研发任务的缺陷
            # （如「对账流程有缺陷」）——放行，交后续路由或走旧路。
            return None
        orig = hits[0]
        odoc = next(b for b in batch if b.get("任务名") == orig)
        if odoc.get("状态") != "已关闭":
            return {"ok": True, "返工账派生": True, "节点": "conc/1081",
                    "任务名": orig, "任务状态": odoc.get("状态"),
                    "当前段": odoc.get("当前段"),
                    "为什么": "这本账还没关（%s，当前第 %s 段）——不开返工账；"
                              "缺陷是记在现账还是另想办法，交人拍板。"
                              % (odoc.get("状态"), odoc.get("当前段"))}
        rtask = orig + "返工"
        rdoc = next((b for b in batch if b.get("任务名") == rtask), None)
        if rdoc is not None:
            return {"ok": True, "返工账派生": True, "节点": "conc/1081",
                    "任务名": rtask, "任务状态": rdoc.get("状态"),
                    "当前段": rdoc.get("当前段"),
                    "为什么": "返工账已经有一本（%s，当前第 %s 段）——如实报"
                              "进度，不覆盖不重开；要另开一本请说清怎么处理。"
                              % (rdoc.get("状态"), rdoc.get("当前段"))}

        def _card(idx):
            sname, sid = _DEV_STAGES[idx]
            _nid = self._route_node(sname)
            nd = self.nodes.get(_nid) if _nid else None
            body = (nd.body or "").strip() if nd is not None else ""
            return {"段": idx + 1, "段名": sname, "依据": sid,
                    "要做什么": body[:400] if body
                    else "（语料里没查到《%s》正文）" % sname}

        doc = {"任务名": rtask, "需求": text[:1500], "状态": "进行中",
               "当前段": 1, "开于": _t.strftime("%Y-%m-%d %H:%M:%S"),
               "关联原账": orig, "缺陷反馈": text[:2000],
               "段账": [{"段": i + 1, "段名": _DEV_STAGES[i][0],
                         "依据": _DEV_STAGES[i][1], "状态": "未开始",
                         "回执": None, "完成于": None}
                        for i in range(len(_DEV_STAGES))]}
        try:
            MW.insert(_DEV_COLL, [doc], _DEV_DB, host, port)
        except (ConnectionRefusedError, TimeoutError, OSError) as exc:
            return {"ok": False, "返工账派生": True, "节点": "conc/1081",
                    "查不了": "对方挂了",
                    "error": "建账失败（%s）——返工账没开成，原账没动。"
                             % type(exc).__name__}
        self._route_log({"来源": "返工账派生 conc/1081", "任务名": rtask,
                         "关联原账": orig, "结果": "开返工账"})
        return {"ok": True, "返工账派生": True, "节点": "conc/1081",
                "任务名": rtask, "关联原账": orig, "任务状态": "进行中",
                "缺陷反馈": text[:300], "总段数": len(_DEV_STAGES),
                "当前段": 1, "第一段任务卡": _card(0),
                "怎么推进": ("人做完一段就交「%s 任务第 N 段完成，回执：……」"
                             "——我记上账、发下一段任务卡。" % rtask),
                "为什么": "原账 %s 已关闭，缺陷反馈收到——按《返工账派生》"
                          "开关联返工账（关联原账记在账上），九段照走，"
                          "旧账原样不动。" % orig}

    def _exec_rule(self, text: str, max_chars: int = 800):
        """《调用本机程序》（conc/1087，v0.2.58）：白名单拉起本机程序——
        python（跑原话点名的代码/脚本）、code（打开原话点名的路径）。
        停手线：白名单外不猜不执行；代码/路径从原话拿，拿不到如实报不编；
        路径不存在如实报；每句一次不重发；输出截断留痕；超时杀进程。
        只在**节点教了**时走（名字是唯一钥匙，遮住节点即回旧行为）。"""
        if not self._route_node("调用本机程序"):
            return None
        m = re.search(r"用\s*(vscode|code|python)\s*(打开|跑|运行|执行)", text,
                      re.IGNORECASE)
        if not m:
            return None
        prog = m.group(1).lower()
        # ── 目标提取：只从原话拿，拿不到不编 ──────────────────────────
        if prog in ("vscode", "code"):
            path = self._extract_path(text)
            if not path:
                return {"ok": False, "调用本机程序": False, "节点": "conc/1087",
                        "还给人": True,
                        "error": "要打开哪个目录或文件？把路径给我。",
                        "为什么": "VSCode 拉起要原话点名路径——没有不猜不编。"}
            if not os.path.exists(path):
                return {"ok": False, "调用本机程序": False, "节点": "conc/1087",
                        "error": "路径不存在：%s——不执行。" % path,
                        "为什么": "停手线：路径不存在如实报，不猜不编造。"}
            cmd = "code %s" % json.dumps(path)
            try:
                p = subprocess.run(cmd, shell=True, capture_output=True,
                                   text=True, timeout=15)
                rc = p.returncode
                out = p.stdout[-max_chars:]
                err = p.stderr[-max_chars:]
            except subprocess.TimeoutExpired:
                return {"ok": False, "调用本机程序": False, "节点": "conc/1087",
                        "error": "code 拉起超时（15 秒）——进程已杀。"}
            self._route_log({"来源": "调用本机程序 conc/1087", "程序": "code",
                             "命令": cmd, "退出码": rc})
            return {"ok": True, "调用本机程序": True, "节点": "conc/1087",
                    "程序": "code", "命令": cmd, "退出码": rc,
                    "输出": out, "错误": err,
                    "为什么": "GUI 异步返回——回执证明拉起了，不证明人看过。"}
        # python：先取内联代码（反引号/引号内），再取 .py 路径
        code_str = self._extract_code(text)
        if code_str:
            cmd = ["python", "-c", code_str]
        else:
            py_path = self._extract_path(text)
            if not py_path or not py_path.endswith(".py"):
                return {"ok": False, "调用本机程序": False, "节点": "conc/1087",
                        "还给人": True,
                        "error": "要跑哪段代码或哪个 .py 脚本？点名给我。",
                        "为什么": "Python 执行要原话点名——没有不猜不编。"}
            if not os.path.exists(py_path):
                return {"ok": False, "调用本机程序": False, "节点": "conc/1087",
                        "error": "脚本不存在：%s——不执行。" % py_path,
                        "为什么": "停手线：路径不存在如实报，不猜不编造。"}
            cmd = ["python", py_path]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            rc = p.returncode
            out = p.stdout[-max_chars:]
            err = p.stderr[-max_chars:]
        except subprocess.TimeoutExpired:
            return {"ok": False, "调用本机程序": False, "节点": "conc/1087",
                    "error": "python 执行超时（30 秒）——进程已杀。"}
        self._route_log({"来源": "调用本机程序 conc/1087", "程序": "python",
                         "命令": " ".join(cmd[:2]) + " …", "退出码": rc})
        return {"ok": True, "调用本机程序": True, "节点": "conc/1087",
                "程序": "python", "退出码": rc,
                "输出": out, "错误": err,
                "为什么": "只跑了原话点名的代码，输出原样带回。"}

    # ── 写代码（conc/1089，v0.2.60）：LDV 认装配单 ⇒ 拼构件 ⇒ 落盘自测 ─────
    #（v0.2.59 曾让外层 LLM 出码，用户纠正口径：教它直接写代码，不是 LLM 代写
    #  ——LDV 不是生成器，它的写=认需求、按教过的装配单拼教过的构件。）

    def _find_assembly_plans(self, spec: str):
        """找装配单：type=装配单 的节点里，需求命中节点名或正文「认：」词。
        返回命中列表（0/1/多）， caller 自行报缺或分辨。"""
        hits = []
        for nid, n in self.nodes.items():
            f = n.fields or {}
            if str(f.get("type") or "") != "装配单":
                continue
            name = str(f.get("name") or "")
            m = re.search(r"认[:：]\s*([^\n]+)", n.body or "")
            words = [w.strip("。；; \t")
                     for w in (m.group(1).split("、") if m else [])
                     if w.strip("。；; \t")]
            if (name and name in spec) or any(w and w in spec for w in words):
                hits.append((nid, n))
        return hits

    def _find_anchor(self, text: str):
        """找锚点节点（锚点树拆分出码，2026-11-19 用户拍板：让它找方向树
        的子节点）：type=概念、名字在原话里出现、front-matter 声明了
        relations（子节点=部件，树序=拼装序）且全部指到语料内的节点。
        返回命中列表（0/1/多），caller 自行报缺或分辨。
        与装配单的关系：装配单优先——命中装配单就不走锚点（旧路不倒退）。"""
        hits = []
        for nid, n in self.nodes.items():
            f = n.fields or {}
            if str(f.get("type") or "") != "概念":
                continue
            name = str(f.get("name") or "")
            if not name or name not in text:
                continue
            rels = f.get("relations")
            if not isinstance(rels, list) or not rels:
                continue
            if any(str(r) not in self.nodes for r in rels):
                continue                      # 悬挂边不算可用锚点（缺才要）
            hits.append((nid, n))
        return hits

    @staticmethod
    def _extract_py_blocks(body: str):
        """取正文里全部 ```python 围栏代码块（保持顺序）。"""
        return [m.group(1) + "\n" for m in
                re.finditer(r"```python\s*\n(.*?)```", body or "", re.DOTALL)]

    @staticmethod
    def _section_bullets(body: str, header_kw: str):
        """取正文里标题含 header_kw 的段落下的条目行（-/* 开头）。
        反向覆盖+参数登记（v0.2.70，conc/1122）的机械解析：
        只认条目式段落，散文段不判（没有条目就没有可对照的东西）。"""
        out, in_sec = [], False
        for line in (body or "").splitlines():
            if re.match(r"^#{1,6}\s", line):
                in_sec = header_kw in line
                continue
            if in_sec and re.match(r"^[-*]\s", line.strip()):
                out.append(line.strip())
        return out

    def _classroom_root(self):
        """写代码白名单根：env LDV_CLASSROOM_ROOT 优先，其次从本文件向上找
        _dev_classroom 目录；都没有退相对路径（靠 cwd）。"""
        env = os.environ.get("LDV_CLASSROOM_ROOT")
        if env:
            return os.path.abspath(env)
        d = Path(__file__).resolve().parent
        for _ in range(6):
            cand = d / "_dev_classroom"
            if cand.is_dir():
                return str(cand)
            if d.parent == d:
                break
            d = d.parent
        return os.path.abspath("_dev_classroom")

    def _write_code_rule(self, text: str, max_chars: int = 800):
        """《写代码》（conc/1089，v0.2.60）：LDV 自己写——认装配单、按清单拼
        教过的构件代码块、落盘+语法自检+跑自测。不用任何生成模型代笔。
        停手线：无「需求：」不接；装配单没认出/点名构件缺 ⇒ 报缺不编（缺才要）；
        落盘路径不在 _dev_classroom 白名单根下不写；文件名带「..」不落；
        同名文件须原话带「覆盖」；py_compile 全过才跑自测；自测只跑一轮
        （挂了如实带输出，改不改由人说）；超时 30 秒杀进程。
        只在节点教了时走（名字唯一钥匙，遮住即回旧行为）。"""
        if not self._route_node("写代码"):
            return None
        has_obj = bool(re.search(r"写.{0,12}?(系统|程序|脚本|代码)", text))
        if "写代码" not in text and not has_obj:
            return None
        if "需求" not in text:
            return None                     # 没有需求不接（防误烧 LLM，落回旧路）
        # ── 需求与落盘目录：只从原话拿 ────────────────────────────────
        m = re.search(r"需求[:：]\s*(.+)$", text, re.DOTALL)
        spec = (m.group(1) if m else "").strip()
        spec = re.sub(r"\s*(落盘|存到|写到)\s+\S+\s*$", "", spec).strip()
        spec = re.sub(r"\s*覆盖\s*$", "", spec).strip()
        if not spec:
            return {"ok": False, "写代码": False, "节点": "conc/1089",
                    "还给人": True,
                    "error": "需求是什么？原话带「需求：……」说清要写什么。",
                    "为什么": "需求从原话拿，提不出不猜不编。"}
        base = None
        for t in ("落盘", "存到", "写到"):
            mt = re.search(t + r"\s+(\S+)", text)
            if mt:
                # 坑三犯（2026-11-19 拍板补进内核）：\S+ 只贪到下一个空白，
                # 「落盘 X。需求：…」句号后没空白 ⇒ 整句被吞进目录名。
                # 修：截断在第一个中文标点；尾缀直接跟「覆盖」的剥掉。
                base = re.split(r"[。；！？，、：]", mt.group(1))[0]
                base = re.sub(r"覆盖$", "", base.strip("\"'"))
                break
        if not base:
            return {"ok": False, "写代码": False, "节点": "conc/1089",
                    "还给人": True,
                    "error": "落到哪个目录？原话带「落盘 <目录>」点名。",
                    "为什么": "落盘路径从原话拿，没有不猜。"}
        root = self._classroom_root()
        cand = base
        if not os.path.isabs(cand):
            # 两种点名都收：相对白名单根（login_demo）或带 _dev_classroom 前缀
            parts = [p for p in re.split(r"[/\\]+", cand) if p]
            if parts and parts[0].lower() == "_dev_classroom":
                parts = parts[1:]
            cand = os.sep.join(parts) if parts else ""
        target = (os.path.abspath(cand) if os.path.isabs(base)
                  else os.path.abspath(os.path.join(root, cand)))
        if target != root and not target.startswith(root + os.sep):
            return {"ok": False, "写代码": False, "节点": "conc/1089",
                    "error": "落盘目录不在 _dev_classroom 白名单根下：%s——不写。"
                             % target,
                    "为什么": "停手线：白名单外不写盘，防乱写。"}
        # ── 认装配单（没教 ⇒ 报缺，缺才要）────────────────────────────
        hits = self._find_assembly_plans(spec)
        plan_from_anchor = False
        if not hits:
            # 锚点树拆分出码（2026-11-19 用户拍板：让它找方向树的子节点）：
            # 装配单没命中时，认「锚点节点」——名字在原话里、front-matter
            # 声明了 relations（子节点=部件，树序=拼装序）。仍是拼装制：
            # 代码全部来自教过的节点代码块，不用生成模型代笔。
            anchors = self._find_anchor(text)
            if not anchors:
                return {"ok": False, "写代码": False, "节点": "conc/1089",
                        "还给人": True,
                        "error": "需求没认出教过的装配单——LDV 只会拼教过的构件，"
                                 "这个系统没教：要写先教装配单。",
                        "为什么": "缺才要：没教的系统不编，这正是要求到登录系统"
                                 "为止的原因。"}
            if len(anchors) > 1:
                return {"ok": False, "写代码": False, "节点": "conc/1089",
                        "还给人": True,
                        "error": "命中多个锚点（%s）——说清要哪个，不猜。"
                                 % "、".join(str((n.fields or {}).get("name"))
                                             for _, n in anchors),
                        "为什么": "停手线：锚点点名含糊不猜。"}
            hits = anchors
            plan_from_anchor = True
        if len(hits) > 1:
            return {"ok": False, "写代码": False, "节点": "conc/1089",
                    "还给人": True,
                    "error": "命中多个装配单（%s）——说清要哪个，不猜。"
                             % "、".join(str((n.fields or {}).get("name"))
                                         for _, n in hits),
                    "为什么": "停手线：装配单点名含糊不猜。"}
        plan_nid, plan_node = hits[0]
        plan_name = str((plan_node.fields or {}).get("name") or "")
        pbody = plan_node.body or ""
        # ── 反向覆盖+参数登记（v0.2.70 锚点路径，v0.2.71 扩到装配单路径，
        # conc/1122）：出码前机械对照。教训（2026-11-19 用户诊断）：补充信息
        # 没被识别成需求，只按第一句做最低满足。规矩：①需求条目必须有落点，
        # 落不了点=这条没实现，报缺；②登记的参数没值 ⇒ 开口问（缺参数触发，
        # 不是主动联想）。都是机械对照；单子没条目式段落时自然空转（不误伤
        # 没教这两段的老装配单，如 conc/1094 登录系统）。
        _no_landing = [b for b in self._section_bullets(pbody, "需求")
                       if "落点" not in b]
        _no_value = []
        for b in self._section_bullets(pbody, "参数"):
            pm = re.match(r"[-*]\s*(.+?)[:：]\s*(\S.*)$", b)
            if pm and pm.group(2).strip().startswith("缺"):
                pname = pm.group(1).strip()
                if not re.search(re.escape(pname) + r"\s*[:：=]\s*\S", text):
                    _no_value.append(pname)
        if _no_landing or _no_value:
            _msgs = []
            if _no_landing:
                _msgs.append("需求条目没落点（这条没实现，如实报缺）：%s"
                             % "／".join(b.lstrip("-* ").strip()
                                         for b in _no_landing))
            for _p in _no_value:
                _msgs.append("参数「%s」的值是什么？原话没给——原话带"
                             "「%s=值」补上，或教进参数段；不问不编。"
                             % (_p, _p))
            return {"ok": False, "写代码": False, "节点": "conc/1089",
                    "还给人": True,
                    "error": "；".join(_msgs),
                    "为什么": "conc/1122 反向覆盖+参数登记：原话逐条要落点、"
                             "没值的参数要问——最低满足不算满足。"}
        # 构件点名：装配单按正文《构件名》出现顺序；锚点按 relations 树序
        comps, seen = [], set()
        if plan_from_anchor:
            for rid in [str(r) for r in
                        (plan_node.fields or {}).get("relations") or []]:
                if rid not in self.nodes:
                    return {"ok": False, "写代码": False, "节点": "conc/1089",
                            "还给人": True,
                            "error": "锚点《%s》的 relations 指到语料外的 %s"
                                     "——悬挂边如实报，不编。" % (plan_name, rid),
                            "为什么": "停手线：部件没教拼不出，缺才要。"}
                cname = str((self.nodes[rid].fields or {}).get("name") or rid)
                if cname in seen:
                    continue
                seen.add(cname)
                comps.append((cname, rid))
        else:
            for cm in re.finditer(r"《(.+?)》", pbody):
                cname = cm.group(1)
                if cname in seen:
                    continue
                cnid = self._route_node(cname)
                if not cnid:
                    return {"ok": False, "写代码": False, "节点": "conc/1089",
                            "还给人": True,
                            "error": "装配单《%s》点了构件《%s》，语料里没有——"
                                     "如实报缺，不编。" % (plan_name, cname),
                            "为什么": "停手线：构件没教拼不出，缺才要。"}
                seen.add(cname)
                comps.append((cname, cnid))
        parts = []
        for cname, cnid in comps:
            blocks = self._extract_py_blocks(self.nodes[cnid].body or "")
            if not blocks:
                return {"ok": False, "写代码": False, "节点": "conc/1089",
                        "还给人": True,
                        "error": "构件《%s》（%s）正文没有代码块——如实报缺，"
                                 "不编。" % (cname, cnid),
                        "为什么": "停手线：构件没教代码拼不出，缺才要。"}
            parts.extend(blocks)
        tmpl = {}
        for tm in re.finditer(r"【拼[:：]\s*(\S+?)】\s*```python\s*\n(.*?)```",
                              pbody, re.DOTALL):
            tmpl[tm.group(1)] = tm.group(2) + "\n"
        if not tmpl or not parts:
            return {"ok": False, "写代码": False, "节点": "conc/1089",
                    "还给人": True,
                    "error": "装配单《%s》缺构件清单或缺「【拼:文件名】」模板"
                             "——如实报缺，不编。" % plan_name,
                    "为什么": "停手线：装配单不完整，缺才要。"}
        # 主程序 = 构件块按序拼接 + 模板；test_ 开头的拼装文件独立用模板
        joined = "\n".join(b.rstrip("\n") for b in parts) + "\n\n"
        files = [(fn, joined + code if not fn.startswith("test_") else code)
                 for fn, code in tmpl.items()]
        # ── 参数消费（v0.2.71，conc/1122）：模板占位 @@名@@ 从原话拿值。
        # 机械替换：原话「名=值」⇒ 替进落盘文件；占位没值 ⇒ 问句不落盘
        # （缺才要，不编默认值）。留痕进回执。
        param_vals = {}
        for pv in re.finditer(r"([^\s：:=＝，,；;。、「」『』（）()]{2,24})"
                              r"\s*[:：=＝]\s*([^\s，,；;]+)", text):
            param_vals[pv.group(1)] = pv.group(2)
        tok_missing, tok_used = [], []
        for fname, code in files:
            for tok in sorted(set(re.findall(r"@@(.+?)@@", code))):
                if tok in param_vals:
                    tok_used.append({"参数": tok, "值": param_vals[tok]})
                else:
                    tok_missing.append(tok)
        if tok_missing:
            _msgs = ["模板占位「@@%s@@」的值原话没给——原话带「%s=值」补上，"
                     "或教进参数段；不问不编。" % (t, t) for t in tok_missing]
            return {"ok": False, "写代码": False, "节点": "conc/1089",
                    "还给人": True,
                    "error": "；".join(_msgs),
                    "为什么": "conc/1122 参数消费：占位没值不编默认值——"
                             "缺才要，写了就是编。"}
        if tok_used:
            for u in tok_used:
                files = [(fn, code.replace("@@%s@@" % u["参数"], u["值"]))
                         for fn, code in files]
        # ── 落盘（白名单内；同名须「覆盖」）──────────────────────────
        overwrite = "覆盖" in text
        made, errs = [], []
        for fname, code in files:
            norm = fname.replace("\\", "/")
            if os.path.isabs(fname) or ".." in norm.split("/"):
                errs.append("%s：文件名不合规，不落" % fname)
                continue
            fpath = os.path.join(target, fname)
            if os.path.exists(fpath) and not overwrite:
                errs.append("%s：已存在——原话带「覆盖」才覆盖" % fname)
                continue
            try:
                os.makedirs(target, exist_ok=True)
                with open(fpath, "w", encoding="utf-8", newline="\n") as f:
                    f.write(code)
                made.append({"文件": fpath,
                             "字节数": len(code.encode("utf-8"))})
            except OSError as e:
                errs.append("%s：写盘失败 %s" % (fname, e))
        if not made:
            return {"ok": False, "写代码": False, "节点": "conc/1089",
                    "落盘挂了": errs,
                    "error": "一个文件都没落成：" + "；".join(errs),
                    "为什么": "同名不覆盖是停手线；要重写原话带「覆盖」。"}
        # ── 语法自检（逐 .py）─────────────────────────────────────────
        syn = []
        for m_ in made:
            p = m_["文件"]
            if not p.endswith(".py"):
                continue
            try:
                cp = subprocess.run(
                    [sys.executable, "-X", "utf8", "-m", "py_compile", p],
                    capture_output=True, text=True, timeout=30)
                syn.append({"文件": os.path.basename(p),
                            "退出码": cp.returncode,
                            "输出": (cp.stdout + cp.stderr)[-300:]})
            except subprocess.TimeoutExpired:
                syn.append({"文件": os.path.basename(p), "退出码": -1,
                            "输出": "py_compile 超时（30 秒）——进程已杀"})
        # ── 自测一轮（py_compile 全过才跑）───────────────────────────
        test_run = None
        tfiles = [m_["文件"] for m_ in made
                  if os.path.basename(m_["文件"]).startswith("test_")]
        if tfiles and syn and all(s["退出码"] == 0 for s in syn):
            tp = tfiles[0]
            try:
                cp = subprocess.run(
                    [sys.executable, "-X", "utf8", os.path.basename(tp)],
                    capture_output=True, text=True, timeout=30, cwd=target)
                test_run = {"文件": os.path.basename(tp),
                            "退出码": cp.returncode,
                            "输出": (cp.stdout + cp.stderr)[-max_chars:]}
            except subprocess.TimeoutExpired:
                test_run = {"文件": os.path.basename(tp), "退出码": -1,
                            "输出": "自测超时（30 秒）——进程已杀"}
        self._route_log({"来源": "写代码 conc/1089", "目录": target,
                         "装配单": "%s(%s)" % (plan_name, plan_nid),
                         "构件": ["%s(%s)" % (c, i) for c, i in comps],
                         "参数": {u["参数"]: u["值"] for u in tok_used},
                         "文件": [os.path.basename(m_["文件"]) for m_ in made],
                         "自测退出码": (test_run or {}).get("退出码")})
        ok = (not errs) and all(s["退出码"] == 0 for s in syn) \
            and (test_run is None or test_run["退出码"] == 0)
        return {"ok": ok, "写代码": True, "节点": "conc/1089",
                "装配单": "%s(%s)" % (plan_name, plan_nid),
                "构件": ["%s(%s)" % (c, i) for c, i in comps],
                "参数替换": tok_used or None,
                "落盘": made, "落盘挂了": errs or None,
                "语法自检": syn or None, "自测": test_run,
                "为什么": ("LDV 自己拼的：构件清单带节点号，拼了什么对着语料"
                           "查得到原文；落盘+语法自检+自测各一轮——挂了如实报，"
                           "要不要改由人说。")}

    @staticmethod
    def _extract_path(text: str):
        """原话里提取文件/目录路径（引号内优先，其次裸 Windows/Unix 路径）。"""
        m = re.search(r"([\"'“”‘’`])([A-Za-z]:[/\\][^\"'“”‘’`]+|[/~][^\"'“”‘’`]+)\1",
                      text)
        if m:
            return m.group(2)
        m = re.search(r"([A-Za-z]:[/\\][^\s，。；！？\"'“”]+|/~[^\s，。；！？\"'“”]+)",
                      text)
        return m.group(1) if m else None

    @staticmethod
    def _extract_code(text: str):
        """原话里提取要执行的代码：反引号/单双引号（ASCII）内优先，
        其次「跑一句/执行」后面的代码片段。"""
        m = re.search(r"[`'\\\"]([^`'\\\"]{1,300})[`'\\\"]", text)
        if m and ("(" in m.group(1) or "=" in m.group(1) or "print" in m.group(1)):
            return m.group(1)
        m = re.search(r"(?:跑一句|跑段|执行|运行)\s*(.+)$", text)
        if m:
            seg = m.group(1).strip()
            seg = re.sub(r"^(一句|一段|代码|这段|这句话)\s*", "", seg)
            if seg and ("(" in seg or "=" in seg) and len(seg) <= 300:
                return seg
        return None

    def _serve_rule(self, text: str):
        """《拉起服务》：说了拉起/启动 + 服务名（须在 _services.json 登记过）
        ⇒ 真启动。没登记不猜路径；没点名交回问人；节点没教走旧路（可移除）。"""
        if not self._route_node("拉起服务"):
            return None
        if not any(w in text for w in _SERVE_TRIGGERS):
            return None
        reg = self._services_registry()
        servers = {k: v for k, v in reg.items()
                   if isinstance(v, dict) and v.get("exe")}
        tl = text.lower()
        named = []
        for k, v in servers.items():
            words = [k] + [str(a) for a in (v.get("别名") or [])]
            if any(w.lower() in tl for w in words):
                named.append(k)
        if len(named) != 1:
            # ★ 「创建」触发但没点到登记过的服务 ⇒ 让位《动作动词报缺》
            #   （conc/1072）：创建≈拉起只对**服务**成立（用户 2026-11-16 口径）；
            #   创建登录系统这类非服务物不在这里交回，交给动词报缺声明。
            if not named and [w for w in _SERVE_TRIGGERS if w in text] == ["创建"]:
                return None
            known = "、".join(servers) if servers else "（注册表一个服务都没登记）"
            if not named:
                why = ("说了要拉起但服务名没对上——登记过的有：%s。不猜。" % known)
            else:
                why = "点到了多个服务（%s）——说清要哪个，不猜。" % "、".join(named)
            self._route_log({"来源": "拉起服务 conc/1058", "点名": named or "无",
                             "结果": "交回"})
            return {"ok": False, "拉起服务": True, "节点": "conc/1058", "还给人": True,
                    "为什么": why,
                    "登记过的服务": list(servers) or None}
        r = self._service_start(named[0], reg)
        r.setdefault("为什么", "按《拉起服务》对注册表登记的服务真执行了一次启动/探测。")
        return r

    def _mongo_addr(self):
        """Mongo 实例地址：注册表 mongodb 段优先（考试实例），没有退 trigger.json。"""
        reg = (self._services_registry().get("mongodb") or {})
        cfg = {}
        base = (Path(self._corpus_root) if getattr(self, "_corpus_root", None)
                else Path("."))
        tp = base / "trigger.json"
        if tp.is_file():
            try:
                cfg = json.loads(tp.read_text(encoding="utf-8") or "{}").get("mongo") or {}
            except ValueError:
                cfg = {}
        host = str(reg.get("host") or cfg.get("host") or "127.0.0.1")
        port = int(reg.get("port") or cfg.get("port") or 27017)
        return host, port, cfg

    def _mongo_rule(self, text: str):
        """《Mongo存取》：说 Mongo + 库.集合（或 集合 X）+ 查/取/看 ⇒ 用线协议
        （mongowire，conc/396）真连一次 find，条数与样本如实带回执。
        连不上报「对方挂了」；过滤 JSON 不合法报「查法不成」；不猜集合名。"""
        if not self._route_node("Mongo存取"):
            return None
        tl = text.lower()
        if "mongo" not in tl:
            return None
        if not any(w in text for w in ("查", "取", "看", "读", "列")):
            return None
        import time as _t
        t0 = _t.time()
        host, port, _cfg = self._mongo_addr()
        db = "ldv_recon"                     # 对账数据都在这个库（conc/1059 口径）
        m = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)", text)
        m2 = re.search(r"集合[：:\s]*([A-Za-z_][A-Za-z0-9_]*)", text)
        if m and (m2 is None or m.start() <= m2.start()):
            db, coll = m.group(1), m.group(2)
        elif m2:
            coll = m2.group(1)
        else:
            return {"ok": False, "Mongo存取": True, "节点": "conc/1059", "还给人": True,
                    "为什么": "说了 Mongo 但没给出 库.集合（或「集合 X」）——不猜集合名。"}
        flt = {}
        mf = re.search(r"\{[^{}]*\}", text)
        if mf:
            try:
                flt = json.loads(mf.group(0))
            except ValueError:
                return {"ok": False, "Mongo存取": True, "节点": "conc/1059",
                        "查不了": "查法不成",
                        "error": "过滤条件不是合法 JSON：%s——如实报，不猜字段。"
                                 % mf.group(0)[:80]}
        if not _tcp_up(host, port, timeout=1.5):
            self._route_log({"来源": "Mongo存取 conc/1059", "库": db, "集合": coll,
                             "结果": "连不上"})
            return {"ok": False, "Mongo存取": True, "节点": "conc/1059", "库": db,
                    "集合": coll, "查不了": "对方挂了",
                    "error": f"{host}:{port} 连不上（MongoDB 没在跑）——"
                             "连不上是故障，不重试、如实报。",
                    "下一步": "说一声「拉起 MongoDB」，或自己把库起起来。"}
        limit = 20
        ml = re.search(r"前?\s*(\d{1,3})\s*条", text)
        if ml:
            limit = min(int(ml.group(1)), 200)
        r = self._mongo({"op": "find", "库": db, "集合": coll,
                         "过滤": flt, "条数": limit,
                         "主机": host, "端口": port})
        rows = r.get("结果") or []
        ms = int((_t.time() - t0) * 1000)
        self._route_log({"来源": "Mongo存取 conc/1059", "库": db, "集合": coll,
                         "过滤": flt, "条数": len(rows), "ok": bool(r.get("ok"))})
        out = {"ok": bool(r.get("ok")), "Mongo存取": True, "节点": "conc/1059",
               "库": db, "集合": coll, "过滤": flt, "条数": len(rows),
               "样本": rows[:5], "耗时毫秒": ms,
               "为什么": "按《Mongo存取》用 MongoDB 线协议（conc/396）真连一次 find——"
                         "条数与样本如实带，超界截断声明。"}
        if not r.get("ok"):
            out["查不了"] = r.get("弃答") or "查法不成"
        return out

    def _procedure_rule(self, text: str, max_chars: int = 600):
        """《规程执行》（conc/1127）：教学同步路由（conc/1126）的落法——
        原话点名规程（执行/跑/办/照 + 规程名）⇒ 读该节点的「## 可执行步单」
        段（JSON 数组，每步一个路由键）复用组合执行器逐步走，段间 «上一步»
        照旧。名字是唯一钥匙，长名优先。
        停手线：①没步单段 ⇒ 如实报缺「还没教可执行步单」（缺才要，不演）；
        ②占位 @@名@@ 原话没给 ⇒ 问句不执行（conc/1122 同款，不编默认值）；
        ③步单不合法 ⇒ 如实报错；④问句头不算执行（问定义走检索）。"""
        if not self._route_node("规程执行"):
            return None
        if text.lstrip().startswith(_VERB_QUESTION_HEADS) or "是什么" in text:
            return None                          # 「账号停用是什么」是问定义；
                                                 # 问句头只看句首——规程参数名带
                                                 # 「为什么发生=」不算问句（v0.2.77）
        if not any(w in text for w in ("执行", "跑", "办", "照")):
            return None
        best = None
        for nid, node in self.nodes.items():
            f = getattr(node, "fields", None) or {}
            nm = str(f.get("name") or "")
            if len(nm) < 3 or nm not in text:
                continue
            if best is None or len(nm) > len(best[1]):
                best = (str(nid), nm)
        if best is None:
            return None                          # 没点名规程 ⇒ 不拦
        nid, nm = best
        node = self.nodes.get(nid)
        body = str(getattr(node, "body", "") or "")
        m = re.search(r"##\s*可执行步单[^`]*?```(?:json)?\s*(\[.*?\])\s*```",
                      body, re.DOTALL)
        if not m:
            # 只对自称规程的节点报缺（名字带「规程」或文档类=流程规程）——
            # 别的概念被点名执行不算漏，回原路
            f = getattr(node, "fields", None) or {}
            if not (nm.endswith("规程") or "规程" in str(f.get("文档类") or "")
                    or "流程" in str(f.get("文档类") or "")):
                return None
            return {"ok": False, "规程执行": True, "节点": "conc/1127",
                    "还给人": True,
                    "缺": "《%s》教了规程但没教「可执行步单」——教了才跑，"
                          "不演、不猜步骤凑数。" % nm,
                    "为什么": "教学同步路由（conc/1126）：概念悬空=漏，报缺问人。"}
        try:
            specs = json.loads(m.group(1))
        except Exception as exc:
            return {"ok": False, "规程执行": True, "节点": "conc/1127",
                    "还给人": True,
                    "error": "《%s》的「可执行步单」不是合法 JSON：%s" % (nm, exc)}
        # ── 参数消费（conc/1122 同款）：@@名@@ 从原话「名=值」拿，缺 ⇒ 问句 ──
        blob = json.dumps(specs, ensure_ascii=False)
        param_vals = {}
        for pv in re.finditer(r"([^\s：:=＝，,；;。、「」『』（）()]{2,24})"
                              r"\s*[:：=＝]\s*([^\s，,；;]+)", text):
            param_vals[pv.group(1)] = pv.group(2)
        missing = sorted({t for t in re.findall(r"@@(.+?)@@", blob)
                          if t not in param_vals})
        if missing:
            return {"ok": False, "规程执行": True, "节点": "conc/1127",
                    "还给人": True, "缺参数": missing,
                    "问句": ("要跑《%s》，先补参数：%s。原话带「名=值」——"
                             "不编默认值。" % (nm, "、".join(missing)))}
        for t, v in param_vals.items():
            blob = blob.replace("@@%s@@" % t, v)
        try:
            specs = json.loads(blob)
        except Exception as exc:
            return {"ok": False, "规程执行": True, "节点": "conc/1127",
                    "error": "参数替换后步单不合法：%s" % exc}
        r = self._compose({"要": "执行规程《%s》" % nm, "步": specs})
        if isinstance(r, dict):
            r = dict(r)
            r["节点"] = "conc/1127"
            r["规程执行"] = True
            r["规程"] = nm
            r["规程节点"] = nid
        return r

    def _recon_rule(self, text: str):
        """《对账执行》：对账 + 日期 ⇒ 按 conc/1031–1057 教的十步口径对
        ldv_recon 真数据跑一遍：归一→去重→关联→分级匹配→异常分类→挂起→报告。
        幂等（重跑先清当日）；未匹配写 recon_unmatched，汇总写 recon_report。"""
        if not self._route_node("对账执行"):
            return None
        if "对账" not in text:
            return None
        md = re.search(r"(\d{4}-\d{2}-\d{2})", text)
        if not md:
            if any(w in text for w in ("执行", "跑", "开始", "做一次", "来一次")):
                return {"ok": False, "对账执行": True, "节点": "conc/1060",
                        "还给人": True,
                        "为什么": "要跑对账但没说哪一天——不猜默认日。"}
            return None                          # 问概念（如「对账匹配是什么」）不拦
        date = md.group(1)
        import time as _t
        from ldv.core import mongowire as MW
        t0 = _t.time()
        host, port, _cfg = self._mongo_addr()
        # ① Mongo 没在跑先按《拉起服务》拉起
        if not _tcp_up(host, port, timeout=1.5):
            up = self._service_start("mongodb", self._services_registry())
            if not up.get("ok"):
                return {"ok": False, "对账执行": True, "节点": "conc/1060",
                        "查不了": "对方挂了",
                        "error": "MongoDB 拉不起来，对账没法跑——"
                                 + str(up.get("error") or "")[:120]}
        # ② 取源数据
        def _fetch(coll):
            try:
                raw = MW.find(coll, {}, 5000, "ldv_recon", host, port)
            except (ConnectionRefusedError, TimeoutError, OSError) as exc:
                return None, f"{host}:{port} 连不上（{type(exc).__name__}）"
            doc = raw.get("文档") or {}
            if not doc.get("ok"):
                return None, str(doc.get("errmsg") or "命令错")
            cur = doc.get("cursor") or {}
            batch = list(cur.get("firstBatch") or [])
            for sec in (raw.get("序列") or []):
                if str(sec.get("字段") or "").endswith("firstBatch"):
                    batch += list(sec.get("文档") or [])
            return batch, None
        ch_rows, e1 = _fetch("channel_settlement")
        bk_rows, e2 = _fetch("bank_statements")
        fx_rows, e3 = _fetch("fx_rates")
        missing = [c for c, b in (("channel_settlement", ch_rows),
                                  ("bank_statements", bk_rows)) if not b]
        if e1 or e2 or missing:
            self._route_log({"来源": "对账执行 conc/1060", "对账日": date,
                             "结果": "报缺", "缺": missing, "error": e1 or e2})
            return {"ok": False, "对账执行": True, "节点": "conc/1060",
                    "查不了": "对方挂了" if (e1 or e2) else "查法不成",
                    "error": ("源数据取不到：%s" % "; ".join(x for x in (e1, e2) if x))
                             if (e1 or e2) else
                             ("源集合没数据（%s）——报缺不跑，不冒充对过账。" % "、".join(missing)),
                    "缺": missing or None}
        # ③ 解析归一 + 去重（渠道：文件重传与重复行；银行：重复行）
        batch_owner = {}
        seen_txn = set()
        seen_bref = set()
        for r0 in ch_rows:
            r0["_币种"] = _norm_currency(r0.get("currency_raw"))
            a = _parse_amount(r0.get("amount_raw"))
            r0["_金额"] = a
            if r0["_币种"] and a is not None:
                r0["_金额小"] = _minor(a, r0["_币种"])
            r0["_结算日"] = str(r0.get("settled_at_utc") or "")[:10]
            r0["_类型"] = str(r0.get("type") or "settlement")
            key = (str(r0.get("file_name") or ""), str(r0.get("checksum") or ""))
            bn = str(r0.get("batch_no") or "")
            if key != ("", "") and key in batch_owner and batch_owner[key] != bn:
                r0["_重传"] = True
            else:
                batch_owner.setdefault(key, bn)
            tn = str(r0.get("txn_id") or "")
            if tn:
                if tn in seen_txn:
                    r0["_重复行"] = True
                else:
                    seen_txn.add(tn)
        for r0 in bk_rows:
            r0["_币种"] = _norm_currency(r0.get("currency_raw"))
            a = _parse_amount(r0.get("raw_amount"))
            r0["_金额"] = a
            if r0["_币种"] and a is not None:
                r0["_金额小"] = _minor(a, r0["_币种"])
            r0["_银行UTC日"] = _bank_utc_date(r0)
            r0["_已用"] = False
            br = str(r0.get("bank_ref") or "")
            if br:
                if br in seen_bref:
                    r0["_重复行"] = True
                else:
                    seen_bref.add(br)
        def _ok(r0):
            return (not r0.get("_重传") and not r0.get("_重复行")
                    and r0.get("_币种") and r0.get("_金额") is not None)
        diffs, clean, matched = [], [], {"精确": [], "容差": [], "模糊": []}
        # 渠道重复（重传/重复行）单独一过：全部报告，不进任何匹配（conc/1046/1055）
        for r0 in ch_rows:
            if r0.get("_重传") or r0.get("_重复行"):
                diffs.append({"类别": "重复", "侧": "渠道",
                              "单": r0.get("txn_id") or r0.get("fee_id"),
                              "说明": "渠道重传/重复行——不入账（conc/1046/1055）"})
        # ④ 手续费挂账（conc/1050：拆行费用按 linked_txn 归到净额；没消化要报）
        fee_map = {}
        for r0 in ch_rows:
            if str(r0.get("type") or "") == "fee" and _ok(r0):
                fee_map.setdefault(str(r0.get("linked_txn") or ""), []).append(r0)
        # ⑤ 退款/冲正/拒付挂原单（conc/1049：金额可能不等、跨币种）
        by_txn = {}
        for r0 in ch_rows:
            if _ok(r0) and r0["_类型"] == "settlement":
                by_txn.setdefault(str(r0.get("txn_id") or ""), r0)
        for r0 in ch_rows:
            if not _ok(r0) or r0["_类型"] == "settlement" or r0["_类型"] == "fee":
                continue
            typ = r0["_类型"]
            orig = by_txn.get(str(r0.get("original_ref") or ""))
            if orig is None:
                diffs.append({"类别": "未匹配", "侧": "渠道", "单": r0.get("txn_id"),
                              "说明": f"{typ} 挂不到原单 %s——挂起"
                                      % (r0.get("original_ref") or "?")})
                continue
            amt, oamt = abs(r0["_金额"]), orig["_金额"]
            if r0["_币种"] != orig["_币种"]:
                rate = self._fx_mid(fx_rows, orig["_币种"], r0["_币种"])
                if rate is None:
                    diffs.append({"类别": "未匹配", "侧": "渠道", "单": r0.get("txn_id"),
                                  "说明": "跨币种退款但 fx_rates 没有中间价——挂起"})
                    continue
                oamt = oamt * rate
            o_min, a_min = _minor(oamt, r0["_币种"]), _minor(amt, r0["_币种"])
            label = {"refund": "退款", "reversal": "冲正", "chargeback": "拒付"}.get(
                typ, typ)
            # 银行侧有没有这笔退款入账（负行、参考号挂得上）
            b_hit = None
            for b in bk_rows:
                if (not b["_已用"] and not b.get("_重复行") and b.get("_币种") == r0["_币种"]
                        and (b.get("_金额") or 0) < 0
                        and abs(b["_金额小"]) == a_min
                        and (str(b.get("match_ref") or "") == str(r0.get("original_ref"))
                             or str(r0.get("original_ref") or "")
                             in str(b.get("narration") or ""))):
                    b_hit = b
                    break
            if b_hit is not None:
                b_hit["_已用"] = True
                clean.append({"类别": label, "单": r0.get("txn_id"),
                              "原单": r0.get("original_ref"),
                              "说明": "挂原单、金额相等、银行退款入账对上"})
            elif a_min == o_min:
                clean.append({"类别": label, "单": r0.get("txn_id"),
                              "原单": r0.get("original_ref"),
                              "说明": "挂原单、金额相等（银行侧无退款行——如冲正直接抵扣）"})
            else:
                gap = o_min - a_min
                diffs.append({"类别": label, "侧": "渠道", "单": r0.get("txn_id"),
                              "原单": r0.get("original_ref"),
                              "说明": ("银行侧未见退款出账" if label == "退款"
                                       else "金额不等")
                                     + "：退款 %s %s vs 原单 %s %s%s——挂起"
                                      % (amt, r0["_币种"], orig["_金额"], orig["_币种"],
                                         "（跨币种按中间价换算）"
                                         if r0["_币种"] != orig["_币种"] else "")})
        # ⑥ 结算匹配分级（conc/1037：精确→容差→模糊；窗口 T+2，conc/1045 跨日）
        def _in_window(bd, sd):
            if not bd or not sd:
                return False
            try:
                b = _dtm.date.fromisoformat(bd)
                s = _dtm.date.fromisoformat(sd)
            except ValueError:
                return False
            return s <= b <= s + _dtm.timedelta(days=2)
        def _has_ref(b, t):
            return (str(b.get("match_ref") or "") == t
                    or t in str(b.get("narration") or ""))
        for r0 in ch_rows:
            if not _ok(r0) or r0["_类型"] != "settlement":
                continue
            fees = fee_map.get(str(r0.get("txn_id")), [])
            for f in fees:
                f["_已用"] = True
            net = r0["_金额小"] - sum(
                _minor(f["_金额"], f["_币种"]) for f in fees
                if f["_币种"] == r0["_币种"])
            t = str(r0.get("txn_id") or "")
            cands = [b for b in bk_rows if not b["_已用"] and not b.get("_重复行")
                     and b.get("_币种") == r0["_币种"]
                     and _in_window(b.get("_银行UTC日"), r0["_结算日"])]
            tol = _tolerance_minor(r0["_币种"])
            ref_c = [b for b in cands if _has_ref(b, t)]
            pick, level = None, None
            if ref_c:
                b = ref_c[0]
                if abs(b["_金额小"] - net) <= tol:
                    pick, level = b, ("精确" if b["_金额小"] == net else "容差")
                else:
                    b["_已用"] = True
                    gap = net - b["_金额小"]
                    diffs.append({"类别": "短款" if gap > 0 else "长款", "侧": "渠道+银行",
                                  "单": t, "银行行": b.get("bank_ref"),
                                  "说明": "参考号对上但金额差 %s %s（渠道净 %s vs 银行 %s）"
                                          % (abs(gap) / (10 ** _RECON_DECIMALS.get(
                                              r0["_币种"], 2)), r0["_币种"],
                                             net, b["_金额小"])})
                    continue
            if pick is None:
                amt_c = [b for b in cands if b["_金额小"] == net]
                if amt_c:
                    pick, level = amt_c[0], ("精确" if ref_c else "模糊")
            if pick is not None:
                pick["_已用"] = True
                matched[level].append({"单": t, "银行行": pick.get("bank_ref"),
                                       "金额": r0["_金额"], "币种": r0["_币种"],
                                       "跨日": pick.get("_银行UTC日") != r0["_结算日"]})
                continue
            # 跨币种（conc/1044/1050）：按中间价换算后容差匹配
            for b in bk_rows:
                if b["_已用"] or b.get("_重复行") or not _in_window(
                        b.get("_银行UTC日"), r0["_结算日"]):
                    continue
                rate = self._fx_mid(fx_rows, r0["_币种"], b["_币种"])
                if rate is None:
                    continue
                major = _dec.Decimal(r0["_金额小"]) / (10 ** _RECON_DECIMALS.get(
                    r0["_币种"], 2))
                exp = _minor(major * _dec.Decimal(str(rate)), b["_币种"])
                tol_b = _tolerance_minor(b["_币种"])
                if not _has_ref(b, t):
                    continue
                b["_已用"] = True
                if abs(exp - b["_金额小"]) <= tol_b:
                    matched["容差"].append({"单": t, "银行行": b.get("bank_ref"),
                                            "说明": "跨币种按中间价 %s 换算匹配" % rate,
                                            "金额": r0["_金额"], "币种": r0["_币种"]})
                else:
                    gap = exp - b["_金额小"]
                    diffs.append({"类别": "汇率差", "侧": "渠道+银行", "单": t,
                                  "银行行": b.get("bank_ref"),
                                  "说明": "按中间价 %s 应入 %s，银行实入 %s——差 %s %s"
                                          % (rate, exp, b["_金额小"],
                                             abs(gap) / (10 ** _RECON_DECIMALS.get(
                                                 b["_币种"], 2)), b["_币种"])})
                break
            else:
                # ⑦ 没匹配上的渠道结算：T+2 内算在途，超了算短款（conc/1038）
                try:
                    age = (_dtm.date.fromisoformat(date)
                           - _dtm.date.fromisoformat(r0["_结算日"])).days
                except ValueError:
                    age = 99
                diffs.append({"类别": "在途" if 0 <= age <= 2 else "短款", "侧": "渠道",
                              "单": t,
                              "说明": "渠道已结算银行未入账（结算日 %s，距对账日 %d 天）"
                                      % (r0["_结算日"], age)})
        # 未被任何结算消化的费用行（conc/1050：手续费独行挂起）
        for fees in fee_map.values():
            for f in fees:
                if not f.get("_已用"):
                    diffs.append({"类别": "手续费独行", "侧": "渠道",
                                  "单": f.get("fee_id") or f.get("txn_id"),
                                  "说明": "费用行挂不到有效原单（%s）——挂起"
                                          % (f.get("linked_txn") or "无 linked_txn")})
        # ⑧ 银行剩余（长款/未匹配）
        for b in bk_rows:
            if b.get("_重复行"):
                diffs.append({"类别": "重复", "侧": "银行", "单": b.get("bank_ref"),
                              "说明": "银行重复行——不入账（conc/1046）"})
                continue
            if not b["_已用"] and _ok(b):
                diffs.append({"类别": "长款", "侧": "银行", "单": b.get("bank_ref"),
                              "说明": "银行有入账无渠道结算（%s %s %s）——挂起"
                                      % (b["_金额"], b["_币种"], b.get("narration") or "")[:60]})
        # ⑨ 幂等落库：先清当日再写（conc/1040 重跑不重复入账）
        now = _dtm.datetime.now().isoformat(timespec="seconds")
        unmatched = [{"recon_date": date, "类别": d["类别"], "侧": d.get("侧") or "",
                      "单": d.get("单"), "说明": d["说明"]} for d in diffs]
        try:
            for coll in ("recon_unmatched", "recon_report"):
                MW.command({"delete": coll,
                            "deletes": [{"q": {"recon_date": date}, "limit": 0}],
                            "$db": "ldv_recon"}, host, port)
            if unmatched:
                MW.insert("recon_unmatched", unmatched, "ldv_recon", host, port)
            report = {"recon_date": date, "ts": now,
                      "渠道_原始": len(ch_rows), "银行_原始": len(bk_rows),
                      "精确匹配": len(matched["精确"]), "容差匹配": len(matched["容差"]),
                      "模糊匹配": len(matched["模糊"]),
                      "对上的特殊项": clean, "差异清单": diffs,
                      "挂起数": len(unmatched),
                      "口径": "conc/1031–1057（十步）——环境干扰 7 类属运行时，不在本数据集"}
            MW.insert("recon_report", [report], "ldv_recon", host, port)
            ms = int((_t.time() - t0) * 1000)
            self._route_log({"来源": "对账执行 conc/1060", "对账日": date,
                             "渠道": len(ch_rows), "银行": len(bk_rows),
                             "差异": len(diffs), "挂起": len(unmatched),
                             "耗时毫秒": ms})
            return {"ok": True, "对账执行": True, "节点": "conc/1060", "对账日": date,
                    "调了": {"库": "ldv_recon", "渠道结算": len(ch_rows),
                             "银行流水": len(bk_rows), "汇率": len(fx_rows or [])},
                    "匹配": {"精确": len(matched["精确"]), "容差": len(matched["容差"]),
                             "模糊": len(matched["模糊"]), "特殊项对上": len(clean)},
                    "差异清单": diffs[:20], "差异笔数": len(diffs),
                    "挂起": {"写进": "recon_unmatched", "笔数": len(unmatched)},
                    "报告": {"写进": "recon_report"},
                    "留痕": "明细在 recon_report / recon_unmatched（幂等：重跑先清当日）",
                    "耗时毫秒": ms,
                    "为什么": "按 conc/1031–1057 教的十步口径对 ldv_recon 真数据跑了一遍——"
                              "归一/去重/关联/分级匹配/异常分类/挂起/报告，不自创规则。"}
        except (ConnectionRefusedError, TimeoutError, OSError) as exc:
            return {"ok": False, "对账执行": True, "节点": "conc/1060",
                    "查不了": "对方挂了",
                    "error": f"对账做完了但报告写不进库：{exc}——如实报，不装写进去了。"}

    def _fx_mid(self, fx_rows, cur_from, cur_to):
        """中间价换算率（conc/1050）：rate_to_usd 相除；缺任一边 ⇒ None 不猜。"""
        if cur_from == cur_to:
            return _dec.Decimal("1")
        r_from = r_to = None
        for f in fx_rows or []:
            if str(f.get("source") or "mid") != "mid":
                continue
            c = str(f.get("currency") or "")
            if c == cur_from:
                r_from = f.get("rate_to_usd")
            elif c == cur_to:
                r_to = f.get("rate_to_usd")
        if not r_from or not r_to:
            return None
        try:
            return _dec.Decimal(str(r_from)) / _dec.Decimal(str(r_to))
        except (TypeError, ValueError, ZeroDivisionError, ArithmeticError):
            return None

    def _text_search_rule(self) -> str | None:
        """《查不到按正文》教过才启用（v0.2.40）——没教走旧路（I5）。

        键=值检索够不着正文：问「讲负数的部分讲了什么」，「负负得正」的
        **正文**里有「负数」，但它的名字、字段都没有——键=值三步全落空，
        只好交人。这一步是阶梯的第四步：三步落空且预算有余时，拿人给的词
        在正文里搜一遍。        教导即配置：本节点删掉，这一步就不存在。
        """
        for nid, n in self.nodes.items():
            if str((n.fields or {}).get("name") or "") == "查不到按正文":
                if str(n.fields.get("裁决") or "") == "判错":
                    return None
                return nid
        return None

    def _body_hits(self, clue_ws, name_ws, *, limit=8) -> dict:
        """《查不到按正文》的动作：在非判错条目的**正文**里搜词。

        只报「哪条的正文里出现过哪个词」，引文带上词的前后文——正文是材料
        不是字段命中。线索给的词排在前（人指的），问句里剥出来的已教名字
        排在后（我认的）；一条命中即停（报第一个词），不替人挑。
        """
        hits = []
        for nid, n in self.nodes.items():
            if str(n.fields.get("裁决") or "") == "判错":
                continue
            # 路由节点（形态=路由）的正文是**教法文本**，不是材料——2026-11-11
            # 实测：561 自己的正文里举了「负数」的例，把自己搜进结果当了第 7 条。
            # 正文检索交的是材料，教法混进去就是拿说明书冒充货。
            if str(n.fields.get("形态") or "") == "路由":
                continue
            body = str(getattr(n, "body", "") or "")
            if not body:
                continue
            got = None
            for src, ws in (("线索", clue_ws), ("问句", name_ws)):
                for w in ws:
                    pos = body.find(w)
                    if pos >= 0:
                        got = {"id": nid, "名字": n.fields.get("name"),
                               "词": w, "词从哪": src,
                               "引文": body[max(0, pos - 16): pos + len(w) + 48]}
                        break
                if got:
                    break
            if got:
                hits.append(got)
        return {"共": len(hits), "条": hits[:limit]}

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
        # v0.2.50：spec 可带 主机/端口 覆盖（考试实例在 27018，trigger.json 不动）
        host = str(spec.get("主机") or cfg_m.get("host") or "127.0.0.1")
        port = int(spec.get("端口") or cfg_m.get("port") or 27017)
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
                    "弃答": f"{host}:{port} 没接上：{type(exc).__name__}: {str(exc)[:80]}"
                            f"——连不上是故障，不重试、如实报人。",
                    "依据": [{"原文": "《MongoDB 端口协议》：连不上（拒绝/超时）才是故障（conc/396）",
                              "可复算": f"对 {host}:{port} 做一次 TCP connect 即可复现"}]}
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
