# -*- coding: utf-8 -*-
"""**保证矩阵** —— 每条承诺 × 哪些判据在守它 × 有没有零判据的。

## 它治的是什么

外部审查的原话：「需要把这些局部保证整理成一份明确的**保证矩阵**，
而不是让读者从大量文档、断言编号和探针输出中自行拼接。」

本工具做三件事：

    ① 抽出**承诺清单**（断言集的 §K/§T/§A/§D/§S + 验收的 B1–B20）
    ② 抽出**判据清单**（`run_tests` 的 `ok(...)` 标签 + `checks/` 的 `rep.add(...)`）
    ③ 按**编号引用**把两边连起来 ⇒ 谁在守、谁没人守

## 为什么用「编号引用」而不是人工映射表

人工映射表有 51 行，**会漂**（改判据名时没人回去改表）。而判据标签里
本来就写着 `§K2` / `B17` —— 扫一遍就是索引，且**改名字会自动跟着变**。

⚠️ 它的**边界**（必须说清）：它判的是「**有没有判据提到这条承诺**」，
**不是**「这条判据判得对」。后者只有反向对照能回答。

## 用法

    python -m ldv.tools.guarantee_matrix           # 打表（静态）
    python -m ldv.tools.guarantee_matrix --check    # 与冻结基线比（新增/已修缺口都红）
    python -m ldv.tools.guarantee_matrix --write    # 重写基线（人确认后才用）
    python -m ldv.tools.guarantee_matrix --run      # 跑一遍判据，把状态贴上来（慢）

退出码：`--check` 有缺口变动 ⇒ 1；否则 0。
"""
from __future__ import annotations

import ast
import io
import json
import re
import sys
from pathlib import Path

from ._paths import REPO

#: 冻结基线（缺口清单）—— **两头都守**：新增缺口红，缺口被修好而不更新基线也红
BASELINE = REPO / "evidence" / "guarantee_gaps.json"

_SPEC_DOC = REPO / "docs" / "形式化断言集.md"
_DESIGN_DOC = REPO / "docs" / "分层方向视图-设计文档.md"


def commitments() -> list[str]:
    """承诺清单 —— 从**两处声明**抽，不从正文猜。

    ① `docs/形式化断言集.md` 第 1 行的范围声明（`§K1–§K9 · T1 · …`）
    ② `docs/分层方向视图-设计文档.md` 里 `**Bxx**` 的表格项

    ⚠️ 为什么不扫 `### §K1` 标题：§T1/§T2 没有这种标题（它们写在 §2 里），
       扫标题会**静默漏掉**两条 —— 而漏掉的那两条正好是「传输层」的承重墙。
    """
    head = io.open(_SPEC_DOC, encoding="utf-8").readline()
    out: list[str] = []
    for pre, lo, hi in re.findall(r"§([KATDS])(\d+)–§?\1(\d+)", head):
        out += [f"{pre}{i}" for i in range(int(lo), int(hi) + 1)]
    out += re.findall(r"(?<![A-Za-z])T\d+(?!\d)", head)
    des = io.open(_DESIGN_DOC, encoding="utf-8").read()
    b_ids = {m for m in re.findall(r"\*\*(B\d+)\*\*", des)}
    out += sorted(b_ids, key=lambda x: int(x[1:]))
    # 去重保序
    seen: set[str] = set()
    return [x for x in out if not (x in seen or seen.add(x))]


