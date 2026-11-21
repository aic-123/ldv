"""扫「只写不读」的局部变量 —— `B3` 里的 `table` 就是这一类（删三重循环后变死）。

## 为什么要扫

`table` 那处是这样被发现的：删掉读它的三重循环之后，它变成**只写不读**，
却还占着全量 **244 MB**。这类残留**不会报错、不会变红、也不会变慢得离谱** ——
它只是白占，而且下一轮读代码的人会以为「留着一定有用」。

⇒ 用一个**能报出清单**的扫描代替「靠读代码发现」。

判据：函数内 `x = ...` 或 `x[k] = ...`，而 `x` 在**同一个函数**里
从没出现在**读取位置**（Load 上下文），并且不是 `return` 值、不是 `global`/`nonlocal`、
不是 `del` 目标。名字以下划线开头的一律放过（那是刻意的丢弃）。

用法：
    python outputs/_scan_dead_stores.py            # 扫 ldv/ 全部
    python outputs/_scan_dead_stores.py ldv/checks # 只扫一个目录
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent / "repo"


class FnScan(ast.NodeVisitor):
    """收集一个函数体里的「写入的名字」与「读取的名字」。"""

    def __init__(self) -> None:
        self.written: dict[str, int] = {}      # 名字 -> 首次写入的行号
        self.read: set[str] = set()
        self.deleted: set[str] = set()
        self.returned: set[str] = set()
        self.declared: set[str] = set()        # `global x` / `nonlocal x`

    def visit_Global(self, node: ast.Global) -> None:
        self.declared.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.declared.update(node.names)

    # --- 不要下钻进**嵌套作用域**，但要记下它**读走**的名字 -----------------
    #: 嵌套函数/类/lambda 有自己的名字空间：
    #:   · 它内部 `x = …` 是**它自己的** x，不是外层的 ⇒ 不能记成外层的写；
    #:   · 但它**闭包捕获**外层变量 = 外层的一次**读取** ⇒ 必须记，否则
    #:     `d0 = k.root` 后接一个用到 `d0` 的内层函数，会被报成「只写不读」。
    #: ⇒ 记它的 Load 名，不记它的 Store 名。
    def _free_reads(self, node: ast.AST) -> None:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                self.read.add(sub.id)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._free_reads(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._free_reads(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._free_reads(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._free_reads(node)

    # --- 读取 ---------------------------------------------------------------
    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.read.add(node.id)
        self.generic_visit(node)

    # --- 写入 ---------------------------------------------------------------
    def visit_Assign(self, node: ast.Assign) -> None:
        self._record_targets(node.targets, node.lineno)
        self.visit(node.value)
        for t in node.targets:
            self.visit(t)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._record_targets([node.target], node.lineno)
        if node.value is not None:
            self.visit(node.value)
        self.visit(node.target)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        # `x += 1` 既是读也是写；`out[k] += 1` 里 `k` 的**下标表达式**是读取 ——
        # 漏掉 `self.visit(node.target)` 就会把 `for a in …: out[str(a.x)] += 1`
        # 里的 `a` 报成「只写不读」（真实踩过）。
        if isinstance(node.target, ast.Name):
            self.read.add(node.target.id)
        self.visit(node.value)
        self.visit(node.target)

    def visit_For(self, node: ast.For) -> None:
        self._record_targets([node.target], node.lineno)
        self.generic_visit(node)

    def visit_Delete(self, node: ast.Delete) -> None:
        for t in node.targets:
            if isinstance(t, ast.Name):
                self.deleted.add(t.id)
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:
        if isinstance(node.value, ast.Name):
            self.returned.add(node.value.id)
        self.generic_visit(node)

    def _record_targets(self, targets: list[ast.expr], lineno: int) -> None:
        for t in targets:
            for name in _target_names(t):
                self.written.setdefault(name, lineno)


def _target_names(t: ast.expr) -> list[str]:
    """`x = …` / `x[k] = …` / `x.a = …` / `a, b = …` 都取到**根名字**。"""
    if isinstance(t, ast.Name):
        return [t.id]
    if isinstance(t, ast.Subscript):
        return _target_names(t.value)
    if isinstance(t, ast.Attribute):
        return _target_names(t.value)          # `self.x = …` 取到 `self` —— 由调用方过滤
    if isinstance(t, (ast.Tuple, ast.List)):
        out: list[str] = []
        for e in t.elts:
            out += _target_names(e)
        return out
    if isinstance(t, ast.Starred):
        return _target_names(t.value)
    return []


def scan(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[str] = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        s = FnScan()
        for stmt in fn.body:
            s.visit(stmt)
        for name, lineno in s.written.items():
            if name in ("self", "cls") or name.startswith("_"):
                continue
            if name in s.declared:          # `global x` / `nonlocal x` —— 不是局部
                continue
            if name in s.read or name in s.deleted or name in s.returned:
                continue
            hits.append(f"{path.relative_to(REPO)}:{lineno}: `{name}` 在 `{fn.name}()` 里只写不读")
    return hits


def main(argv: list[str]) -> int:
    roots = [REPO / a for a in argv[1:]] or [REPO / "ldv"]
    files: list[Path] = []
    for r in roots:
        files += sorted(r.rglob("*.py")) if r.is_dir() else [r]
    all_hits: list[str] = []
    for f in files:
        if "__pycache__" in f.parts:
            continue
        all_hits += scan(f)
    print(f"扫了 {len(files)} 个文件")
    if all_hits:
        print(f"\n只写不读的局部变量（{len(all_hits)} 处）：")
        for h in all_hits:
            print("  · " + h)
    else:
        print("没有发现只写不读的局部变量")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
