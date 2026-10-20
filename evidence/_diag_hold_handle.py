# -*- coding: utf-8 -*-
"""**同进程持一个读句柄，会不会挡住 `os.replace`？**

如果会 —— 那就拿到一条**确定性**的「写者重试真的接上了」判据：
另一个线程在 50 ms 后释放句柄 ⇒ `commit` 必须**重试后成功**（尝试次数 > 1）。
比「两个真子进程抢窗口」强得多（后者是概率判据）。
"""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, r"C:/Users/19253/WorkBuddy AI/2026-10-05-14-16-35/repo")
from ldv.core import view_persist as vp  # noqa: E402

root = Path(tempfile.mkdtemp(prefix="ldv-holdhandle-"))
target = root / "views.json"
body = '{"格式": "' + vp.FORMAT + '", "哨兵": "' + "X" * 300000 + '"}'
target.write_text('{"格式": "' + vp.FORMAT + '", "哨兵": "old"}', encoding="utf-8")

print("平台：", sys.platform)

# ① 对照：没人持句柄
t0 = time.time()
_p, n = vp.commit(target, body)
print(f"① 没人持句柄        ⇒ 尝试 {n} 次，耗时 {time.time()-t0:.3f}s")

# ② 持句柄 50 ms 后释放
fh = open(target, "rb")
threading.Thread(target=lambda: (time.sleep(0.05), fh.close()), daemon=True).start()
t0 = time.time()
try:
    _p, n2 = vp.commit(target, body)
    print(f"② 同进程持句柄 50 ms ⇒ 尝试 {n2} 次，耗时 {time.time()-t0:.3f}s"
          f"　{'★ 重试接上了' if n2 > 1 else '⚠️ 没被挡住（尝试 1 次）'}")
except Exception as e:
    print(f"② 同进程持句柄 50 ms ⇒ 抛 {type(e).__name__}: {str(e)[:70]}")

# ③ 持句柄一直不放
fh2 = open(target, "rb")
t0 = time.time()
try:
    _p, n3 = vp.commit(target, body)
    print(f"③ 一直持句柄        ⇒ 尝试 {n3} 次（**不该成功**）")
except Exception as e:
    print(f"③ 一直持句柄        ⇒ 抛 {type(e).__name__}，耗时 {time.time()-t0:.3f}s"
          f"（= 重试预算用尽后如实抛 —— 这是**对**的行为）")
finally:
    fh2.close()