def judgments() -> list[str]:
    """判据清单 —— 两处来源，**都是判据自己的名字**。

    ① `ldv/tests/run_tests.py` 里 `ok("…")` 的第一个参数（判据标签）
    ② `ldv/checks/*.py` 里 `rep.add("Bxx", "标题")` 的编号 + 标题

    ⚠️ 只扫 `ok()` 的第一个参数，不扫 docstring —— docstring 里到处引用 §K8，
       那不是「这条判据在守 §K8」，只是「它讨论过 §K8」。

    ★★ **两种写法会漏，必须显式报出来**（`unresolved`）：
       - 标题是 **f-string**（`rep.add("B4", f"劈开成划分（{path}）")`）——
         实测 B4 就是这么写的，第一版正则漏了它 ⇒ 被误报成「零判据」
       - 编号是**变量**（`rep.add(code, …)`）—— 静态拿不到值
    """
    out: list[str] = []
    tree = ast.parse(io.open(REPO / "ldv" / "tests" / "run_tests.py",
                             encoding="utf-8").read())
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "ok" and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            out.append(node.args[0].value)
    for f in sorted((REPO / "ldv" / "checks").glob("*.py")):
        src = io.open(f, encoding="utf-8").read()
        # ⚠️ `f?` 必须留着：标题可能是 f-string
        for code, title in re.findall(
                r'rep\.add\(\s*"([A-Z]+\d+)"\s*,\s*f?"([^"]*)"', src):
            out.append(f"[{code}] {title}")
    return out


def unresolved() -> list[str]:
    """**抓不到的注册写法** —— 静态拿不到编号的那些。

    ⚠️ 不静默：它们可能正是「零判据」里的某几条。工具报出来，人去看。

    目前两类：

        `rep.add(code, …)`   编号是变量，静态拿不到值
        `ok(f"…")`           标签是 **f-string** ⇒ 里面的编号静态看不见
                             （实测：6 条合成对照第一版就是这么写的，
                              全部没进覆盖率读数 —— 第二次栽在同一个坑）
    """
    bad: list[str] = []
    for f in sorted((REPO / "ldv" / "checks").glob("*.py")):
        src = io.open(f, encoding="utf-8").read()
        for arg in re.findall(r"rep\.add\(\s*([^,]+),", src):
            if not re.match(r'^"[A-Z]+\d+"$', arg.strip()):
                bad.append(f"{f.name}: rep.add({arg.strip()[:20]}, …)")
    tree = ast.parse(io.open(REPO / "ldv" / "tests" / "run_tests.py",
                             encoding="utf-8").read())
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "ok" and node.args
                and isinstance(node.args[0], ast.JoinedStr)):
            bad.append("run_tests.py: ok(f\"…\") 动态标签")
    return bad


def coverage(labels: list[str] | None = None) -> dict[str, list[str]]:
    """承诺 ⇒ 提到它的判据标签（**只按编号引用**，不做语义匹配）。"""
    labels = judgments() if labels is None else labels
    cov: dict[str, list[str]] = {}
    for cid in commitments():
        pat = re.compile(r"(?<![A-Za-z0-9])§?" + re.escape(cid) + r"(?![0-9])")
        cov[cid] = [s for s in labels if pat.search(s)]
    return cov


def gaps(cov: dict[str, list[str]] | None = None) -> list[str]:
    cov = coverage() if cov is None else cov
    return [c for c, hits in cov.items() if not hits]


#: 「这条判据**自称**带对照」的标记 —— 只认这三种写法
#: （「对照」单独太宽：56 条里有一半是「对照实现」「对照读」这类别的意思）
CONTROL_MARKS = ("反向对照", "合成对照", "已知答案")


def controls(cov: dict[str, list[str]] | None = None) -> dict[str, list[str]]:
    """承诺 ⇒ **自称带对照**的判据标签（**读数，不进退出码**）。

    ★★ 为什么只是读数：它读的是**标签里有没有写「对照」**，
       **不是**「那条对照真的会红」。后者只有真去回退源码才知道。
       ⇒ 它可以指出「哪 40 条只有正着判」，**不能**证明「这 11 条反着判就对了」。

    ⚠️ 它**低估**：一条判据带了对照却没在标签里写，这里看不见 ——
       和「判了但没写编号」是同一个毛病，修法也一样（把话说进标签）。
    """
    cov = coverage() if cov is None else cov
    return {cid: [s for s in hits if any(m in s for m in CONTROL_MARKS)]
            for cid, hits in cov.items()}


