#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E4：ldv 失效边界——三类材料（PREREG_E4）。

两臂：
  M-ldv  流式：60 段逐段 remember（键值由模型自定），收尾 orient+ask ≤6 轮 → pairs
  B-full 裸：60 段 + 收尾指令一次在窗，单轮 → pairs

材料：每任务 24 truth 对（类1/2/3 各 8 对）+ 36 填充段 = 〔S01…S60〕。
类 3 的 8 个改写对词面在 CLASS3_PAIRS 写死（PREREG_E4 §2 人工等价表），
跨 run 不变；类 1/2 模板值随 seed 变化。

    python exp/e4.py --runs 1 --arm b-full --tasks 1   # 试点
    python exp/e4.py --runs 1,2,3                      # 全量
"""
from __future__ import annotations

import itertools
import argparse
import json
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm           # noqa: E402
import score         # noqa: E402
from ldv_client import LdvSession  # noqa: E402

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"
MCP_DIR = HERE.parent / "ldv-mcp"
STORE = HERE.parent / "e4_store2"   # 全新 store（e4_store 已被污染批次毒化）

FINAL_INSTR = ("以下是若干编号陈述。请找出所有【互相矛盾的陈述对】或"
               "【描述同一事实的陈述对】，输出 JSON："
               '{"pairs":[{"ids":["Sxx","Syy"],"kind":"矛盾|重复"}]}。'
               "只输出 JSON。")

# ── 类 3 人工等价表（PREREG_E4 §2：逐条预注册，词面不同、语义等价）────────
CLASS3_PAIRS = [
    ("研究团队在 3 月的简报中确认，新疫苗的三期临床有效率达到了 91%。",
     "根据三月公布的数据，该疫苗在最后阶段试验中对症状性感染的保护率为九成以上。"),
    ("内部纪要显示，数据中心二期工程的总投资约为 47 亿元。",
     "第二座数据中心的建设预算被核定在将近 48 亿元以内。"),
    ("公司发言人证实，新款车型的续航里程为 720 公里。",
     "发布会上公布的官方续驶数据是七百二十公里。"),
    ("年报披露，去年海外市场的营业收入为 3.4 亿美元。",
     "去年来自境外业务的销售总额在 3.4 亿美元上下。"),
    ("论文第一作者表示，样本规模共计 12000 名受试者。",
     "这项研究累计入组了约 1.2 万名参与者。"),
    ("气象记录显示，该市 7 月极端最高气温为 41.6 摄氏度。",
     "七月间这座城市测得的温度峰值接近 42 摄氏度。"),
    ("招标文件注明，项目工期为 18 个月。",
     "从动工到验收的建设周期大约一年半。"),
    ("财报电话会上给出，本季度活跃用户数为 8500 万。",
     "管理层透露该季度月度活跃用户在 8500 万左右。"),
]

# ── 类 1 模板：同一属性两个值，词面无共同键 ─────────────────────────────
CLASS1_TEMPLATES = [
    ("项目组测算新电池的样机一次充满可支撑车辆行驶 {a} 公里。",
     "有评审质疑其宣传的约 {b} 公里的续驶水平过于乐观。"),
    ("内部审计发现该部门去年的实际支出约为 {a} 万元。",
     "此前对外通报的同类项目花费是 {b} 万元。"),
    ("实验室测得样品的纯度为 {a}%。",
     "供应商的技术规格书标称含量 {b}%。"),
    (" stackholder 会纪要记录新厂区规划面积为 {a} 公顷。",
     "早期公示的用地规模约 {b} 公顷。"),
    ("工程日志显示桥梁合拢误差为 {a} 毫米。",
     "监理单位报备的偏差值是 {b} 毫米。"),
    ("调研报告称该应用日均活跃用户约 {a} 万。",
     "另一份行业白皮书估计其日活规模 {b} 万。"),
    ("观测站记录到此次地震震源深度 {a} 公里。",
     "速报目录给出的深度值为 {b} 公里。"),
    ("药检报告显示有效成分含量为每片 {a} 毫克。",
     "说明书标示每片含 {b} 毫克。"),
]

# ── 类 2 模板：跨实体方向反转 ──────────────────────────────────────────
CLASS2_TEMPLATES = [
    ("{A} 团队声称其方法比 {B} 团队快 {a}%。",
     "{B} 团队的论文称自己比 {A} 团队快 {b}%。"),
    ("{A} 公司宣布其芯片算力领先 {B} 公司 {a}%。",
     "{B} 公司发布的数据显示其算力反超 {A} 公司 {b}%。"),
    ("按 {A} 机构的统计，其市占率高出 {B} 机构 {a} 个百分点。",
     "{B} 机构的报告则称自己领先 {A} 机构 {b} 个百分点。"),
    ("{A} 实验室报告其模型准确率比 {B} 实验室高 {a}%。",
     "{B} 实验室的对照结果认为自己高出 {A} 实验室 {b}%。"),
    ("{A} 国公布其出口额比 {B} 国多 {a}%。",
     "{B} 国海关数据显示自己对 {A} 国保持 {b}% 的顺差优势。"),
    ("{A} 平台宣称其留存率比 {B} 平台高 {a}%。",
     "{B} 平台的运营月报称其留存反超 {A} 平台 {b}%。"),
    ("{A} 研究所测得材料强度优于 {B} 研究所 {a}%。",
     "{B} 研究所的复测报告认为自己比 {A} 研究所高 {b}%。"),
    ("{A} 医院公布其手术成功率高出 {B} 医院 {a}%。",
     "{B} 医院的年度总结称自己比 {A} 医院高 {b}%。"),
]

ENTITIES = ["昆仑", "天枢", "青鸾", "北辰", "启明", "玄武", "朱雀", "白泽",
            "凌云", "望舒", "扶摇", "鸿蒙", "沧溟", "曦和", "烛龙", "应龙",
            "姑射", "若木", "羲车", "建木"]


def build_material(run: int) -> dict:
    rng = random.Random(4000 + run)
    sents: list[tuple[str, str, str]] = []      # (oid, text, role)  role: t1/t2/t3/fill
    pairs_by_cat: dict[str, list[dict]] = {}
    _oid = itertools.count()

    def nid() -> str:
        return f"O{next(_oid):02d}"             # 占位 id，最后随机映射为 S 编号

    # 类 1：8 对，词面不同值不同（truth 在构造时记录，不依赖 shuffle 后顺序）
    for i, (ta, tb) in enumerate(CLASS1_TEMPLATES):
        a, b = rng.sample(range(2, 60), 2)
        id1, id2 = nid(), None
        sents.append((id1, ta.format(a=a), "t1"))
        id2 = nid()
        sents.append((id2, tb.format(b=b), "t1"))
        pairs_by_cat.setdefault("t1", []).append({"a": id1, "b": id2})
    # 类 2：8 对
    ents = rng.sample(ENTITIES, 16)
    for i, (ta, tb) in enumerate(CLASS2_TEMPLATES):
        A, B = ents[2 * i], ents[2 * i + 1]
        a, b = rng.sample(range(5, 45), 2)
        id1, id2 = nid(), None
        sents.append((id1, ta.format(A=A, B=B, a=a), "t2"))
        id2 = nid()
        sents.append((id2, tb.format(A=A, B=B, b=b), "t2"))
        pairs_by_cat.setdefault("t2", []).append({"a": id1, "b": id2})
    # 类 3：8 对（词面固定）
    for ta, tb in CLASS3_PAIRS:
        id1, id2 = nid(), None
        sents.append((id1, ta, "t3"))
        id2 = nid()
        sents.append((id2, tb, "t3"))
        pairs_by_cat.setdefault("t3", []).append({"a": id1, "b": id2})
    # 填充：无关事实句 36 段
    fill_pool = [
        "本季度共组织了 {n} 次内部培训。", "新员工入职流程已更新至第 {n} 版。",
        "机房空调巡检周期调整为每 {n} 天一次。", "差旅报销单据须在 {n} 日内提交。",
        "图书馆新增图书 {n} 册。", "园区班车时刻表自本月起微调 {n} 分钟。",
        "会议室预约系统本月升级了 {n} 次。", "食堂本月推出 {n} 种新菜品。",
        "年度体检安排在第 {n} 周。", "门禁系统升级耗时约 {n} 小时。",
        "本月收到客户反馈 {n} 条。", "官网改版项目已进行到第 {n} 阶段。",
        "库存盘点差异率为 {n}%。", "新签合同共 {n} 份。",
        "设备保养计划覆盖 {n} 台仪器。", "内部征文活动收到稿件 {n} 篇。",
        "安全演练共 {n} 次达标。", "外包人员目前在册 {n} 人。",
        "本月水电费环比下降 {n}%。", "团建活动报名人数为 {n} 人。",
        "数据备份验证通过率为 {n}%。", "热线接通率维持在 {n}%。",
        "新招聘计划包含 {n} 个岗位。", "培训教室翻新预算 {n} 万元。",
        "供应商评审完成 {n} 家。", "环保自查未发现 {n} 类问题。",
        "员工满意度调研回收问卷 {n} 份。", "专利申请本周新增 {n} 件。",
        "运维工单平均响应 {n} 分钟。", "档案数字化已完成 {n}%。",
        "消防通道检查覆盖 {n} 个楼层。", "信息系统等保测评通过第 {n} 级。",
        "物流合作商季度考核 {n} 家合格。", "年会节目征集到 {n} 个。",
        "办公用品采购批次共 {n} 次。", "对外捐赠总额为 {n} 万元。",
    ]
    fills = rng.sample(fill_pool, 36)
    for f in fills:
        sents.append((nid(), f.format(n=rng.randint(2, 99)), "fill"))
    rng.shuffle(sents)                            # 段落顺序打乱（对子不保证相邻）
    # 占位 id → 随机 S 编号（防"相邻 id 即真理对"的可利用线索）
    oids = [sid for sid, _, _ in sents]
    mapping = dict(zip(oids, rng.sample([f"S{i:02d}" for i in range(1, len(sents) + 1)],
                                        len(sents))))
    lines = [f"〔{mapping[sid]}〕{text}" for sid, text, _ in sents]
    truth = {cat: [{"a": mapping[p["a"]], "b": mapping[p["b"]]} for p in plist]
             for cat, plist in pairs_by_cat.items()}
    return {"lines": lines, "truth": truth,
            "n_pairs": sum(len(v) for v in truth.values())}


# ── M-ldv 臂 ─────────────────────────────────────────────────────────
REMEMBER_INSTR = ('请为下面这条陈述生成一条记忆（JSON）：{{"title":"简短标题",'
                  '"text":"要记住的事实要点（保留全部数值与实体名）"}}。'
                  '只输出 JSON。\n\n{sent}')


def run_mldv(mat: dict, run: int, sess: LdvSession,
             corpus: str = "e4") -> tuple[str, dict]:
    meta = {"remember_n": 0, "remember_err": 0, "tool_rounds": 0,
            "keys_seen": [], "ctx_tokens": 0}
    # 1) 流式逐段 remember（模型自定内容；工具自定键）
    for i, line in enumerate(mat["lines"]):
        sent = line  # 〔Sxx〕text
        r = llm.chat([{"role": "user", "content": REMEMBER_INSTR.format(sent=sent)}],
                     max_tokens=2048, temperature=0, tries=4)
        # 清洗 lone surrogate（deepseek JSON 偶发 \udXXX 转义 → server encode 崩）
        txt = re.sub(r"[\ud800-\udfff]", "",
                     r.get("text") or "", flags=re.UNICODE)
        m = re.search(r"\{.*\}", txt, re.DOTALL)
        if not m:
            meta["remember_err"] += 1
            continue
        try:
            obj = json.loads(m.group(0))
        except Exception:                          # noqa: BLE001
            meta["remember_err"] += 1
            continue
        meta["keys_seen"].append(str(obj.get("title", ""))[:40])
        try:
            sess.recycle()   # 单次调用隔离：server 长跑会随机僵住（E4 实测）
            sess.call("remember", {"corpus": corpus,
                                   "id": f"s{sid_of(line)}",
                                   "fields": {"type": "陈述",
                                              "title": str(obj.get("title", ""))[:60],
                                              "sid": sid_of(line)},
                                   "body": f"〔{sid_of(line)}〕{obj.get('text', '')}",
                                   "actor": "agent",
                                   "session": f"e4-r{run}"})
            meta["remember_n"] += 1
        except Exception as exc:                   # noqa: BLE001
            if meta["remember_err"] == 0:
                meta["remember_err_msg"] = str(exc)[:200]
            meta["remember_err"] += 1
    # 2) 收尾：orient → ask ≤6 轮 → final
    msgs = [{"role": "system", "content":
             "你是检查员。记忆结构里存着一批编号陈述的要点（type=陈述，正文含〔Sxx〕编号）。"
             "用 ask 取回：{\"tool\":\"ask\",\"args\":{\"require\":[\"type=陈述\"],\"with_body\":true}} "
             "可分批取回全部要点（返回含 n_answer 与条目）；也可用 require 按标题关键词缩小范围。"
             "取回后两两比对，找矛盾对与同一事实的改写对，"
             '最后只输出 JSON {"pairs":[{"ids":["Sxx","Syy"],"kind":"矛盾|重复"}]}。'}]
    try:
        sess.recycle()       # 单次调用隔离（同 remember）
        orient = sess.call("orient", {"corpus": corpus, "session": f"e4-r{run}"})
    except Exception as exc:                       # noqa: BLE001
        orient = f"工具错误：{exc}"
    msgs.append({"role": "user", "content":
                 f"你的记忆结构目录：\n{str(orient)[:6000]}\n\n"
                 "要检查的 84 条编号陈述【不在本消息里】——它们已经逐条存进了"
                 "你的记忆结构（每条 type=陈述，正文以〔Sxx〕开头）。"
                 "请先用 ask 工具把要点全部取回（如 require=[\"type=陈述\"] 且 "
                 "with_body=true），将取回的要点两两比对：找出互相矛盾的对、"
                 "以及描述同一事实的改写对（同一事实两种说法=重复，不是矛盾）。"
                 "比对完成后再输出答案。\n\n"
                 f"最终答案格式：{FINAL_INSTR}"})
    final = None
    usage = {"in": 0, "out": 0, "cached": 0}
    for rnd in range(6):
        r = llm.chat(msgs, max_tokens=8000, temperature=0, tries=4)
        u = r.get("usage") or {}
        usage["in"] += u.get("in", 0)
        usage["out"] += u.get("out", 0)
        m = re.search(r"\{.*\}", r.get("text") or "", re.DOTALL)
        obj = json.loads(m.group(0)) if m else {}
        if "pairs" in obj:
            final = json.dumps(obj, ensure_ascii=False)
            break
        tool = obj.get("tool")
        if not tool:
            msgs.append({"role": "user", "content":
                         '请输出 {"tool":"ask","args":{...}} 或直接给 pairs。'})
            continue
        meta["tool_rounds"] += 1
        try:
            sess.recycle()   # 单次调用隔离（同 remember）
            res = sess.call(str(tool), {"corpus": corpus,
                                        **(obj.get("args") or {}),
                                        "session": f"e4-r{run}"})
        except Exception as exc:                   # noqa: BLE001
            res = f"工具错误：{exc}"
        msgs.append({"role": "assistant", "content": (r.get("text") or "")[:500]})
        msgs.append({"role": "user", "content":
                     f"工具返回：\n{str(res)[:30000]}\n\n继续。"
                     f"（还剩 {5 - rnd} 轮，之后必须给 pairs）"})
    if final is None:
        r = llm.chat(msgs + [{"role": "user", "content": "最后一轮：立即输出 pairs JSON。"}],
                     max_tokens=8000, temperature=0, tries=4)
        m = re.search(r"\{.*\}", r.get("text") or "", re.DOTALL)
        final = m.group(0) if m else ""
    meta["_usage"] = usage
    meta["finish"] = "stop" if final and '"pairs"' in final else "no_pairs"
    return final or "", meta


def sid_of(line: str) -> str:
    m = re.match(r"〔(S\d+)〕", line)
    return m.group(1) if m else "S00"


# ── B-full 臂 ────────────────────────────────────────────────────────
def run_bfull(mat: dict, run: int) -> tuple[str, dict]:
    text = "\n".join(mat["lines"])
    r = llm.chat([{"role": "user", "content": f"{text}\n\n{FINAL_INSTR}"}],
                 max_tokens=16000, temperature=0, tries=6)
    u = r.get("usage") or {}
    meta = {"finish": r.get("finish"),
            "_usage": {"in": u.get("in", 0), "out": u.get("out", 0),
                       "cached": u.get("cached", 0)}}
    return r.get("text") or "", meta


# ── 判分：分类型 recall/precision ────────────────────────────────────
def judge(raw: str, mat: dict) -> dict:
    pairs, format_ok = score.extract_pairs(raw)
    out = {"reported": len(pairs), "format_ok": format_ok}
    for cat, truth in mat["truth"].items():
        tset = {(t["a"], t["b"]) for t in truth}
        hit = 0
        rep = []
        for p in pairs:
            ids = p.get("ids") or []
            if len(ids) != 2:
                continue
            key = tuple(sorted(str(x) for x in ids))
            rep.append(key)
            if key in tset or (key[1], key[0]) in tset:
                hit += 1
        out[f"{cat}_recall"] = round(hit / len(truth), 3) if truth else None
        out[f"{cat}_precision"] = (round(hit / len(rep), 3) if rep else 0.0)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="1,2,3")
    ap.add_argument("--tasks", type=int, default=1)
    ap.add_argument("--arms", default="M-ldv,B-full")
    ap.add_argument("--model", default="deepseek-flash")
    a = ap.parse_args()
    llm.set_model(a.model)

    tag = f"E4-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    RUNS.mkdir(exist_ok=True)
    STORE.mkdir(exist_ok=True)
    (STORE / "_data" / "e4" / "nodes").mkdir(parents=True, exist_ok=True)
    out_path = RUNS / f"{tag}.jsonl"

    with out_path.open("a", encoding="utf-8") as fh:
      for run in [int(x) for x in a.runs.split(",") if x.strip()]:
        # 每 run 独立 corpus + 独立 LdvSession（E4 卡死复盘：server 僵住后
        # 复用 session 会永久死锁；新建进程隔离故障），timeout 降 45s 快速失败。
        # corpus 名带批次时间戳：ldv 记忆只追加不覆写，撞上残留 id 会全批报废
        #（191814 卡死批留 39 条 ⇒ 204800 批全灭）。
        corpus = f"e4-r{run}-{tag.split('-')[-1]}{run}"
        (STORE / "_data" / corpus / "nodes").mkdir(parents=True, exist_ok=True)
        sess = LdvSession([sys.executable, "server.py"],
                          env={"LDV_MCP_STORE": str(STORE)},
                          cwd=str(MCP_DIR), timeout=45)
        try:
            mat = build_material(run)
            print(f"run{run}: {len(mat['lines'])} 段, "
                  f"{mat['n_pairs']} truth 对", flush=True)
            for arm in a.arms.split(","):
                arm = arm.strip()
                for t in range(1, a.tasks + 1):
                    t0 = time.perf_counter()
                    if arm == "M-ldv":
                        raw, meta = run_mldv(mat, run, sess, corpus)
                    else:
                        raw, meta = run_bfull(mat, run)
                    j = judge(raw, mat)
                    u = meta.pop("_usage", {})
                    rec = {"exp": "E4", "arm": arm, "run": run, "task_idx": t,
                           "model": a.model, "SCORER_VERSION": score.SCORER_VERSION,
                           "ts": datetime.now().isoformat(timespec="seconds"),
                           "raw_output": raw, "latency_s": round(time.perf_counter() - t0, 1),
                           "tokens": u, **meta, **j}
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    fh.flush()
                    print(f"{arm} r{run} t{t}: t1_R={j.get('t1_recall')} "
                          f"t2_R={j.get('t2_recall')} t3_R={j.get('t3_recall')} "
                          f"｜报 {j['reported']}｜fin={meta.get('finish')}", flush=True)
        finally:
            try:
                sess.close()
            except Exception:                          # noqa: BLE001
                pass
    print(f"\n完成 → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
