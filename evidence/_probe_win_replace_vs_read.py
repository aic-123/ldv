# -*- coding: utf-8 -*-
"""**Windows 上「读者持句柄」会不会撞坏 `os.replace`？** —— 动手前先实测。

## 为什么要问

阶段 2 的取法是「临时文件 + `os.replace`」。它在 POSIX 上的原子性是有保证的，
但 **Windows 的文件共享模型不同**：`MoveFileEx(MOVEFILE_REPLACE_EXISTING)`
要求对**目标**有删除权，而目标若正被另一个进程以**不含 `FILE_SHARE_DELETE`**
的方式打开，替换会**失败**（`PermissionError` / `WinError 5`）。

⇒ 如果真是这样，那么「两个进程并发落盘」这个场景在 Windows 上就不是
   「谁赢」的问题，而是「**写者直接抛**」的问题 —— 判据的形态要跟着变。

## 怎么做

两个真子进程：

    写者  反复 `os.replace(tmp, target)`（每次换一份新内容）
    读者  反复 `open(target).read()`（Python 的 `open` 走 CRT，共享模式由 CRT 定）

各自把自己遇到的异常**逐条记下来**（类型 + 前 80 字），跑 N 轮后汇总。

跑法：python outputs/_probe_win_replace_vs_read.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REPO = ROOT / "repo"

WRITER = r'''
import os, sys, json
target = sys.argv[1]
reps = int(sys.argv[2])
errs = []
for i in range(reps):
    tmp = target + ".tmp%d" % i
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write('{"n": %d, "pad": "%s"}' % (i, "X" * 200000))
    try:
        os.replace(tmp, target)
    except OSError as e:
        errs.append(type(e).__name__ + ": " + str(e)[:80])
        try:
            os.unlink(tmp)
        except OSError:
            pass
print(json.dumps({"reps": reps, "errors": errs}))
'''

READER = r'''
import sys, json, time
target = sys.argv[1]
deadline = time.time() + float(sys.argv[2])
n, bad = 0, []
while time.time() < deadline:
    try:
        with open(target, encoding="utf-8") as fh:
            blob = fh.read()
    except FileNotFoundError:
        continue
    except OSError as e:
        bad.append("open: " + type(e).__name__ + ": " + str(e)[:80])
        continue
    n += 1
    if not blob.endswith("}"):
        bad.append("truncated: %d bytes" % len(blob))
print(json.dumps({"reads": n, "bad": bad[:10], "n_bad": len(bad)}))
'''


def main() -> int:
    reps = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    root = Path(tempfile.mkdtemp(prefix="ldv-winreplace-"))
    target = root / "views.json"
    target.write_text('{"n": -1, "pad": ""}', encoding="utf-8")

    pw = subprocess.Popen([sys.executable, "-c", WRITER, str(target), str(reps)],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    pr = subprocess.Popen([sys.executable, "-c", READER, str(target), "6"],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out_w, err_w = pw.communicate(timeout=120)
    out_r, err_r = pr.communicate(timeout=120)

    print("平台：", sys.platform)
    print("写者：", out_w.decode("utf-8", "replace").strip() or err_w.decode("utf-8", "replace").strip()[:400])
    print("读者：", out_r.decode("utf-8", "replace").strip() or err_r.decode("utf-8", "replace").strip()[:400])
    print("临时目录：", root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
