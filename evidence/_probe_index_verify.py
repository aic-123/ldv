# -*- coding: utf-8 -*-
"""数据验证：语料索引（短期/长期两层缓冲）—— 多份真实语料上的加速与等价。

判据（每条都要能红）：
  ① **等价**：索引路径 vs 直读 ⇒ id / keys / fields **逐字相同**（body 为空，如实标注）
  ② **加速**：大语料上二次装载显著更快（读数，按语料报）
  ③ **失效**：改一个文件 ⇒ **只重读它**（`重读 == 1`），其余仍命中
  ④ **端到端**：CLI `--index` 输出与默认**逐字相同**（索引只影响速度，不影响结果）

跑法：python outputs/_probe_index_verify.py
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent / "repo"
sys.path.insert(0, str(REPO))
os.chdir(REPO)

PY = r"C:/Users/19253/.workbuddy-ai/binaries/python/versions/3.13.12/python.exe"

from ldv.corpus.index import load_nodes_indexed     # noqa: E402
from ldv.corpus.loader import load_nodes           # noqa: E402

#: 四份真实语料（小 → 大，跨领域）
CORPORA = [
    ("pypi-deps", 60, "软件依赖"),
    ("snap-email-eu-mini", 120, "邮件切片"),
    ("ext-coauth", 9875, "学术合作"),
    ("ext-enron", 36692, "企业邮件"),
]


def ok(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  [{'过' if cond else '红'}] {label}" + (f"  —— {detail}" if detail else ""))
    return cond


def main() -> int:
    all_ok = True
    rows = []
    tmp_root = Path(tempfile.mkdtemp(prefix="ldv-idx-verify-"))

    print("── ① 等价 + ② 加速（逐语料）──")
    print(f"{'语料':22s}{'节点':>7s}{'直读s':>8s}{'首次s':>8s}{'二次s':>8s}{'加速':>7s}"
          f"{'命中':>7s}{'重读':>5s}{'等价':>5s}")
    for name, n_expect, desc in CORPORA:
        src = REPO / "_data" / name / "nodes"
        t0 = time.time()
        direct = load_nodes(src)
        t_direct = time.time() - t0

        t0 = time.time()
        n1, s1 = load_nodes_indexed(src, tmp_root)
        t_first = time.time() - t0

        t0 = time.time()
        n2, s2 = load_nodes_indexed(src, tmp_root)
        t_second = time.time() - t0

        same = (set(direct) == set(n2)
                and all(direct[k].keys == n2[k].keys for k in direct)
                and all(direct[k].fields == n2[k].fields for k in direct))
        speed = t_direct / t_second if t_second > 0 else 0
        print(f"{name:22s}{len(direct):7d}{t_direct:8.2f}{t_first:8.2f}{t_second:8.2f}"
              f"{speed:6.1f}×{s2['命中']:7d}{s2['重读']:5d}{'✓' if same else '✗':>5s}")
        all_ok &= same
        rows.append({"语料": name, "节点": len(direct), "说明": desc,
                     "直读s": round(t_direct, 2), "首次s": round(t_first, 2),
                     "二次s": round(t_second, 3), "加速": round(speed, 1),
                     "命中": s2["命中"], "重读": s2["重读"], "等价": same})

    all_ok &= ok("① 四份语料全部：id / keys / fields 逐字相同",
                 all(r["等价"] for r in rows),
                 "｜".join(f"{r['语料']}:{'✓' if r['等价'] else '✗'}" for r in rows))

    # ── ③ 失效：改一个文件 ⇒ 只重读它 ────────────────────────────────
    print("\n── ③ 失效（改一个文件 ⇒ 只重读它）──")
    src = REPO / "_data" / "pypi-deps" / "nodes"
    victim = sorted(src.glob("*.md"))[0]
    orig = victim.read_text(encoding="utf-8")
    try:
        victim.write_text(orig + "\n<!-- index-verify -->\n", encoding="utf-8")
        os.utime(victim, None)
        _n3, s3 = load_nodes_indexed(src, tmp_root)
        all_ok &= ok("改一个文件后：重读 1、其余全命中", s3["重读"] == 1,
                     f"重读 {s3['重读']}｜命中 {s3['命中']}")
    finally:
        victim.write_text(orig, encoding="utf-8")
        os.utime(victim, None)
    # 还原后应回到全命中
    _n4, s4 = load_nodes_indexed(src, tmp_root)
    all_ok &= ok("还原后：重读 1（mtime 变了）、再下一次全命中", s4["重读"] <= 1,
                 f"重读 {s4['重读']}｜命中 {s4['命中']}")

    # ── ④ 端到端：CLI --index 与默认逐字相同 ─────────────────────────
    print("\n── ④ 端到端：CLI `--index` 输出 == 默认（逐字）──")
    env = dict(os.environ)
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
                "LDV_INDEX_DIR": str(tmp_root)})
    outs = {}
    for flag in ("默认", "--index"):
        cmd = [PY, "-m", "ldv.cli", "--plugin", "keyset"]
        if flag != "默认":
            cmd.append(flag)
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           cwd=str(REPO), env=env, timeout=300)
        outs[flag] = p.stdout
    # ⚠️ 头部**两行**都带参数标记（第 2 行有「｜**语料索引**」）——
    #    只跳第 1 行会拿标记本身当差异（踩过一次）。⇒ 从「═══ 方向」开始比。
    def _body(s: str) -> str:
        i = s.find("═══ 方向")
        return s[i:] if i >= 0 else s

    a = _body(outs["默认"])
    b = _body(outs["--index"])
    all_ok &= ok("`--index` 与默认：正文（从「═══ 方向」起）**逐字相同**",
                 a == b and len(a) > 100,
                 f"{len(a.encode())}B vs {len(b.encode())}B")

    shutil.rmtree(tmp_root, ignore_errors=True)
    print(f"\n═══ 汇总：{'全过' if all_ok else '有红'} ═══")
    with io.open(HERE / "_probe_index_verify.rows.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print("留档：outputs/_probe_index_verify.rows.json")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
