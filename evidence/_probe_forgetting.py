"""遗忘机制：短期显性记忆（缓冲）+ 长期隐性记忆（稳定区）—— 端到端实测。

跑法（cwd = repo）：python evidence/_probe_forgetting.py

七条判据：
  F1 两层记忆各有名字，且各自的遗忘方式说得出来
  F2 短期遗忘 = 容量淘汰（写 cap+5 批 ⇒ 只剩 cap）
  F3 长期遗忘 = **降权**：同方向一老一新两条相反证据 ⇒ 倾向被新的拉过去
  F4 降权**不删**：算完衰减，记录条数一条不少
  F5 可回退：半衰期关掉 ⇒ 回到等权平均（逐字相同）
  F6 入口端到端：真语料，把一条记录改老 ⇒ 倾向下降，但 `ordered_by` 仍是 usage
  F7 ★★ 遗忘不改结论：答案集合 / 支数 / n_answer 与遗忘前逐字相同

⚠️ F6/F7 需要 `_data/ext-wiki-vote`；不在就**跳过**（不是过）。
⚠️ 会往 `_data/<语料>/usage/` 写文件；开头与结尾都 clear ⇒ 可重跑、不积垃圾。
"""
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import entry as E                                            # noqa: E402
from ldv.tools import usage_store as US                      # noqa: E402

CORPUS = "ext-wiki-vote"
REQ = ["influence=≥100"]
FAIL = 0
NOW = datetime(2026, 10, 22, 12, 0, 0)


def chk(tag: str, what: str, okr: bool, detail: str) -> None:
    global FAIL
    if not okr:
        FAIL += 1
    print(f"  [{'过' if okr else '红'}] {tag} {what}\n        {detail}")


