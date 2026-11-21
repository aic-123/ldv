# -*- coding: utf-8 -*-
"""**反向对照**：阶段 2 的**四条**机制，逐条从**真源码**里回退掉 ⇒ 对应判据**必须红**。

## 回退的是**真源码**，不是 monkeypatch 假实现

    A 原子块        `临时文件 + os.replace`            → `target.write_text(body)`（修前那一行）
    B 读者侧重试    `read()` 的 for/except             → 裸 `read_text`（读者侧没补）
    C 写者侧重试    `commit()` 的 for/except           → 单次 `os.replace`
    D 重试边界      `_transient` 判「只在 Windows」    → 恒 `True`

## 期望（每条**只**红它该红的那几条）

    A ⇒ ①红 ②红      B ⇒ ③红      C ⇒ ⑤红      D ⇒ ④红
    真源码 ⇒ 全绿；每趟跑完**字节级还原 + sha256 相符**

跑法：python outputs/_probe_persist_atomic_reverse.py [A|B|C|D ...]
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REPO = ROOT / "repo"
TARGET = REPO / "ldv" / "core" / "view_persist.py"

RUNNER = r'''
import sys
sys.path.insert(0, sys.argv[1])
import ldv.tests.run_tests as R
R.test_persist_atomic()
import json
print("@@RES" + json.dumps({"pass": len(R.PASS), "fail": len(R.FAIL),
                            "reds": [f[:26] for f in R.FAIL]}))
'''

# ── A 原子块 → 直接覆盖写（修前那一行） ────────────────────────────────────────
A_OLD = '''    attempt = 0
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())          # 先落盘，再改名 —— 顺序反了等于没做
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                os.replace(tmp, target)    # ★ 原子：同卷改名
                break
            except PermissionError as e:   # ← Windows：目标正被别的进程持着
                if not _transient(e) or attempt == RETRY_ATTEMPTS:
                    raise
                time.sleep(_pause(attempt))
    except BaseException:
        try:
            os.unlink(tmp)                 # 目标**没被动过**，这里只收拾临时文件
        except OSError:
            pass
        raise
    return target, attempt'''
A_NEW = '''    os.close(fd)
    target.write_text(body, encoding="utf-8")     # ← 回退：直接覆盖写（先截断）
    try:
        os.unlink(tmp)
    except OSError:
        pass
    return target, 1'''

# ── B 读者侧重试 → 裸 read_text ───────────────────────────────────────────────
B_OLD = '''    p = _P(path)
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            if not p.is_file():
                return {}
            return json.loads(p.read_text(encoding="utf-8"))
        except PermissionError as e:
            if not _transient(e) or attempt == RETRY_ATTEMPTS:
                raise
            time.sleep(_pause(attempt))
    raise AssertionError("unreachable")          # pragma: no cover'''
B_NEW = '''    p = _P(path)
    if not p.is_file():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))    # ← 回退：读者侧不重试'''

# ── C 写者侧重试 → 单次 os.replace ────────────────────────────────────────────
C_OLD = '''        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                os.replace(tmp, target)    # ★ 原子：同卷改名
                break
            except PermissionError as e:   # ← Windows：目标正被别的进程持着
                if not _transient(e) or attempt == RETRY_ATTEMPTS:
                    raise
                time.sleep(_pause(attempt))'''
C_NEW = '''        attempt = 1
        os.replace(tmp, target)            # ← 回退：单次，不重试'''

# ── D 重试边界 → 恒真 ─────────────────────────────────────────────────────────
D_OLD = '''    import os

    return os.name == "nt" and isinstance(e, PermissionError)'''
D_NEW = '''    return True                             # ← 回退：什么错都当瞬时'''

CASES = {
    "A": ("原子块 → 直接覆盖写", A_OLD, A_NEW, ["①", "②"]),
    "B": ("读者侧重试 → 裸 read_text", B_OLD, B_NEW, ["③"]),
    "C": ("写者侧重试 → 单次 os.replace", C_OLD, C_NEW, ["⑤"]),
    "D": ("重试边界 → 恒真", D_OLD, D_NEW, ["④"]),
}


def run_case(label: str) -> tuple[int, int, list[str]]:
    pr = subprocess.run([sys.executable, "-c", RUNNER, str(REPO)],
                        cwd=str(REPO), capture_output=True, timeout=600)
    out = (pr.stdout + pr.stderr).decode("utf-8", "replace")
    for line in out.splitlines():
        if line.startswith("@@RES"):
            import json
            d = json.loads(line[5:])
            print(f"── {label} ──")
            print(f"   过 {d['pass']}｜红 {d['fail']}｜红的是 {d['reds']}")
            return d["pass"], d["fail"], d["reds"]
    print(f"── {label} ──\n   ⚠️ 没解析到结果：{out[-500:]}")
    return -1, -1, []


def main() -> int:
    want = [a for a in sys.argv[1:] if a in CASES] or list(CASES)
    before = TARGET.read_bytes()
    digest = hashlib.sha256(before).hexdigest()
    nl = "\r\n" if b"\r\n" in before else "\n"
    print("=" * 78)
    print("反向对照：阶段 2 的四条机制，逐条从**真源码**里回退掉 ⇒ 对应判据必须红")
    print("=" * 78)
    print(f"改前 view_persist.py sha256 = {digest[:16]}…（{len(before)} 字节，行尾 {nl!r}）\n")

    ok_all = True
    for key in want:
        label, old_s, new_s, expect = CASES[key]
        a = old_s.replace("\n", nl).encode("utf-8")
        b = new_s.replace("\n", nl).encode("utf-8")
        assert before.count(a) == 1, f"{key}: 锚点命中 {before.count(a)} 次（必须恰好 1）"
        try:
            TARGET.write_bytes(before.replace(a, b))
            n_pass, n_fail, reds = run_case(f"{key} · {label}（期望红 {expect}）")
        finally:
            TARGET.write_bytes(before)
        # ⚠️ 判据名是「★★ [阶段2] ① …」，**从名字里取编号**，别取首字符
        import re
        got = sorted({m.group(1) for r in reds
                      if (m := re.search(r"\[阶段2\] (.)", r))})
        # ⚠️ 期望是「**至少**红这几条」：A 那种大回退会连带红别的（它同时拆了原子性
        #    与重试）⇒ 不许写成「恰好等于」——那会把**正确**的结果判成失败。
        good = n_fail > 0 and all(e in got for e in expect)
        ok_all &= good
        print(f"   ⇒ {'✓ 如期' if good else '✗ 不如期'}"
              f"（期望**至少**红 {expect}；实红 {got}，共 {n_fail} 条）\n")

    after = TARGET.read_bytes()
    same = hashlib.sha256(after).hexdigest() == digest
    print(f"还原后 sha256 = {hashlib.sha256(after).hexdigest()[:16]}… "
          f"{'✓ 字节级还原' if same else '✗ **还原失败！**'}\n")

    n_pass, n_fail, _ = run_case("真源码（期望全绿）")
    ok_all &= (n_fail == 0 and same)
    print("=" * 78)
    print("✓ 反向对照：**如期**（每条机制回退 ⇒ 只红它该红的那几条；真源码全绿）"
          if ok_all else "✗ 反向对照**不如期**")
    print("=" * 78)
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
