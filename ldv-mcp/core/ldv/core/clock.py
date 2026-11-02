# -*- coding: utf-8 -*-
"""系统内部时钟：**把「现在」收成一个可替换的单点**（2026-10-29）。

## 为什么现在要有

议程层（`agenda.py`）回答「现在轮到谁」，但它**自己没有时间**：

- `now` 靠调用方传，默认值 `datetime.now()` 散在好几处；
- 于是「我闲了」只能由**宿主数着**报（`next(idle=k)`）—— 宿主不报，门槛就不降，
  一条待办可以永远排在「未到」里；
- 判据那边只能靠**手工造时间**绕过去，测不到「真实间隔会不会让 k 涨起来」。

这一层只做三件事，不碰任何判定：

    ① `now()`           —— 唯一的「现在」；默认系统本地时间（naive，与既有
                           `agenda.jsonl` 的 `datetime.now().isoformat()` 逐字兼容）
    ② `set_source()` / `frozen()` —— 判据里把时间**钉住**或**快进**；
    ③ `age_days()`      —— 「多久以前」的唯一口径（议程层不再各算各的）

⚠️ **边界（别越）**：时钟只提供「现在几点」，**不提供「该不该做」**。
   档位仍由 `significance()` 按后果定；触发仍由 `agenda()` 判。
   把「定时器一到就执行」写进来，就是把「只提议」改成「自己动手」——
   那是另一件事，要单独裁定。

⚠️ **默认值不许变**：`now()` 的默认源仍是系统时钟，且返回 **naive 本地时间**。
   现有 `agenda.jsonl` 里存的就是这个格式；换成 tz-aware 会让**旧账本读不出来**，
   而「读不出来」与「没有记录」在议程层长得一模一样（都返回空 dict）——
   那正是本仓库一直在防的那种静默。
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Callable, Iterator

#: 当前时间源。判据可以替换它；**生产路径不许替换**。
_SOURCE: Callable[[], datetime] = datetime.now

#: 被 `frozen()` 钉住时的时间；`None` 表示没钉。
_FROZEN: datetime | None = None


def now() -> datetime:
    """现在。默认系统本地时间（naive）；被钉住时返回钉住的那个时刻。"""
    if _FROZEN is not None:
        return _FROZEN
    return _SOURCE()


def set_source(fn: Callable[[], datetime]) -> None:
    """换一个时间源（判据用）。传 `datetime.now` 即还原。"""
    global _SOURCE
    if not callable(fn):
        raise TypeError("时间源必须是可调用对象，返回 datetime")
    _SOURCE = fn


def reset() -> None:
    """还原成系统时钟，并解冻。"""
    global _SOURCE, _FROZEN
    _SOURCE = datetime.now
    _FROZEN = None


@contextmanager
def frozen(t: datetime) -> Iterator[datetime]:
    """在 `with` 块里把「现在」钉成 `t`；退出时**恢复原状**（含原时间源）。"""
    global _FROZEN
    prev = _FROZEN
    _FROZEN = t
    try:
        yield t
    finally:
        _FROZEN = prev


def age_days(t: datetime, at: datetime | None = None) -> float:
    """`t` 距离「现在」多少天（`t` 在未来 ⇒ 负值，不夹到 0 —— 时钟回拨要看得见）。"""
    at = at if at is not None else now()
    # 一边带时区一边不带 ⇒ 相减会抛 TypeError；统一按 `at` 的口径剥掉
    if (t.tzinfo is None) != (at.tzinfo is None):
        t = t.replace(tzinfo=None)
        at = at.replace(tzinfo=None)
    return (at - t).total_seconds() / 86400.0
