# -*- coding: utf-8 -*-
"""`§A5` 第二类对照的**反向判据** —— 三种「看起来对」的假实现必须全被抓。

为什么非要这一份（2026-10-12）：
    原缺陷 = `warranted_of` **只实现了一类账**（滞留），而「根覆盖之外」那一类
    按定义恒判 False。它一直没露，是因为**只验了一类**。
    所以「补上第二类对照」这件事本身，必须配一条**反向判据**证明
    「第二类对照真的走到了那一支」—— 否则它可能被别名成第一类，正照样绿。

三种假实现（全程 monkeypatch，跑完必复原）：
    F1  别名：`a5_out_of_scope_known_answer = a5_known_answer`
        ⇒ 第二类对照根本没验第二类。**必须被抓**。
    F2  恒 YES：第二类对照无条件返回 YES ⇒ `dropped=True` 那一态失效。**必须被抓**。
    F3  恒 NO：无条件返回 NO ⇒ 成立那一态失效。**必须被抓**。
    F0  真实现：必须**不**被抓（否则这条判据自己就是「永远红」的假对照）。

跑法（cwd 必须是 repo/）：
    python ../outputs/_probe_a5_reverse_contrast.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "repo"))

from ldv.checks import abstraction as absn
from ldv.checks._framework import Tri
from ldv import run_checks as rc

FAILS = []


def show(label, got, want):
    mark = "OK " if got == want else "RED"
    if got != want:
        FAILS.append(label)
    print(f"  [{mark}] {label}: got={got!r} want={want!r}")


def a5_good() -> object:
    """表里 A5 那一行的「全对」位（True/False）。"""
    rows = {c: g for c, _d, g in rc._skip_evidence()}
    return rows.get("A5", "<缺行>")


print("── 0 真实现（基线） ──")
show("真实现 ⇒ 表里 A5 全对 = True", a5_good(), True)
show("真实现 ⇒ 两类对照是不同函数",
     absn.a5_out_of_scope_known_answer is not absn.a5_known_answer, True)
show("真实现 ⇒ 第二类两态 YES/NO",
     (absn.a5_out_of_scope_known_answer(False) is Tri.YES,
      absn.a5_out_of_scope_known_answer(True) is Tri.NO), (True, True))

_real_oos = absn.a5_out_of_scope_known_answer
_real_w = absn.warranted_of

print("── F1 别名（第二类 = 第一类） ──")
absn.a5_out_of_scope_known_answer = absn.a5_known_answer
try:
    show("F1 ⇒ A5 全对 = False（被抓）", a5_good(), False)
    show("F1 ⇒ 身份相同（身份钉会红）",
         absn.a5_out_of_scope_known_answer is not absn.a5_known_answer, False)
finally:
    absn.a5_out_of_scope_known_answer = _real_oos

print("── F2 恒 YES ──")
absn.a5_out_of_scope_known_answer = lambda dropped: Tri.YES
try:
    show("F2 ⇒ A5 全对 = False（被抓）", a5_good(), False)
finally:
    absn.a5_out_of_scope_known_answer = _real_oos

print("── F3 恒 NO ──")
absn.a5_out_of_scope_known_answer = lambda dropped: Tri.NO
try:
    show("F3 ⇒ A5 全对 = False（被抓）", a5_good(), False)
finally:
    absn.a5_out_of_scope_known_answer = _real_oos

print("── F4 内核级：把「根覆盖之外」那一支改回恒判 False（= 原来那个缺陷） ──")


def _broken(kernel, did, item, kind=absn.LEDGER_STAYED):
    if kind == absn.LEDGER_OUT_OF_SCOPE:
        return False
    return _real_w(kernel, did, item, kind)


absn.warranted_of = _broken
try:
    show("F4 ⇒ 第二类「成立」那一态由 YES 变 NO（这一支真的被走到了）",
         absn.a5_out_of_scope_known_answer(False), Tri.NO)
    show("F4 ⇒ 表里 A5 全对 = False（被抓）", a5_good(), False)
finally:
    absn.warranted_of = _real_w

print("── 复原核对 ──")
show("复原后 A5 全对 = True", a5_good(), True)
show("复原后 warranted_of 是原件", absn.warranted_of is _real_w, True)
show("复原后第二类两态 YES/NO",
     (absn.a5_out_of_scope_known_answer(False) is Tri.YES,
      absn.a5_out_of_scope_known_answer(True) is Tri.NO), (True, True))

print()
if FAILS:
    print(f"⇒ 红 {len(FAILS)} 条：{FAILS}")
    sys.exit(1)
print("⇒ 全对：四种假实现全被抓、真实现不被抓、复原无残留")
sys.exit(0)
