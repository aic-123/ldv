# -*- coding: utf-8 -*-
"""核对 `outputs/ldv-裁定推荐-项2与项7.md` 里**引用块（`> `）形态**的英文引文。

为什么单独写：`.prior-art/verify_quotes.py` 只抽 `「…」`，而这个文档里的英文引文
用的是 markdown 引用块 —— **正好落在它的扫描盲区里**。用户要的是「推荐理由站得住」，
所以这些引文必须逐字回源文比对，一条都不能靠印象。

用法：cd <工作区根> && python outputs/_verify_rec_quotes.py
"""
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, ".prior-art"))
import verify_quotes as V  # noqa: E402

#: 默认目标。命令行给了就按命令行来（可以给多个）。
DEFAULT_DOCS = [
    os.path.join(_HERE, "ldv-裁定推荐-项2与项7.md"),
    os.path.join(_ROOT, "repo", "docs", "分层方向视图-设计文档.md"),
    os.path.join(_ROOT, "repo", "docs", "分层方向视图-成熟方案与跨领域文献.md"),
]

# 归属 → 源文。归属行写在引用块的**最后一行**（`— 作者 …`）。
SRC_BY_ATTR = {
    "Acar": "acar02.txt",
    "GRAIL": "grail10.txt",
}


def blocks(md: str):
    """返回 (起行号, 引用块正文, 归属源文) 的列表。

    一个「引用块」= 连续的 `> ` 行。归属**先看块内**（出处行写在块尾），
    块内没有就**回看块前 3 行散文** —— 有些块的出处写在块外面（如 GRAIL）。
    """
    lines = md.splitlines()
    out, cur, start = [], [], None
    for i, line in enumerate(lines, 1):
        if line.startswith(">"):
            if start is None:
                start = i
            cur.append(line.lstrip(">").rstrip())
        else:
            if cur:
                out.append((start, "\n".join(cur)))
                cur, start = [], None
    if cur:
        out.append((start, "\n".join(cur)))

    res = []
    for start, body in out:
        ctx = body + "\n" + "\n".join(lines[max(0, start - 4):start - 1])
        src = None
        for key, fn in SRC_BY_ATTR.items():
            if key in ctx:
                src = fn
                break
        res.append((start, body, src))
    return res


def main(argv: list[str]) -> int:
    docs = argv[1:] or DEFAULT_DOCS
    srcs = V.load_sources()
    print(f"源文 {len(srcs)} 份\n")

    total = bad = 0
    for doc in docs:
        if not os.path.exists(doc):
            print(f"⚠️ 找不到 {doc} —— 跳过（不是「过」）")
            continue
        md = open(doc, encoding="utf-8").read()
        print(f"=== {os.path.basename(doc)} ===")
        for start, body, src in blocks(md):
            if src is None:
                continue
            if src not in srcs:
                print(f"  L{start}  ⚠️ 源文 {src} 不在语料里 —— 未核")
                continue
            # 把出处行从正文里剔掉（它不是引文）
            text = "\n".join(l for l in body.splitlines()
                             if not l.strip().startswith("—"))
            q = V.norm(text)
            if len(q) < 8:
                continue
            total += 1
            grams = V.kgrams(srcs[src])
            worst, notes = 1.0, []
            for frag in V.fragments(q):
                if len(frag) < V.MIN_FRAG:
                    continue
                ratio, g = V.coverage(frag, grams, srcs[src])
                worst = min(worst, ratio)
                if ratio < V.COVER_MIN:
                    notes.append(f"覆盖比 {ratio:.2f}：{frag[:80]}")
                if g:
                    notes.append("缺口：" + " ‖ ".join(x[:60] for x in g))
            if notes:
                bad += 1
                print(f"  L{start}  [{src}]  最低覆盖比 {worst:.2f}")
                for x in notes[:4]:
                    print(f"        {x}")
            else:
                print(f"  L{start}  [{src}]  ✓ 全覆盖（最低 {worst:.2f}）")
        print()
    print(f"合计：引用块引文 {total} 条｜有问题 {bad} 条")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
