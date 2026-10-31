# -*- coding: utf-8 -*-
"""**调度器**（P1，2026-10-29）—— 谁按什么频率调 `agenda.tick()`。

## 它补的是最后一段

`core/clock.py` 让 `k` 可以由时间推（`--agenda-tick` 能算出「闲了 3 轮」），
但**没人按节奏调它** —— 那个入口是手动的。没有调度器，「主动性」仍然要求
「有人记得来敲一下」。服务层的职责就是把这一敲接过去。

## 一条红线：`tick()` 自己不幂等，幂等由这里守

`agenda.tick()` 每被调一次，就会在账本里记一行 `seen`（把这一轮**摆出来的**记下来）。
所以「同一时刻被调度两次」不是浪费 CPU —— 它会让账本多一行，
而账本是 `k` 的**唯一**基线 ⇒ **重复 tick 会催熟 `k`**（门槛降得比时间过得快）。

⇒ 这里用**槽位**（slot = `epoch // interval`）做幂等键：

    同一个槽，只 tick 一次；换了槽，才允许下一次。

且槽位**落盘**（`<root>/.sched.json`）：只放内存的话，一个重启循环
（systemd `Restart=always` + 起不来）会在同一槽里反复 tick ——
那正是幂等最该挡住的情形，而它恰好是内存版挡不住的。

## 不承诺什么

- **不保证 tick 的间隔均匀**：进程停着的那段时间不会有补偿（不补票）。
  停了 3 小时再起来，下个槽才 tick 一次 —— `k` 是按**账本时间**算的，不是按次数。
- **不做租约**（那是 P3）：两个实例同时跑会各 tick 一次 ⇒ 同一槽两次。
  单实例是 P1 的前提，写在这里而不是「大家注意一下」。
- **不替人决定「到点就该做」**：`tick()` 只把待办摆出来。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import serve_tenants as T

#: 默认调度间隔（秒）。30 分钟 = `agenda.IDLE_PERIOD_MIN` ——
#: 一槽一档，于是「闲置多久 ⇒ k 涨多少」与服务自己的节奏**同一个刻度**。
DEFAULT_INTERVAL_S = 1800

#: 状态文件（在租户根**里面**：备份=拷目录时它跟着走，不会出现「目录回来了、槽位没回来」）
STATE_NAME = ".sched.json"


def _default_tick(name: str, nodes_dir: Path, now: float) -> dict:
    """默认的一格：建一个只含根的 keyset 内核，然后 `agenda.tick()`。

    ⚠️ 内核**每槽新建**（§2.1：内核持锁、构建有缓存，复用 = 把缓存与锁的生命周期
       绑到调度器线程上）。一格一次的代价可以接受 —— 它 30 分钟才发生一次。
    ⚠️ 用 `checks._fixtures.make_keyset_root`（**同一个**「怎么建根内核」的地方），
       不在这里copy 一份 `Kernel(KeysetPlugin(), items(...))` —— 抄一份就会漂。
    """
    from ldv.checks._fixtures import make_keyset_root
    from ldv.core import agenda
    from ldv.corpus.loader import load_nodes

    nodes = load_nodes(nodes_dir)
    kern = make_keyset_root(nodes)()
    out = agenda.tick(kern, nodes_dir, now=_datetime(now))
    return {"k": out.get("空闲轮次"), "到点": len(out.get("到点") or ()),
            "项": len(nodes)}


def _datetime(epoch: float):
    from datetime import datetime
    # 与账本同源：`agenda.jsonl` 存的是**本地 naive** 时间（`clock.now()` 的默认口径）
    return datetime.fromtimestamp(epoch)


class Scheduler:
    """单实例调度器。测试可以只用 `run_once()`（不启线程）。"""

    def __init__(self, root: str | Path, interval_s: int = DEFAULT_INTERVAL_S,
                 state_file: str | Path | None = None, tick_fn=None, now=None):
        self.root = Path(root)
        self.interval_s = max(1, int(interval_s))
        self.state_file = (Path(state_file) if state_file is not None
                           else self.root / STATE_NAME)
        self.tick_fn = tick_fn or _default_tick
        self._now = now or time.time
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.ticks_total = 0
        self.skipped_total = 0
        self.last: dict = {}

    # ── 槽位 / 状态 ───────────────────────────────────────────────────
    def slot_of(self, now: float) -> int:
        return int(now // self.interval_s)

    def _load_state(self) -> dict:
        try:
            raw = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # 读不出来 ⇒ 当成空状态。**多 tick 一次**比「永远不 tick」更可接受，
            # 而且多出来的那一行账本不会改变判定逻辑（k 仍按时间算）。
            return {}
        if not isinstance(raw, dict):
            return {}
        slots = raw.get("slots")
        return slots if isinstance(slots, dict) else {}

    def _save_state(self, slots: dict) -> None:
        """原子写：临时文件 + `os.replace`（半截的 JSON 会让下次启动读不出槽位）。"""
        tmp = self.state_file.with_suffix(self.state_file.suffix + ".tmp")
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps({"slots": slots, "interval_s": self.interval_s},
                                      ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.state_file)
        except OSError:
            # 落盘失败**不许**把调度搞死：内存里那份还在，本进程内仍然幂等
            pass

    # ── 走一格 ────────────────────────────────────────────────────────
    def run_once(self, now: float | None = None) -> dict:
        """对该 tick 的租户各走一格。返回 `{ticked, already, skipped, errors}`。"""
        t = self._now() if now is None else now
        slot = self.slot_of(t)
        out: dict = {"slot": slot, "ticked": [], "already": [], "skipped": [],
                     "errors": {}}
        with self._lock:
            slots = self._load_state()
            for name in T.list_tenants(self.root):
                if not T.complete(self.root, name):
                    out["skipped"].append(name)     # 缺 nodes/ 或 view_spec：不猜它想要什么
                    self.skipped_total += 1
                    continue
                if slots.get(name) == slot:
                    out["already"].append(name)     # ★ 幂等：这一槽已经走过了
                    continue
                try:
                    out.setdefault("detail", {})[name] = self.tick_fn(
                        name, self.root / name / "nodes", t)
                except Exception as exc:            # noqa: BLE001 - 一格失败不许停调度
                    out["errors"][name] = f"{type(exc).__name__}: {exc}"
                    continue                        # ⚠️ 不记槽位：下回还要再试
                out["ticked"].append(name)
                slots[name] = slot
                self.ticks_total += 1
                self._save_state(slots)
        self.last = out
        return out

    # ── 线程 ──────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()

        def loop() -> None:
            while True:
                try:
                    self.run_once()
                except Exception:                   # noqa: BLE001 - 线程不许死
                    pass
                if self._stop.wait(self.interval_s):
                    return

        self._thread = threading.Thread(target=loop, name="ldv-sched", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        th, self._thread = self._thread, None
        if th is not None:
            th.join(timeout=5)

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())
