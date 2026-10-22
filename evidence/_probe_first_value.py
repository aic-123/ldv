"""首值时间实测 —— 「从运行第一条命令到看到第一个非空答案」的秒数。

跑法（cwd = repo）：python evidence/_probe_first_value.py

★ 口径（明写，否则这个数没法比）：
    **不含 `git clone`**（网络时间不可控，且不是系统的问题）。
    含：进程启动 → `import entry` → `App()` 建树 → 第一次 `query()` 返回。
    不含：人读 README / 理解定位的时间 —— 那部分取决于文档质量，不是系统性能。

⚠️ 「5 分钟」是**采纳生死线**（`USER.md`）。这个探针只测**系统侧**那一小段；
   真正的 5 分钟里，大头是「读懂它在干什么」。所以下面同时报**命令条数**
   （用户要敲几条才有第一个答案）。
"""
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

T0 = time.perf_counter()

import entry as E                                            # noqa: E402

T_IMPORT = time.perf_counter() - T0

T1 = time.perf_counter()
app = E.App(None)                     # 内置 36 项：clone 之后就有
T_BOOT = time.perf_counter() - T1

T2 = time.perf_counter()
r = app.query({"require": []})        # 最宽的查询：全量
T_QUERY = time.perf_counter() - T2

T_TOTAL = time.perf_counter() - T0

print("=" * 74)
print("首值时间（系统侧）—— 从「运行第一条命令」到「看到第一个非空答案」")
print("=" * 74)
print(f"  import entry          {T_IMPORT * 1000:8.1f} ms")
print(f"  App(内置 36 项) 建树   {T_BOOT * 1000:8.1f} ms")
print(f"  第一次 query           {T_QUERY * 1000:8.1f} ms")
print(f"  ────────────────────────────────────")
print(f"  合计                   {T_TOTAL * 1000:8.1f} ms  ({T_TOTAL:.3f} s)")
print(f"  第一个答案：{r['n_answer']} 项 / {r['n_branches']} 支")
print()
print("  ⚠️ 不含 `git clone`（网络不可控），也不含「人读 README」的时间。")
print("     用户要敲的命令：**1 条**（`python entry.py` 或 `python -m ldv.cli --plan`）。")
print(f"  ⇒ 系统侧首值 {T_TOTAL:.3f}s，离「≤5 分钟」的生死线**余量极大**；")
print("     真正的风险在「读懂定位」那一段，不在性能。")
print()

# 反向对照：如果语料换成外部大语料，boot 会慢多少（有就跑，没有就说没跑）
ext = REPO / "_data" / "ext-wiki-vote" / "nodes"
if ext.is_dir():
    T3 = time.perf_counter()
    app2 = E.App("ext-wiki-vote")
    T_BOOT_EXT = time.perf_counter() - T3
    r2 = app2.query({"require": ["influence=≥100"]})
    print(f"  对照（ext-wiki-vote 7115 项）：建树 {T_BOOT_EXT:.2f}s｜"
          f"首查 {r2['n_answer']} 项 / {r2['n_branches']} 支")
    print("  ⚠️ 外部语料在 `_data/`（不进仓库）⇒ clone 之后**没有**这份，"
          "所以它是「本地对照」，不是「首值」。")
else:
    print("  对照（ext-wiki-vote）：**跳过** —— 外部语料不在。")
