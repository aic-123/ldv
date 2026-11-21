# -*- coding: utf-8 -*-
"""§D7 的实体证据：派生维度**可完全回退**（2026-10-21）。

形式化断言集 §D7 的陈述：派生维度以 sidecar（`_data/<语料>/derived/<名>.json`）
存在，入口启动时叠加成键；**删掉该文件后重建的结构，与从无此文件的语料重建的
结构逐字相同**。

本探针把这句话跑成实体（三步，都在 ext-wiki-vote 上，它带 influence.json）：

    ① 带 sidecar 建一次         ⇒ 记录 指纹 / 树签名 / derived 自报 / 键里有没有 influence=
    ② 把 sidecar **移开**再建一次 ⇒ derived 必须报空、influence= 键必须一个都没有
    ③ 移回来再建一次            ⇒ 与 ① **逐字相同**（指纹 + 全树签名）

⚠️ 移走/移回都在同一个目录里（同卷 ⇒ 原子改名）；finally 保证移回，并在结尾
    **验恢复**。判据自己下判 + 退出码：0 = 回退性成立；1 = 有红。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import entry as E  # noqa: E402

CORPUS = "ext-wiki-vote"
SIDE_NAME = "influence.json"


def tree_signature(app) -> list:
    """全树签名：每个方向 (did, rank, 父did, 成员集)。按 did 排序 ⇒ 可比。"""
    k = app.kernels["keyset"]
    rows = []
    for d in k.all_directions():
        par = d.parent                      # ⚠️ parent 存的是 **did 字符串**，不是对象
        rows.append((d.did, d.rank, par if isinstance(par, str) else None,
                     sorted(k.members_of(d))))
    return sorted(rows)


def build_once(tag: str):
    t0 = time.time()
    app = E.App(CORPUS)
    print(f"  [{tag}] {len(app.nodes)} 项｜{len(app.all_keys)} 键｜{app.tree}"
          f"｜fp {app.fingerprint()}｜建核 {app.boot_s:.1f}s")
    return app


def main() -> int:
    data_dir = REPO / "_data" / CORPUS
    side = data_dir / "derived" / SIDE_NAME
    print("=" * 74)
    print("§D7 派生维度可完全回退 —— 三步实测（ext-wiki-vote × influence）")
    print("=" * 74)
    if not side.is_file():
        print(f"✗ 找不到 sidecar：{side} —— 本探针以它在场为前提")
        return 1

    checks: list[tuple[str, bool, str]] = []

    def chk(name, ok, detail):
        checks.append((name, bool(ok), detail))
        print(f"  [{'过' if ok else '红'}] {name}\n        {detail}")

    # sidecar 是一份**外生声明**（外生声明标准 E：材料变 ⇒ 声明作废）：
    # 它必须说清自己是什么（what）、从哪来（derived_from）、谁命名（named_by）、
    # 并且**自称可逆**（reversible）—— 自称可逆这句话，正是本探针要验的那件事。
    doc = json.loads(side.read_text(encoding="utf-8"))
    meta = doc.get("_meta") or {}
    need = ("what", "derived_from", "named_by", "reversible")
    chk("sidecar 的 _meta 是份完整外生声明（what / 来源 / 命名 / 自称可逆）",
        all(meta.get(k) for k in need),
        f"_meta 键：{sorted(meta)}｜缺 {[k for k in need if not meta.get(k)] or '无'}")

    hidden = side.with_name(SIDE_NAME + ".hidden")
    try:
        a1 = build_once("① 带 sidecar")
        sig1 = tree_signature(a1)
        has_k1 = any(k.startswith("influence=") for k in a1.all_keys)
        side.rename(hidden)
        a2 = build_once("② sidecar 移开")
        sig2 = tree_signature(a2)
        has_k2 = any(k.startswith("influence=") for k in a2.all_keys)
    finally:
        if hidden.is_file() and not side.is_file():
            hidden.rename(side)
    restored = side.is_file() and not hidden.is_file()
    a3 = build_once("③ sidecar 移回")
    sig3 = tree_signature(a3)

    chk("② 移开后 derived 自报为空、influence= 键一个都不剩",
        a2.derived == {} and not has_k2 and a2.meta().get("derived") == {},
        f"derived={a2.derived}｜influence= 键 {has_k2}｜键数 "
        f"{len(a1.all_keys)} → {len(a2.all_keys)}")
    chk("① 带 sidecar 时 derived 如实自报、键里确实有 influence=",
        bool(a1.derived) and has_k1 and a1.meta().get("derived"),
        f"derived={a1.derived}｜influence= 键 {has_k1}")
    chk("③ 移回后与 ① 逐字相同（指纹 + 全树签名）—— 这就是「可完全回退」",
        restored and a3.fingerprint() == a1.fingerprint() and sig3 == sig1,
        f"恢复 {restored}｜指纹 {a1.fingerprint()} → {a3.fingerprint()}"
        f"｜树签名 {len(sig1)} 个方向逐字相同 {sig3 == sig1}")
    chk("② 与 ① 的树**不同**（sidecar 真的在起作用，不是两边都空转）",
        a2.fingerprint() != a1.fingerprint() and sig2 != sig1,
        f"指纹 {a1.fingerprint()} → {a2.fingerprint()}"
        f"｜方向数 {len(sig1)} → {len(sig2)}")

    n_ok = sum(1 for c in checks if c[1])
    print(f"\n合计 {len(checks)} 条｜过 {n_ok}｜红 {len(checks)-n_ok}")
    return 0 if n_ok == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
