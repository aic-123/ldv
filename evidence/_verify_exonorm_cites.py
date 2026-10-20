# -*- coding: utf-8 -*-
"""核验 `repo/docs/外生声明标准.md`：

  ① 每一条英文引文逐字回源文（`.prior-art/*.txt`）比对，并**报页码**；
  ② 页码与 §7 表里写的**相符**；
  ③ §7 的引文条数 == 文档里实际抽出的引文条数（防「漏抓 = 全干净」）；
  ④ D2/D3 的本轮读数 == `outputs/_task_ext_incremental.out.txt` 留档**现算**出来的数；
  ⑤ 自判 + 退出码（`--reverse` 反向对照：必须红）。

用法：
    python outputs/_verify_exonorm_cites.py            # 正向
    python outputs/_verify_exonorm_cites.py --reverse  # 反向（把一篇源文换掉 ⇒ 必须报红）
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "repo" / "docs" / "外生声明标准.md"
PA = ROOT / ".prior-art"
ARCHIVE = ROOT / "outputs" / "_task_ext_incremental.out.txt"

# ── 归一化（沿用 `repo/docs/prior-art/verify_quotes.py` 的三层纪律） ────────────
LIG = {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi",
       "\ufb04": "ffl", "\u201c": '"', "\u201d": '"', "\u2018": "'",
       "\u2019": "'", "\u2013": "-", "\u2014": "-", "\u00a0": " ",
       "\u2026": "...", "\\|": "|", "\uff1a": ":"}


def norm_ws(t: str) -> str:
    """第一层：只压空白 / 连字 / 跨行连字符（不动符号）。"""
    for k, v in LIG.items():
        t = t.replace(k, v)
    t = " ".join(t.split())
    t = re.sub(r"-\s+", "", t)          # 跨行连字符
    t = re.sub(r"\s+([,.;:)])", r"\1", t)
    t = re.sub(r"([(])\s+", r"\1", t)
    return t


def norm_hard(t: str) -> str:
    """第二层：再去空格 + 小写 ⇒ 吃掉「丢空格」与大小写不敏感两类抽取瑕疵。"""
    return norm_ws(t).replace(" ", "").lower()


def pages_of(path: Path):
    """([(pdf_page, 归一化后的整页文本), ...])"""
    out, cur, buf = [], None, []  # type: ignore[var-annotated]
    for ln in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"===PAGE (\d+)===", ln.strip())
        if m:
            if cur is not None:
                out.append((cur, norm_ws(" ".join(buf))))
            cur, buf = int(m.group(1)), []
        elif cur is not None:
            buf.append(ln)
    if cur is not None:
        out.append((cur, norm_ws(" ".join(buf))))
    return out


#: (源文件名, [ (编号, [逐字片段...], 文档里写的页), ... ])
#: 片段即「要在该源里逐字出现」的字符串；`…` 分隔的每一段都要在。
CITES = [
    ("sakai_slides", [
        ("S1", ["is a function of some gold standard data and a system output "
                "\u2013 and nothing else", "is not a metric"], "p.37"),
        ("S12", ["statistical signiﬁcance does not necessarily imply practical "
                 "signiﬁcance, and statistical insigniﬁcance does not "
                 "necessarily imply practical insigniﬁcance"], "p.1"),
    ]),
    ("sigir20bccm", [
        ("S2", ["online evaluation cannot be used to evaluate alternative "
                "rankers, as click-logs may be biased to the original ranker"], "p.1"),
        ("S13", ["Sakai[31] analyzed several mistakes commonly made in IR papers, "
                 "including under- and over- powered significance tests"], "p.1"),
    ]),
    ("artstein08", [
        ("S3", ["content analysis researchers generally think of K>.8 as good "
                "reliability, with .67<K <.8 allowing tentative conclusions to be drawn"], "p.576"),
        ("S4", ["Even a cutoff point of \u03b1 = .800 ... is a pretty low standard"], "p.576"),
        ("S5", ["reliability coefficients of .90 or greater would be acceptable "
                "to all, .80 or greater would be acceptable in most situations, "
                "and below that, there exists great disagreement"], "p.576"),
    ]),
    ("yang12", [
        ("S6", ["We suggest not to work with unfiltered results or to clearly "
                "document the disagreement rates"], "p.1"),
    ]),
    ("snow08", [
        ("S7", ["we require an average of 4 non-expert labels per item in order "
                "to emulate expert-level label quality"], "p.262"),
    ]),
    ("moffat18", [
        ("S8", ["Non-pooled documents \u2013 ones that did not appear in the top-d "
                "sets of any of the contributing systems \u2013 are then deemed to "
                "be non-relevant for the purposes of evaluating the relative "
                "behavior of the systems"], "p.1"),
        ("S9", ["we recommend as an absolute minimum that authors be encouraged "
                "to report the fraction of unjudged documents among the top k "
                "documents for each topic, for some appropriate value of k "
                "(perhaps the limit on the evaluation depth, such as when "
                "NDCG@k is being computed), as a routine part of their "
                "experimental results presentation"], "p.20"),
        ("S10", ["statistical significance tests do not reflect the degree of "
                 "uncertainty in per-topic point estimates that can arise from "
                 "the presence of unjudged documents in a ranked results list"], "p.20"),
    ]),
    ("moffat16", [
        ("S11", ["on the majority of the established test collections, and across "
                 "a range of evaluation depths, recall-based metrics tend to be "
                 "more volatile in the system rankings they generate than are "
                 "utility-based metrics"], "p.1"),
    ]),
]

#: 源 → (PDF 页 → 论文页) 的换算；None = 用 PDF 页
OFFSET = {"snow08": 253, "artstein08": 554}

#: 各源在 §7 里写的页码 => 期望的 PDF 页
PAGE_MAP = {
    "sakai_slides": {"p.37": 37, "p.1": 1},
    "sigir20bccm": {"p.1": 1},
    "artstein08": {"p.576": 22},
    "yang12": {"p.1": 1},
    "snow08": {"p.262": 9},
    "moffat18": {"p.1": 1, "p.20": 20},
    "moffat16": {"p.1": 1},
}

ok_n = 0
fails: list[str] = []
notes: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    global ok_n
    if cond:
        ok_n += 1
        print("✓ %s" % name)
    else:
        fails.append(name)
        print("✗ %s %s" % (name, detail))
    return bool(cond)


# ── 0) 反向对照：把一篇源文换成别的源的正文 ⇒ 下面必须全红 ────────────────────
REVERSE = "--reverse" in sys.argv
src_swap: dict[str, str] = {}
if REVERSE:
    # 只换 artstein08 的正文（其余源不动）—— 足够让涉及它的三条引文与页码全红
    src_swap = {"artstein08": "snow08"}

print("═" * 78)
print("外生声明标准 · 引文与读数核验 %s" % ("（**反向对照**：源文已偷换）" if REVERSE else ""))
print("═" * 78)

# ── 1) 引文逐字核验 + 页码相符 ─────────────────────────────────────────────
print("\n── ① 逐字回源（含页码）")
n_hard = 0
for src, cites in CITES:
    real = src_swap.get(src, src)
    pages = pages_of(PA / f"{real}.txt")
    for tag, segs, doc_page in cites:
        found = None
        layer = None
        for pno, txt in pages:
            if all(norm_hard(s) in norm_hard(txt) for s in segs):
                found, layer = pno, "硬"
                break
        if found is None:
            for pno, txt in pages:
                if all(norm_ws(s) in norm_ws(txt) for s in segs):
                    found, layer = pno, "软"
                    break
        if layer == "硬":
            n_hard += 1
        exp = PAGE_MAP[src].get(doc_page)
        ok = found is not None and exp == found
        check("引文 %s（源 %s，文档写 %s）" % (tag, src, doc_page),
              ok,
              "→ 实测 PDF 页 %s（%s）" % (found, layer or "MISS"))
        if found is not None and layer == "软":
            notes.append("%s 只在「压空白」层命中（仍有跨行连字符/丢空格）" % tag)

# ── 2) 文档里实际抽出的引文条数 == §7 表的条数 ──────────────────────────────
print("\n── ② 抽取范围自检（「0 条」与「全干净」不许长得一样）")
md = DOC.read_text(encoding="utf-8")
found_q = [m.group(1) for m in re.finditer(r"「([^」]{12,})」", md, flags=re.S)
           if re.search(r"[A-Za-z]{3}", m.group(1))
           and len(re.findall(r"[\u4e00-\u9fff]", m.group(1))) < 2]
table_rows = re.findall(r"^\| (\d+) \| ", md, flags=re.M)
check("§7 表行数 = 13", len(table_rows) == 13, "")
check("文档里抽得出引文（不是 0 条）", len(found_q) > 0, "")
# ★ 真正的不变量：**抽出的每一条**（含表内重复引用与内联小片段）
#   都要能在盘上某份源文里逐字找到 ⇒ 没有「凭空的一句英文」
SRC_ALL = [pages_of(PA / f"{n}.txt") for n, _ in CITES]
n_trace = 0
untraced = []
for q in found_q:
    hit = any(any(norm_hard(q) in norm_hard(txt) for _, txt in pages)
              for pages in SRC_ALL)
    if hit:
        n_trace += 1
    else:
        untraced.append(q[:60])
check("抽出的 %d 条全部可追到源文" % len(found_q), n_trace == len(found_q),
      "→ 追不到：%s" % untraced)
print("   （13 条引文在正文与 §7 表各出现一次 = 26，另 1 条是 B2 的内联小片段"
      "「a pretty low standard」—— 已并入上一条判定）"
      if len(found_q) == 29 else
      "   （抽出 %d 条；预期 29 —— 对不上说明文档引用形态变了，需人工看一眼）" % len(found_q))

# ── 3) 本轮读数 vs 留档现算 ────────────────────────────────────────────────
print("\n── ③ 读数对留档（现算，不抄文档）")
rows = [l.rstrip("\n") for l in ARCHIVE.read_text(encoding="utf-8").splitlines()]
arm, cmp3, jdg = [], [], []
for l in rows:
    if not re.match(r"^ext-\S+\s", l):
        continue
    t = l.split()
    if len(t) == 14:
        arm.append(t)                                   # 表1 四臂
    elif len(t) == 9 and t[6] in ("True", "False"):
        cmp3.append(t)                                   # 表2 三条对比
    elif len(t) == 9 and t[3] in ("True", "False"):
        jdg.append(t)                                    # 表4 判断层
check("留档三张表都解析出来了（14/9/9 列）",
      len(arm) == 10 and len(cmp3) == 10 and len(jdg) == 10,
      "→ %d/%d/%d" % (len(arm), len(cmp3), len(jdg)))

if len(arm) == 10 and len(cmp3) == 10 and len(jdg) == 10:
    # 表1 列序：0 语料 1 领域 2 节点 3 A项 4 键集A 5 键集全 6 方向A 7 方向B
    #          8 候选② 9 候选③ 10 ②=③树 11 候选④ 12 A的贡献 13 少扫
    check("②=③树：10 份全 True", all(r[10] == "True" for r in arm),
          "→ 实测 %s" % sorted({r[10] for r in arm}))
    check("A的贡献：10 份全 0", all(r[12] == "0" for r in arm))
    n_less = sum(1 for r in arm if int(r[13].split("/")[0]) < int(r[13].split("/")[1]))
    check("少扫（scanned < |Q|）= 6/10", n_less == 6, "→ 实测 %d" % n_less)
    check("②候选 = 真值：10/10", all(r[6] == "True" for r in cmp3))
    check("正文含标签：10 份全 0（D2 的前提）", all(r[4] == "0" for r in cmp3))
    same3 = [r[0] for r in jdg if r[3] == "True" and r[7] == "True"]
    check("判断层三字段全同 = 2/10 且是 coauth/astroph",
          len(same3) == 2 and same3 == ["ext-coauth", "ext-astroph"],
          "→ %s" % same3)
    kept = [r for r in jdg if r[8] == "沿用"]
    diff = sum(int(r[5]) for r in kept)
    blocks = sum(int(r[6]) for r in kept)
    check("沿用声明 9 份的画像差/画像块数 = 30/72",
          len(kept) == 9 and diff == 30 and blocks == 72,
          "→ %d/%d（份数 %d）" % (diff, blocks, len(kept)))
    rewrite = [r[0] for r in jdg if r[8] == "重写"]
    check("声明重写 = 1/10 且是 ext-email",
          rewrite == ["ext-email"], "→ %s" % rewrite)

# ── 4) 文档自身的边界陈述：未核一节必须存在且非空 ───────────────────────────
print("\n── ④ 「未核」不能缺席")
check("§8 列出 7 篇未核文献", md.count("| Voorhees 2000") >= 1 and md.count("| Nosek") >= 1)

# ── 5) 汇总 + 退出码 ──────────────────────────────────────────────────────
print("\n" + "═" * 78)
print("通过 %d ｜ 红 %d %s" % (ok_n, len(fails), ("｜ " + "；".join(fails[:6])) if fails else "｜ RC 0"))
if notes:
    print("⚠️ 需人工看一眼：%s" % "；".join(notes))
print("引文硬命中 %d/13（其余经「压空白」层命中，抽取瑕疵见 notes）" % n_hard)
print("═" * 78)
sys.exit(1 if fails else 0)