def table(cov: dict[str, list[str]], status: dict[str, str] | None = None
          ) -> str:
    status = status or {}
    rows = ["| 承诺 | 判据数 | 状态 | 判据（前两条） |", "|---|---|---|---|"]
    for cid, hits in cov.items():
        st = status.get(cid) or ("**零判据**" if not hits else "有判据")
        sample = "；".join(h[:34] for h in hits[:2]) or "—"
        rows.append(f"| `{cid}` | {len(hits)} | {st} | {sample} |")
    n_gap = sum(1 for h in cov.values() if not h)
    rows.append("")
    rows.append(f"合计 **{len(cov)}** 条承诺｜有判据 **{len(cov) - n_gap}**"
                f"｜**零判据 {n_gap}**")
    return "\n".join(rows)


def b_without_control(ctl: dict[str, list[str]] | None = None) -> list[str]:
    """**还没有合成对照的 `B` 判据** —— 这就是「下一步该造哪条反例」的清单。

    ★ 为什么只盯 `B`：`§K/§A/§D/§S` 这些承诺在实现上由 `B` 检查守
      （断言集的「系统落点」一栏写的就是 `Bxx`）⇒ **给 B 造出对照，
      § 那一层就跟着有了**。反过来不成立：只给 § 造，B 仍然没人验。

    ★★ **判定看符号，不看标签**（2026-10-23 修）：第一版只看标签里的
       「合成对照 / 已知答案」，于是把 **B18 误报成「没有对照」** ——
       它的对照函数 `b18_known_answer` 明明在 `coverage.py` 里，
       只是 `run_tests` 那一条的标签没写这几个字。
       ⇒ 「标签没写」和「对照不存在」是**两件事**，这里必须查**函数在不在**。

    ⚠️ 这不等于「剩下的都造得出」：`B8` 要 `make_kernel`、
       `B19` 要一份**连续没变细**的 profile —— 能造，但成本不同。
    """
    ctl = controls() if ctl is None else ctl
    have = {c for c, v in ctl.items() if v}
    # 符号层证据：`def bN_known_answer(...)` 出现在任何检查文件里
    for f in sorted((REPO / "ldv").rglob("*.py")):
        for m in re.finditer(r"^def (b\d+)_known_answer\b",
                             io.open(f, encoding="utf-8").read(), re.M):
            have.add(m.group(1).upper())
    return [f"B{i}" for i in range(1, 21) if f"B{i}" not in have]


#: 还没对照的 `B` ⇒ **怎么造反例 / 卡在哪**（人判，2026-10-23）。
#:
#: ★ 结论先说：**这 10 条都造得出**，只是成本不同 —— 没有一条是「天生造不出」。
#:   （我原以为「有些断言的反例等于改实现」，逐条看下来这个判断不成立：
#:    它们的输入都是**可构造的假插件 / 假 profile / 假 make_kernel**。）
#:
#: ⚠️ 这张表是**人判**，不是读数 —— 判据只守「表里覆盖了所有缺对照的 B」，
#:    不守「你判得对」。
B_CONTROL_PLAN = {
    "B2": ("已造", "假插件 `merge` 丢键 ⇒ B2 红（`_controls.b2_known_answer`）"),
    "B3": ("已造", "假插件 `penalty` 抛异常 ⇒ B3 红"),
    "B5": ("已造", "假插件 `decode` 丢键 ⇒ B5 红"),
    "B16": ("已造", "覆盖少给一个成员 ⇒ B16 红"),
    "B19": ("已造", "手造 `prof`：连续 10 次没变细 ⇒ B19 红"),
    "B6": ("可造·中", "要**真内核**：它探 `kernel.record_usage` + 插件同一性"),
    "B8": ("可造·难", "要一个**行为像内核**的假 `make_kernel`（逐项插入 + 变动集）"),
    "B10": ("可造·中", "要**真内核**：它探 `kernel.query` 的第三态与渲染"),
    "B11": ("可造·中", "要**真内核**：它探 `record_usage` 的倾向 + `show` 的位次"),
    "B15": ("可造·难", "要一个**插入顺序敏感**的假 `make_kernel`"),
}


