# -*- coding: utf-8 -*-
"""把交付物里的英文引文回源文逐字比对（v4，覆盖式 + 覆盖比）。

用法:
    python verify_quotes.py <doc.md> [doc2.md ...]

v4 判定方式（**三条判据，任一条不成立就报**）:

  0. 先把引文按 `…` / `...` **切成片段**再逐段判 ——
     那两个符号的意思是「这里省略了一段」，所以**每一段**都得逐字出现。
     （v3 把整条当一个整体判，省略号本身把覆盖比拉下来，
       于是「合法的省略」与「编造的句子」在输出里长得一样。）
  1. 片段短于 `K` ⇒ 只能**逐字包含**，没有别的判法（k-gram 无从下手）。
  2. 片段不短于 `K` ⇒ 覆盖比必须 ≥ `COVER_MIN`。
  3. 且未被覆盖的**连续片段**不得 ≥ `GAP_MIN`（定位用：告诉你是哪一段掉了）。

⚠️ **为什么必须加第 2 条（v3 的洞）**：v3 只有第 3 条，而 `GAP_MIN = 25`。
   一条 30 字符的引文，只要**碰巧**有十来字符的 k-gram 命中，
   剩下的未覆盖段就都短于 25 ⇒ v3 判「全覆盖」。
   实测抓到的实例：`「E.p is the Union of all entries on N.」`
   （设计文档 §10.2 A，署名 Hellerstein 1995）——
   语料里**根本没有**这句，但 `istheunion` / `allentries` 这类短串碰巧命中，
   v3 报 0 缺口。覆盖比只有 **23/29 = 0.79**。
   ⇒ 短引文是这套检查的盲区，而**短引文恰恰是最容易凭印象写出来的那种**。

⚠️ v3 的 docstring 写「k=28」，代码里是 `K = 10` —— 文字与代码不一致，
   而且 10 太小（偶然命中率高）。v4 把 `K` 定到 `12` 并**由覆盖比兜底**。
"""
import sys, re, glob, unicodedata, os

_HERE = os.path.dirname(os.path.abspath(__file__))
#: 工作区根 —— `repo/docs/prior-art/` 往上**三级**（`prior-art` → `docs` → `repo` → 根）。
_WS = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
#: ⚠️ **两份语料，不是一个**（2026-10-07 修）：
#:   `<工作区根>/.prior-art/*.txt`  —— 设计文档 / 成熟方案文档的源档
#:   `<工作区根>/sources/*.txt`     —— `outputs/ldv-成熟答案印证.md` 的源档
#: v3 只读了第一份 ⇒ **把一半的源文当成不存在**，于是真引文被报成「缺口」。
#: 实测：`E.p is the Union of all entries on N.`（GiST 1995）在
#: `sources/gist1995.txt` 里逐字有，v3 却看不见。
#:
#: ⚠️ **版权正文不进仓库** ⇒ 这两份语料**不在**本目录里（见 `README.md`）。
#: 路径可用环境变量覆盖；**找不到就报错退出**，绝不静默报「全覆盖」——
#: 那正是「空转与通过长得一模一样」。
_PRIOR = os.environ.get("LDV_PRIOR_ART") or os.path.join(_WS, ".prior-art")
_SOURCES = os.environ.get("LDV_SOURCES") or os.path.join(_WS, "sources")
SRC_DIRS = [_PRIOR, _SOURCES]
#: 旧数学排版的源：符号被换成码（`/:`→`.`、`/n28`→`(`、`/_`→`∨` …）。
#: **只对这些源做符号码还原** —— 拿去改干净正文会把假绿引进来。
SOFT_FILES = {"gist1995.txt"}

