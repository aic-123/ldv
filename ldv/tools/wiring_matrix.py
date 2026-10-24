# -*- coding: utf-8 -*-
"""**接线矩阵** —— 每个能力「承接上游 · 传递下游」了没有。

## 它治的是什么（2026-10-24 用户定的验收标准）

原验收：**判据绿 = 功能可用**。
问题：`core/impact.py` 有 4 条判据、有专章文档、有留档探针，**产品路径一次都没调用** ——
判据全绿，功能等于不存在。`session.py` / `versioning.py` 同理。

新验收：**必须能承接上游、传递下游，才算成功。**

    上游  从产品入口（entry / serve / cli / flow / protocol / run_checks）
          到它有**可达路径**，且它**真的被调用**（不是只被 import）
          另外两种合法上游：`cli`（它自己是 `__main__` 入口，人来跑）
    下游  它的输出有人消费：
          `code`（可达模块拿它的返回值）、`file`（它落盘，产物被人读）、
          `ext`（仓外消费者，白名单声明）
    判据  有判据但两端不通 ⇒ **仍然不算成功**（这正是本条要治的）

## 三态（不是二元 —— 「半链路」必须单独报）

    全链路    上游 + 下游都通
    半链路    只有一端（比如「有 CLI 入口，但输出没人读」）
    未接线    两端都不通

## 用法

    python -m ldv.tools.wiring_matrix            # 打表
    python -m ldv.tools.wiring_matrix --check     # 与冻结基线比（新增/已修都红）
    python -m ldv.tools.wiring_matrix --write     # 重写基线（人确认后才用）

退出码：`--check` 与基线不符 ⇒ 1；否则 0。

⚠️ 边界：它判的是「**链路通不通**」，不是「链路对不对」。
   `impact` 接上了但用错参数，这里照样绿 —— 那是判据的事，不是接线的事。
"""
from __future__ import annotations

import ast
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "evidence" / "wiring_gaps.json"

#: 产品入口（从这些出发做可达性）
ENTRIES = ("entry", "serve", "ldv.cli", "ldv.flow", "ldv.protocol", "ldv.run_checks")

#: 不参与判定的目录（判据自己不算「消费者」）
SKIP_PREFIX = ("ldv.checks", "ldv.tests", "ldv.tools")

#: 白名单：两端不通但有正当理由的，写在这里（两头都守）
#: kind: ext = 仓外消费者 / cli = 人工上游 / never = 故意不接
WHITELIST: dict[str, dict[str, str]] = {
    "ldv.core.persist": {"kind": "ext",
                         "why": "落盘格式给仓外调用方用（ldv-mcp 的 undo 在用）"},
    "ldv.recognize": {"kind": "cli",
                      "why": "认识的持续维护要人写 spec（T2 已知做不了），只有 CLI 入口"},
}


