# -*- coding: utf-8 -*-
"""造一个带 `period` 字段的小样本，用来验「字段名 + 取值粒度」是否合适（2026-10-20）。

用法（cwd = repo）：

    python ../outputs/_make_period_sample.py 3      # 小学/中学/大学
    python ../outputs/_make_period_sample.py 6      # 六个学段

产出：`_data/_sample-period-<k>/nodes/*.md`（`_data/*` 已 gitignore，不进仓库）
"""
from __future__ import annotations

import io
import shutil
import sys
from pathlib import Path

SRC = Path("_data/snap-email-eu-mini/nodes")
DST_ROOT = Path("_data")

#: 三个学段（用户举的例子）；六个学段 = 再细分
PERIODS_3 = ["小学", "中学", "大学"]
PERIODS_6 = ["小学低年级", "小学高年级", "初中", "高中", "本科", "研究生"]


def add_field(text: str, key: str, val: str) -> str:
    """往 front matter 里插一行（在**闭合的 `---` 之前**）。"""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[:i] + [f"{key}: {val}"] + lines[i:])
    return text


def main() -> int:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    periods = PERIODS_3 if k == 3 else PERIODS_6
    dst = DST_ROOT / f"_sample-period-{k}" / "nodes"
    if dst.exists():
        shutil.rmtree(dst.parent)
    dst.mkdir(parents=True)

    files = sorted(SRC.glob("*.md"))
    if not files:
        print(f"找不到源语料 {SRC}")
        return 1
    # 按 id 顺序**均分**（每个学段大致同样多）—— 这样最容易看出粒度的影响
    for i, f in enumerate(files):
        val = periods[i * len(periods) // len(files)]
        text = io.open(f, encoding="utf-8").read()
        io.open(dst / f.name, "w", encoding="utf-8").write(add_field(text, "period", val))
    print(f"已造 {dst.parent}：{len(files)} 个节点，{k} 个学段 {periods}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
