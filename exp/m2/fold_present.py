#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""字段折叠呈现（PREREG_V16 §1）：把同字段的连续二元切分合并成一步。

纯呈现层：只读 `chain_of()` 的输出，不改内核。
同时跑机械验收 M1–M4（折叠不改决策内容、答案不变、确实变短、过路不混入）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import entry as E                                   # noqa: E402
from ldv.core import chain as CH                     # noqa: E402


def field_of(step: dict) -> str | None:
    cut = step.get("切")
    return str(cut).split("=")[0] if cut else None


def fold(steps: list[dict], fields: set[str]) -> list[dict]:
    """按字段折叠。`fields` = 本次需求的字段（判相干用）。跳过根（steps[0]）。"""
    body = steps[1:]
    out: list[dict] = []
    i = 0
    while i < len(body):
        s = body[i]
        f = field_of(s)
        if f is None:
            out.append({"字段": None, "动作": "子类细分（过路）",
                        "成员数": s["成员数"], "过路": True, "刀数": 1})
            i += 1
            continue
        j = i
        cuts = 0
        last = s
        while j < len(body):
            f2 = field_of(body[j])
            if f2 == f:
                cuts += 1
                last = body[j]
                j += 1
            else:
                break
        out.append({"字段": f, "动作": f"按 {f} 筛选",
                    "刀数": cuts, "成员数": last["成员数"],
                    "过路": f not in fields,
                    "相干": f in fields})
        i = j
    return out


def render(folded: list[dict]) -> str:
    out = []
    for i, s in enumerate(folded, 1):
        if s.get("过路"):
            out.append(f"{i}. [过路] 该方向还有子类细分 ｜ 到 {s['成员数']} 项")
        else:
            out.append(f"{i}. 按「{s['字段']}」筛选（{s['刀数']} 刀）｜ 到 {s['成员数']} 项")
    return "\n".join(out)


def verify(chain: dict, folded: list[dict], fields: set[str]) -> dict:
    """机械验收 M1–M4。"""
    steps = chain["路"][1:]
    old_fields = [field_of(s) for s in steps if field_of(s)]
    # M1 相干字段序列（去掉连续重复后）一致
    def seq(fs):
        out = []
        for f in fs:
            if not out or out[-1] != f:
                out.append(f)
        return out
    m1 = seq([f for f in old_fields if f in fields]) == \
        seq([x["字段"] for x in folded if x.get("相干")])
    # M2 末层成员一致
    m2 = (steps[-1]["成员数"] == folded[-1]["成员数"]) if folded else False
    # M3′ 折叠步数 ≤ 原步数；有同字段连续 >=2 刀的段时必须严格变短
    runs = 0
    i = 0
    while i < len(steps):
        f = field_of(steps[i])
        j = i
        while j < len(steps) and field_of(steps[j]) == f:
            j += 1
        if f and j - i >= 2:
            runs += 1
        i = j
    m3 = len(folded) <= len(steps) and (runs == 0 or len(folded) < len(steps))
    # M4′ 无信息增发：折叠版字段 ⊆ 原版字段；相干字段无一遗漏
    old_fields = {field_of(s) for s in steps if field_of(s)}
    new_fields = {x["字段"] for x in folded if x.get("字段")}
    rel_old = {f for f in old_fields if f in fields}
    rel_new = {x["字段"] for x in folded if x.get("相干")}
    m4 = new_fields <= old_fields and rel_old <= rel_new
    return {"M1_字段序列": m1, "M2_末层成员": m2,
            "M3_变短": m3, "M4_无增发": m4,
            "原步数": len(steps), "折叠步数": len(folded),
            "原字段序列": seq(old_fields), "折叠字段序列": seq([x["字段"] for x in folded if x.get("相干")])}


def main() -> int:
    sys.path.insert(0, str(REPO / "exp" / "m2"))
    from relca_probe import parse_card, build_corpus, REQS
    from ldv.core import declare as DECL
    import tempfile

    rows = parse_card()
    root = Path(tempfile.mkdtemp(prefix="m2fold-")) / "_data" / "self120"
    ok, bad = build_corpus(rows, root)
    app = E.App(root=str(root / "nodes"))
    k = app.kernels["keyset"]
    declared: set[str] = set()
    print(f"语料 {ok}｜签名 {app.fingerprint()}", flush=True)

    out = []
    all_m = []
    for name, req in REQS:
        cid = name.split()[0]
        miss = [x for x in req if x not in app.all_keys and x not in declared]
        for key in miss:
            DECL.add(root, key, why="V16 折叠原型", by="agent")
            declared.add(key)
        if miss:
            app = E.App(root=str(root / "nodes"))
            k = app.kernels["keyset"]
        r = app.query({"require": req})
        if not r.get("ok") or r.get("out_of_structure"):
            print(f"{name}: 跳过", flush=True)
            continue
        fields = {str(x).split("=")[0] for x in req}
        node = r["nodes"][0]                      # 单支需求取那支；多支各取第一支示例
        c = CH.chain_of(k, node["did"], require=req)
        folded = fold(c["路"], fields)
        v = verify(c, folded, fields)
        all_m += [v["M1_字段序列"], v["M2_末层成员"], v["M3_变短"], v["M4_无增发"]]
        out.append({"需求": name, "chain": c, "folded": folded, "verify": v})
        print(f"\n=== {name}（{v['原步数']} 步 → {v['折叠步数']} 步）===", flush=True)
        print(render(folded), flush=True)
        flags = [kk for kk in ("M1_字段序列", "M2_末层成员", "M3_变短", "M4_无增发")
                 if not v[kk]]
        if flags:
            print(f"  ✗ 未过: {flags}", flush=True)

    print(f"\n=== 机械验收 ===")
    print(f"M1-M4 全过: {all(all_m)}/{len(all_m)} 项 → {'✓ 通过' if all(all_m) else '✗ 有红项'}")
    outp = Path(__file__).with_name("fold_present.json")
    outp.write_text(json.dumps({"all_mech_pass": all(all_m), "detail": out},
                               ensure_ascii=False, indent=1, default=str),
                    encoding="utf-8", newline="\n")
    print(f"→ {outp}")
    return 0 if all(all_m) else 1


if __name__ == "__main__":
    raise SystemExit(main())