def load_baseline() -> list[str]:
    if not BASELINE.exists():
        return []
    return json.loads(io.open(BASELINE, encoding="utf-8").read()).get("零判据", [])


def write_baseline(g: list[str]) -> None:
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    io.open(BASELINE, "w", encoding="utf-8").write(
        json.dumps({"零判据": g,
                    "_note": "冻结的缺口清单。新增缺口 ⇒ 红；缺口被修好却没更新这里 ⇒ 也红。"},
                   ensure_ascii=False, indent=2) + "\n")


def run_status() -> dict[str, str]:
    """跑一遍 `run_tests`，把每条判据的**真实状态**抓回来（`过` / `红` / `跳过`）。

    ⚠️ 跳过在 `run_tests` 里是**记成 PASS 的**（只是文案里带「跳过」）——
       所以必须看文案，不能只看 PASS/FAIL。这正是「跳过 ≠ 通过」那一节的落地。
    """
    import contextlib
    import os

    sys.path.insert(0, str(REPO))
    os.chdir(REPO)                      # 判据依赖 cwd = repo
    from ldv.tests import run_tests as R

    with contextlib.redirect_stdout(io.StringIO()):
        R.main()
    status: dict[str, str] = {}
    for s in R.PASS:
        status[s] = "跳过" if "跳过" in s else "过"
    for s in R.FAIL:
        status[s] = "红"
    return status


DOC = REPO / "docs" / "保证矩阵.md"


def render(cov: dict[str, list[str]] | None = None) -> str:
    """整份 `docs/保证矩阵.md` 的内容（**生成器**，不手写）。

    ⚠️ 不写日期/提交号 —— 那会让每次重生成都变，核验就变成噪声。
    """
    cov = coverage() if cov is None else cov
    g = gaps(cov)
    head = [
        "# 保证矩阵（生成物 · 勿手改）",
        "",
        "> 由 `python -m ldv.tools.guarantee_matrix --write-doc` 生成。",
        "> 判据 `[保证] ④` 会比对「生成出来的」与「磁盘上的」—— **手改会让它红**。",
        "",
        "## 它回答什么",
        "",
        "每条**承诺**（断言集的 §K/§T/§A/§D/§S + 验收的 B1–B20）分别有哪些**判据**在守它。",
        "",
        "    ✓ 有判据   判据标签里**提到了这个编号**",
        "    ✗ 零判据   没有任何判据提到它 —— 见文末「零判据」一节",
        "",
        "## 它**不**回答什么（边界，必须说清）",
        "",
        "它判的是「**有没有判据提到这条承诺**」，**不是**「提到的那条判得对」。",
        "后者只有**反向对照**（把真源码回退、看判据是否红）能回答。",
        "",
        "⚠️ 反过来也不成立：**判了但标签里没写编号 ⇒ 读者查不到 ⇒ 在这里等价于没判**。",
        "这不是工具的毛病，是标签的毛病 —— 要让人查得到，就把编号写进标签。",
        "",
        "## 矩阵",
        "",
        table(cov),
        "",
        "## 零判据（已知缺口，冻结在 `evidence/guarantee_gaps.json`）",
        "",
    ]
    if g:
        head += [f"- `{c}` —— 没有任何判据标签提到它" for c in g]
        head += ["",
                 "⚠️ 这几条**可能是真缺口**，也可能是「判了但标签没写编号」。",
                 "两种情况都要人看一遍：前者要补判据，后者要补标签 —— **两种都不许留在暗处**。"]
    else:
        head.append("（无）")

    ctl = controls(cov)
    n_with = sum(1 for v in ctl.values() if v)
    head += [
        "",
        "## 反向对照覆盖（**读数**，不进退出码）",
        "",
        "「有判据」只说明**正着判过**。要说明判据**会红**，得有一条**反着判**的对照",
        "（回退真源码 / 塞一个已知答案 / 人造一个坏输入）。",
        "",
        f"合计 **{len(cov)}** 条承诺｜**至少一条判据自称带对照 {n_with}**"
        f"｜**只有正着判 {len(cov) - n_with}**",
        "",
        "| 承诺 | 自称带对照的判据 |",
        "|---|---|",
    ]
    for cid, hits in ctl.items():
        head.append(f"| `{cid}` | "
                    f"{'；'.join(h[:30] for h in hits[:2]) if hits else '—'} |")
    head += [
        "",
        "⚠️ **这是声明层的读数**：它读的是「标签里写没写『对照』」，",
        "**不是**「那条对照真的会红」。后者只有真去回退源码才知道 ——",
        "本仓库做过这件事的有 `[动机]` ⑫′/⑬、`[反思]` ㉔、服务壳 ⑯–⑲、`[增量]` ①′、`[D7]`。",
        "",
        "⚠️ 它也**低估**：带了对照却没在标签里写的，这里看不见。",
        "和「判了但没写编号」是同一个毛病 —— **把话说进标签**。",
        "",
        f"⚠️ **低估的量化**：静态索引抓不到的注册写法 **{len(unresolved())}** 条",
        "（`rep.add(code, …)` 变量编号 + `ok(f\"…\")` 动态标签）—— 这些判据的编号",
        "在矩阵里**完全看不见**，所以上面两个数都只会**偏小**。",
        "",
        "### 下一步该造哪条反例",
        "",
        "`§K/§A/§D/§S` 这些承诺在实现上由 `B` 检查守（断言集的「系统落点」一栏写的",
        "就是 `Bxx`）⇒ **给 `B` 造出对照，`§` 那一层就跟着有了**；反过来不成立。",
        "",
        "还没有对照的 `B`：",
        "",
    ]
    _noctl = b_without_control(ctl)
    head += ([f"- `{c}`" for c in _noctl] or ["（无）"])
    head += [
        "",
        f"⇒ **{len(_noctl)} / 20** 条还缺。**逐条看下来没有一条是「天生造不出」**，"
        "只是成本不同：",
        "",
        "| B | 能不能造 | 怎么造 / 卡在哪 |",
        "|---|---|---|",
    ]
    for c in _noctl:
        how, why = B_CONTROL_PLAN.get(c, ("未评估", "**表里没有它 —— 补上**"))
        head.append(f"| `{c}` | {how} | {why} |")
    head += [
        "",
        "⚠️ 这张表是**人判**，不是读数。判据只守「表里覆盖了所有缺对照的 `B`」，",
        "不守「判得对」—— 真正的检验是把它造出来、看它红。",
    ]
    return "\n".join(head) + "\n"


