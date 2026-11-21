# -*- coding: utf-8 -*-
"""两个 ldv 的对话实验（2026-10-21）—— 文件层 + HTTP 层，同一套消息。

系统 A：ext-wiki-vote（7115 项，三维：type / evidence_status / influence）
系统 B：cora-labeled   （2708 项，另一份材料、另一棵键）

看点：
  ① 双方**词汇表不同** ⇒ A 的键发给 B，B 必须**如实拒收**（不许静默 0 条）
  ② **双向对等** —— 谁都能提问、谁都能回答（没有主从）
  ③ 两层传输（文件 / HTTP）对同一组消息给出**逐字相同**的结果
  ④ 每条 RESULT 都过 `verify_result`（不依赖对方诚信）
"""
import io
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

#: 本文件住在 repo/evidence/ 下 ⇒ parent.parent 就是 repo 根。
#: ⚠️ 曾经写成 parent.parent / "repo"（outputs/ 时期的路径）⇒ 在 evidence/ 下
#:    推成 repo/repo ⇒ import 直接失败；2026-10-21 验收时实测「宣称可重跑、
#:    实则跑不起来」才抓住。改路径行不够，还要真跑一遍才算数。
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from ldv import protocol as PRT          # noqa: E402
import entry as E                        # noqa: E402


def hr(title):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def show_msg(tag, msg, keys=()):
    p = msg.get("payload") or {}
    if msg.get("type") == PRT.MSG_RESULT and p.get("ok"):
        ns = p.get("nodes") or []
        line = (f"  {tag} ⇒ RESULT｜答案 {p['n_answer']} 项｜{len(ns)} 支"
                f"｜verify {PRT.verify_result(msg)[0]}")
        for n in ns[:3]:
            line += (f"\n        {n['did']} 第{n['rank']}层 {n['n_members']}项"
                     f"｜含答案 {n.get('n_answer')}｜精确 {n.get('precision')}")
        if len(ns) > 3:
            line += f"\n        …另 {len(ns)-3} 支"
    else:
        err = (p.get("error") or "")[:150]
        line = f"  {tag} ⇒ {msg.get('type')}｜{err}"
    print(line)


