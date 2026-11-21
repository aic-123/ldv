"""P6 探针：往返逐字相同 + 漂移存档的对照。

跑法：
    cd repo && python ../outputs/_probe_persist.py

⚠️ 临时脚本放 `outputs/`，**不进 `repo/`**。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "repo"))

from ldv.checks._fixtures import items, load, sequences  # noqa: E402
from ldv.core import persist as P  # noqa: E402
from ldv.core.kernel import Kernel  # noqa: E402
from ldv.plugins.keyset import KeysetPlugin  # noqa: E402
from ldv.plugins.reach import ReachPlugin  # noqa: E402
from ldv.plugins.sequence import SequencePlugin  # noqa: E402


def build(cls, which, nodes, edges):
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
    k = cls(plug, items(nodes))
    k.build(root)
    for nid in sorted(nodes):
        k.insert(nid)
    return k, plug


def main() -> int:
    loaded = load()
    if loaded is None:
        print("语料不在 ⇒ 跳过")
        return 0
    nodes, edges, _ = loaded

    print("=== ① 往返逐字相同（三个方向 × 批建）===")
    for which in ("keyset", "reach", "sequence"):
        k, plug = build(Kernel, which, nodes, edges)
        # 删一项，让删除路径的账也进存档
        victim = sorted(nodes)[0]
        k.remove(victim)
        data = P.to_dict(k)
        k2 = P.from_dict(json.loads(json.dumps(data)), plug)
        same = P._fingerprint(k) == P._fingerprint(k2)
        print(f"  {which:9s} 往返相同 = {same}｜方向 {len(k.all_directions())}"
              f"｜账本 {len(k.ledger)} 条｜存档 {len(json.dumps(data))} 字节")

    print("=== ② 存档里没有派生边 ===")
    for which in ("keyset", "reach", "sequence"):
        k, _ = build(Kernel, which, nodes, edges)
        data = P.to_dict(k)
        leaked = [r["did"] for r in data["方向"] if "parent" in r or "witness" in r]
        print(f"  {which:9s} 带 parent/witness 的行 = {len(leaked)}")

    print("=== ③ 派生边可由权威边重算 ===")
    for which in ("keyset", "reach", "sequence"):
        k, _ = build(Kernel, which, nodes, edges)
        derived = P.derive_parents({p: tuple(v) for p, v in k._children.items()},
                                   k.root.did)
        bad = [d.did for d in k.all_directions() if derived.get(d.did) != d.parent]
        bad += [d.did for d in k.all_directions()
                if d.witness != (() if d.parent is None else (d.parent,))]
        print(f"  {which:9s} 与内存里的 parent/witness 不符 = {len(bad)}")

    print("=== ④ 漂移存档（已知答案对照）===")
    for which in ("keyset", "reach", "sequence"):
        k, plug = build(Kernel, which, nodes, edges)
        clean = P._to_dict_storing_derived(k)
        a0 = P.from_dict(json.loads(json.dumps(clean)), plug)
        b0 = P._from_dict_trusting_derived(json.loads(json.dumps(clean)), plug)
        drift = P._to_dict_storing_derived(k)
        dids = [r["did"] for r in drift["方向"]]
        for row in drift["方向"]:
            if row["parent"] is None:
                continue
            row["parent"] = dids[0]
            row["witness"] = [dids[0]]
        a = P.from_dict(json.loads(json.dumps(drift)), plug)
        b = P._from_dict_trusting_derived(json.loads(json.dumps(drift)), plug)
        print(f"  {which:9s} 未漂移：权威 {P._fingerprint(k) == P._fingerprint(a0)}"
              f"｜双份 {P._fingerprint(k) == P._fingerprint(b0)}"
              f"   ← ⚠️ 未漂移时两者**同输出**，对照分不开")
        print(f"  {which:9s} 已漂移：权威 {P._fingerprint(k) == P._fingerprint(a)}"
              f"｜双份 {P._fingerprint(k) == P._fingerprint(b)}")

    print("=== ⑤ 权威边自相矛盾 ⇒ 必须报错 ===")
    k, _ = build(Kernel, "keyset", nodes, edges)
    data = P.to_dict(k)
    parents = [p for p, kids in data["子"].items() if len(kids)]
    victim = data["子"][parents[1]][0]
    data["子"][parents[0]] = list(data["子"][parents[0]]) + [victim]
    try:
        P.from_dict(json.loads(json.dumps(data)), KeysetPlugin())
        print("  ✗ 静默接受了 DAG 存档")
    except P.PersistenceError as e:
        print(f"  ✓ 报错：{str(e)[:70]}…")

    print("=== ⑥ reach 缓存不落盘、但代价读数相同 ===")
    k, plug = build(Kernel, "reach", nodes, edges)
    before = {d.did: [plug.penalty(d, k.items[i]) for i in sorted(k.members_of(d))]
              for d in k.all_directions()}
    data = P.to_dict(k)
    k2 = P.from_dict(json.loads(json.dumps(data)), plug)
    after = {d.did: [plug.penalty(d, k2.items[i]) for i in sorted(k2.members_of(d))]
             for d in k2.all_directions()}
    print(f"  代价读数逐项相同 = {before == after}")
    print(f"  读回后缓存是冷的 = "
          f"{len(getattr(plug, '_dist_cache', {})) > 0}（原件）→ "
          f"{len(getattr(plug, '_dist_cache', {})) > 0}（同一插件实例，缓存本来就在）")

    print("=== ⑦ 存档幂等（同一份结构 ⇒ 同一份字节）===")
    for which in ("keyset", "reach", "sequence"):
        k, plug = build(Kernel, which, nodes, edges)
        s1 = json.dumps(P.to_dict(k), sort_keys=True)
        k2 = P.from_dict(json.loads(s1), plug)
        s2 = json.dumps(P.to_dict(k2), sort_keys=True)
        print(f"  {which:9s} 字节稳定 = {s1 == s2}｜{len(s1)} 字节")

    print("=== ⑧ 双份 loader 读回的内核 ⇒ B13 红（两条边打架）===")
    from ldv.checks._framework import Report
    from ldv.checks.structure import b13_rank_well_founded
    for which in ("keyset", "reach", "sequence"):
        k, plug = build(Kernel, which, nodes, edges)
        drift = P._to_dict_storing_derived(k)
        dids = [r["did"] for r in drift["方向"]]
        for row in drift["方向"]:
            if row["parent"] is None:
                continue
            row["parent"] = dids[0]
            row["witness"] = [dids[0]]
        a = P.from_dict(json.loads(json.dumps(drift)), plug)
        b = P._from_dict_trusting_derived(json.loads(json.dumps(drift)), plug)
        ra, rb = Report(plugin=which, expects=("B13",)), Report(plugin=which, expects=("B13",))
        b13_rank_well_founded(a, ra)
        b13_rank_well_founded(b, rb)
        print(f"  {which:9s} B13：权威 {ra.assertions[0].result}｜双份 {rb.assertions[0].result}"
              f"｜{str(rb.assertions[0].detail)[:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