def _modname(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()[:-3].replace("/", ".")


def load_sources() -> dict[str, str]:
    out: dict[str, str] = {}
    for p in list(ROOT.glob("*.py")) + list(ROOT.glob("ldv/**/*.py")):
        s = p.as_posix()
        if "_archive" in s or "/corpus/" in s:
            continue
        try:
            out[_modname(p)] = io.open(p, encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            continue
    return out


def imports_of(mod: str, src: str) -> list[tuple[str, str | None, str]]:
    """返回 [(目标模块, 导入的符号 or None, 本地名)]（含相对导入）。

    ⚠️ 两种写法**调用形状不同**，混了就会把中心模块判成未接线（第一版就是这么错的）：
        `import X as a`      ⇒ 调用长 `a.func(`
        `from X import F as b` ⇒ 调用长 `b(`（b 就是符号本身，不是模块）
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    pkg = mod.rsplit(".", 1)[0] if "." in mod else ""
    out: list[tuple[str, str | None, str]] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            base = n.module or ""
            if n.level:
                parts = pkg.split(".") if pkg else []
                up = n.level - 1
                if up:
                    parts = parts[:-up] if up <= len(parts) else []
                base = ".".join([p for p in parts + ([base] if base else []) if p])
            for a in n.names:
                # `from X import Y`：X 是模块，Y 可能是**符号**（大多数）或**子模块**。
                # ⇒ 两条候选都记：符号那条按 `Y(` 判，子模块那条按 `alias.func(` 判。
                if base:
                    out.append((base, a.name, a.asname or a.name))
                    out.append((f"{base}.{a.name}", None, a.asname or a.name))
                else:
                    out.append((a.name, None, a.asname or a.name))
        elif isinstance(n, ast.Import):
            for a in n.names:
                out.append((a.name, None, a.asname or a.name.split(".")[-1]))
    return out


def public_names(src: str) -> list[str]:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    return [n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.ClassDef))
            and not n.name.startswith("_")]


def writes_files(src: str) -> bool:
    return any(k in src for k in ("write_text(", "os.replace(", "open(", ".write("))


def code_only(src: str) -> str:
    """去掉注释与字符串，只留**代码 token**。

    ⚠️ 为什么要这一步：`(LockedKernel if locked else Kernel)(...)` 这种**条件表达式调用**
       里没有 `LockedKernel(` 这个子串 ⇒ 纯文本判会漏（第一版就把 locking 判成未接线，
       而 cli.py 第 140 行明明在用）。去掉注释/字符串再数出现次数，才不会被
       「只在 docstring 里提了一句」骗到。
    """
    import io as _io
    import tokenize

    out: list[str] = []
    try:
        for tok in tokenize.generate_tokens(_io.StringIO(src).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            if tok.type in (tokenize.NAME, tokenize.OP, tokenize.NUMBER):
                out.append(tok.string)
    except (tokenize.TokenError, IndentationError):
        return src
    return " ".join(out)


def build() -> dict[str, dict]:
    files = load_sources()
    # 可达性（从入口）
    reached: set[str] = set()
    stack = [e for e in ENTRIES if e in files]
    edges: dict[str, list[tuple[str, str | None, str]]] = {}
    while stack:
        cur = stack.pop()
        if cur in reached:
            continue
        reached.add(cur)
        imp = [t for t in imports_of(cur, files[cur]) if t[0] in files]
        edges[cur] = imp
        stack.extend(t[0] for t in imp)

    rows: dict[str, dict] = {}
    for mod, src in sorted(files.items()):
        if mod.startswith(SKIP_PREFIX) or mod.endswith("__init__"):
            continue
        if not mod.startswith("ldv."):
            continue
        names = public_names(src)
        # 上游：谁 import 了它，且**真的调用了**（按导入形状判，见 imports_of）
        callers = []
        for consumer, imp in edges.items():
            if consumer == mod:
                continue
            for target, symbol, alias in imp:
                if target != mod:
                    continue
                code = code_only(files[consumer])
                if symbol is None:                 # import X as a ⇒ a.func(
                    hit = any(f"{alias} . {n}" in code or f"{alias}.{n}" in code
                              for n in names)
                else:                              # from X import F ⇒ F( 或条件表达式里用 F
                    hit = code.count(alias) >= 1
                if hit:
                    callers.append(consumer)
                    break
        is_cli = "__main__" in src and "argparse" in src
        is_entry = mod in ENTRIES
        up = "code" if (callers or is_entry) else ("cli" if is_cli else "none")
        # 下游
        # 下游：代码消费 / 落盘产物 / **终端**（入口自己就是终点：CI 与 stdout）
        down = ("code" if callers else ("file" if writes_files(src)
                else ("sink" if is_entry else "none")))
        n_tests = 0
        rt = files.get("ldv.tests.run_tests", "")
        short = mod.rsplit(".", 1)[-1]
        if short in rt:
            n_tests = rt.count(short)
        wl = WHITELIST.get(mod)
        if wl and down == "none":
            down = wl["kind"]
        verdict = ("全链路" if up != "none" and down != "none"
                   else "半链路" if up != "none" or down != "none"
                   else "未接线")
        rows[mod] = {"up": up, "down": down, "tests": n_tests,
                     "callers": callers[:3], "verdict": verdict,
                     "whitelist": bool(wl)}
    return rows


def render(rows: dict[str, dict]) -> None:
    print(f"{'模块':<28}{'上游':<8}{'下游':<8}{'判据':>5}  {'结论':<8}备注")
    print("-" * 78)
    for mod, r in rows.items():
        note = ("白名单: " + WHITELIST[mod]["kind"]) if r["whitelist"] else ""
        if r["verdict"] != "全链路" and not note:
            note = "**验收不通过**"
        print(f"{mod:<28}{r['up']:<8}{r['down']:<8}{r['tests']:>5}  "
              f"{r['verdict']:<8}{note}")
    n = {v: sum(1 for r in rows.values() if r["verdict"] == v)
         for v in ("全链路", "半链路", "未接线")}
    print("-" * 78)
    print(f"全链路 {n['全链路']}｜半链路 {n['半链路']}｜未接线 {n['未接线']}"
          f"（共 {len(rows)} 个模块）")
    bad = [m for m, r in rows.items() if r["verdict"] != "全链路" and not r["whitelist"]]
    print(f"验收不通过：{bad if bad else '无'}")


def main(argv: list[str]) -> int:
    rows = build()
    if "--write" in argv:
        doc = {"未接线": sorted(m for m, r in rows.items() if r["verdict"] == "未接线"),
               "半链路": sorted(m for m, r in rows.items() if r["verdict"] == "半链路"),
               "_note": "冻结的接线缺口。新增缺口 ⇒ 红；缺口修好却没更新这里 ⇒ 也红。"}
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        io.open(BASELINE, "w", encoding="utf-8", newline="\n").write(
            json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
        print(f"已写基线：{BASELINE}")
        return 0
    if "--check" in argv:
        if not BASELINE.is_file():
            print(f"没有基线：{BASELINE}（先跑 --write）")
            return 1
        base = json.loads(io.open(BASELINE, encoding="utf-8").read())
        cur = {k: sorted(m for m, r in rows.items() if r["verdict"] == k)
               for k in ("未接线", "半链路")}
        ok = True
        for k in ("未接线", "半链路"):
            new = sorted(set(cur[k]) - set(base.get(k, [])))
            fixed = sorted(set(base.get(k, [])) - set(cur[k]))
            if new:
                ok = False
                print(f"✗ 新增{k}：{new}")
            if fixed:
                ok = False
                print(f"✗ {k}已修好但基线没更新：{fixed}（跑 --write 并说明）")
        print("✓ 接线基线与现状一致" if ok else "✗ 接线基线与现状不符")
        return 0 if ok else 1
    render(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
