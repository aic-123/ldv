# -*- coding: utf-8 -*-
"""一次性树端到端实测（2026-10-21）：真实交流 → ingest → build → 查 → promote → abandon。"""
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent / "repo"
sys.path.insert(0, str(REPO))
WRK = Path(__file__).resolve().parent / "_e2e"
shutil.rmtree(WRK, ignore_errors=True)
WRK.mkdir()

from ldv import protocol as PRT          # noqa: E402
import entry as E                        # noqa: E402
from ldv.tools import exchange_staging as XS  # noqa: E402

A = E.App("ext-wiki-vote")
B = E.App("cora-labeled")
fpA, fpB = A.fingerprint(), B.fingerprint()

# ── ① 真实交流 3 轮（一问一答算一轮，双向） ──────────────────────────────
msgs = []
def send(src, dst, payload):
    m = PRT.envelope("QUERY", payload, frm=src.who(), fp=src.fingerprint())
    r = dst.msg(m)
    msgs.extend([m, r])
    return r

send(A, B, {"require": ["type=同行评审"]})          # A 用 B 的词汇问 B
send(B, A, {"require": ["type=出度1-3"]})           # B 反问 A
send(A, B, {"require": ["evidence_status=入度0"]})  # A 用自己的词汇 ⇒ B 拒收
EX = WRK / "exchange.jsonl"
EX.write_text("\n".join(json.dumps(m, ensure_ascii=False) for m in msgs) + "\n",
              encoding="utf-8")
print(f"① 交流 {len(msgs)} 条消息 ⇒ {EX}")

# ── ② ingest 成一次性树（BUILTIN 语料的缓冲区） ──────────────────────────
corpus = REPO / "ldv" / "corpus"
r = XS.ingest(corpus, EX, "dlg-test")
print(f"② ingest ⇒ {r['节点']} 节点｜撞 id {r['撞 id 拒收']}")
print(f"   树可用：{r['树可用']}")

# ── ③ 把它当语料 build（零改动，LDV_CORPUS 指路径） ─────────────────────
nodes_dir = Path(r["目录"]) / "nodes"
env = dict(os.environ, LDV_CORPUS=str(nodes_dir), PYTHONIOENCODING="utf-8",
           PYTHONUTF8="1", PYTHONPATH=str(REPO))
out = subprocess.run([sys.executable, "-c", """
import sys; sys.path.insert(0, '.')
import entry as E
app = E.App(None)                     # default_corpus() 在场 ⇒ 指向暂存目录
m = app.meta()
print('CORPUS', m['corpus'], '|', m['nodes'], '项 | from', m['corpus_from'])
q = app.query({'require': ['type=RESULT']})
print('QUERY type=RESULT ⇒ 答案', q['n_answer'], '项 |', q['n_branches'], '支')
q2 = app.query({'require': ['who=A']})
print('QUERY who=A        ⇒ 答案', q2['n_answer'], '项 |', q2['n_branches'], '支')
"""], capture_output=True, text=True, encoding="utf-8", cwd=str(REPO), env=env)
print("③ 一次性树当语料 ⇒")
print("   " + out.stdout.strip().replace("\n", "\n   "))
if out.returncode:
    print("   stderr:", out.stderr[-400:])

# ── ④ 隔离：正式结构不因它变 ─────────────────────────────────────────────
from ldv.corpus.loader import load_nodes  # noqa: E402
before = len(load_nodes(corpus))
print(f"④ load_nodes（正式）仍是 {before} 项 ⇒ 一次性树不进结构")

# ── ⑤ promote 然后 abandon ───────────────────────────────────────────────
p = XS.promote(corpus, "dlg-test")
print(f"⑤ promote ⇒ 移进暂存 {p['移进暂存']} 个｜撞名跳过 {p['撞名跳过']}")
from ldv.tools import staging as ST  # noqa: E402
c = ST.commit(corpus)
print(f"   commit ⇒ 已提交 {len(c.get('已提交', []))}｜拒收 {len(c.get('拒收', []))}")
after = len(load_nodes(corpus))
print(f"   正式节点 {before} → {after}")
# 还原（判据也会跑这条路径，但这里是手工实测 ⇒ 自己收拾）
import glob  # noqa: E402
for f in glob.glob(str(corpus / "nodes" / "dlg-test-*.md")):
    os.remove(f)
print(f"   实测后还原 ⇒ {len(load_nodes(corpus))} 项")

a = XS.abandon(corpus, "dlg-test")
print(f"⑥ abandon 不存在的 ⇒ {a}")
XS.ingest(corpus, EX, "dlg-test")
a = XS.abandon(corpus, "dlg-test")
print(f"   abandon 存在的 ⇒ {a}")
print("端到端结束。")
