"""跨进程实验：从快照恢复的**半成品树**，查询答案与完整建树逐字相同吗？

跑法（cwd = repo）：python evidence/_probe_resume_query.py

三个**独立进程**（不是同进程造一个半成品再查）：
    A  build       只建根 → plan 两轮 → 存快照          （进程退出）
    B  query-lazy  从快照恢复 → 查询 → 输出答案指纹      （进程退出）
    C  query-full  完整建树 → 查询 → 输出答案指纹        （进程退出）

判据：B 与 C 的答案指纹相同、三态计数相同，且 A 之后的方向数 < C。

⚠️ 为什么必须跨进程：同进程里 `make_keyset_root` 造的半成品与快照恢复的半成品
   可能因为「共享内存里的同一份 Node」而表现一致；只有真从磁盘读回来，
   才证明**落盘的那份状态**足够重建出等价的查询结果。
"""
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

REQ = frozenset({"type=出度0"})          # 有区分度的条件（内置语料里非空）


def _plug_and_items():
    from ldv.checks._fixtures import items, load
    from ldv.plugins.keyset import KeysetPlugin

    loaded = load()
    if loaded is None:
        raise SystemExit("语料读不到")
    nodes, edges, _d = loaded
    return KeysetPlugin(), items(nodes), nodes, edges


def _paths(root: Path):
    from ldv.core import snapshot as S

    _p, its, _n, edges = _plug_and_items()
    fp = S.fingerprint(its, edges, {"方向函数": "keyset", "用途": "probe-续建"})
    return S.tree_path("keyset", fp, root), fp


def stage_build(root: str) -> int:
    from ldv.checks._fixtures import make_keyset_root
    from ldv.core import snapshot as S
    from ldv.core.kernel import Kernel
    from ldv.core.reflect import plan as _plan

    plug, its, nodes, _e = _plug_and_items()
    p, fp = _paths(Path(root))
    k = make_keyset_root(nodes)()
    r = _plan(k, rounds=2, per_round=3)
    S.save(k, plug, "keyset", fp, path=p, extra={"plan_tried": r["试过方向"]})
    print(json.dumps({"阶段": "build", "方向数": len(k.all_directions()),
                      "剩余待展开": r["剩余待展开"], "快照": p.name},
                     ensure_ascii=False))
    return 0


def stage_query(root: str, mode: str) -> int:
    from ldv.core import snapshot as S
    from ldv.core.kernel import Kernel, Query
    from ldv.checks._fixtures import build_keyset

    plug, its, nodes, _e = _plug_and_items()
    if mode == "lazy":
        p, fp = _paths(Path(root))
        k, rep = S.restore(Kernel, plug, its, p, fp)
        if k is None:
            print(json.dumps({"阶段": "query-lazy", "错误": rep}, ensure_ascii=False))
            return 1
        n_before = len(k.all_directions())
        src = f"快照恢复（{rep.get('结果')}）"
    else:
        k, _pl = build_keyset(nodes)
        n_before = len(k.all_directions())
        src = "完整建树"

    r = k.query(Query(ideal=frozenset(nodes), require=REQ))
    print(json.dumps({
        "阶段": f"query-{mode}", "来源": src,
        "方向数_查询前": n_before, "方向数_查询后": len(k.all_directions()),
        "状态": str(r.status), "计数": r.counts,
        "命中数": len(r.hit_items),
        "命中指纹": hashlib.sha256("|".join(sorted(r.hit_items)).encode()).hexdigest()[:16],
    }, ensure_ascii=False))
    return 0


def _run(*args: str) -> dict:
    out = subprocess.run([sys.executable, str(Path(__file__)), *args],
                         capture_output=True, text=True, encoding="utf-8",
                         cwd=str(REPO))
    if out.returncode != 0:
        print(f"子进程失败：{args}\n{out.stdout}\n{out.stderr}")
        raise SystemExit(1)
    return json.loads(out.stdout.strip().splitlines()[-1])


def main() -> int:
    if "--stage" in sys.argv:
        i = sys.argv.index("--stage")
        stage, root = sys.argv[i + 1], sys.argv[i + 2]
        if stage == "build":
            return stage_build(root)
        return stage_query(root, stage.replace("query-", ""))

    root = tempfile.mkdtemp(prefix="ldv_resume_")
    print("=" * 78)
    print("跨进程实验：半成品树（快照恢复）vs 完整建树 —— 查询答案是否相同")
    print(f"快照目录：{root}")
    print("=" * 78)

    a = _run("--stage", "build", root)
    b = _run("--stage", "query-lazy", root)
    c = _run("--stage", "query-full", root)

    for row in (a, b, c):
        print("  " + json.dumps(row, ensure_ascii=False))

    ok = (b["命中指纹"] == c["命中指纹"] and b["计数"] == c["计数"]
          and b["状态"] == c["状态"] and b["方向数_查询前"] < c["方向数_查询前"])
    print()
    print(f"  A 建半成品：{a['方向数']} 个方向（剩余待展开 {a['剩余待展开']}）")
    print(f"  B 恢复后查询前 {b['方向数_查询前']} → 查询后 {b['方向数_查询后']}"
          f"（查询自己展开了 {b['方向数_查询后'] - b['方向数_查询前']} 个）")
    print(f"  C 完整建树 {c['方向数_查询前']} 个方向")
    print(f"  答案指纹：B {b['命中指纹']} ｜ C {c['命中指纹']}")
    print(f"  三态计数：B {b['计数']} ｜ C {c['计数']}")
    print()
    print("  " + ("✅ 全过：跨进程恢复的半成品树，查询答案与完整建树逐字相同"
                  if ok else "❌ 不一致"))
    import shutil

    shutil.rmtree(root, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
