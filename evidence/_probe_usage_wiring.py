"""接入验证：查询 → 缓冲区 → 下钻 promote → 正式记录 → 只改呈现次序。

跑法（cwd = repo）：python evidence/_probe_usage_wiring.py

十条判据各自下判、脚本给退出码：
  W1  默认态：无记录 ⇒ ordered_by = default
  W2  查询即入缓冲：缓冲 1 批，n_shown = 展示支数
  W3  ★★ 缓冲**不算有用**：usage_loaded = 0、正式 0 条、ordered_by 仍 default
  W4  下钻 promote：正式 +1，缓冲里移除该支
  W5  ★ 记录**不进 nodes**：项数 / 键数 / 树 / 指纹与写记录前**逐字相同**
  W6  重建 App ⇒ 读回记录（usage_loaded > 0），ordered_by = usage
  W7  ★★ 排序**不改答案集合**：answer_ids / n_answer / n_branches 前后逐字相同
  W8  只追加不叠加：同一 (qfp, did) 再写一次 ⇒ written = False
  W9  不在缓冲里的支不许 promote（翻到了 ≠ 用了）
  W10 clear 之后回到 default（正式 + 缓冲都清）

⚠️ 会往 _data/<语料>/usage/ 写文件（_data 不进仓库）。开头先 clear ⇒ 可重跑。
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import entry as E                                            # noqa: E402
from ldv.tools import usage_store                            # noqa: E402

CORPUS = "ext-wiki-vote"
REQ = ["influence=≥100"]
FAIL = 0


def chk(tag: str, what: str, okr: bool, detail: str) -> None:
    global FAIL
    if not okr:
        FAIL += 1
    print(f"  [{'过' if okr else '红'}] {tag} {what}\n        {detail}")


def shape(app) -> dict:
    return {"项数": len(app.nodes), "键数": len(app.all_keys),
            "树": dict(app.tree), "指纹": app.fingerprint()}


def ans_of(r: dict) -> tuple:
    return (tuple(r.get("answer_ids") or ()), r.get("n_answer"), r.get("n_branches"))


def main() -> int:
    d = REPO / "_data" / CORPUS
    ud = d / "usage"
    print("=" * 78)
    print(f"接入验证｜语料 {CORPUS}｜查询 {REQ}")
    print("=" * 78)

    print("\n① 从零开始（先清空正式 + 缓冲）")
    usage_store.clear(d)
    a0 = E.App(CORPUS)
    s0 = shape(a0)
    r0 = a0.query({"require": REQ})
    chk("W1", "默认态：无记录 ⇒ ordered_by = default",
        r0.get("ordered_by") == "default",
        f"ordered_by = {r0.get('ordered_by')}｜{r0['n_answer']} 项 / {r0['n_branches']} 支")

    print("\n② 查询之后：应该**进了缓冲**，但**还不算有用**")
    batches = usage_store.stage_load(d)
    n_shown = len(batches[0]["shown"]) if batches else 0
    chk("W2", "查询即入缓冲（1 批，n_shown = 展示支数）",
        len(batches) == 1 and n_shown == r0["n_branches"],
        f"缓冲 {len(batches)} 批｜n_shown = {n_shown}｜展示 {r0['n_branches']} 支")
    chk("W3", "★★ 缓冲不算有用：正式 0 条、usage_loaded = 0、ordered_by 仍 default",
        usage_store.load(d) == [] and a0.usage_loaded == 0
        and r0.get("ordered_by") == "default",
        f"正式 {len(usage_store.load(d))} 条｜usage_loaded {a0.usage_loaded}"
        f"｜ordered_by {r0.get('ordered_by')}")

    print("\n③ 下钻一支 —— 这才是「有用」，从缓冲 promote 出来")
    did0 = r0["nodes"][0]["did"]
    dr = a0.drill(did0, REQ)
    left = usage_store.stage_load(d)
    left_n = len(left[0]["shown"]) if left else 0
    chk("W4", "下钻 promote：正式 +1，缓冲里移除该支",
        dr.get("recorded") is True and len(usage_store.load(d)) == 1
        and all(did0 not in b["shown"] for b in left),
        f"recorded = {dr.get('recorded')}｜{dr.get('record_note')}"
        f"｜正式 {len(usage_store.load(d))} 条｜缓冲剩 {left_n} 支")

    print("\n④ 记录不许进 nodes（§K9：使用记录不驱动结构）")
    a1 = E.App(CORPUS)
    s1 = shape(a1)
    chk("W5", "项数 / 键数 / 树 / 指纹与写记录前**逐字相同**",
        s0 == s1,
        f"前 {s0['项数']}/{s0['键数']}/{s0['树']}｜后 {s1['项数']}/{s1['键数']}/{s1['树']}")

    print("\n⑤ 重建 App ⇒ 读回正式记录")
    r1 = a1.query({"require": REQ})
    chk("W6", "读回记录（usage_loaded > 0），ordered_by = usage",
        a1.usage_loaded > 0 and r1.get("ordered_by") == "usage",
        f"读回 {a1.usage_loaded} 条｜ordered_by = {r1.get('ordered_by')}"
        f"｜倾向覆盖 {len(a1._tendency)} 个方向")

    print("\n⑥ ★★ 排序不许改答案集合")
    chk("W7", "answer_ids / n_answer / n_branches 前后逐字相同",
        ans_of(r0) == ans_of(r1),
        f"前 {ans_of(r0)[1]} 项 / {ans_of(r0)[2]} 支｜后 {ans_of(r1)[1]} 项 / {ans_of(r1)[2]} 支"
        f"｜答案集相同 {ans_of(r0)[0] == ans_of(r1)[0]}")

    print("\n⑦ 只追加、不叠加")
    qfp = a1._last_qfp
    again = usage_store.append(d, did=did0, outcome=1.0, propensity=0.5,
                               qfp=qfp, note="drill")
    chk("W8", "同一 (qfp, did) 再写一次 ⇒ written = False",
        again.get("written") is False, f"{again.get('why')}")

    print("\n⑧ 不在缓冲里的支不许 promote")
    bad = usage_store.promote(d, qfp=qfp, did="D_NOT_THERE", propensity=0.5)
    chk("W9", "凭空 promote ⇒ written = False（翻到了 ≠ 用了）",
        bad.get("written") is False, f"{bad.get('why')}")

    print("\n⑨ 可回退：clear 之后回默认")
    out = a1.clear_usage()
    r2 = a1.query({"require": REQ})
    chk("W10", "clear 之后 ordered_by 回 default（正式 + 缓冲都清）",
        out.get("deleted", 0) >= 1 and r2.get("ordered_by") == "default",
        f"正式删 {out.get('deleted')} / 缓冲删 {out.get('staged')}"
        f"｜ordered_by = {r2.get('ordered_by')}")

    print("\n" + "=" * 78)
    print(f"  红 {FAIL} 条" if FAIL else "  全过")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
