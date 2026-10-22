"""使用记录的落盘通道 —— 一条记录一文件，**只追加、不改、不覆写**。

⚠️ 与一次性树（`exchange_staging`）的区别，这条不能含糊：

    一次性树装的是 **消息**（外部内容），所以可以 `promote` 进 `nodes/`；
    使用记录是**系统对自己的记账**，**永不进 `nodes/`** ——
    一旦进去就是「使用记录驱动结构」，直接撞 `§K9`
    （可复现性与可解释性同时丢，而且是很久以后才发现）。

目录：`<语料>/usage/<qfp8>-<did>.md`，front matter 六个字段：

    id          记录 id（= 文件名去扩展名）
    did         方向 id
    outcome     +1 = 有用（被下钻）；**不记负信号**（没被下钻 ≠ 没用）
    propensity  曝光位次折算的倾向权重（内核填，不由本模块算）
    qfp         查询指纹（哪一次查询）
    note        人可读的一句话

**去重键 = `(qfp, did)`**：同一查询对同一方向只记一次 ——
重复下钻不叠加（叠加等于把「多看了一眼」当成「更有用」）。
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any

DIRNAME = "usage"
FIELDS = ("id", "did", "outcome", "propensity", "qfp", "note")


def _dir(corpus_dir: Any) -> Path:
    return Path(corpus_dir) / DIRNAME


def _qfp8(qfp: str) -> str:
    return hashlib.sha1(str(qfp).encode("utf-8")).hexdigest()[:8]


def _safe(s: str) -> str:
    """文件名里只留安全字符（did 形如 D0 / K=a|b，竖线要换掉）。"""
    return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in str(s))


def append(corpus_dir: Any, *, did: str, outcome: float, propensity: float,
           qfp: str, note: str = "") -> dict:
    """追加一条记录。**同 `(qfp, did)` 已存在 ⇒ 不写、不覆写、不报错。**"""
    d = _dir(corpus_dir)
    d.mkdir(parents=True, exist_ok=True)
    name = f"{_qfp8(qfp)}-{_safe(did)}.md"
    p = d / name
    if p.exists():
        return {"ok": True, "written": False, "path": str(p),
                "why": "已有同一 (qfp, did) 的记录 —— 只追加，不覆写"}
    body = (
        "---\n"
        f"id: {p.stem}\n"
        f"did: {did}\n"
        f"outcome: {float(outcome)}\n"
        f"propensity: {float(propensity)}\n"
        f"qfp: {qfp}\n"
        f"note: {note}\n"
        "---\n"
        f"一次使用记录：查询 {qfp} 展示了方向 {did}，随后它被下钻（outcome = 有用）。\n"
        "⚠️ 没被下钻的方向**不产记录** —— 「没看见」与「看见了不好」不是一回事。\n"
    )
    # ⚠️ 显式 newline="\n"：Windows 上默认会把 \n 写成 \r\n，而仓库规定 LF。
    io.open(p, "w", encoding="utf-8", newline="\n").write(body)
    return {"ok": True, "written": True, "path": str(p)}


def load(corpus_dir: Any) -> list[dict]:
    """读回全部记录（按文件名排序 ⇒ 确定性）。解析不了的**跳过并计数**，不静默。"""
    d = _dir(corpus_dir)
    out: list[dict] = []
    bad: list[str] = []
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.md")):
        try:
            t = io.open(p, encoding="utf-8", errors="replace").read()
            head = t.split("---", 2)[1]
            row = {}
            for line in head.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    row[k.strip()] = v.strip()
            if not (row.get("did") and row.get("outcome")):
                raise ValueError("缺 did 或 outcome")
            out.append({"did": row["did"], "outcome": float(row["outcome"]),
                        "propensity": float(row.get("propensity") or 1.0),
                        "note": row.get("note", ""), "qfp": row.get("qfp", ""),
                        "file": p.name})
        except Exception:                                    # noqa: BLE001
            bad.append(p.name)
    if bad:
        print(f"  ⚠️ usage 里有 {len(bad)} 个文件解析不了（已跳过）：{bad[:3]}")
    return out


def clear(corpus_dir: Any) -> dict:
    """显式清空 —— 返回删了几个。**这是唯一的删除路径，不做自动淘汰。**"""
    d = _dir(corpus_dir)
    if not d.is_dir():
        return {"ok": True, "deleted": 0}
    n = 0
    for p in sorted(d.glob("*.md")):
        p.unlink()
        n += 1
    return {"ok": True, "deleted": n}


def status(corpus_dir: Any) -> dict:
    """只报不淘汰（沿用 `capacity_status` 的纪律）。"""
    rows = load(corpus_dir)
    per: dict[str, int] = {}
    for r in rows:
        per[r["did"]] = per.get(r["did"], 0) + 1
    return {"记录数": len(rows), "覆盖方向数": len(per),
            "目录": str(_dir(corpus_dir)),
            "最多的方向": sorted(per.items(), key=lambda kv: (-kv[1], kv[0]))[:5]}


def main(argv: list[str] | None = None) -> int:
    import sys

    a = list(sys.argv[1:] if argv is None else argv)
    if len(a) < 2:
        print("用法：python -m ldv.tools.usage_store <status|clear|list> <语料目录>")
        return 2
    cmd, corpus = a[0], a[1]
    if cmd == "status":
        print(json.dumps(status(corpus), ensure_ascii=False, indent=1))
    elif cmd == "clear":
        print(json.dumps(clear(corpus), ensure_ascii=False))
    elif cmd == "list":
        for r in load(corpus):
            print(f"  {r['file']:24s} {r['did']:10s} outcome={r['outcome']:+.1f} "
                  f"propensity={r['propensity']:.3f} {r['note']}")
    else:
        print(f"不认识的命令 {cmd!r}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
