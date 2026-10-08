# -*- coding: utf-8 -*-
"""把**没有文字层**的扫描件 PDF 渲成 PNG —— 好让「读图」这一步**可复现**。

用法:
    python render_pages.py <pdf> <out_dir> [起页] [止页] [dpi]

    # 从**工作区根**跑（与 `verify_quotes.py` 的用法同一套相对路径）
    python repo/docs/prior-art/render_pages.py .prior-art/pagetarjan.pdf outputs/_ptpages 3 4 130

⚠️ **为什么需要它**：`extract.py` 靠 PDF 的**文字层**抽正文。有些源**没有文字层**
（纯扫描图）⇒ `extract.py` 抽出来是空的，`verify_quotes.py` 也就**核不到**它的引文。

    实测：`pagetarjan.pdf`（Paige & Tarjan, Princeton TR-038, 1986）
          25 页 / **416 字符** —— 等于没有。

⇒ 这类源只有一条路：**把页面渲成图，人（或多模态模型）读**。
   本脚本做前一半；后一半是「打开 `p04.png`，读出那一句，写进交付物，
   **并在正文里显式声明这一条不经过 `verify_quotes`**」。

⚠️ **不要**把这类源塞进 `verify_quotes.py` 的 `SRC_DIRS`：
   一份 416 字符的 `.txt` 会让**每一条**引文都算「没命中」⇒ 全红，
   而那个红**指错了地方**（看起来像「引文写错了」，其实是「源文是空的」）。
   这正是「**源文不在」与「引文有问题」必须长得不一样**那条纪律的同一个面。

⚠️ **版权**：渲出来的 PNG 是**别人的作品**，与 PDF 本身同一条规矩 ——
   **不随仓库发布**（见 `README.md`）。所以 `out_dir` 应当指向仓库**外面**
   （工作区根的 `outputs/` 一类），不要放在 `docs/prior-art/` 里。
"""
import os
import sys


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    pdf, out = sys.argv[1], sys.argv[2]
    lo = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    hi = int(sys.argv[4]) if len(sys.argv) > 4 else 6
    dpi = int(sys.argv[5]) if len(sys.argv) > 5 else 130
    try:
        import pymupdf
    except ImportError:
        print("✗ 需要 pymupdf：pip install pymupdf")
        return 2
    if not os.path.exists(pdf):
        print(f"✗ 找不到 {pdf} —— **这不是「没有可读的页」，是「源文不在」**。")
        return 2
    os.makedirs(out, exist_ok=True)
    doc = pymupdf.open(pdf)
    print(f"{pdf}｜{doc.page_count} 页｜渲染 {lo}–{min(hi, doc.page_count)} @ {dpi} dpi")
    for i in range(lo - 1, min(hi, doc.page_count)):
        pix = doc[i].get_pixmap(dpi=dpi)
        f = os.path.join(out, "p%02d.png" % (i + 1))
        pix.save(f)
        print(f"  {os.path.basename(f)}  {os.path.getsize(f)} B  {pix.width}x{pix.height}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
