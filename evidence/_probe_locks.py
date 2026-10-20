"""P8 探针：锁的作用域 / 公共争用点 / 与扇出的关系 / 互斥。

跑法：
    cd repo && python ../outputs/_probe_locks.py

⚠️ 临时脚本放 `outputs/`，**不进 `repo/`**。
"""
from __future__ import annotations

import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "repo"))

from ldv.checks._fixtures import items, load, sequences  # noqa: E402
from ldv.core.locking import (  # noqa: E402
    FanoutLocks, GlobalLocks, LockedKernel, NoLocks, PathLocks,
)
from ldv.plugins.keyset import KeysetPlugin  # noqa: E402
from ldv.plugins.reach import ReachPlugin  # noqa: E402
from ldv.plugins.sequence import SequencePlugin  # noqa: E402


def build(cls, which, nodes, edges, locks=None):
    if which == "keyset":
        plug = KeysetPlugin()
        root = plug.merge([])
    elif which == "reach":
        plug = ReachPlugin(edges, traverse_budget=10_000)
        root = frozenset(nodes)
    elif which == "sequence":
        plug = SequencePlugin(sequences(nodes, edges))
        root = frozenset({()})
    else:
        raise ValueError(which)
    if locks is None:
        k = cls(plug, items(nodes))
    else:
        k = cls(plug, items(nodes), locks=locks)
    k.build(root)
    for nid in sorted(nodes):
        k.insert(nid)
    return k, plug


def scope_of(k, fn, *a):
    k.locks.taken.clear()
    fn(*a)
    return {did for _, did in k.locks.taken}


def main() -> int:
    loaded = load()
    if loaded is None:
        print("语料不在 ⇒ 跳过")
        return 0
    nodes, edges, _ = loaded
    ids = sorted(nodes)

    print("=== ① 作用域 == 锥（写操作真的拿了哪些锁）===")
    for which in ("keyset", "reach", "sequence"):
        k, _ = build(LockedKernel, which, nodes, edges)
        bad_i = bad_r = 0
        sizes = []
        for x in ids:
            got = scope_of(k, k.insert, x)
            cone = set(k.cone(x))
            sizes.append(len(got))
            if got != cone:
                bad_i += 1
        for x in ids[:6]:
            cone = set(k.cone(x))
            got = scope_of(k, k.remove, x)
            if got != cone:
                bad_r += 1
        print(f"  {which:9s} insert 不符 {bad_i}/{len(ids)}｜remove 不符 {bad_r}/6"
              f"｜|锥| min {min(sizes)} max {max(sizes)}")

    print("=== ② 所有锥的**公共**交 == 根？（「根是唯一公共争用点」）===")
    for which in ("keyset", "reach", "sequence"):
        k, _ = build(LockedKernel, which, nodes, edges)
        cones = [set(k.cone(x)) for x in ids]
        cones = [c for c in cones if c]
        common = set.intersection(*cones) if cones else set()
        print(f"  {which:9s} 公共交 = {sorted(common)}｜根 = {k.root.did}"
              f"｜== {{根}} ? {common == {k.root.did}}")

    print("=== ③ 作用域与**扇出**无关（对照：扇出锁会红）===")
    for which in ("keyset", "reach", "sequence"):
        k, _ = build(LockedKernel, which, nodes, edges)
        fan = {d.did: len(k.children_of(d)) for d in k.all_directions()}
        # 路径锁：三个写操作各自拿了几个
        path_sizes = [len(scope_of(k, k.insert, x)) for x in ids]
        # 对照：扇出锁 —— 同一个内核，只把锁管理器换掉
        k2, _ = build(LockedKernel, which, nodes, edges)
        k2.locks = FanoutLocks(
            lambda did: [c.did for c in k2.children_of(k2.direction(did))])
        fan_sizes = [len(scope_of(k2, k2.insert, x)) for x in ids]
        print(f"  {which:9s} 扇出 max {max(fan.values()) if fan else 0}"
              f"｜路径锁 max {max(path_sizes)}｜扇出锁 max {max(fan_sizes)}"
              f"｜{max(path_sizes) < max(fan_sizes)}")

    print("=== ④ 互斥探针（barrier 强制两线程同进临界区）===")
    for name, mk in (("PathLocks", lambda: PathLocks()),
                     ("GlobalLocks", lambda: GlobalLocks()),
                     ("NoLocks", lambda: NoLocks())):
        locks = mk()
        broken = _mutex_broken(locks)
        print(f"  {name:12s} 互斥被破 = {broken}")

    print("=== ⑤ 并发压力（**只报不判**：它的红是概率性的）===")
    for name, mk in (("LockedKernel", lambda: None),
                     ("全局锁", lambda: GlobalLocks()),
                     ("空锁", lambda: NoLocks())):
        k, _ = build(LockedKernel, "keyset", nodes, edges,
                     locks=(mk() if mk() is not None else PathLocks()))
        seen: list[str] = []
        errs: list[str] = []
        lock = threading.Lock()

        def work(chunk):
            try:
                for x in chunk:
                    k.insert(x)
            except Exception as e:  # noqa: BLE001
                with lock:
                    errs.append(repr(e))

        extra = [f"x{i}" for i in range(120)]
        for x in extra:
            k.items[x] = {"id": x, "keys": frozenset()}
        parts = [extra[i::8] for i in range(8)]
        ts = [threading.Thread(target=work, args=(p,)) for p in parts]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        dids = [d.did for d in k.all_directions()]
        print(f"  {name:12s} 方向 {len(dids)}｜编号唯一 {len(set(dids)) == len(dids)}"
              f"｜异常 {len(errs)}{('：' + errs[0][:50]) if errs else ''}"
              f"｜账本 {len(k.ledger)} 条 seq 连续 {[e.seq for e in k.ledger] == list(range(len(k.ledger)))}")
    return 0


def _mutex_broken(locks, key="D0", timeout=0.4) -> bool:
    both: list[bool] = []
    barrier = threading.Barrier(2, timeout=timeout)

    def worker():
        with locks.guard(key):
            try:
                barrier.wait()
                both.append(True)
            except threading.BrokenBarrierError:
                pass

    ts = [threading.Thread(target=worker) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return bool(both)


if __name__ == "__main__":
    raise SystemExit(main())
