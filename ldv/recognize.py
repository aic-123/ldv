"""认识的**持续维护**入口 —— 把抽象层与检索层之间那条线接上（`§7.4` / `§A8`）。

    跑法（cwd = repo）：
        python -m ldv.recognize                 # 维护到当前结构并落盘
        python -m ldv.recognize --dry-run       # 只印，不写
        python -m ldv.recognize keyset          # 只跑一条方向

---

## 它补的是哪一段（2026-10-10 之前**一个生产入口都没有**）

    `core/views.propagate`（`E′`）  生产路径 **0 次**（只在 `§A7` 判据里被调过）
    `core/view_persist`            生产路径 **0 次**（只在 `§A6` 判据里被调过）
    `views.json`                   **从来没人写过**（`run_checks` 是拿当前结构**现造**）

⇒ 检索层 `T1` 每次读到的都是 `None` ⇒ **现算** ⇒ `T2`（「指纹相符」）**只有一半在判**，
  而「抽象层持续影响检索层」这句话**没有实体**。

## 它做什么（四步，每步可复核）

    ① 取**当前**外生声明（`spec_paths`：语料旁优先、仓内兜底）
    ② 读**盘上旧的**认识（`view_persist.read`）—— 只当它是**读数**（见下）
    ③ **维护**（`core/views.maintain`）：交付**总是** `csr(spec)`
    ④ **落盘** + 印出「交付了什么、只传播会是什么、两者同不同构、为什么」

## ★★ 为什么第 ③ 步是「总是 `csr`」而不是「能传播就传播」

**实测**（`run_tests.test_maintain` ③、`MEASUREMENTS` 结果二十四）：

    `§A3`（视图最粗）要枚举**更粗的**候选划分，候选数随块数**组合爆炸**：

        `csr`  8 块 ⇒ 候选 **4140**    ⇒ `§A3` **判得了**（枚举完，已是最粗）
        传播  12 块 ⇒ 候选 **4213597** ⇒ `§A3` **判不了**（超上限）⇒ **跳过**

⇒ 交付一个**比 `csr` 更细**的划分，会让「最粗」这条承重性质
  **从可判退化成判不了**，而本仓库的规矩是 **「跳过 ≠ 通过」**。
⇒ 所以这里没有「预算」这个旋钮 —— 那条折中**不存在**（上一版设过，已被实测否掉）。

⚠️ 「维护」这件事因此**便宜**：`csr` 只依赖 `spec`，而真语料上它是**毫秒级**
   （全量 79 个方向：`(视图)` 组整组 3.4 s）。**贵的从来不是维护，是别的。**

## 两条边界

    **只读结构，不写结构。** 不 `expand`、不 `insert` —— 那分别是流程 A 的 `R3a`
      与流程 B 的事（`§1` 表）。它只把**已建成的结构**重新编码成认识。
    **不改外生项。** `P` / `E` 仍只能由人声明（`§K9`）；本入口**消费**声明，不生产它。

## 为什么它是个**独立入口**（而不是塞进 `cli`）

`cli` 每条方向**当场**建内核、跑 A/B/D，**不落盘结构**（`core/persist.py` 也没接线）
⇒ 「哪一份结构是权威」在 `cli` 里**还没有答案**。
而增量建与一次建**不是同一棵树**（实测：`先建 6、维护 30` ⇒ 3 个方向；
`一次建 36` ⇒ 25 个方向）⇒ 挂在 `cli` 上的认识，下次读时指纹必然不符。

⇒ 所以本入口按 **`spec` 认**（外生声明、进版本库、确定性），
  认识的**版本就是 `spec` 的指纹**。**结构落盘是另一件事，要人拍。**
"""
from __future__ import annotations

import sys

from .checks._fixtures import find_corpus, load
from .checks._fixtures import coverage_of
from .checks.abstraction import build_views, load_spec_file, spec_for
from .checks.coverage import corpus_fingerprint
from .core import view_persist
from .core.views import maintain, shrink_ratio
from .run_checks import batch_kernel

DIRECTIONS = ("keyset", "reach", "sequence")

#: 视图列表在落盘字典里的**键名**。一处定义（`to_dict` 用的就是它）。
VIEWS_KEY = "视图"