#: 扫描件（OCR）源：字形混淆。**只对这些源生效**，否则 `("mam","main")` 一类
#: 会去改干净正文，把假绿引进来。表与 `outputs/_verify_blocks.py` 同源。
OCR_FILES = {"rstree.txt", "guttman84.txt", "xtree96.txt", "driscoll89.txt"}
OCR_FIX = [
    ("0( 1)", "O(1)"), ("0(1)", "O(1)"), ("Q(n)", "\u0398(n)"), ("G(i)", "\u0398(i)"),
    ("1s", "is"), ("OTl", "OT1"),
    ("re-msertlon", "re-insertion"), ("Re-msert", "Re-insert"),
    ("remsert", "reinsert"), ("Remsert", "Re-insert"),
    ("msertlon", "insertion"), ("msertion", "insertion"),
    ("mserted", "inserted"), ("msert", "insert"),
    ("supemode", "supernode"), ("Supemode", "Supernode"),
    ("ehnnnate", "eliminate"), ("Ehnnnate", "Eliminate"),
    ("ehmmatron", "elimination"), ("ehmmated", "eliminated"), ("elunmated", "eliminated"),
    ("entrles", "entries"), ("entnes", "entries"), ("Entnes", "Entries"),
    ("utlllzatton", "utilization"), ("restructurmg", "restructuring"),
    ("correspondmg", "corresponding"), ("Summarizmg.", "Summarizing,"),
    ("decreasmg", "decreasing"), ("covermg", "covering"), ("makmg", "making"),
    ("maxlmum", "maximum"), ("mmunum", "minimum"),
    ("dlsposmg", "disposing"), ("dlstnbuted", "distributed"), ("outhned", "outlined"),
    ("dflers", "differs"), ("t*", "task"),
    ("rmplement", "implement"), ("routme", "routine"), ("unproved", "improved"),
    ("accom phshes", "accomplishes"), ("durmg", "during"), ("dunng", "during"),
    ("Insertion", "insertion"),
    ("mvoke", "invoke"), ("mstead", "instead"), ("possrble", "possible"),
    ("adlacent", "adjacent"), ("whch", "which"), ("slblmg", "sibling"),
    ("mth", "with"), ("smce", "since"), ("wdl", "will"), ("hgher", "higher"),
    ("therr", "their"), ("mam", "main"), ("l&e", "like"),
    ("sphts", "splits"), ("spht", "split"), ("Spht", "Split"),
    ("M+l", "M+1"), ("date structure", "data structure"), ("keyk", "key k"),
    # 扫描件把句点印成空格：Guttman AT3 / CT4 里 `EN.I` 被印成 `EN I`。
    # 实测：只差这 1 个字符，就把它后面 9 个 12-gram 全部错开 ⇒ 覆盖比掉到 0.84。
    ("EN I", "EN.I"),
]
OCR_REGEX = [(r"\bm\b", "in"), (r"\bIS\b", "is")]


def ocr(t: str) -> str:
    for a, b in OCR_FIX:
        t = t.replace(a, b)
    for pat, rep in OCR_REGEX:
        t = re.sub(pat, rep, t)
    return t

K = 12
GAP_MIN = 25
COVER_MIN = 0.90
MIN_FRAG = 8          # 比这短的片段不当引文（是 `Modified node` 这类术语标签）
ELLIPSIS = re.compile(r"\.{3}|…")

LIG = {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi",
       "\ufb04": "ffl", "\ufb05": "st", "\ufb06": "st"}
QUOTES = {"\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
          "\u201c": '"', "\u201d": '"', "\u201e": '"'}
DASHES = {"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
          "\u2014": "-", "\u2015": "-", "\u2212": "-", "\u00ad": ""}
# pypdf 在本批 PDF 里把 ¬(U+00AC) 误映射成 ⁄(U+2044)；等价化以便比对
MIS_MAP = {"\u2044": "\u00ac"}
CJK = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")


def base_norm(t: str) -> str:
    t = unicodedata.normalize("NFC", t)
    for k, v in LIG.items():
        t = t.replace(k, v)
    for k, v in QUOTES.items():
        t = t.replace(k, v)
    for k, v in DASHES.items():
        t = t.replace(k, v)
    for k, v in MIS_MAP.items():
        t = t.replace(k, v)
    t = t.replace("\u2227", " and ").replace("\u2228", " or ")
    return re.sub(r"\s+", " ", t)


def strip_md(t: str) -> str:
    t = t.replace("\\|", "|")
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = t.replace("**", "").replace("*", "")
    t = re.sub(r"^[ \t]*[>|-]+[ \t]*", "", t, flags=re.M)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    return t


def soft(t: str) -> str:
    """旧数学排版的符号码还原 —— **只对 `SOFT_FILES` 里的源用**。

    证据：`sources/gist1995.txt:752-757` 的 PR1/PR2 原文长这样 ——

        PR2: Otherwise, modify the entry E which points to
        N so that E/: p is the Union of all entries onN .
        Then AdjustKeys(R , Parent(N ).)

    其中 `/:` 是 `.` 的码、`/n28` `/n29` 是括号。引文按**读法**写
    （`E_i.p` → `E.p`），所以比对前先把源文还原成读法。
    """
    t = t.replace("/n28", "(").replace("/n29", ")")
    t = t.replace("/_", "\u2228").replace("/!", "\u2192").replace("/$", "\u2261")
    t = t.replace("/:", ".").replace("/;", ",")
    t = re.sub(r"\s+/([A-Za-z0-9])", r"\1", t)     # 被拆行的下标：`ptr /1` → `ptr1`
    return t


def norm(t: str, soft_first: bool = False, ocr_first: bool = False) -> str:
    """归一化 + 去空白 + 去跨行连字符 + 小写。

    ⚠️ **`*` 与 `\\` 在两边一起去掉**：文档里写 `{x\\*}`（markdown 转义），
    源文里写 `{x*}`。只去掉文档那边的 `*` 会留下一个反斜杠 ⇒ 假缺口。
    两边对称地去掉，比较才成立。
    """
    if soft_first:
        t = soft(t)
    if ocr_first:
        t = ocr(t)
    t = base_norm(strip_md(t))
    t = re.sub(r"-\s+", "", t)
    t = t.replace("\\", "").replace("*", "")
    t = re.sub(r"\s+", "", t)
    return t.lower()