def main(argv: list[str]) -> int:
    cov = coverage()
    g = gaps(cov)
    if "--write-doc" in argv:
        DOC.parent.mkdir(parents=True, exist_ok=True)
        io.open(DOC, "w", encoding="utf-8", newline="").write(render(cov))
        print(f"已写 {DOC.name}（承诺 {len(cov)} 条｜零判据 {len(g)}）")
        return 0

    if "--write" in argv:
        write_baseline(g)
        print(f"基线已写：{len(g)} 条零判据 → {BASELINE.name}")
        return 0
    if "--run" in argv:
        st = run_status()
        # 把每条判据的状态归到承诺上：只要有一条判据是「过」就算「判过」
        per: dict[str, str] = {}
        for cid, hits in cov.items():
            states = [st.get(h, "未跑") for h in hits]
            per[cid] = ("**零判据**" if not hits
                        else "过" if "过" in states
                        else "红" if "红" in states
                        else "跳过")
        print(table(cov, per))
        return 0
    if "--check" in argv:
        base = set(load_baseline())
        now = set(g)
        added = sorted(now - base)
        fixed = sorted(base - now)
        print(table(cov))
        print()
        if added:
            print(f"✗ **新增零判据 {len(added)} 条**：{added}")
        if fixed:
            print(f"✗ **缺口被修好但基线没更新 {len(fixed)} 条**：{fixed}"
                  f" —— 跑 `--write` 并说明")
        if not added and not fixed:
            print(f"✓ 与基线一致：零判据 {len(now)} 条（冻结）")
        return 1 if (added or fixed) else 0
    print(table(cov))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
