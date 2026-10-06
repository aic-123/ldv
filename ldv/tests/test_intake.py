"""外部语料（公开数据集切片）接进来之后的**验收**。

    跑法：LDV_CORPUS=openalex-citations python -m ldv.tests.test_intake
    或：  python -m ldv.tests.test_intake          （自动按名字找 `_data/`）
    冻结红基线：python -m ldv.tests.test_intake --write-red-baseline

数据不在 ⇒ **报「跳过」、单独计数**，退出码 0。**跳过 ≠ 通过。**

---

## 这份料要证明什么（**判据先写、再跑** —— 与 `tools/intake_openalex.py` 同一句话）

    ① 两条**互不相干**的数法给出同一组 `(节点数, 图边数)`；
       且 `节点数 >= 3000`、`图边数 >= 节点数`（内部引用密度 ≥ 1.0）
    ② `run_checks`（**判据全跑、贵的探针不跑**）的红集合 == **本语料上冻结的**基线
       （新增红 ⇒ 失败；**基线条目在本次没红 ⇒ 也失败**，说明基线过期；
        **基线为空 ⇒ 也失败**，那说明守卫空转）
    ③ `B17` 若报「跳过」，必须同时给出「等价类全是单点」的**实测计数**

拿不准的量（假阳率 / 叶容量 / 进步量 / 耗时）**只披露、不判过**。

⚠️ 全量（3907 项）跑 ② 要**二十分钟级**（`B8` n 次插入 + `B15` 3 次整建），
   所以默认只在 ≤ `CHEAP_N` 项上跑，全量要 `LDV_INTAKE_CHECKS=1`。
   小切片上跑时记得同时下调 ① 的门槛：`LDV_INTAKE_MIN_NODES=200`。

---

## ★ 原判据 ② 是「红数为 0」—— **实测被证伪**，而且证伪得有价值

在 281 项子切片上 `run_checks` 报**红 1**：`B17`（`reach`）。
这**不是**新缺陷，是设计文档 `§10 留白` 早就记下的那一条在**真实语料上**露了出来。
设计文档原话（§10 留白表 + §7.2）：

> `reach` 在**合环图**上会把互相可达的两项劈成两个**覆盖完全相同**的子方向。
> **真实语料是 DAG，所以这个缺口在语料上不露**

⇒ 公开数据集**证伪了最后半句**：OpenAlex 的引用图**有环**（论文互引），
   281 项切片里有 **6** 个大小 ≥2 的强连通分量 ⇒ 缺口露了，
   而 `B17` 报的「被拆开的等价类」**正好 6 个**。

⇒ 所以判据 ② 改成**基线守卫**（与 `B18` 同一形状）：

    基线红集合（冻结）  本次红集合
    ────────────────────────────────
    新增的               ⇒ **红**（真出了新问题）
    基线里有、本次没有的  ⇒ **红**（基线过期 —— 缺口被修了却没更新基线）
    两边都是空的          ⇒ **红**（守卫空转 —— 它什么都没守，不算通过）

把「红数为 0」写成判据，等于**要求一条已知的缺口消失**；
而把基线写死不再核对，等于**让缺口悄悄变成常态**。两边都不行。

⚠️ **基线按语料指纹分开**（与 `B18` 同一条纪律，那个坑刚踩过）：
   `openalex-small` 上 `sequence` 维护路径漏 **183**，而 `B18` 的基线是在 36 项上冻的
   **16** —— 不按语料分开的话，「换了语料」会被报成「新增违规」。

---

## 两条数法为什么算「互不相干」

    路 1  走 **markdown 往返**：`load_nodes()` 读 `nodes/*.md`，`load_edges()` 数边
    路 2  走 **原始 JSON**：直接读 `raw/*.json`（OpenAlex 的原始响应），自己数

两条路**取的源不同**（一份是转换器写出来的文本，一份是数据源本身）。
转换器漏写一个节点、漏写一条边、或者把 `relations` 写成别的形状 ——
两条路立刻对不上。**共用同一个表达式**的数法做不到这件事。

## 耗时：为什么 ② 用 `--no-probes` 而**不用** `--cap`

先分清两类东西（`run_checks` 的模块 docstring 有完整推导）：

    判据（进退出码）   B1–B19。贵的只有 `B15`（3 次整建）与 `B8`（n 次插入）
    探针（只报不判）   十个度量。贵的是 `规范重建`（O(n) 次重建 × 每次 O(n)）

实测（281 项语料）：

    方向       整趟      其中 `规范重建`   `--no-probes` 之后
    ────────────────────────────────────────────────────
    keyset     27.4 s      26.96 s             ~0.4 s
    reach     450.8 s     446.54 s             ~4.3 s
    三个方向合计 ~780 s     ~774 s            **6.05 s**

⇒ 所以 ② 用 `--no-probes`：**判据一条不少地全跑**（`B17` 那条红照样露），
   只把贵的**探针**关掉。**关探针是安全的** —— 度量不进退出码，
   关掉它**不可能把红变成绿**；报告里会写明哪几行未跑。

⚠️ **`--cap` 在这里用不了**，因为它**连判据一起截**：
   实测 `openalex-citations` 的环**全落在第 400 项之后**（前 400 项 SCC ≥2 的组 = **0**），
   所以 `--cap 160` 上 `B17` 那条红**根本不会出现** ——
   而基线若是照那次冻的，就会是空集，守卫**空转**。
   ⇒ 见 `red_baseline_path`：**基线为空就报红**（「空转不许算通过」）。

⚠️ `--cap` 换的是范围，不是结论：本趟没覆盖到的项，本趟什么也没说。
   基线文件里**记着 cap**，换了 cap 必须重冻。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ldv.corpus.loader import load_edges, load_nodes  # noqa: E402

NAME = "openalex-citations"

#: ① 的规模门槛。**全量切片是 3000**；跑**子切片**（`--cocite-k 5` 那种）时按需下调 ——
#: 门槛写在环境变量里而不是写死，是为了让「我这次跑的是子切片」**看得见**。
MIN_NODES = int(os.environ.get("LDV_INTAKE_MIN_NODES", "3000"))

#: ② 在多大的语料上**默认**跑。`--no-probes` 之后，281 项只要 6 s；
#: 但 3907 项上 `B8`（n 次插入）+ `B15`（3 次整建）仍是**二十分钟级** ——
#: 所以全量要显式打开（`LDV_INTAKE_CHECKS=1`）。
#: ⚠️ 这里**不用** `--cap`：它连判据一起截，会把 `B17` 那条红藏掉（见模块开头）。
CHEAP_N = int(os.environ.get("LDV_INTAKE_CHEAP_N", "400"))

PASS: list[str] = []
FAIL: list[str] = []
SKIP: list[str] = []
INFO: list[str] = []


def ok(what: str, cond: bool, why: str = "") -> None:
    (PASS if cond else FAIL).append(what + ("" if cond else f"  ← {why}"))


def corpus_dir() -> Path | None:
    """找外部语料目录。

        `LDV_CORPUS`（**路径或名字**）→ `_data/<名字>/nodes`（仓库旁，再上一级）
        没设 → `_data/<NAME>/nodes`

    ⚠️ 与 `_fixtures.find_corpus` **同一套优先级**：外部语料既可以指路径也可以指名字，
       两边必须一样，否则「测试跑的是哪份料」和「检查跑的是哪份料」会悄悄分叉。
    """
    root = Path(__file__).resolve().parents[2]
    names = [os.environ["LDV_CORPUS"]] if os.environ.get("LDV_CORPUS") else [NAME]
    for name in names:
        p = Path(name)
        if p.is_dir() and any(p.glob("*.md")):
            return p
        for base in (root, root.parent):
            d = base / "_data" / name / "nodes"
            if d.is_dir() and any(d.glob("*.md")):
                return d
    return None


def count_via_markdown(nodes_dir: Path) -> tuple[int, int]:
    """**路 1**：走仓库自己的装载器（markdown 往返）。"""
    nodes = load_nodes(nodes_dir)
    edges, _ = load_edges(nodes)
    return len(nodes), sum(len(v) for v in edges.values())


def count_via_raw(raw_dir: Path) -> tuple[int, int, dict]:
    """**路 2**：直接读原始 JSON，自己数。**不碰 markdown，也不碰 `loader`。**"""
    recs: dict[str, dict] = {}
    files = sorted(raw_dir.glob("*.json"))
    for f in files:
        for r in json.loads(f.read_text(encoding="utf-8")):
            recs[r["id"].rsplit("/", 1)[-1]] = r
    ids = set(recs)
    n_edges = 0
    dropped = 0
    for wid, r in recs.items():
        refs = {x.rsplit("/", 1)[-1] for x in r.get("referenced_works") or []}
        n_edges += len(refs & ids)
        dropped += len(refs) - len(refs & ids)
    return len(recs), n_edges, {"原始 JSON 文件数": len(files), "丢弃外部引用": dropped}


def parse_reds(out: str) -> list[str]:
    """从 `run_checks` 的输出里抽**红集合**，形如 `B17|reach`。

    ⚠️ 必须带**方向**：同一条编号在不同方向上可以一红一绿
       （`B17` 在 `keyset` 上绿、在 `reach` 上红）—— 只记编号会把两者混成一个。
    """
    reds: list[str] = []
    plugin = "?"
    for line in out.splitlines():
        m = re.match(r"═══ 插件 (\S+) ═══", line)
        if m:
            plugin = m.group(1)
            continue
        m = re.match(r"\s*\[红\] (B\d+)\b", line)
        if m:
            reds.append(f"{m.group(1)}|{plugin}")
    return sorted(reds)


def red_baseline_path(nodes_dir: Path) -> Path:
    """红基线**跟着外部语料走**（不进仓库 —— 外部数据不进仓库，它的读数也不进）。"""
    return nodes_dir.parent / "expected_reds.json"


def run_checks_red(nodes_dir: Path, cap: int = 0) -> tuple[list[str] | None, str]:
    """**②** 在新语料上真跑一遍流程 C（子进程 —— 验的是**真入口**，不是它的替身）。

    带 `--no-probes`：**判据全跑，贵的探针不跑**（见模块开头「耗时」）。
    探针不进退出码 ⇒ 关掉它不可能把红变成绿。
    """
    env = dict(os.environ)
    env["LDV_CORPUS"] = str(nodes_dir)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    cmd = [sys.executable, "-m", "ldv.run_checks", "--no-probes"]
    if cap > 0:
        cmd += ["--cap", str(cap)]
    r = subprocess.run(cmd, capture_output=True, env=env,
                       cwd=str(Path(__file__).resolve().parents[2]))
    out = r.stdout.decode("utf-8", "replace")
    if not re.search(r"断言 (\d+) 条：红 (\d+)，跳过 (\d+)", out):
        return None, out
    return parse_reds(out), out


def corpus_fingerprint(nodes_dir: Path) -> dict[str, int]:
    """与 `checks/coverage.py: corpus_fingerprint` 同口径 —— 基线**必须按语料分开**。

    同一条红在不同语料上可以是「已知缺口」也可以是「新缺陷」；
    基线不按语料分开，两者就长得一模一样（`B18` 刚踩过这个坑，见 `coverage.py`）。
    """
    nodes = load_nodes(nodes_dir)
    edges, _ = load_edges(nodes)
    return {"项数": len(nodes), "边数": sum(len(v) for v in edges.values())}


def main() -> int:
    print("═══ 外部语料验收：公开数据集切片 ═══")
    d = corpus_dir()
    if d is None:
        SKIP.append(f"外部语料 `_data/{NAME}/`（按 `tools/intake_openalex.py` 取）")
        print(f"  ⊘ 跳过：没找到 `_data/{NAME}/nodes`")
        print("     —— **跳过不等于通过**。取法见 ldv/tools/intake_openalex.py")
        print(f"\n═══ 过 {len(PASS)} · 红 {len(FAIL)} · **跳过 {len(SKIP)}** ═══")
        return 0

    print(f"  语料 {d}")
    raw = d.parent / "raw"

    # ── ① 两条独立数法 ────────────────────────────────────────────────────
    n1, e1 = count_via_markdown(d)
    n2, e2, extra = count_via_raw(raw)
    print(f"  路 1（markdown 往返）：节点 {n1} · 边 {e1}")
    print(f"  路 2（原始 JSON）    ：节点 {n2} · 边 {e2}   {extra}")
    ok("① 两条独立数法给出同一个节点数", n1 == n2, f"{n1} vs {n2}")
    ok("① 两条独立数法给出同一个边数", e1 == e2, f"{e1} vs {e2}")
    ok(f"① 节点数 ≥ {MIN_NODES}", n1 >= MIN_NODES, f"只有 {n1}")
    ok("① 图边数 ≥ 节点数（内部引用密度 ≥ 1.0）", e1 >= n1, f"{e1} < {n1}")

    # ── 反空跑自检：先把**各分支的原始计数**摆出来，再谈别的 ────────────────
    mf = d.parent / "MANIFEST.json"
    if mf.exists():
        man = json.loads(mf.read_text(encoding="utf-8"))
        print(f"  MANIFEST：type {len(man['type 取值'])} 值 · "
              f"source.kind {len(man['source.kind 取值'])} 值 · "
              f"evidence_status {len(man['evidence_status 取值'])} 值 · "
              f"丢弃外部引用 {man['丢弃的外部引用数']}")
        for k in ("type 取值", "source.kind 取值", "evidence_status 取值"):
            ok(f"① 枚举轴 `{k}` 至少两个取值（一个取值的轴不是轴，是噪声）",
               len(man[k]) >= 2, f"{man[k]}")

    # ── ③ 键集方向的**分辨率下界**（只披露，不判过）──────────────────────
    nodes = load_nodes(d)
    classes = {n.keys for n in nodes.values()}
    biggest = max(sum(1 for m in nodes.values() if m.keys == k) for k in classes)
    INFO.append(f"键词表 {len(set().union(*(n.keys for n in nodes.values())))} · "
                f"等价类 {len(classes)} · 最大等价类 **{biggest}**"
                f"（占语料 {biggest / len(nodes):.1%}）")
    print(f"  {INFO[-1]}")
    print("     ⚠️ 这是**披露**：它说明这个键词表在这份语料上最多只能分到这一层。")

    # ── ② `run_checks` 红集合 vs 冻结基线 ─────────────────────────────────
    if not (os.environ.get("LDV_INTAKE_CHECKS") == "1" or n1 <= CHEAP_N):
        SKIP.append(f"② `run_checks` 红集合（{n1} 项 —— `B8`+`B15` 是二十分钟级；"
                    f"`LDV_INTAKE_CHECKS=1` 打开）")
        print(f"  ⊘ 跳过：② 在 {n1} 项上要二十分钟级（`B8` n 次插入 + `B15` 3 次整建），"
              f"`LDV_INTAKE_CHECKS=1` 打开")
    else:
        print("  跑 `run_checks --no-probes`（判据全跑，贵的探针不跑）…", flush=True)
        reds, out = run_checks_red(d)
        if reds is None:
            ok("② `run_checks` 跑得出汇总", False, out[-400:])
        else:
            for line in out.splitlines():
                if line.startswith("  [红]") or line.startswith("  [跳]") \
                        or line.startswith("  断言") or "跳过的是" in line:
                    print("    " + line.strip())
            print(f"    红集合：{reds or '（空）'}")
            fp = corpus_fingerprint(d)
            bp = red_baseline_path(d)
            if "--write-red-baseline" in sys.argv:
                bp.write_text(json.dumps(
                    {"语料": fp, "cap": 0, "no_probes": True, "reds": reds},
                    ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(f"    已冻结红基线 → {bp}")
                INFO.append(f"红基线已写入 {bp}（{fp}，{len(reds)} 条红）")
            elif not bp.exists():
                ok("② 红基线存在（先跑一次 `--write-red-baseline`）", False,
                   f"{bp} 不存在 —— 没有基线就没有「新增」这个概念")
            else:
                base = json.loads(bp.read_text(encoding="utf-8"))
                if base.get("语料") != fp:
                    ok("② 基线是**本语料**上冻的（换了语料必须重冻）", False,
                       f"基线 {base.get('语料')} vs 现在 {fp}")
                else:
                    new = sorted(set(reds) - set(base["reds"]))
                    gone = sorted(set(base["reds"]) - set(reds))
                    ok("② 没有**新增**红（基线守卫）", not new, f"新增 {new}")
                    ok("② 没有**失效**的基线条目（缺口被修了却没更新基线）",
                       not gone, f"基线里有、本次没红：{gone}")
                    ok("② 基线**不是空的**（空基线 ⇒ 守卫空转，不许算通过）",
                       bool(base["reds"]),
                       "基线红集合为空 ⇒ 本趟范围里这条守卫什么都没守")

        # ③ `B17` 若跳过，必须给出实测计数
        if "跳过的是" in out:
            ok("③ `B17` 报「跳过」时，同一次输出里有「等价类全是单点」的实测计数",
               ("单点" in out) or ("等价类" in out),
               "报了跳过但没说为什么 —— 「跳过」与「通过」就分不开了")

    print(f"\n═══ 过 {len(PASS)} · 红 {len(FAIL)} · **跳过 {len(SKIP)}** ═══")
    for f in FAIL:
        print(f"  ✗ {f}")
    for s in SKIP:
        print(f"  ⊘ {s}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