def load_sources():
    """读**两份**语料（见 `SRC_DIRS`）。同名文件后读的覆盖先读的 —— 会打印出来。

    ⚠️ **一份源文都找不到 ⇒ 报错退出**（`SystemExit(2)`），不返回空表。
       空表会让每一条引文都算「没命中」⇒ 全红 —— 方向是对的，但**红得莫名其妙**；
       而如果有人把「红」读成「引文写错了」，就会去改**本来是对的**引文。
       「源文不在」与「引文有问题」必须长得不一样。
    """
    srcs, seen = {}, {}
    for d in SRC_DIRS:
        for p in sorted(glob.glob(os.path.join(d, "*.txt"))):
            name = os.path.basename(p)
            if name in seen:
                print(f"⚠️ 同名源文出现两次：{seen[name]} 与 {p} —— 用后者")
            seen[name] = p
            srcs[name] = norm(open(p, encoding="utf-8", errors="replace").read(),
                              soft_first=(name in SOFT_FILES),
                              ocr_first=(name in OCR_FILES))
    if not srcs:
        print("✗ 一份源文都没找到 —— **这不是「引文全过」，是核验根本没跑**。")
        print(f"  找过这两处：")
        for d in SRC_DIRS:
            print(f"    {d}")
        print("  版权正文**不随仓库发布**（见 `docs/prior-art/README.md`）：")
        print("  按那份索引里的 URL 自己取一份，或设 `LDV_PRIOR_ART` / `LDV_SOURCES`")
        print("  指向已有的工作副本。")
        raise SystemExit(2)
    return srcs


def extract_quotes(md: str):
    qs = []
    for m in re.finditer(r"「([^」]{12,})」", md, flags=re.S):
        body = m.group(1)
        if not re.search(r"[A-Za-z]{3}", body):
            continue
        if len(CJK.findall(body)) >= 2:
            continue
        qs.append((md[:m.start()].count("\n") + 1, body))
    return qs


def kgrams(s, k=K):
    return {s[i:i + k] for i in range(len(s) - k + 1)}


def fragments(q: str):
    """把一条引文按 `…` / `...` 切成片段 —— 省略号两侧各自都要逐字出现。

    ⚠️ **先把 `...` 与紧挨着的句点合并**：`Completeness).** ...` 归一化之后
    变成 `(completeness)....`（**四个点**），按 `\\.{3}` 切会留下一个孤立的前导点，
    于是片段成了 `.g(a)⇒d(a)` ⇒ 判成 0.00（假缺口）。实测踩到过。
    """
    q = re.sub(r"\.{3,}", "\u2026", q)
    out = []
    for p in q.split("\u2026"):
        p = p.strip().strip(".")
        if p:
            out.append(p)
    return out


def coverage(q: str, grams: set, flat: str, k: int = K):
    """返回 (覆盖比, 未覆盖的连续片段列表)。

    片段短于 `k` ⇒ 只能逐字包含（`flat` 是全语料去空白后的拼接）。
    """
    n = len(q)
    if n < k:
        return (1.0, []) if q in flat else (0.0, [q])
    covered = [False] * n
    for i in range(n - k + 1):
        if q[i:i + k] in grams:
            for j in range(i, i + k):
                covered[j] = True
    ratio = sum(covered) / n
    out, run = [], 0
    for i in range(n):
        if covered[i]:
            if run >= GAP_MIN:
                out.append(q[i - run:i])
            run = 0
        else:
            run += 1
    if run >= GAP_MIN:
        out.append(q[n - run:])
    return ratio, out


def gaps(q: str, grams: set, flat: str = "", k: int = K):
    """兼容旧调用：只回缺口列表。"""
    return coverage(q, grams, flat, k)[1]


def main():
    srcs = load_sources()
    grams = set()
    for s in srcs.values():
        grams |= kgrams(s)
    flat = "".join(srcs.values())
    print(f"源文 {len(srcs)} 份，k-gram（k={K}）集合 {len(grams)} 条，"
          f"判据：覆盖比 ≥ {COVER_MIN} 且无 ≥ {GAP_MIN} 字符的连续缺口\n")

    total = ok = bad_n = 0
    for doc in sys.argv[1:]:
        md = open(doc, encoding="utf-8").read()
        print(f"=== {os.path.basename(doc)} ===")
        for line, body in extract_quotes(md):
            q = norm(body)
            if len(q) < 8:
                continue
            total += 1
            worst, notes = 1.0, []
            for frag in fragments(q):
                if len(frag) < MIN_FRAG:
                    continue
                ratio, g = coverage(frag, grams, flat)
                if ratio < worst:
                    worst = ratio
                if ratio < COVER_MIN:
                    notes.append(f"覆盖比 {ratio:.2f}：{frag[:90]}")
                if g:
                    notes.append("缺口：" + " ‖ ".join(x[:60] for x in g))
            if notes:
                bad_n += 1
                print(f"  L{line}  最低覆盖比 {worst:.2f}")
                for x in notes[:3]:
                    print(f"        {x}")
            else:
                ok += 1
        print()
    print(f"合计：引文 {total} 条｜全覆盖 {ok}｜有缺口 {bad_n}")


if __name__ == "__main__":
    main()