def main() -> int:
    print("=" * 78)
    print("遗忘机制：短期显性记忆 + 长期隐性记忆")
    print("=" * 78)

    tmp = Path(tempfile.mkdtemp(prefix="ldv_forget_"))
    try:
        # ── F1 两层有名字 ────────────────────────────────────────────────
        print("\n① 两层记忆的名字（用户要求「给它们名字」）")
        US.stage(tmp, qfp="q1", shown=["D1", "D2"])
        US.append(tmp, did="D1", outcome=1.0, propensity=0.5, qfp="q1",
                  at=(NOW - timedelta(days=1)).isoformat())
        st = US.status(tmp)
        chk("F1", "两层各有名字，各自的遗忘方式说得出来",
            US.SHORT_TERM in st and US.LONG_TERM in st,
            f"{US.SHORT_TERM} → {st[US.SHORT_TERM]['遗忘方式']}｜"
            f"{US.LONG_TERM} → {st[US.LONG_TERM]['遗忘方式']}")

        # ── F2 短期遗忘 = 容量淘汰 ───────────────────────────────────────
        print("\n② 短期遗忘：容量淘汰（已有规则，这里只验它真的生效）")
        for i in range(US.STAGE_CAP + 5):
            US.stage(tmp, qfp=f"cap-{i:04d}", shown=["D1"])
        n_stage = len(US.stage_load(tmp))
        n_formal = len(US.load(tmp))
        chk("F2", "写 cap+5 批 ⇒ 缓冲只剩 cap，**正式记录一条不动**",
            n_stage == US.STAGE_CAP and n_formal == 1,
            f"缓冲 {n_stage} 批（上限 {US.STAGE_CAP}）｜正式 {n_formal} 条")

        # ── F3 长期遗忘 = 降权 ───────────────────────────────────────────
        print("\n③ 长期遗忘：降权（同方向一老一新两条相反证据）")
        tmp2 = Path(tempfile.mkdtemp(prefix="ldv_forget2_"))
        try:
            US.append(tmp2, did="D9", outcome=1.0, propensity=0.5, qfp="old",
                      at=(NOW - timedelta(days=90)).isoformat())
            US.append(tmp2, did="D9", outcome=-1.0, propensity=0.5, qfp="new",
                      at=(NOW - timedelta(days=1)).isoformat())
            aged = US.aged_tendency(tmp2, now=NOW)["D9"]
            flat = US.aged_tendency(tmp2, now=NOW, half_life_days=0)["D9"]
            w_old = US.decay_weight((NOW - timedelta(days=90)).isoformat(), now=NOW)
            w_new = US.decay_weight((NOW - timedelta(days=1)).isoformat(), now=NOW)
            chk("F3", "老的权重更小，倾向被**新的**那条拉过去",
                w_old < w_new < 1.0 and aged < 0 < flat + 1e-12,
                f"90 天前 w={w_old:.4f}｜1 天前 w={w_new:.4f}｜"
                f"衰减后 {aged:+.4f}（负 = 新证据占上风）｜关掉半衰期 {flat:+.4f}")

            # ── F4 不删 ─────────────────────────────────────────────────
            print("\n④ 降权不删：记录是账本，只追加")
            chk("F4", "算完衰减，记录条数一条不少",
                len(US.load(tmp2)) == 2,
                f"2 条 → {len(US.load(tmp2))} 条")

            # ── F5 可回退 ───────────────────────────────────────────────
            print("\n⑤ 可回退：半衰期关掉 ⇒ 回到等权")
            chk("F5", "半衰期 0 ⇒ 等权平均（与加衰减之前逐字相同）",
                abs(flat) < 1e-12,
                f"关掉 {flat:+.4f}｜开着 {aged:+.4f}")
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ── F6/F7 入口端到端（需要外部语料） ─────────────────────────────────
    print("\n⑥ 入口端到端：真语料，把一条记录改老")
    d = REPO / "_data" / CORPUS
    if not (d / "nodes").is_dir():
        print(f"  （**跳过**：{CORPUS} 不在 —— 外部语料不进仓库）")
        print("\n" + "=" * 78)
        print(f"  红 {FAIL} 条" if FAIL else "  全过（F6/F7 跳过）")
        return 1 if FAIL else 0

    US.clear(d)
    a0 = E.App(CORPUS)
    r0 = a0.query({"require": REQ})
    did0 = r0["nodes"][0]["did"]
    a0.drill(did0, REQ)
    # ⚠️ 直接算，不读 `a0._tendency` —— 那是**启动时**算的，会话内新写的记录
    #    不会立刻改变次序（这是「长期隐性记忆」的语义：巩固是跨会话的事）。
    t_fresh = US.aged_tendency(d)

    # 把这条记录的时间改成 300 天前 —— 相当于「很久以前用过」
    rows = US.load(d)
    if not rows:
        chk("F6", "drill 应当产出一条记录", False, "0 条 —— 记录通道没走通")
        US.clear(d)
        print("\n" + "=" * 78)
        print(f"  红 {FAIL} 条")
        return 1
    rec = rows[0]
    old_at = (datetime.now() - timedelta(days=300)).isoformat()
    p = d / "usage" / rec["file"]
    body = p.read_text(encoding="utf-8").replace(
        f"at: {rec['at']}", f"at: {old_at}")
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)

    t_old = US.aged_tendency(d)
    a1 = E.App(CORPUS)
    r1 = a1.query({"require": REQ})
    chk("F6", "记录改老 300 天 ⇒ 倾向下降，但记录还在（没被删）",
        t_old.get(did0, 0) < t_fresh.get(did0, 0) and did0 in t_old
        and len(US.load(d)) == 1,
        f"{did0}：新 {t_fresh.get(did0):.4f} → 旧 {t_old.get(did0):.4f}"
        f"｜记录 {len(US.load(d))} 条（没删）｜重建读回 {a1.usage_loaded} 条")

    def ans_of(r):
        return (tuple(r.get("answer_ids") or ()), r["n_answer"], r["n_branches"])

    chk("F7", "★★ 遗忘不改结论：答案集合 / 支数 / n_answer 逐字相同",
        ans_of(r0) == ans_of(r1),
        f"前 {ans_of(r0)[1]} 项 / {ans_of(r0)[2]} 支｜后 {ans_of(r1)[1]} 项 / {ans_of(r1)[2]} 支")

    US.clear(d)
    print("\n  收尾：usage 已清空")

    print("\n" + "=" * 78)
    print(f"  红 {FAIL} 条" if FAIL else "  全过")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