def run_one(which: str, nodes, edges, *, dry_run: bool) -> tuple[bool, str]:
    nodes_dir = find_corpus()
    corpus = corpus_fingerprint(nodes, edges)
    doc = load_spec_file(nodes_dir, corpus)
    spec, why = spec_for(doc, which, corpus)
    if spec is None:
        return True, (f"═══ 方向 {which} ═══\n  ⊘ {why}\n"
                      f"     ⇒ **本方向没跑**（跳过 ≠ 通过）")
    kernel, plugin, _queries, _mk = batch_kernel(which, nodes, edges)
    cover = coverage_of(which, nodes, edges)
    path = view_persist.default_path(nodes_dir)

    disk = view_persist.read(path)
    seed_q = None
    seed_why = "没读"
    if disk:
        from .core.view_persist import from_dict
        try:
            vs_old = from_dict(disk, kernel, cover, plugin)
        except Exception as exc:  # noqa: BLE001 —— 坏存档要**报出来**
            seed_why = f"盘上那份**读不回来**（{type(exc).__name__}: {exc}）⇒ 重建"
        else:
            if set(vs_old.spec.universe) <= set(spec.universe):
                seed_q, seed_why = list(vs_old.q), "盘上那份可作种子（只影响读数）"
            else:
                seed_why = "盘上那份的 `universe` 超出当前 ⇒ 不能当种子"
    else:
        seed_why = f"盘上没有认识（首轮）—— {path}"

    m = maintain(seed_q, spec)
    q_csr = len(m.q)
    lines = [
        f"═══ 方向 {which} ═══",
        f"  声明 {why}",
        f"  落点 {path}",
        f"  种子 {seed_why}",
        f"  交付 **{len(m.q)} 块**（最粗 `csr`）｜shrink {m.shrink:.3f}"
        f"｜动作「{m.action}」",
        f"        {m.reason}",
    ]
    if seed_q is not None:
        lines.append(
            f"  读数：只传播会是 {len(_propagated(seed_q, spec))} 块"
            f"（shrink {m.shrink_if_propagated:.3f}）｜两条路**同构** {m.propagate_equal}"
            f"　⚠️ 无论如何**交付 `csr`** —— 更细的划分会让 `§A3` 判不了")
    if dry_run:
        lines.append("  （`--dry-run` —— **没写文件**）")
        return True, "\n".join(lines)

    vs = build_views(kernel, spec, cover, plugin)
    assert len(vs.q) == q_csr, (
        f"`build_views` 算出来的 {len(vs.q)} 块 ≠ 维护交付的 {q_csr} 块 —— 两处分岔了")
    p = view_persist.write(path, vs)
    back = view_persist.read(p)
    # ⚠️ 键名**从 `to_dict` 那一处取**（`FORMAT_KEY` / `VIEWS_KEY`）——
    #    这里原来写死了 `"format"` / `"views"`（真键名是 `"格式"` / `"视图"`）⇒
    #    打印出来是 `None` 与 `0`，而**文件其实是好的** ⇒ 又是一次「两处各写一份」。
    lines.append(f"  ✔ 已写入 {p}（读回格式串 {back.get(view_persist.FORMAT_KEY)!r}｜"
                 f"{len(back.get(VIEWS_KEY) or [])} 张视图｜q {len(back.get('q') or [])} 块）")
    return True, "\n".join(lines)


def _propagated(seed_q: object, spec) -> list:
    from .core.views import propagate

    return list(propagate(seed_q, spec)) if seed_q else []


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    which = [a for a in argv[1:] if a in DIRECTIONS] or list(DIRECTIONS)
    loaded = load()
    if loaded is None:
        print("⚠ 找不到语料 —— 什么都没维护（**这不是通过**）。"
              "设 `LDV_CORPUS` 或把语料放到 `ldv/corpus/nodes/`。")
        return 1
    nodes, edges, _ = loaded
    print("认识维护 · 流程 E 的**持续**那一步（`§7.4` / `§A8`）")
    print(f"语料 {find_corpus()}｜项 {len(nodes)}｜方向 {which}"
          + ("｜**dry-run**" if dry else ""))
    print()
    for name in which:
        _ok, text = run_one(name, nodes, edges, dry_run=dry)
        print(text)
        print()
    print("═══ 汇总 ═══")
    print(f"  维护了 {len(which)} 条方向｜交付物一律 `csr`｜退出码 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
