"""抽 PDF 正文，带页码标记。用法: python extract.py in.pdf out.txt"""
import sys
from pypdf import PdfReader

src, dst = sys.argv[1], sys.argv[2]
r = PdfReader(src)
body = ""
for i, pg in enumerate(r.pages):
    body += "\n\n===PAGE %d===\n\n" % (i + 1) + (pg.extract_text() or "")
open(dst, "w", encoding="utf-8").write(body)
print(f"{src} → {dst}｜{len(r.pages)} 页｜{len(body)} 字符")
