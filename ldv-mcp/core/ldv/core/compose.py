"""组合算子（v0.1.58，v0.1.59 扩）：零件 A 经运算 R 得到什么 —— 「2+3」的那一步。

考卷实证（2026-11-03）：教了一二三四和加减乘除，问 2+3 全线不通——
谓词语言只有字段比较，没有「把两个已知数合成第三个数」的组合算子；
检索满分、组合零分。本层补的就是这一步，纪律与自问层同款：

- **依据逐字引用**：操作数的值来自语料节点字段（`数值: 2`），运算的
  **资格**来自语料节点（存在 `运算=加` 的节点 ⇒ 加法教过）——缺一样就弃答。
  解释器只自带四种运算的语义（同 `=` 代表相等一样的原语），不发明运算。
- **无证据必弃答**：操作数 0 命中 / 多命中 / 缺字段 / 非数值 / 除零 /
  运算没教 —— 每种都逐字说为什么，不编造、不猜。
- **只读**：不改树、不改账本、不落盘、不进缓冲。
- **I5 式可移除**：spec 不带 `derive`/`solve` ⇒ 查询行为与旧版逐字相同；
  删掉本模块与 `App.query` 里的几行接线，其余逐字回退。

v0.1.59 扩两件（遗留候选 1+2，用户"都教它吧"拍板）：
- **solve 未知数槽位**：`{"solve": {"运算": "+", "已知": "name=三", "side": "right",
  "得": 5, "字段": "数值"}}` —— 「几加三得五」问得进去了。已知操作数
  同样逐字引用，未知数按四则逆运算解出，回查语料找它的名字。
- **链式**：`derive` 的操作数可以是**嵌套 derive spec** —— 二加三加四
  表达为 `左 = {运算:+, 左:二, 右:三}`。嵌套的依据逐字上提，
  嵌套弃答则整链弃答。

v0.1.60 再扩（用户"遇到不认识的数字不会用数的组成自查吗"）：
- **数的组成自查**：操作数 `name=二十三` 被词汇表拒时，若名字能按
  数的组成**拆成已教的零件**（二十 + 三），自动拆、算、并把拆法写进
  依据——零件没教齐则**保留原拒**（不编造）。这是把已教的
  「数的组成 / 数位顺序」从知识变成拒答后的自救动作。

v0.1.61 再扩（用户指定机制：出现什么事，反查出对应的道德，依照标准作答）：
- **moral 反查**：`{"moral": {"事": "name=捡到"}}` —— 事节点带 `应照` 字段
  （教的时候写明这件事归哪条道德管），反查出道德节点，**引其标准原文**作答。
  事没教 → 拒；事教了但没对上道德 → 弃答"这件事还没有对上道德，要不要教"
  ——不现场发明道德，不越权裁决。

v0.1.62 再扩（用户"不显性指挥——让它用语文的分析能力，查到道德，做出反应"）：
- **分析流水线**：`{"分析": "name=捡到"}` —— 考官只摆事，不点科目。它自己
  走语文的分析三件：**复述骨架**（人物/行为/转折，读事节点字段）、
  **人物分析三问**（做了什么→为什么→说明什么人，逐字引字段）、
  **寓意**（教训/寓意字段）——分析的末端带 `应照` 字段，**反查到道德**，
  引标准原文，照标准**做出反应**。哪段分析没教，就如实缺那一段——不编。

v0.1.66 再扩（用户"补上散文和变形生成语法"）：
- **成文**：`{"成文": "name=X"}` —— 把分析流水线的产出**组装成段**：
  记叙段（谁/做了什么/后来怎样）→ 三问段 → 寓意段 → 反应段（引标准）。
  每一句带 `出自`（哪个字段），缺段如实缺——组装是语法，内容零发明。
- **变形**：`{"变形": {"句": ..., "式": "被字句"}}` —— 教过的句例
  （施事/受事/谓语三槽）按规矩互换：把字句 ⇄ 被字句；未教的句式拒。
- **反义**：`{"反义": "大"}` —— 查教过的**反义对**（正/反 两槽），
  双向可查；没教过它的对面 ⇒ 拒"要不要教一对，由你定"。

v0.1.67 再扩（用户"给它一个主题，让它生成一段句子"）：
- **主题成文**：`{"主题": {"题": "海上的夜", "词": ["name=月亮", ...]}}` ——
  主题没有现成的"事"时，从**概念库**里组景：主题的词逐个找到收它的
  概念节点（名字对上或正文首行含它），每句逐字引其正文首行，照
  "景在先、理在后"的次序组装；找不到料的词如实列在 `缺料` 里。
  组装是语法，内容零发明——它写不出没教过的字句，也不装会。

v0.1.69 再扩（用户"教他写句子"）：
- **造句**：`{"造句": {"词": "月亮", "式": "比喻句"}}` —— 句型模板
  （教过的：格式 + 槽位词类）+ **词卡**（教过的：词 + 词类）⇒ 组出
  **没教过的新句**：给的词按词类入槽，其余槽从同词类的词卡里取，
  每个词引用它的词卡。这正是它「造句」课的定义：拿一个词，排成
  自己的句子——词是教过的，句子是它自己的。
  词没词卡（不知词类）⇒ 拒"要不要教词卡"；同词类词卡不够填槽 ⇒
  拒"再教一个"；句式没教过 ⇒ 拒。

v0.1.70 再扩（用户"字典里有联想吗？有的话让它问之前自问一遍"）：
- **字典联想**：`{"联想": "光"}` —— 字典没有"联想"字段，但释义里
  藏着构词联想：**"～"记号**（"～亮。～芒。日～。"——把 ～ 换回
  字就是联想词）。通道把它提取出来，附释义头，逐条引出处。
- **问前自问**：造句撞上没词卡的词时，**先查一遍字典再开口**——
  字典有 ⇒ 拒答里带上「字典自问」的结果（释义 + 联想词），
  问就问得有据；字典也没有 ⇒ 照旧只说"没有"。
"""