def main():
    A = E.App("ext-wiki-vote")
    B = E.App("cora-labeled")
    fpA, fpB = A.fingerprint(), B.fingerprint()
    hr("系统自报（对话前先交换身份）")
    print(f"  A = {A.corpus_name}｜{len(A.nodes)} 项｜{len(A.all_keys)} 键｜fp {fpA}")
    print(f"  B = {B.corpus_name}｜{len(B.nodes)} 项｜{len(B.all_keys)} 键｜fp {fpB}")

    # 各自挑一个「自己世界里的」键（跨系统不一定通）
    keyA = sorted(k for k in A.all_keys if "=" in k)[0]
    keyB = sorted(k for k in B.all_keys if "=" in k)[0]
    hr(f"① A 用自己的词汇问 B（A: {keyA}）")
    q1 = PRT.build_query([keyA], [], frm=A.who(), fp=fpA)
    r1 = B.msg(q1)
    show_msg("B 收到「" + keyA + "」后", r1)
    print(f"        ⚠️ 这就是跨语料对话的常态：**词汇不通必须如实说**"
          f"（ok=false 且给出建议），不许静默 0 条")

    hr(f"② A 用 B 的词汇问 B（B: {keyB}）")
    q2 = PRT.build_query([keyB], [], frm=A.who(), fp=fpA)
    r2 = B.msg(q2)
    show_msg("B 回答", r2)

    hr(f"③ B 反问 A（用 A 的词汇：{keyA}）—— 对等，没有主从")
    q3 = PRT.build_query([keyA], [], frm=B.who(), fp=fpB)
    r3 = A.msg(q3)
    show_msg("A 回答", r3)

    hr("④ 文件层批交换：三条消息（B 的键 / A 的键 / 坏信封）一次过")
    # 临时目录：开头清（重跑保护）、结尾也清（可重跑不该积累垃圾；
    # 残留文件还会变成 evidence_index 的孤儿 —— 2026-10-21 实测踩到）
    tmp = Path(__file__).resolve().parent / "_dlg"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    bad = dict(q2)
    del bad["fp"]
    inp = tmp / "in.jsonl"
    inp.write_text("\n".join(json.dumps(m, ensure_ascii=False) for m in (q2, q1, bad)) + "\n",
                   encoding="utf-8")
    st = PRT.exchange_file(B, inp, tmp / "out.jsonl")
    print(f"  stats {st}")
    for ln in (tmp / "out.jsonl").read_text(encoding="utf-8").splitlines():
        m = json.loads(ln)
        show_msg(m["type"], m)

    hr("⑤ 同一句话，两棵不同的树 —— 各自「最大的那一类」")
    # 各取覆盖最多的那个键（不写死，按数据挑）
    # ⚠️ 先 **sorted** 再 max：并列时 max 取「迭代序里第一个」，而 all_keys 是
    #    frozenset（字符串哈希随进程随机化）⇒ 不排序的话，两个都覆盖全部的
    #    恒定键之间选哪个**每次跑都可能不一样**（实测：B 上 同行评审 与
    #    source.kind=数据集 都是 2708/2708，两次跑出了两个名字）⇒
    #    §10.7 ⑤ 记的键名必须可复现，排序即固定（字典序）。
    bestA = max(sorted(k for k in A.all_keys if "=" in k),
                key=lambda k: sum(1 for ks in A.keys_of.values() if k in ks))
    bestB = max(sorted(k for k in B.all_keys if "=" in k),
                key=lambda k: sum(1 for ks in B.keys_of.values() if k in ks))
    ra = A.msg(PRT.build_query([bestA], [], frm="X", fp=fpA))
    rb = B.msg(PRT.build_query([bestB], [], frm="X", fp=fpB))
    print(f"  A 世界最大类 {bestA} ⇒ 答案 {ra['payload']['n_answer']} 项"
          f"（共 {len(A.nodes)}）")
    print(f"  B 世界最大类 {bestB} ⇒ 答案 {rb['payload']['n_answer']} 项"
          f"（共 {len(B.nodes)}）")
    print("  ⇒ 两个系统**不共享词汇、不共享结构**；协议传的是结构，不是共识")

    hr("⑥ 结果逐字性：文件层 vs HTTP 层对同一组消息")
    import urllib.request
    urls = {"A": "http://127.0.0.1:8771", "B": "http://127.0.0.1:8772"}
    procs = {}
    for who, port in (("A", 8771), ("B", 8772)):
        procs[who] = subprocess.Popen(
            [sys.executable, "entry.py", "--port", str(port), "--corpus",
             "ext-wiki-vote" if who == "A" else "cora-labeled"],
            cwd=str(REPO), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        try:
            urllib.request.urlopen(urls["A"] + "/api/meta", timeout=2)
            urllib.request.urlopen(urls["B"] + "/api/meta", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    try:
        for who, target, q in (("A", "B", q2), ("B", "A", q3)):
            got = PRT.send_msg(urls[target] + "/api/msg", q)
            local = {"A": A, "B": B}[target].msg(q)
            same = (got["payload"].get("answer_ids")
                    == local["payload"].get("answer_ids"))
            print(f"  {who} → {target}（HTTP）⇒ 答案 {got['payload']['n_answer']} 项"
                  f"｜与本端文件层逐字相同：{same}"
                  f"｜verify {PRT.verify_result(got)[0]}")
    finally:
        for p in procs.values():
            p.terminate()
    print("\n实验结束。")
    shutil.rmtree(tmp, ignore_errors=True)   # 收尾清理（见 ④ 的注释）


if __name__ == "__main__":
    main()
