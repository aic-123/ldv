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
#: 缓冲区子目录 —— 查询先落这里，**不参与倾向**（`load` 不递归进来）
STAGE = "_staging"
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


# ── 缓冲区：查询先落这里，**不算有用** ──────────────────────────────────────
#
# ★★ 为什么要有缓冲：如果 `query` 直接记 outcome=+1，一次查询就把 16 支全记一遍
#    ⇒ 倾向被刷成「谁被摆出来谁有用」。而「被摆出来」只是**曝光**，不是**有用**。
#    所以：query 写「这次摆了哪些支」（一批一条，不算分），
#    drill 才 `promote` 成 outcome=+1（这一支真的被往下看了）。
#
# ⚠️ 缓冲**不参与倾向计算** —— `load()` 只 glob `usage/*.md`，不递归进 `_staging/`。
#    这是「装上但不算有用」的兑现点：判据 `[接入] ⑤` 守它。


def _stage_dir(corpus_dir: Any) -> Path:
    return _dir(corpus_dir) / STAGE


def stage(corpus_dir: Any, *, qfp: str, shown: list[str], note: str = "") -> dict:
    """把「这次展示了哪些支」写进缓冲区 —— **一次查询一批，同 qfp 覆盖**。

    ⚠️ 覆盖是**刻意**的：缓冲区存的是「这次查询摆了什么」这个**状态**，
       不是账本。同一次查询重跑 ⇒ 展示列表一样 ⇒ 覆盖成同一份。
    """
    d = _stage_dir(corpus_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{_qfp8(qfp)}.md"
    existed = p.exists()
    body = (
        "---\n"
        f"id: {p.stem}\n"
        f"qfp: {qfp}\n"
        f"shown: {','.join(str(x) for x in shown)}\n"
        f"n_shown: {len(shown)}\n"
        f"note: {note}\n"
        "---\n"
        "一次查询的**展示批次**：这次摆了哪些支。\n"
        "⚠️ 它还不是「使用记录」—— 被摆出来只是曝光，不是有用。\n"
        "只有被下钻的那一支才 promote 成 outcome = +1。\n"
        "⚠️ 缓冲不参与倾向计算（load 只读正式目录）。\n"
    )
    io.open(p, "w", encoding="utf-8", newline="\n").write(body)
    return {"ok": True, "path": str(p), "overwrote": existed, "n_shown": len(shown)}


def stage_load(corpus_dir: Any) -> list[dict]:
    """读缓冲区（一次查询一条）。解析不了的跳过，不静默。"""
    d = _stage_dir(corpus_dir)
    out: list[dict] = []
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.md")):
        try:
            t = io.open(p, encoding="utf-8", errors="replace").read()
            head = t.split("---", 2)[1]
            row: dict[str, str] = {}
            for line in head.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    row[k.strip()] = v.strip()
            out.append({"qfp": row.get("qfp", ""),
                        "shown": [x for x in (row.get("shown") or "").split(",") if x],
                        "file": p.name})
        except Exception:                                    # noqa: BLE001
            continue
    return out


def promote(corpus_dir: Any, *, qfp: str, did: str, propensity: float,
            note: str = "drill") -> dict:
    """缓冲 → 正式：把 `did` 标成 outcome = +1，并从这批的 `shown` 里移除。

    ★★ **不在缓冲里的 did 不许 promote** —— 否则就是把「翻到了」当成「用了」。
       这是与「不合成反馈」同一条线：信号必须来自真实动作。
    """
    sp = _stage_dir(corpus_dir) / f"{_qfp8(qfp)}.md"
    if not sp.exists():
        return {"ok": False, "written": False,
                "why": "缓冲区里没有这次查询的展示批次（先 query 再 drill）"}
    cur = next((r for r in stage_load(corpus_dir) if r["file"] == sp.name), None)
    if cur is None or did not in cur["shown"]:
        return {"ok": False, "written": False,
                "why": "这一支不在这次查询的展示里，不记"}
    out = append(corpus_dir, did=did, outcome=1.0, propensity=propensity,
                 qfp=qfp, note=note)
    rest = [x for x in cur["shown"] if x != did]
    if rest:
        stage(corpus_dir, qfp=qfp, shown=rest, note="（已 promote 一支，剩余）")
    else:
        sp.unlink()
    return {**out, "remaining": len(rest)}


def clear(corpus_dir: Any) -> dict:
    """显式清空 —— 返回删了几个（**正式 + 缓冲都清**，两笔分开报）。

    ★ 这是唯一的删除路径，不做自动淘汰。
    """
    d = _dir(corpus_dir)
    n = s = 0
    if d.is_dir():
        for p in sorted(d.glob("*.md")):
            p.unlink()
            n += 1
    sd = _stage_dir(corpus_dir)
    if sd.is_dir():
        for p in sorted(sd.glob("*.md")):
            p.unlink()
            s += 1
    return {"ok": True, "deleted": n, "staged": s}


def status(corpus_dir: Any) -> dict:
    """只报不淘汰（沿用 `capacity_status` 的纪律）。**正式与缓冲分开报。**"""
    rows = load(corpus_dir)
    staged = stage_load(corpus_dir)
    per: dict[str, int] = {}
    for r in rows:
        per[r["did"]] = per.get(r["did"], 0) + 1
    return {"记录数": len(rows), "覆盖方向数": len(per),
            "缓冲批次数": len(staged),
            "缓冲里待 promote 的支": sum(len(r["shown"]) for r in staged),
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