from __future__ import annotations

from typing import Any, Callable

#: 支持的运算符 → 语料里的「运算」字段取值（教过哪个才能用哪个）
OPS: dict[str, str] = {"+": "加", "-": "减", "*": "乘", "/": "除"}

_CONTRACT = ("组合题的依据全部来自语料节点字段（操作数的数值）与已教的运算节点，"
             "本层只做计算与引用——没教过的运算不算，缺证据的操作数不猜。")
_DERIVE_KEYS = ("运算", "左", "右", "字段")


class _Refused(Exception):
    """弃答走异常上抛——嵌套链里任何一环弃答，整链如实弃答。"""

    def __init__(self, payload: dict[str, Any]):
        super().__init__(payload.get("弃答", ""))
        self.payload = payload


def _num(v: Any) -> float | None:
    """字段值 → 数值；非数值（含 bool）⇒ None。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def _refuse(why: str, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """弃答的统一形状：ok=False + 逐字原因 + 空依据 —— 不编造。"""
    out = {"ok": False, "弃答": why, "依据": [], "长说明": _CONTRACT}
    if extra:
        out.update(extra)
    return out


def _op_node(op: str, nodes: dict[str, Any]) -> Any | None:
    """运算资格：语料里得有教过这个运算的节点（`运算=加` ⇒ 加法教过）。"""
    op_cn = OPS[op]
    return next((n for n in nodes.values()
                 if str(n.fields.get("运算", "")) == op_cn), None)


def _intify(res: float) -> float | int:
    return int(res) if res == int(res) else res


def derive(spec: dict[str, Any], *, run_query: Callable[[dict], dict],
           nodes: dict[str, Any]) -> dict[str, Any]:
    """`spec = {"derive": {"运算": "+", "左": "name=二", "右": "name=三", "字段": "数值"}}`。

    左/右操作数：**字符串谓词**（一次真实查询，恰好命中 1 个节点）
    或**嵌套 derive spec**（链式：二加三加四）。算完拿算得值直接扫
    字段回查语料——命中就列出，没命中就如实说「这个数还没有名字」。
    """
    bad = sorted(set(spec) - set(_DERIVE_KEYS))
    if bad:
        return _refuse(f"derive 里不认识的键 {bad} —— 只认 {list(_DERIVE_KEYS)}")
    missing = [k for k in _DERIVE_KEYS if k not in spec]
    if missing:
        return _refuse(f"derive 缺键 {missing} —— 四个都要：运算符、左操作数、右操作数、取值字段")

    op, field = str(spec["运算"]), str(spec["字段"])
    if op not in OPS:
        return _refuse(f"不认识的运算符 {op!r} —— 只认 {sorted(OPS)}（加减乘除）")
    op_cn = OPS[op]

    # ── 运算资格：语料里得有教过这个运算的节点 ──────────────────────────
    op_node = _op_node(op, nodes)
    if op_node is None:
        return _refuse(f"语料里没有「运算={op_cn}」的节点 —— 这个运算没教过，不代算。"
                       f"（教了它，组合题才能用 {op}）")

    # ── 操作数：各一次真实查询（或嵌套 derive），恰好一个值 ─────────────
    vals: list[float] = []
    based: list[dict[str, Any]] = [
        {"原文": f"运算资格：{op_node.id}（运算={op_cn}）",
         "可复算": f"语料里存在 运算={op_cn} 的节点"}]
    try:
        for side, operand in (("左", spec["左"]), ("右", spec["右"])):
            v, ev = _operand(side, operand, field, run_query, nodes)
            vals.append(v)
            based.extend(ev)
    except _Refused as rf:
        return rf.payload

    # ── 计算（除零要单独说）────────────────────────────────────────────
    a, b = vals
    if op == "/" and b == 0:
        return _refuse(f"{a} ÷ 0 —— 除零没有定义，不编一个结果")
    res = a + b if op == "+" else a - b if op == "-" else a * b if op == "*" else a / b
    res = _intify(res)

    # ── 算得值回查语料：直接扫字段（结果未必有名字，如实报）────────────
    hits = [{"id": nid, "name": n.fields.get("name", "?")}
            for nid, n in sorted(nodes.items())
            if _num(n.fields.get(field)) is not None
            and abs(_num(n.fields.get(field)) - res) < 1e-9]

    return {"ok": True,
            "算式": f"{a} {op} {b} = {res}",
            "算得": res,
            "字段": field,
            "依据": based,
            "语料中算得值的节点": hits,
            "请求": None if hits else f"算出了 {res}，但语料里没有{field}={res} 的节点"
                                      f"——这个数还没有名字，要不要教",
            "长说明": _CONTRACT}


_TENS: list[tuple[str, int]] = [("九十", 90), ("八十", 80), ("七十", 70), ("六十", 60),
                                ("五十", 50), ("四十", 40), ("三十", 30), ("二十", 20), ("十", 10)]
_UNITS: dict[str, int] = {"一": 1, "二": 2, "三": 3, "四": 4,
                          "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _split_name(name: str) -> list[str]:
    """按数的组成拆中文数名（≤99，不含零与位级百千）：二十三 → [二十, 三]。"""
    for tok, _ in _TENS:
        if name == tok:
            return [name]
        if name.startswith(tok):
            rest = name[len(tok):]
            if rest and rest in _UNITS:
                return [tok, rest]
            if not rest:
                return [name]
            return []                      # 尾巴不是个位数词 ⇒ 不拆（如 三十块）
    if name in _UNITS:
        return [name]
    return []


def _decompose_name(name: str, field: str,
                    run_query: Callable[[dict], dict],
                    nodes: dict[str, Any]):
    """数的组成自查：把没教过的数名拆成已教零件，全部命中才返回 (值, 名, 依据)。

    任一零件没教 / 不可拆（含零、百千位）⇒ None —— **保留原拒，不编造**。
    """
    parts = _split_name(name)
    if not parts or len(parts) < 2:
        return None                        # 单零件 ⇒ 没有更细的组成可自查
    parts_sum = 0.0
    ev: list[dict[str, Any]] = []
    for p in parts:
        r = run_query({"require": [f"name={p}"]})
        ids = list(r.get("answer_ids") or []) if r.get("ok") else []
        if len(ids) != 1:
            return None                    # 零件没教 ⇒ 自查失败，保留原拒
        v = _num(nodes[ids[0]].fields.get(field))
        if v is None:
            return None
        parts_sum += v
        ev.append({"原文": f"零件 {ids[0]}（{p}）的 {field}={nodes[ids[0]].fields.get(field)}",
                   "可复算": f"查询 name={p} ⇒ 命中 {ids[0]}，读其 {field} 字段"})
    return parts_sum, name, ev


def _operand(side: str, operand: Any, field: str,
             run_query: Callable[[dict], dict],
             nodes: dict[str, Any]) -> tuple[float, list[dict[str, Any]]]:
    """解析一个操作数：字符串谓词 → 查询；dict → 嵌套 derive（链式）。

    返回 (数值, 依据条目列表)；证据不齐时抛 `_Refused`（整链如实弃答）。
    """
    if isinstance(operand, dict):
        # ── 链式：嵌套 derive，依据逐字上提，弃答逐字透传 ────────────────
        sub = derive(operand, run_query=run_query, nodes=nodes)
        if not sub.get("ok"):
            raise _Refused(_refuse(f"{side}操作数是一条嵌套组合，它自己问不进去 —— "
                                   f"{sub.get('弃答') or sub.get('error', '')}",
                                   extra={"依据": sub.get("依据", [])}))
        ev = [{"原文": f"{side}操作数是嵌套组合（{sub.get('算式')}）＝ {sub['算得']}",
               "可复算": "重跑该嵌套 derive，算得应相同"}] + list(sub.get("依据", []))
        return float(sub["算得"]), ev

    # ── 字符串谓词：一次真实查询，恰好命中 1 个 ─────────────────────────
    r = run_query({"require": [str(operand)]})
    if not r.get("ok"):
        # 数的组成自查（v0.1.60）：name=二十三 被拒 ⇒ 拆成已教零件再算
        s = str(operand)
        if s.startswith("name="):
            dc = _decompose_name(s[5:], field, run_query, nodes)
            if dc is not None:
                parts_sum, name_, ev = dc
                return parts_sum, [{"原文": f"`{s}` 不在词汇表 → 按数的组成自查："
                                        f"{name_} = {' + '.join(_split_name(name_))} = {parts_sum}",
                                "可复算": f"重跑部件查询 name={'、name='.join(_split_name(name_))}，"
                                          f"数值相加应得 {parts_sum}"}] + ev
        raise _Refused(_refuse(f"{side}操作数 `{operand}` 问不进去 —— {r.get('error', '')}",
                               extra={"need_predicate": r.get("need_predicate", []),
                                      "unknown_keys": r.get("unknown_keys", [])}))
    ids = list(r.get("answer_ids") or [])
    if len(ids) != 1:
        raise _Refused(_refuse(f"{side}操作数 `{operand}` 命中 {len(ids)} 个节点"
                               + (f"（{ids}）" if ids else "")
                               + " —— 组合需要恰好一个，不替你挑"))
    node = nodes[ids[0]]
    v = _num(node.fields.get(field))
    if v is None:
        got = node.fields.get(field, "（无此字段）")
        raise _Refused(_refuse(f"{side}操作数节点 {ids[0]} 的「{field}」不是数值"
                               f"（实际：{got!r}）—— 没法参与计算"))
    return v, [{"原文": f"{ids[0]}（{node.fields.get('name', '?')}）的 {field}={node.fields.get(field)}",
                "可复算": f"查询 `{operand}` ⇒ 命中 {ids[0]}，读其 {field} 字段"}]


def solve(spec: dict[str, Any], *, run_query: Callable[[dict], dict],
          nodes: dict[str, Any]) -> dict[str, Any]:
    """未知数槽位（v0.1.59）：「几加三得五」——方程的第一步。

    `spec = {"solve": {"运算": "+", "已知": "name=三", "side": "right",
                       "得": 5, "字段": "数值"}}`

    `已知` 是那个有名字的操作数（谓词串或嵌套 derive），`side` 说它站
    左边还是右边，`得` 是题目给定的结果值——未知数在另一头，按四则
    逆运算解出，再回查语料找它的名字。结果值是**人给的题设**，
    依据里逐字注明。
    """
    keys = ("运算", "已知", "side", "得", "字段")
    bad = sorted(set(spec) - set(keys))
    if bad:
        return _refuse(f"solve 里不认识的键 {bad} —— 只认 {list(keys)}")
    missing = [k for k in keys if k not in spec]
    if missing:
        return _refuse(f"solve 缺键 {missing} —— 五个都要：运算符、已知操作数、"
                       "它在哪一侧（side）、结果（得）、取值字段（字段）")

    op, field = str(spec["运算"]), str(spec["字段"])
    side = str(spec["side"])
    if op not in OPS:
        return _refuse(f"不认识的运算符 {op!r} —— 只认 {sorted(OPS)}（加减乘除）")
    if side not in ("左", "右"):
        return _refuse(f"side 只认 '左' / '右'（已知操作数站哪边），给的是 {side!r}")
    want = _num(spec["得"])
    if want is None:
        return _refuse(f"「得」不是数值（实际：{spec['得']!r}）—— 未知数要有确定的等式才解得动")
    op_cn = OPS[op]

    op_node = _op_node(op, nodes)
    if op_node is None:
        return _refuse(f"语料里没有「运算={op_cn}」的节点 —— 这个运算没教过，不代算。")

    try:
        v, ev = _operand("已知", spec["已知"], field, run_query, nodes)
    except _Refused as rf:
        rf.payload["弃答"] = f"已知操作数问不进去 —— {rf.payload['弃答']}"
        return rf.payload
    based = [{"原文": f"运算资格：{op_node.id}（运算={op_cn}）",
              "可复算": f"语料里存在 运算={op_cn} 的节点"},
             {"原文": f"题设：结果（得）= {want}",
              "可复算": "得 是题目给定的数，不是算出来的"}] + ev

    # ── 四则逆运算解未知数（除零/不唯一单独说）─────────────────────────
    if op == "+":
        unk = want - v
        expr = f"? {op} {v} = {want}" if side == "右" else f"{v} {op} ? = {want}"
    elif op == "-":
        if side == "左":          # 左 - ? = 得 → ? = 左 - 得
            unk, expr = v - want, f"{v} {op} ? = {want}"
        else:                     # ? - 右 = 得 → ? = 得 + 右
            unk, expr = want + v, f"? {op} {v} = {want}"
    elif op == "*":
        if v == 0:
            return _refuse(f"已知操作数是 0：? × 0 = {want} "
                           + ("有无数个解（得必须是 0）" if want == 0 else "无解")
                           + " —— 不替你挑")
        unk, expr = want / v, f"? {op} {v} = {want}"
    else:  # "/"
        if side == "左":          # 左 / ? = 得 → ? = 左 / 得
            if want == 0:
                return _refuse(f"{v} ÷ ? = 0：{v} ≠ 0 时无解，{v} = 0 时有无数个解 —— 不替你挑")
            unk, expr = v / want, f"{v} {op} ? = {want}"
        else:                     # ? / 右 = 得 → ? = 得 × 右
            unk, expr = want * v, f"? {op} {v} = {want}"
    unk = _intify(unk)

    hits = [{"id": nid, "name": n.fields.get("name", "?")}
            for nid, n in sorted(nodes.items())
            if _num(n.fields.get(field)) is not None
            and abs(_num(n.fields.get(field)) - unk) < 1e-9]

    return {"ok": True,
            "算式": expr,
            "解得": unk,
            "字段": field,
            "依据": based,
            "语料中解得值的节点": hits,
            "请求": None if hits else f"解出了 {unk}，但语料里没有{field}={unk} 的节点"
                                      f"——这个数还没有名字，要不要教",
            "长说明": "未知数按四则逆运算解出；结果值是题设，操作数值来自语料节点字段，"
                      "运算资格来自已教的运算节点——没教过的不算，缺证据的不猜。"}


def moral(spec: dict[str, Any], *, run_query: Callable[[dict], dict],
          nodes: dict[str, Any]) -> dict[str, Any]:
    """道德反查（v0.1.61）→ 分析流水线（v0.1.62）。

    `spec = {"moral": {"事": "name=捡到"}}` 或 `{"分析": "name=捡到"}`（不显性指挥：
    考官只摆事，不点科目）。

    流水线：**语文分析**（复述骨架 = 人物/行为/转折，读事节点字段；
    人物分析三问 = 做了什么/为什么/说明什么人，逐字引字段；寓意 = 教训/寓意
    字段）→ 末端的 `应照` 字段**反查到道德** → 引标准原文，照标准**做出反应**。
    哪段分析没教，如实缺那一段——不编。裁决权在人。
    """
    bad = sorted(set(spec) - {"事"})
    if bad:
        return _refuse(f"moral 里不认识的键 {bad} —— 只认 ['事']")
    if "事" not in spec:
        return _refuse("moral 缺键 ['事'] —— 要反查，先说出发生了什么事")

    r = run_query({"require": [str(spec["事"])]})
    if not r.get("ok"):
        return _refuse(f"这件事问不进去 —— {r.get('error', '')}",
                       extra={"need_predicate": r.get("need_predicate", []),
                              "unknown_keys": r.get("unknown_keys", [])})
    ids = list(r.get("answer_ids") or [])
    if len(ids) != 1:
        return _refuse(f"这件事命中 {len(ids)} 个节点 —— 反查需要恰好一件事，不替你挑")
    thing = nodes[ids[0]]
    thing_name = thing.fields.get("name", ids[0])

    m_name = thing.fields.get("应照")
    if not m_name:
        return _refuse(f"「{thing_name}」这件事还没有对上道德 —— 要不要教"
                       f"（教的时候写明 应照: 哪条道德）",
                       extra={"事": ids[0]})

    m_node = next((n for n in nodes.values()
                   if n.fields.get("name") == str(m_name)), None)
    if m_node is None:
        return _refuse(f"应照的道德「{m_name}」在语料里没有节点 —— 道德没教过，"
                       "不现场发明一条。")

    # ── 两条已教的标准在这件事上相撞 ⇒ 两难：摆盘，不裁决（v0.1.63）──────
    both = m_node.fields.get("标准两边")
    if both:
        names = both if isinstance(both, list) else [
            s.strip() for s in str(both).replace("，", ",").split(",")]
        stds: dict[str, Any] = {}
        for mn in names:
            mn_node = next((n for n in nodes.values()
                            if n.fields.get("name") == str(mn).strip()), None)
            if mn_node is None:
                return _refuse(f"两边之一的道德「{mn}」在语料里没有节点 —— "
                               "道德没教过，不现场发明一条。")
            stds[str(mn).strip()] = {"节点": mn_node.id,
                                     "标准原文": mn_node.body.strip()}
        xiang = next((n for n in nodes.values()
                      if n.fields.get("name") == "两难的想路"), None)
        return {"ok": True,
                "事": ids[0],
                "事由": thing_name,
                "两难": True,
                "相撞的标准": stds,
                "想路": xiang.body.strip() if xiang else None,
                "裁决": "裁决在人——两条都是已教的标准，本层只摆盘，不举筷。",
                "依据": [{"原文": f"事：{ids[0]}（{thing_name}），应照：{m_name}（两难）",
                          "可复算": f"查询 {spec['事']} ⇒ 命中 {ids[0]}，其 应照 指向两难实例"},
                         {"原文": f"两条标准逐字引自：{'、'.join(v['节点'] for v in stds.values())}",
                          "可复算": "按各自节点正文即得，无一出自本层"}],
                "长说明": "两条都是已教的标准——撞上的是标准与标准。本层只摆出"
                          "两边原文与想路，裁决在人。"}
    standard = m_node.body.strip()

    # ── 语文分析三件（字段是教的，本层只逐字搬运成段）───────────────────
    f = thing.fields
    fany = lambda *ks: next((f[k] for k in ks if f.get(k) not in (None, "")), None)
    xu = fany("人物", "谁")
    xing = fany("行为", "发生了什么")
    zhuan = fany("转折", "缘由")
    yuyi = fany("寓意", "教训")
    fenxi: dict[str, Any] = {}
    if xu or xing or zhuan:
        fenxi["复述骨架"] = {"人物": xu, "行为": xing, "转折": zhuan}
    yuan = fany("缘由", "为什么")
    fenxi["人物分析三问"] = {
        "做了什么": xing,
        "后来怎样": zhuan,
        "为什么": yuan or f"照「{m_name}」的标准看",
        "说明什么人": yuyi or f"照「{m_name}」的标准衡量",}
    if yuyi:
        fenxi["寓意"] = yuyi

    return {"ok": True,
            "事": ids[0],
            "事由": thing_name,
            "分析": fenxi,
            "应照的道德": m_name,
            "道德节点": m_node.id,
            "道德标准": standard,
            "反应": f"依照「{m_name}」的标准：{standard.splitlines()[0]}",
            "依据": [{"原文": f"事：{ids[0]}（{thing_name}），应照：{m_name}",
                      "可复算": f"查询 {spec['事']} ⇒ 命中 {ids[0]}，其 应照 字段指向 {m_name}"},
                     {"原文": f"分析依据：人物/行为/转折/寓意 逐字引自 {ids[0]} 的字段",
                      "可复算": f"读 {ids[0]} 的字段即得，无一出自本层"},
                     {"原文": f"道德标准原文：{m_node.id}（{m_name}）",
                      "可复算": f"语料中存在 name={m_name} 的节点，标准即其正文"}],
            "长说明": "分析三件逐字引自事节点的字段（教的时候写好的），本层只搬运成段；"
                      "末端应照反查出道德，反应即引标准原文。裁决与执行在人——"
                      "摆盘的人不举筷。"}


def 反义(spec: str, *, run_query: Callable[[dict], dict],
         nodes: dict[str, Any]) -> dict[str, Any]:
    """反义通道（v0.1.66）：查教过的反义对，双向可查，不发明。"""
    word = str(spec).strip()
    if not word:
        return _refuse("反义缺词——要找对面，先说出是哪个词")
    pairs: list[tuple[str, str]] = []          # (正, 反, 节点)
    for n in nodes.values():
        f = getattr(n, "fields", {}) or {}
        zheng, fan = f.get("正"), f.get("反")
        if zheng and fan:
            pairs.append((str(zheng).strip(), str(fan).strip(), n.id))
    for zheng, fan, nid in pairs:
        if word == zheng:
            return {"ok": True, "词": word, "对面": fan, "对子节点": nid,
                    "依据": [{"原文": f"反义对 {nid}：正「{zheng}」反「{fan}」",
                              "可复算": f"读 {nid} 的 正/反 字段即得"}]}
        if word == fan:
            return {"ok": True, "词": word, "对面": zheng, "对子节点": nid,
                    "依据": [{"原文": f"反义对 {nid}：正「{zheng}」反「{fan}」",
                              "可复算": f"读 {nid} 的 正/反 字段即得"}]}
    return _refuse(f"「{word}」的对面没有教过——要不要教一对反义，由你定；我不代定。")


def 变形(spec: dict[str, Any], *, run_query: Callable[[dict], dict],
         nodes: dict[str, Any]) -> dict[str, Any]:
    """变形通道（v0.1.66）：教过的句例三槽互换（把字句 ⇄ 被字句）。"""
    bad = sorted(set(spec) - {"句", "式"})
    if bad:
        return _refuse(f"变形里不认识的键 {bad} —— 只认 ['句', '式']")
    sent = str(spec.get("句") or "").strip()
    shi = str(spec.get("式") or "被字句").strip()
    if not sent:
        return _refuse("变形缺句——要变，先给出原句")
    if shi not in ("被字句", "把字句"):
        return _refuse(f"式「{shi}」没教过 —— 现在只会把字句⇄被字句这一对，不硬掰别的")
    # 收教过的三槽句例（施事/受事/谓语/主动/被动 任一写法）
    triples: list[tuple[str, str, str, str, str]] = []   # (施,受,谓,主动,被动)
    for n in nodes.values():
        f = getattr(n, "fields", {}) or {}
        shou, shi2, wei = f.get("施事"), f.get("受事"), f.get("谓语")
        if shou and shi2 and wei:
            zhu = str(f.get("主动") or f"{shou}把{shi2}{wei}")
            bei = str(f.get("被动") or f"{shi2}被{shou}{wei}")
            triples.append((str(shou), str(shi2), str(wei), zhu, bei))
    if shi == "被字句":
        for shou, shi2, wei, zhu, bei in triples:
            if sent == zhu:
                return {"ok": True, "原句": sent, "变式": shi, "变句": bei,
                        "依据": [{"原文": f"句例：施事「{shou}」受事「{shi2}」谓语「{wei}」",
                                  "可复算": "按三槽规矩组装：受事+被+施事+谓语"}]}
            if sent == bei:
                return {"ok": True, "原句": sent, "变式": shi, "变句": zhu,
                        "依据": [{"原文": f"句例：施事「{shou}」受事「{shi2}」谓语「{wei}」",
                                  "可复算": "按三槽规矩组装：施事+把+受事+谓语"}]}
    return _refuse(f"「{sent}」的零件没有教全（施事/受事/谓语要对得上句例）——"
                   "要不要教这个句例，由你定；我不硬掰。")


def 成文(spec: dict[str, Any], *, run_query: Callable[[dict], dict],
         nodes: dict[str, Any]) -> dict[str, Any]:
    """成文通道（v0.1.66）：把分析产出组装成段——逐字引字段，缺段如实缺。"""
    base = moral({"事": spec.get("事")}, run_query=run_query, nodes=nodes)
    if not base.get("ok"):
        return base
    fx = base.get("分析", {})
    kuang = fx.get("复述骨架", {}) or {}
    san = fx.get("人物分析三问", {}) or {}
    std_first = str(base.get("道德标准") or "").strip().splitlines()[0]

    sentences: list[dict[str, str]] = []
    missing: list[str] = []
    if kuang.get("人物"):
        sentences.append({"句": f"{kuang['人物']}经历了一件事。", "出自": "字段「人物」"})
    else:
        missing.append("人物")
    if kuang.get("行为"):
        sentences.append({"句": f"事情是：{kuang['行为']}。", "出自": "字段「行为」"})
    else:
        missing.append("行为")
    if kuang.get("转折"):
        sentences.append({"句": f"后来，{kuang['转折']}。", "出自": "字段「转折」"})
    else:
        missing.append("转折")
    if san.get("做了什么"):
        sentences.append({"句": f"问：做了什么？答：{san['做了什么']}。", "出自": "三问·做了什么"})
    if fx.get("寓意"):
        sentences.append({"句": f"这件事的寓意：{fx['寓意']}。", "出自": "字段「寓意」"})
    else:
        missing.append("寓意")
    sentences.append({"句": f"依照「{base.get('应照的道德')}」的标准：{std_first}",
                      "出自": f"道德标准 {base.get('道德节点')}"})

    text = "".join(s["句"] for s in sentences)
    return {"ok": True, "事由": base.get("事由"), "成文": text,
            "句引": sentences,
            "缺段": missing or None,
            "长说明": "成文只做组装：每句逐字来自教过的字段与反查的标准，"
                      "句子次序照记叙的规矩（谁/做了什么/后来/寓意/反应）。"
                      "缺段如实缺——组装是语法，内容零发明。"}


def 主题(spec: dict[str, Any], *, run_query: Callable[[dict], dict],
         nodes: dict[str, Any]) -> dict[str, Any]:
    """主题成文（v0.1.67）：主题词逐个找收它的概念，逐字组段，零发明。"""
    bad = sorted(set(spec) - {"题", "词"})
    if bad:
        return _refuse(f"主题里不认识的键 {bad} —— 只认 ['题', '词']")
    title = str(spec.get("题") or "").strip()
    words = [str(w).strip() for w in (spec.get("词") or [])]
    if not title or not words:
        return _refuse("主题缺料——要成段，先给「题」和主题的「词」（词形 name=X）")
    picked: list[tuple[str, str, str]] = []     # (nid, name, 首句)
    used: set[str] = set()
    miss: list[str] = []
    for tok in words:
        w = tok[5:] if tok.startswith("name=") else tok
        hit = None
        for n in nodes.values():
            f = getattr(n, "fields", {}) or {}
            nm = str(f.get("name") or "")
            body = str(getattr(n, "body", "") or "").strip()
            first = (body.split("。")[0] + "。") if "。" in body else \
                    (body.splitlines()[0] if body else "")
            if w and (w == nm or w in nm or (first and w in first)) and n.id not in used:
                hit = (n.id, nm, first)
                break
        if hit:
            used.add(hit[0])
            picked.append(hit)
        else:
            miss.append(w)
    if len(picked) < 2:
        return _refuse(f"主题「{title}」下的料不够（只找到 {len(picked)} 条）——"
                       "先教相关的概念，由你定；我不凭空编景。")
    picked.sort(key=lambda t: 0 if nodes[t[0]].fields.get("type") == "自然" else 1)
    # 规程类节点取**全文**（v0.2.19）：审计规范/交付规范这类，条目（①②③、四个维度）
    # 都在首句之后——只取首句等于把"该做什么"丢掉，只剩"这是件什么事"。
    _VERB = {"审计规范", "交付规范", "调用规程", "接口规范", "查询语法",
             "流程规程", "流程禁止", "应用规范", "审计目标"}
    sentences = []
    for nid, nm, first in picked:
        _doc = str((nodes[nid].fields or {}).get("文档类") or "")
        _body = str(getattr(nodes[nid], "body", "") or "").strip()
        _txt = _body if (_doc in _VERB and _body) else first
        sentences.append({"句": _txt if _txt.endswith("。") else _txt + "。",
                          "出自": f"课程 {nid}（{nm}）"})
    text = "".join(s["句"] for s in sentences)
    return {"ok": True, "主题": title, "成文": text, "句引": sentences,
            "缺料": miss or None,
            "长说明": "主题成文只做组装：每句逐字来自教过的课程正文，"
                      "次序照「景在先、理在后」；规程类（文档类=审计/交付/调用规范）"
                      "取全文，条目一条不落。写不出没教过的字句，也不装会。"}


def 造句(spec: dict[str, Any], *, run_query: Callable[[dict], dict],
         nodes: dict[str, Any], semnet: Any = None) -> dict[str, Any]:
    """造句通道（v0.1.69）：句型模板 + 词卡 ⇒ 组出教过的词的新句子。

    v0.1.72：槽选择从"字典序"升级为"语义相邻"——锚词与其余各词
    必须在语义网（字典构词衍生）上可达（≤2 步），凑不出畅通组合
    就如实拒：能组成一句话，要语义畅通，不只是词形和模板。
    """
    bad = sorted(set(spec) - {"词", "式"})
    if bad:
        return _refuse(f"造句里不认识的键 {bad} —— 只认 ['词', '式']")
    word = str(spec.get("词") or "").strip()
    shi = str(spec.get("式") or "比喻句").strip()
    if not word:
        return _refuse("造句缺词——拿一个词来，才排得出句子")
    # 收教过的模板
    tpl = next((n for n in nodes.values()
                if getattr(n, "fields", {}).get("句型") == shi), None)
    if tpl is None:
        return _refuse(f"句式「{shi}」没教过——教它的格式与槽位，我再排；不硬编。")
    fmt = str(tpl.fields.get("格式") or "")
    raw = tpl.fields.get("槽") or {}
    if isinstance(raw, str):                       # 节点字段是标量：串槽 "名1=名词,形=形容词"
        slots = {}
        for part in raw.replace("，", ",").split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                slots[k.strip()] = v.strip()
    else:
        slots = dict(raw)
    if not fmt or not slots:
        return _refuse(f"模板「{shi}」缺「格式」或「槽」——教全了我才排。")
    # 收词卡（词 → 词类）
    cards: dict[str, str] = {}
    for n in nodes.values():
        f = getattr(n, "fields", {}) or {}
        w, c = f.get("词"), f.get("词类")
        if w and c:
            cards.setdefault(str(w).strip(), str(c).strip())
    wc = cards.get(word)
    if wc is None:
        return _refuse(f"「{word}」还没有词卡（不知道它的词类）——"
                       "要不要教一张词卡（词+词类），由你定；我不代定。")
    # 该词类必须在模板的槽里
    slot_names = [k for k, v in slots.items() if str(v).strip() == wc]
    if not slot_names:
        return _refuse(f"「{word}」是{wc}，但句式「{shi}」没有{wc}的槽——换一句式或教新模板。")
    # 其余槽从同词类词卡里取（确定性：字典序第一且不与已填重复）
    def _chars(w: str) -> list[str]:
        return [c for c in w if "\u4e00" <= c <= "\u9fff"]

    def _rank(cands: list[str]) -> list[tuple[str, Any]]:
        """候选按与锚词的语义距离排：共字0步 < 1步 < 2步 < 不通（最后）。"""
        if semnet is None:
            return [(c, None) for c in sorted(cands)]
        ca = _chars(word)
        scored = []
        for c in sorted(cands):
            r = semnet(_chars(c))
            scored.append((c, r))
        scored.sort(key=lambda t: (t[1] is None, len(t[1][0]) if t[1] else 9))
        return scored

    fill: dict[str, str] = {}
    used = {word}
    srcs: list[dict[str, str]] = []
    sem_basis: list[str] = []
    for k, v in slots.items():
        v = str(v).strip()
        if k in slot_names and v == wc and k == slot_names[0]:
            fill[k] = word                      # 给的词入第一个同词类槽
            srcs.append({"槽": k, "词": word, "出自": f"词卡「{word}」（{wc}）"})
            continue
        cands = [w for w, c in cards.items() if c == v and w not in used]
        if not cands:
            return _refuse(f"槽「{k}」要{v}，词卡里没有可用的——再教一张{v}词卡。")
        placed = False
        for cand, res in _rank(cands):
            if semnet is not None and res is None:
                continue                        # 语义不通的候选直接跳过
            fill[k] = cand
            used.add(cand)
            srcs.append({"槽": k, "词": cand, "出自": f"词卡「{cand}」（{v}）"})
            if semnet is not None:
                _path, _basis = res
                if _basis:
                    sem_basis.append(f"{word}—{cand}：" + " → ".join(_basis))
            placed = True
            break
        if not placed:
            return _refuse(f"槽「{k}」的候选都和「{word}」语义不通——"
                           "能成一句话要语义畅通，不只是词类对上。"
                           "再教相关的词，或换个词。")
    sent = fmt.format(**fill)
    out = {"ok": True, "词": word, "句式": shi, "句子": sent,
            "填槽": srcs,
            "依据": [{"原文": f"句型模板「{shi}」：{fmt}",
                      "可复算": f"读模板节点的 格式/槽 字段，按词卡词类填槽即得"}] + [
                     {"原文": f"{s['槽']} ← {s['词']}（{s['出自']}）"} for s in srcs],
            "长说明": "造句 = 教过的模板 + 教过词类的词卡 + 语义畅通验证。"
                      "词是教过的，语义是从字典衍生的，组合是它自己的。"}
    if sem_basis:
        out["语义畅通"] = sem_basis
    return out


def _dict_associate(entry: dict, word: str) -> list[str]:
    """从字典条目的释义里提取"～"构词联想：～亮 → 光亮；日～ → 日光。"""
    import re as _re
    exp = str(entry.get("explanation") or "")
    out: list[str] = []
    for m in _re.finditer(r"[^，。；：、！？（）()\s\u2460-\u2473]*～[^，。；：、！？（）()\s]*", exp):
        run = m.group(0)
        if len(run) <= 6:
            w = run.replace("～", word)
            if w != word and w not in out:
                out.append(w)
    return out[:8]
