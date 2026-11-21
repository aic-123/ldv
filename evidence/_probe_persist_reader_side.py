# -*- coding: utf-8 -*-
"""**读者侧**会不会也被拒？—— 「原子替换 + 写者重试」只补了一半。

## 为什么问这个

`view_persist.commit` 已经配了**写者侧重试**（`PLACE_ATTEMPTS=50 × 2 ms`）。
但 `os.replace` 撞的不只是写者 —— **读者**在替换的那一瞬间去 `open()` 同一个路径，
Windows 上也会被拒（`_probe_win_replace_vs_read.py` 实测：读者 44812 次读里 **42 次**被拒）。

⇒ 那么 `view_persist.read()`（它没有重试）在生产上到底会不会抛？
   本探针量它 —— **用真进程、真 `commit` / 真 `read`**，不模拟。

## 跑法

    python outputs/_probe_persist_reader_side.py [写次数]

两个真子进程：
    写者  调 `view_persist.commit(target, body)` N 次（**带着已落地的写者重试**）
    读者  调 `view_persist.read(target)` 直到写者结束，**逐条记下异常类型**

⚠️ 读数口径：读者异常 **0 次** ⇒ 读者侧不用补；**> 0** ⇒ 那是一个**真缺口**。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REPO = ROOT / "repo"

WRITER = r'''
import sys
sys.path.insert(0, sys.argv[1])
from ldv.core import view_persist as vp
target, reps = sys.argv[2], int(sys.argv[3])
body = '{"格式": "' + vp.FORMAT + '", "哨兵": "' + "A" * 300000 + '"}'
errs = []
attempts = []
for _ in range(reps):
    try:
        _p, n = vp.commit(target, body)
        attempts.append(n)
    except Exception as e:
        errs.append(type(e).__name__ + ": " + str(e)[:70])
print("@@WRITER" + __import__("json").dumps(
    {"reps": reps, "errors": errs,
     "attempts_max": max(attempts) if attempts else 0,
     "attempts_gt1": sum(1 for a in attempts if a > 1)}))
'''

READER = r'''
import sys, json
sys.path.insert(0, sys.argv[1])
from ldv.core import view_persist as vp
target, deadline = sys.argv[2], float(sys.argv[3])
import time
n, errs = 0, []
t0 = time.time()
while time.time() - t0 < deadline:
    try:
        vp.read(target)          # ← 生产读者：**没有重试**
        n += 1
    except FileNotFoundError:
        pass
    except Exception as e:
        errs.append(type(e).__name__ + ": " + str(e)[:70])
print("@@READER" + json.dumps({"reads": n, "n_err": len(errs), "errors": errs[:5]}))
'''


def _pick(out: bytes, tag: str) -> dict:
    for line in out.decode("utf-8", "replace").splitlines():
        if line.startswith(tag):
            return json.loads(line[len(tag):])
    return {}


def main() -> int:
    reps = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    root = Path(tempfile.mkdtemp(prefix="ldv-readerside-"))
    target = root / "views.json"

    pw = subprocess.Popen([sys.executable, "-c", WRITER, str(REPO), str(target), str(reps)],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    pr = subprocess.Popen([sys.executable, "-c", READER, str(REPO), str(target), "12"],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    ow, ew = pw.communicate(timeout=180)
    orr, er = pr.communicate(timeout=180)

    w = _pick(ow, "@@WRITER")
    r = _pick(orr, "@@READER")
    print("平台：", sys.platform)
    print("写者：", json.dumps(w, ensure_ascii=False) if w else
          ("解析失败 —— " + (ew or ow).decode("utf-8", "replace")[:300]))
    print("读者：", json.dumps(r, ensure_ascii=False) if r else
          ("解析失败 —— " + (er or orr).decode("utf-8", "replace")[:300]))
    if w and r:
        print()
        print(f"★ 写者：{w['reps']} 次 commit，失败 {len(w['errors'])} 次；"
              f"重试次数 >1 的有 {w['attempts_gt1']} 次（max {w['attempts_max']}）")
        print(f"★ 读者：{r['reads']} 次 read，**异常 {r['n_err']} 次**"
              + (f" —— 例：{r['errors'][:2]}" if r["n_err"] else "（读者侧不用补）"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
