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
    """**抓不到的注册写法** —— 静态拿不到编号的那些 `rep.add(...)`。

    ⚠️ 不静默：它们可能正是「零判据」里的某几条。工具报出来，人去看。
    """
    bad: list[str] = []
    for f in sorted((REPO / "ldv" / "checks").glob("*.py")):
        src = io.open(f, encoding="utf-8").read()
        for arg in re.findall(r"rep\.add\(\s*([^,]+),", src):
            if not re.match(r'^"[A-Z]+\d+"$', arg.strip()):
                bad.append(f"{f.name}: rep.add({arg.strip()[:20]}, …)")
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
