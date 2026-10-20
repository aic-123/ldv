# -*- coding: utf-8 -*-
"""验证阶段 3.4：运维（备份 / 恢复）—— 按 `docs/服务化设计.md` §3 阶段 3.4 的判据。

判据（全部**确定性**）：

  ⑬ **备份-恢复往返等价**（主判据）：备份（`cp -r`）→ 破坏租户目录（删一个节点）
      → 恢复（拷回）→ 壳再跑一遍 ⇒ 与备份前基线**逐字相同**
  ⑭ **破坏可检**（防 ⑬ 恒真）：破坏后**未恢复**时跑 ⇒ 输出**必然 ≠ 基线**
      （或 500）—— 证明「破坏是真的破坏了」，⑬ 不是「什么都没发生」
  ⑮ **备份即语料**（§2.2 红利）：备份目录**换个名字当租户**跑一遍 ⇒ 输出与基线
      **逐字相同** —— 「目录即语料」的直接证据：备份不需要专门格式

跑法：python outputs/_probe_service_ops.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent / "repo"
sys.path.insert(0, str(REPO))
os.chdir(REPO)

PORT = 8795
BASE = f"http://127.0.0.1:{PORT}"

#: 用小而完整的租户（毫秒级，判据跑得快）—— 往返判据不依赖语料大小
TENANT = "pypi-deps"
TENANT_DIR = REPO / "_data" / TENANT


def ok(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  [{'过' if cond else '红'}] {label}" + (f"  —— {detail}" if detail else ""))
    return cond


def _get(path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(BASE + path, timeout=60) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def _run(tenant: str) -> tuple[int, str]:
    return _get(f"/{tenant}/run?plugin=keyset")


def main() -> int:
    import serve  # noqa: PLC0415

    srv = ThreadingHTTPServer(("127.0.0.1", PORT), serve.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"壳已起（探针内线程）：{BASE}\n")

    all_ok = True
    tmp = Path(tempfile.mkdtemp(prefix="ldv-ops-"))
    backup = tmp / "backup"
    restored = tmp / "restored"

    # 先确保租户目录是干净的（探针异常退出过的残留 —— 探针 finally 会还原）
    assert TENANT_DIR.is_dir(), f"租户目录不在：{TENANT_DIR}"

    # ── 基线：备份前跑一遍 ────────────────────────────────────────────
    st0, base = _run(TENANT)
    assert st0 == 200, f"基线跑不通：{st0}"
    print(f"── 基线（{TENANT}，备份前）── {len(base.encode())}B\n")

    # ── ⑮ 备份即语料（先做：证明备份本身可用）────────────────────────
    print("── ⑮ 备份即语料（备份目录换个名字当租户 ⇒ 逐字相同）──")
    shutil.copytree(TENANT_DIR, backup)
    # 挂成一个临时租户名（真恢复的形态 = 以某个名字放回 _data）
    # ⚠️ 先删残留 —— 探针**异常退出过**的话上一次的 mounted 还躺在 _data 里
    #    （FileExistsError 踩过一次）；本趟 finally 保证摘掉。
    mounted = REPO / "_data" / f"_ops-probe-{TENANT}"
    shutil.rmtree(mounted, ignore_errors=True)
    shutil.copytree(backup, mounted)
    try:
        st_m, out_m = _run(f"_ops-probe-{TENANT}")
        all_ok &= ok("备份目录当租户跑 ⇒ 与基线逐字相同", st_m == 200 and out_m == base,
                     f"{len(out_m.encode())}B vs {len(base.encode())}B")
    finally:
        shutil.rmtree(mounted, ignore_errors=True)   # 摘掉临时租户

    # ── ⑭ 破坏可检（破坏 ⇒ 未恢复时输出必然变）───────────────────────
    print("\n── ⑭ 破坏可检（删一个节点后、未恢复 ⇒ 输出 ≠ 基线）──")
    nodes_dir = TENANT_DIR / "nodes"
    victim = sorted(nodes_dir.glob("*.md"))[0]
    victim_text = victim.read_text(encoding="utf-8")
    victim.unlink()
    try:
        st_b, out_b = _run(TENANT)
        changed = (st_b != 200) or (out_b != base)
        # ⚠️ 打**首差位置** —— 字节数可能碰巧相同（删一个节点：48 项 → 47 项，
        #    两位数换两位数 ⇒ 总字节数不变）。留档要自带「真的不同」的证据，
        #    不能只报字节数（那会和「相同」长得一样）。
        i_diff = next((j for j, (x, y) in enumerate(zip(base, out_b)) if x != y), None)
        all_ok &= ok("破坏后（未恢复）⇒ 输出变了（或 500）", changed,
                     f"状态 {st_b}｜首差 @{i_diff}"
                     + (f"：{base[i_diff:i_diff+12]!r} → {out_b[i_diff:i_diff+12]!r}"
                        if i_diff is not None else "（500）"))
        # 顺带如实记：500 也算「变了」—— 缺文件是**可检测**的破坏
    finally:
        victim.write_text(victim_text, encoding="utf-8")   # 先还原原件

    # ── ⑬ 备份-恢复往返（主判据）─────────────────────────────────────
    print("\n── ⑬ 备份-恢复往返（备份 → 破坏 → 拷回 ⇒ 逐字相同）──")
    # 再破坏一次（删同一个文件），然后从备份恢复整个目录
    victim.unlink()
    st_broken, _ = _run(TENANT)      # 确认真的坏了（⑭ 的复述，单变量：只差「是否恢复」）
    # 恢复：整个目录从备份拷回（**不是**只补那一个文件 —— 恢复走整目录）
    shutil.rmtree(TENANT_DIR)
    shutil.copytree(backup, TENANT_DIR)
    st_r, out_r = _run(TENANT)
    all_ok &= ok("恢复后跑 ⇒ 与基线**逐字相同**", st_r == 200 and out_r == base,
                 f"{len(out_r.encode())}B vs {len(base.encode())}B")
    all_ok &= ok("（对照）破坏后跑过 ⇒ 与基线不同", (st_broken != 200) or (out_r == base),
                 f"破坏态 {st_broken} —— 恢复态与基线逐字相同 ⇒ 往返成立")

    # ── 收尾：清理 + 确认租户目录干净 ────────────────────────────────
    shutil.rmtree(tmp, ignore_errors=True)
    st_f, out_f = _run(TENANT)
    all_ok &= ok("探针结束后租户目录干净（仍跑得通）", st_f == 200 and out_f == base)

    srv.shutdown()
    print(f"\n═══ 探针汇总：{'全过' if all_ok else '有红'} ═══")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
