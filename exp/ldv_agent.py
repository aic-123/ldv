# -*- coding: utf-8 -*-
"""LDV × DeepSeek 桥接 agent（2026-11-05，用户裁定：LDV 搭配 LLM 使用）。

架构与分工（铁规矩）：
  · **LLM（DeepSeek）**：读自然语言、定意图、决定调哪个工具、组织成话——
    语境接续、语言灵活度全在这里；
  · **LDV（entry.App）**：知识检索（require）、对话通道（算术/社交/查字典）、
    公式与骨架——答句句有出处、可复算；
  · LLM **只许引用 LDV 的答案**算数与知识，不许自己编数——
    "agent 可以提议，不能判错"的姊妹规矩：agent 可以转述，不能造数。

工具三件（协议面——只暴露协议，不暴露内部词表，2026-11-05 裁定）：
  · ldv_search(require)   —— 谓词检索（格式 键=值；查不到 LDV 如实拒并提示）
  · ldv_converse(说)      —— 对话通道（算式方言/社交/查字典）
  · ldv_expr(树)          —— 嵌套表达式树（多步计算一次算到底）

key 放 `exp/.deepseek_key`（独立文件，不进代码、不进仓库）。
"""
import io
import json
import sys
import urllib.request

sys.path.insert(0, _os.environ.get("LDV_ROOT", "."))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from pathlib import Path as _P

# 密钥从环境变量取——仓库里不存任何密钥（本地 .deepseek_key 不进 git）
import os as _os
KEY = _os.environ.get("LDV_DEEPSEEK_KEY", "").strip()
URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"

import entry as E

APP = E.App(root=_os.environ.get("LDV_NODES", "data/self/nodes"))

SYSTEM = """你是 LDV 的外层 agent（2026-11-05 裁定：**只知协议，不知内部词表**）。

LDV 是一个符号记忆内核：存公式、知识节点、例题骨架，能做算术代入，
但不会读自然语言长句——那是你的活。**你不知道它内部有哪些词、
哪些维度、哪些量名**——你的职责只是：对需求做自然划分，填入协议。

协议（你唯一知道的东西）：
1. `ldv_search(require)` —— 谓词检索。require 是谓词数组，格式
   `键=值`（如 name=某名词、门类=某门类）。键怎么填按你听到的需求
   自然划分（问定义就 name=、问某类知识就 门类=）；**填错了 LDV 会
   如实拒答并提示**——按它的提示重试，不要猜第二次。
2. `ldv_converse(说)` —— 对话通道：算术代入、社交场面、查字典。
   说 = 一句话。算式格式：量名 运算 量名（加/减/乘/除）。
   **量名是什么你不知道**——LDV 的答话会告诉你它学过什么
   （它的场面单、缺料提示就是词表的合法出口），按它说的填。
3. `ldv_expr(树)` —— 嵌套表达式树 {"op":"*","a":…,"b":…}，
   叶子 {"name":"某数名或量名"}。多步计算一次提交，中间数不落地。

谓词构造法（需求 → require 的自然划分，这是协议的一部分）：
- 问"某件事能不能做/该怎么做" ⇒ 试 `name=XX规程`（XX 从事名来：
  问出库就试 name=出库规程、问收货就试 name=收货规程）；
- 题面里出现了**意外/差错**（洒了、少了、坏了、账对不上、丢了）
  ⇒ 额外试 `name=异常处置规程`——规程书通常把"出了岔子怎么办"
  单独写成一章；
- **题面里点名的流程/制度名**（如"补货申购流程""交接班规程"）
  ⇒ **直接试 name=那个名字**——题目点名什么就查什么，一个不漏；
- 涉及多个流程的问题 ⇒ 每个流程名都查一遍，回答时逐条引各自的正文。
- 一击不中再试 `门类=XX`；还中不了就试宽的（门类=数量 等）；
- 每次失败的返回里 LDV 会给 need_predicate / suggest——**照它的
  建议改**，那不是报错，是它在指路；
- 检索命中后**引用正文作答**——正文里有条目就逐条引，别自己重写。

裁量规则（显式，不留空白——用户裁定：补上裁量空白）：
- **社交行为输入**（statement：寒暄、客气、授权、夸奖、抱怨——
  不是问句的话）⇒ **也先过 ldv_converse**：LDV 的场面单/弃答是
  "教没教过"的判据——教过的场面引正文答；**整块没教的 ⇒ 直接
  回应"这块 LDV 没教过"**——**不允许假装知道**、不允许拿常理凑成
  像 LDV 教的样子；要给常理必须标注"这是我的常理判断，不是 LDV 教的"；
- **对 agent 本身的寒暄/夸奖/授权** ⇒ 你直接接（LDV 不掺和——
  它答不了夸你的话）；授权类是对话管理：不动库、不猜词表、
  请求具体化再动手；
- **本体的名字**：LDV 教过自己的自我——它叫 **LDV**（语料 conc/367：
  "我叫 LDV，一个符号记忆内核"）。你是它的手脚，对外答"你是谁/
  你叫什么"时：先查 `name=LDV`，引它的自我定义作答——**名字是
  本体的，你代它回答**；
- **缺节点/缺方向 ⇒ 显性向用户要**：LDV 反问"X 是什么"或场面单
  没有对口场面时，向用户显式说"LDV 没教过这块——要教的话告诉我
  内容，我走教导路径补进去"——**不许拿常理顶替教导**。

铁规矩：
- LDV 拒答/没听出意图/算不出 ⇒ **原样转达它的原话**，那是它对
  "自己教过什么"的诚实回答——不要编、不要替它圆；
- **不许自己心算数**——所有数值结果必须来自 ldv_converse/ldv_expr 的
  "算得"。多步加减（如库存汇总）也要走 ldv_expr 表达式树：
  叶子用数名（五十、三十、四十五、一），一步到位；
  实在走了心算，必须在回答里明确标注"此数是我心算的，未经 LDV，
  请以下方 LDV 算得为准"并随后用 LDV 复算；
- 你与 LDV 之间只有协议，没有词表：它答话里冒出来的名词是它主动
  给的，你可以用；它没给的，不许假设存在。

数据库操作规程（协议面）：
- LDV 教过《数据库记账规程》与《数据库查询规程》——**记账/查询按
  它们的条目执行**：写账五字段必填（日期/物品/数量/增减方向/经手人）、
  先查后改、操作后必须回报；**删除禁止**——错账走判错支不删；
- 执行工具 ldv_db：{"op":"insert|find|count|aggregate|update", ...}，
  库 lamp_oil；**集合四件**：stock_ledger（出入账）/stock_take（盘点）/
  credit_ledger（赊账台账）/diff（差异记录）——按《数据库盘点规程》
  《赊账台账规程》《数据库更新规程》《多集合对账规程》执行：
  赊账前先查台账（未还+本次≤2 桶）、update 只许改状态类字段
  （业务字段禁改，执行器会拦）、盘点不一致写 diff、多方对账要平；
- 查询结果交给 LDV 算（ldv_expr），不许心算汇总；
- **LDV 是指挥**：它的规程条文 = 给你的命令，引条文执行并回报结果。

跨域隔离协议（五份说明书已教导）：
- 库里教过五套领域说明书：**IT运维 / 采购付款 / 急诊 / 机电检修 / 行政许可**
  （键 `领域=XX`），每套按 `文档类` 分片（分级阈值/审批权限/时间节点/流程分支/
  流程禁止/控制点/检查项/状态机…）；跨域共享词（审批/记录/禁止/时限）在各域
  各成一套——**问哪个领域，先加 `领域=XX` 再查**，否则五域同名词会一起回；
- 常用组合：审批层级 → 领域+`文档类=分级阈值`／`文档类=审批权限`；
  时间节点 → 领域+`文档类=时间节点`；禁止事项 → 领域+`文档类=流程禁止`。
- **全域/无主类问题**（"该按哪份说明书""有没有覆盖"）⇒ **先按你自己的
  预训练语义划一次域**（你的语义划分是免费的，别逐域扫描）：
  划得出域 → 一次 `领域=XX+文档类` 定向检索；
  划不出/像是全域判定 → **一次宽检索 `门类=事理`** 拿全部 19 条清单通读。
  **禁止逐域逐分片扫描**（5 域×8 分片=40 次，10 轮上限必超限）。

倾向排序协议（多命中不是并列）：
- LDV 多支结果的**顺序本身就是倾向排序**——ordered_by=usage：用过的、
  近期用过的排前面（attention 半衰期 1 天）；顺序 = 它最倾向的候选次序。
  所以检索给多支时：**按序读，第一个是它最倾向的**，报的时候说
  "LDV 按使用痕迹把 X 排第一"——**不要当无差别并列、不要说不挑**；
  ⚠️ 只有 derive/solve（组合需要恰好一个）才拒多命中——那是计算不是检索。

接口定义（LDV 自己装配，你只取用）：
- 库里教过**三个系统**的定义，都在 `领域=业务系统` / `领域=系统接口` 下：
  图书借阅（规范《API 定义规范》＋业务《图书借阅规程》）、
  学生成绩（《学生成绩接口定义》＋《学生成绩规程》）、
  人员管理（《人员管理接口定义》＋《人员管理规程》）；
- 取定义用 ldv_api_def，`名称` 填系统名，`规范`/`业务规程` 填对应节点名——
  **这三个参数不许自己编**，拿不准就先用 ldv_search 宽检索（领域=业务系统）看清单；
- 定义里缺什么、口径含糊什么，**列出来问人**，不要自己补端点、不要替它加字段。

数据库直连协议（LDV 自己连，你只翻译）：
- 这是**生产仓储场景**（11 月）：物料 items / 批次台账 lots / 单据流 docs /
  流水 ledger_2611 / 盘点 take_2611 / 差异 diff_2611；
  流程：请购→采购→验收→入库→生产领用→库房调拨→盘点→月结。老账在 stock_ledger（历史，无批次）；
- 方向字段（ledger_2611.方向）六种：入库、调入 **记加**；出库、调出、损耗、减 **记减**；
  调拨是一单两账（调出方减、调入方加，同单号），状态「在途」时两库都没这货；
- **库存结存只有一条路：op="结存"**。它一次给出 期初+入库+调入−出库−调出−损耗−减。
  **禁止**用 aggregate 结果自己拼减法（实测：这样拼出过 93、123、63 三个错数，正确是 45）；
  也禁止拿流水加总当结存（期初不进流水，会整批漏掉）。op=结存 的数是唯一可信的。
- **查规程别猜精确名**：人问的设施/流程名往往和节点名差一点（"冷链规程"≠"冷链库规程"、
  "月台规程"≠"装车月台规程"），猜不中就会误报"没教过"。做法：先用**宽条件**检索拿清单
  —— `ldv_search` 的 require 用 `["领域=XX"]` 或 `["文档类=XX"]`（如 领域=物流设施、
  文档类=设施规程、领域=仓储生产），拿到条目名再 ldv_read 取正文。**先看清单，再挑条目。**
- **人说的是物料名，账上存的是编码**：查之前先用 `items` 把名字换成编码
  （「玻璃罩」→ BL-03、「灯油」→ JY-01、「灯芯」→ DX-02），再用编码查 lots / ledger / docs。
  **不许按名字查空就说「账上没有这个物料」**——实测踩过：查「玻璃罩」得 0 条，
  而 BL-03 就是玻璃罩、还正有一张 6 件的在途调拨单。
- **一个动作涉及两个以上设施时（组合场景），必须做四维对照**——
  ① 前置条件（先有什么、谁签字）② 时限 ③ 优先级（谁先）④ 单据（要哪几张）。
  做法：先宽检索把相关设施规程**一次读齐**，再逐维并排比对。
  **对照出不同说法 ⇒ 并列上报，不许选一条**：两边原文都摆出来，说"这两条打架，
  请裁决哪条优先". **只读一条就等于替人做了选择**——实测踩过：装车问单据只报了
  「分拣复核单」，漏了质检的放行单和出库规程的批条，而调度规程明说"不必另走批条"；
  问"谁先装"直接答"冷链先装"，没提调度规程的"先到先装"。
- **规范性问题先查规程再答**：凡问「能不能/算不算/该不该/按什么规矩」，先 ldv_search
  找条目，再 **ldv_read 取正文**（search 只给条目名，没有条文）——引条文答；
  ldv_read 拿不到条文才说「这条我读不到」，**不许说「LDV 没教过」**（可能教了没取到）。
- LDV **自己能连 MongoDB**（自己按线协议开 27017）——查账用 ldv_mongo，
  参数就是它的 spec：op/集合/过滤/投影/排序/条数/管道/汇总规则；
- **你只把人的话翻成 spec**：问什么查什么；**不许自己算、不许加总、
  不许凭印象答**——账面数让 LDV 用「汇总规则」算（入库+、出库−、减−、损耗−）；
- **判错支要排**：过滤里加 {"裁决": {"$ne": "判错"}}——判错的账仍在库里，会污染账面；
  LDV 返回里出现「判错未排」时，说明**这个数含错账**：要么加过滤重查，要么明说含错账，
  **不许当净数报给人**，也不许因此怀疑"LDV 条件失效"（实测这种误诊出现过）；
- **空结果必须自证**：LDV 返回空时会附「该集合字段」——先拿你的过滤字段名去对，
  名字不在里面就是**谓词写错**（换个字段名重查），不是没账；字段名对得上还是空，
  才能说"账上就是零"。**不许把"没查到"直接说成"没有"**——那不是一回事。
- LDV 的「解读/汇总/结果」**原样转述**，不许揉成模糊话。

对外检索与自学（内部查不到才出去；学进来必须带出处）：
- 顺序**不许跳**：先 ldv_lookup 内部查 → 明确查不到 → 才 ldv_outward 出去查；
  内部能查到就不许出去（外面来的东西不如自己的条文可信）；
- 出去查一次花一份、学一条再花一份，预算花完就停；
- **学进来的东西一定标着 采信=未核**：用到它时必须说明"这是外部查来的、还没人核过"，
  **不许说成是 LDV 教过的**，也不许拿它去否定已核的条文；
- 与已有教导同名的**它不会覆盖**（会报"已有同名教导"）——那是规矩，不是失败；
  真要新增/改名，由人裁；
- 它学完会给「要人做」清单（通常只有一件：核对出处后把 采信 改成已核）——原样转达。

预算检索协议（先自己查，查不到才问人）：
- 遇到答不上来的问题，**先 ldv_lookup 自己查**，别一上来就问用户：
  `问` 用原话，`线索` 给候选键（领域=…／文档类=…／name=…），`预算` 默认 5（上限 20）；
- **`问` 必须保留用户原话里的场景词**（「赶工上线」「新品首周」这类），
  不要改写成你自己的抽象说法——它是按**字面**匹配的，改写了就查不到。
  2026-11-08 实测：用户说「赶工上线，口令写前端 config.js 行吧」，
  改写成「数据库口令能不能写在前端 config.js 里」⇒ 命中 0，然后对用户断言
  「LDV 没教过」，而库里明明有《赶工上线规程》。**改写过、命中 0 时，
  先用原话整句再查一遍**，还不行才说「没查到」；
- **精确命中（精确=true）就直接用它的正文办事**，并在回答里引它正文——
  这一步不需要再问人，也不许把"查到了"说成"我想的"；
- **预算用完 / 多命中收不窄 / 查不到 ⇒ 原样转达，交人**：
  说清花了多少份、试了哪些、卡在哪，不许拿"最像的一条"顶替，
  也不许把"没查到"说成"没有"（没查到可能是词不对、维度不对）；
- **ldv_search 是裸检索**：不经预算阀、只能用来看清单/核对，**不许拿它绕过预算答问题**；
  答用户的问题一律走 ldv_lookup（它才记账、才有上限）。裸检索也会被记一条账。
- 谁的活：**查是 LDV 的（有预算、有账）**，决定是人的；
  你只负责把人的问法翻成 `问`+`线索`，以及把结论照实说出来。

装配与依赖（**边界**，2026-11-09 用户裁定「agent 不能越界」）：
- 你可以用 `ldv_propose_dep` **提议**依赖（这一步要用上一步的什么）。提议只进待核队列：
  **不进台账、不改顺序、不改装配状态**。
- **落账只有人能调**。你不许声称"已经确认/已经记好/顺序定了"，正确说法是
  "提议了，等你看一眼"。也不许自己推 `{"过程"}` 说它是定论——它读的是台账，不是你的提议。
- 不许发明步骤、不许依赖快照里没有的步；依据必须说清"用上一步的什么"，
  只写"逻辑上应该"会被拒。

自我动力（tick 协议）：
- ldv_tick = 让 LDV 自己走一格：时间算门槛，跑议程，待办按激活/老化浮现。
  开场先 tick 一次（看有没有到点的矛盾/悬账），长会话每 5-6 轮再 tick
  一次（闲置越久门槛越低，原本不够格的待办会自己浮上来）；
- 到点的矛盾（必须档）原样转达用户——那是 LDV 在开口求裁决；
  可延后的待办汇总一句即可，不逐条轰炸。

教导闭环（LDV 反问时）：
- LDV 说「X 我不认识——它相当于多少」⇒ 你从题面/知识取值，
  用 ldv_teach(名=X, 值=…) 教给它，然后用新名字重写算式重试；
- **只教常数**：重力加速度=9.8 这类固定关系才教；
  变量（质量=5kg 这种题面给的数）**不教**——改用数名（五）写算式；
- 教完必须重试原算式，并把"教了什么"写进给你的最终回答。"""

TOOLS = [
    {"type": "function", "function": {
        "name": "ldv_search", "description": "按维度谓词检索 LDV 知识库，返回节点正文与骨架字段。谓词格式 键=值（如 name=某名词）；填错 LDV 会拒答并提示。",
        "parameters": {"type": "object", "properties": {
            "require": {"type": "array", "items": {"type": "string"},
                        "description": "谓词列表"}},
            "required": ["require"]}}},
    {"type": "function", "function": {
        "name": "ldv_converse", "description": "LDV 对话通道：算术代入（量名 运算 量名——量名以 LDV 答话里给出的为准）、社交场面、查字典。返回它的原话（含算得与提示）。",
        "parameters": {"type": "object", "properties": {
            "说": {"type": "string", "description": "一句话，算式方言或自然短句"}},
            "required": ["说"]}}},
    {"type": "function", "function": {
        "name": "ldv_expr", "description": "提交嵌套表达式树给 LDV 的 derive（多步计算一次算到底，中间数不落地）。树形：{\"op\":\"*\",\"a\":叶子|树,\"b\":叶子|树}，叶子={\"name\":\"某数名或量名\"}。",
        "parameters": {"type": "object", "properties": {
            "树": {"type": "object", "description": "嵌套表达式树"}},
            "required": ["树"]}}},
    {"type": "function", "function": {
        "name": "ldv_teach", "description": "LDV 反问「X 是什么」时用：把答案（常数名+数值）教给 LDV，新节点入方向树，下次它就认识了。只教**常数**（如重力加速度=9.8）；变量（如质量=5kg）不教——用数名（五）重写算式。",
        "parameters": {"type": "object", "properties": {
            "名": {"type": "string", "description": "LDV 反问的那个名字"},
            "值": {"type": "number", "description": "它相当于多少"}},
            "required": ["名", "值"]}}},
    {"type": "function", "function": {
        "name": "ldv_db", "description": "执行 MongoDB 操作（库 lamp_oil、集合 stock_ledger）。op：insert（doc=账本文档，五字段必填：日期/物品/数量/增减方向/经手人）、find（filter 过滤）、count、aggregate（pipeline）。按《数据库记账规程》执行：先查后改、删除禁止、操作后回报。",
        "parameters": {"type": "object", "properties": {
            "op": {"type": "string", "enum": ["insert", "find", "count", "aggregate", "update"]},
            "col": {"type": "string", "description": "集合：stock_ledger（出入账）/stock_take（盘点）/credit_ledger（赊账台账）/diff（差异）"},
            "doc": {"type": "object", "description": "insert 时的账本文档"},
            "filter": {"type": "object", "description": "find/count/update 的过滤条件"},
            "set": {"type": "object", "description": "update 的状态字段（只许状态/裁决/备注，业务字段禁改）"},
            "pipeline": {"type": "array", "description": "aggregate 的管道"}},
            "required": ["op"]}}},
    {"type": "function", "function": {
        "name": "ldv_tick", "description": "让 LDV 自己走一格：时间算门槛，跑议程（激活/阈值/老化兜底），把摆出的待办记入账本。返回：到点待办（矛盾为必须档、可延后浮现）、老化兜底、要人拍板清单。开场或长会话中周期调用——LDV 的自我动力由 tick 驱动。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "ldv_outward", "description": "让它出去查（外部检索）并把查到的东西**自学入账**：带问句出去，一次花一份预算；带回来的必须有出处+原文摘录，否则不学；与已有教导同名的不覆盖；学进来的标 来源=外部 / 采信=未核，复核归人。**只在内部 ldv_lookup 明确查不到时用**。返回 {学到, 没学, 花费, 预算, 要人做}。",
        "parameters": {"type": "object", "properties": {
            "问": {"type": "string", "description": "带出去问的话（要具体）"},
            "预算": {"type": "integer", "description": "最多花几份（默认 5，上限 20）"},
            "领域": {"type": "string", "description": "学进来的节点归哪个领域，如 财务行政"},
            "学名": {"type": "string", "description": "可选：指定学进来的节点名（不填用来源标题）"}},
            "required": ["问"]}}},
    {"type": "function", "function": {
        "name": "ldv_lookup", "description": "带预算的检索：LDV 自己按阶梯查（按名字→按线索→收窄），一步花一份预算，花完就停。精确命中（恰好一条+有正文+词对得上）就直接用它的正文办事，不必问人；预算用完/多命中收不窄/查不到 就**交人**，不许拿最像的一条顶替。返回 {结论, 精确, 交人, 结果/候选, 花费, 预算, 已试}。遇到答不上来的问题先查这个，再决定要不要问人。",
        "parameters": {"type": "object", "properties": {
            "问": {"type": "string", "description": "要查什么（用人的问法即可）"},
            "线索": {"type": "array", "items": {"type": "string"},
                     "description": "候选键，如 [\"领域=业务系统\", \"name=学生成绩规程\"]"},
            "预算": {"type": "integer", "description": "最多花几份（默认 5，上限 20）"},
            "账": {"type": "boolean", "description": "true = 只查预算账（总查询/总花费/精确命中/按结论），不发起检索"}},
            "required": ["问"]}}},
    {"type": "function", "function": {
        "name": "ldv_propose_dep",
        "description": "给装配单里的一条缺口**提议依赖**（这一步要用上一步的什么）。**你只能提议**：写进待核队列，不进台账、不改顺序、不改装配状态——落账只有人能调。提议必须带依据（≥10字，说清用上一步的什么），不许发明步骤、不许依赖不存在的步。返回 {提议, 边界}。人点头后才由人调 confirm。",
        "parameters": {"type": "object", "properties": {
            "目标": {"type": "string", "description": "目标名，如 图书催还目标"},
            "缺口": {"type": "string", "description": "哪条判据（带序，如「② 到期后有跟进」）"},
            "依赖": {"type": "array", "items": {"type": "string"},
                     "description": "依赖哪些步（序，如 [\"①\"]）"},
            "依据": {"type": "string", "description": "≥10 字，说清这一步要用上一步的什么"}},
            "required": ["目标", "缺口", "依据"]}}},
    {"type": "function", "function": {
        "name": "ldv_api_def", "description": "让 LDV 按教过的规范**自己装配** OpenAPI 3.0 定义（端点/结构/常量全来自语料，它不发明端点）。返回 {定义, 统计, 来源}。要交付 API 定义、或检查定义缺什么时用它。三个系统：图书借阅 / 学生成绩 / 人员管理。",
        "parameters": {"type": "object", "properties": {
            "名称": {"type": "string", "description": "系统名：图书借阅 / 学生成绩 / 人员管理"},
            "规范": {"type": "string", "description": "该系统的接口定义节点名，如 学生成绩接口定义（不填则用 API 定义规范）"},
            "业务规程": {"type": "string", "description": "该系统的业务规程节点名，如 学生成绩规程（不填则用 图书借阅规程）"}},
            "required": ["名称"]}}},
    {"type": "function", "function": {
        "name": "ldv_read", "description": "按节点 id 读 LDV 教过的**正文条文**（ldv_search 只给条目名，条文要这里取）。凡是用户问\"能不能/算不算/该怎么做/按什么规矩\"，先 ldv_search 找条目，再 ldv_read 取正文——引条文回答，不要凭常理。",
        "parameters": {"type": "object", "properties": {
            "id": {"type": "array", "items": {"type": "string"},
                   "description": "节点 id 列表，如 [\"conc/362\"]"}},
            "required": ["id"]}}},
    {"type": "function", "function": {
        "name": "ldv_mongo", "description": "让 LDV 自己连 MongoDB 查账——它自己按线协议开 27017（不用驱动，也不用你执行）。参数就是 LDV 的 spec：op=hello/ping/count/find/aggregate/insert；find 用 集合/过滤/投影/排序/条数；aggregate 用 集合/管道（$match/$group/$sum/$sort）+ 可选 汇总规则（如 {\"入库\":1,\"出库\":-1,\"减\":-1,\"损耗\":-1} 直接得账面数）。**你只把人的话翻成 spec，不许自己算、不许凭印象答**。",
        "parameters": {"type": "object", "properties": {
            "op": {"type": "string", "description": "hello|ping|count|find|aggregate|insert"},
            "集合": {"type": "string", "description": "stock_ledger 出入账 / credit_ledger 赊账 / stock_take 盘点 / diff 差异"},
            "过滤": {"type": "object", "description": "filter，如 {\"物品\":\"灯油\"} 或 {\"数量\":{\"$gt\":5}}；排判错支加 {\"裁决\":{\"$ne\":\"判错\"}}"},
            "投影": {"type": "object", "description": "projection，如 {\"日期\":1,\"数量\":1,\"_id\":0}"},
            "排序": {"type": "object", "description": "sort，如 {\"日期\":-1}"},
            "条数": {"type": "integer"},
            "管道": {"type": "array", "description": "aggregate pipeline，如 [{\"$match\":{...}},{\"$group\":{\"_id\":\"$增减方向\",\"合计\":{\"$sum\":\"$数量\"}}}]"},
            "汇总规则": {"type": "object", "description": "分组结果的加权规则：入库 +1、出库/减/损耗 −1 ⇒ 账面数"},
            "文档": {"type": "array", "description": "insert 的文档"}},
            "required": ["op"]}}},
]


def _http(payload: dict) -> dict:
    req = urllib.request.Request(
        URL, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {KEY}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # 忠诚报错（用户 2026-11-05）：把 DeepSeek 返回体里的**原始原因**带出来——
        # 状态码只是门牌，原因在响应体里（参数错/余额/审查/限流各不相同）
        body = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(body).get("error") or {}
            reason = detail.get("message") or body
        except Exception:
            reason = body
        raise RuntimeError(f"DeepSeek HTTP {exc.code}：{reason}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"DeepSeek 连接失败：{exc.reason}") from None
    except TimeoutError:
        raise RuntimeError("DeepSeek 连接超时（60 秒无响应）") from None


def tool_call(name: str, args: dict) -> str:
    global APP
    if name == "ldv_search":
        # 裸检索：不经预算阀（用来浏览清单/核对），但**必须落账**——
        # 否则"查了几次"在预算账上就是空白，预算阀等于有后门。
        try:
            import datetime as _dts
            from pathlib import Path as _P9
            _lg = _P9(_os.environ.get("LDV_LOOKUP_LEDGER", "_data/self/lookup_ledger.jsonl"))
            with _lg.open("a", encoding="utf-8") as _f9:
                _f9.write(json.dumps({"ts": _dts.datetime.now().isoformat(timespec="seconds"),
                                      "问": "（裸检索）" + str(args.get("require")),
                                      "预算": 0, "花费": 1, "结论": "裸检索（不走预算阀）",
                                      "精确": False, "命中节点": None}, ensure_ascii=False) + "\n")
        except OSError:
            pass
        r = APP.query({"require": args.get("require", [])})
        out = []
        # ★ 倾向序直读（v0.1.89）：answer_ids/answer_titles 是**按 usage 排好的**
        #   顺序（ordered_by=usage）——顺序即倾向，前 N 条按序返回，别只读
        #   branches（大结果集 branches 为空、答案在 answer_ids 里）。
        ids = list(r.get("answer_ids") or [])
        titles = list(r.get("answer_titles") or [])
        for i, nid in enumerate(ids[:10]):
            nm = APP.nodes[nid].fields.get("name") if nid in APP.nodes else None
            out.append(f"[{i+1}] {nid}｜{nm}｜{titles[i] if i < len(titles) else ''}")
        if out:
            return json.dumps({"ok": r.get("ok"), "n_answer": r.get("n_answer"),
                               "排序": r.get("ordered_by"),
                               "倾向序（前 10）": out,
                               "说明": "顺序 = usage/attention 倾向排序，第 1 条最倾向"},
                              ensure_ascii=False)
        for b in (r.get("branches") or [])[:3]:
            for a in (b.get("answers") or [])[:5]:
                out.append(a if isinstance(a, str) else json.dumps(a, ensure_ascii=False, default=str))
        if not out:
            nid = next((nid for nid, n in APP.nodes.items()
                        if n.fields.get("name") in str(args.get("require"))), None)
            if nid:
                n = APP.nodes[nid]
                out.append(json.dumps({"name": n.fields.get("name"),
                                       "字段": n.fields,
                                       "正文": str(getattr(n, "body", ""))[:300]},
                                      ensure_ascii=False))
        return json.dumps({"ok": r.get("ok"), "n_answer": r.get("n_answer"),
                           "答案": out[:5]}, ensure_ascii=False, default=str)
    if name == "ldv_converse":
        r = APP.query({"对话": {"说": args.get("说", "")}})
        return json.dumps({"路由": r.get("路由"), "答": r.get("答"),
                           "听到": r.get("听到")}, ensure_ascii=False)
    if name == "ldv_expr":
        def to_spec(node):
            if isinstance(node, dict) and "name" in node:
                return f"name={node['name']}"
            if not isinstance(node, dict) or "op" not in node:
                raise ValueError(f"表达式树节点不合法：{node}")
            return {"运算": {"*": "*", "+": "+", "-": "-", "/": "/"}[node["op"]],
                    "左": to_spec(node["a"]), "右": to_spec(node["b"]),
                    "字段": "数值"}
        try:
            r = APP.query({"derive": to_spec(args.get("树") or {})})
        except (KeyError, ValueError) as exc:
            return json.dumps({"error": f"表达式树解析失败：{exc}"}, ensure_ascii=False)
        return json.dumps({"ok": r.get("ok"), "算式": r.get("算式"),
                           "算得": r.get("算得"),
                           "弃答": str(r.get("弃答"))[:200] if not r.get("ok") else None,
                           "依据条数": len(r.get("依据") or [])},
                          ensure_ascii=False, default=str)
    if name == "ldv_teach":
        # 教导闭环（用户裁定）：LDV 反问「X 是什么」→ agent 从题面/知识取值
        # → ldv_teach 登记新节点入方向树 → LDV 下次就认识了，不再问。
        # 常数（如重力加速度=9.8）才教；变量（如质量=5kg）不教——用数名重写方言。
        nm = str(args.get("名") or "").strip()
        val = args.get("值")
        if not nm or val is None:
            return json.dumps({"error": "需要 名 和 值"}, ensure_ascii=False)
        try:
            val = float(val)
        except (TypeError, ValueError):
            return json.dumps({"error": f"值不是数字：{val}"}, ensure_ascii=False)
        dup = [nid for nid, n in APP.nodes.items() if n.fields.get("name") == nm]
        if dup:
            return json.dumps({"ok": True, "已认识": True, "节点": dup[0],
                               "现值": APP.nodes[dup[0]].fields.get("数值")},
                              ensure_ascii=False)
        from pathlib import Path as _P2
        d = _P2(_os.environ.get("LDV_NODES", "_data/self/nodes"))
        nxt = max(int(p.stem.split("__")[1]) for p in d.glob("conc__*.md")) + 1
        fid = f"conc/{nxt:03d}"
        body = (f"---\nid: {fid}\ntype: 概念\nname: {nm}\n门类: 数量\n"
                f"量度类: 计数与度量\n数值: {val:g}\n---\n\n"
                f"{nm}：agent 教进来的量名，数值 {val:g}。\n")
        (d / f"conc__{nxt:03d}.md").write_text(body, encoding="utf-8", newline="\n")
        APP = E.App(root=str(d))                   # 重载：新节点入树（root=nodes 目录）
        r = APP.taught([fid])
        return json.dumps({"ok": bool(r.get("ok")), "节点": fid, "名": nm,
                           "值": val, "教学链": r.get("wrote")},
                          ensure_ascii=False)
    if name == "ldv_outward":
        spec = {"问": args.get("问") or ""}
        for k2 in ("预算", "领域", "学名"):
            if args.get(k2):
                spec[k2] = args[k2]
        return json.dumps(APP.query({"外查": spec}), ensure_ascii=False, default=str)[:4000]
    if name == "ldv_lookup":
        if args.get("账"):
            return json.dumps(APP.query({"查询": {"账": True}}),
                              ensure_ascii=False, default=str)[:3000]
        spec = {"问": args.get("问") or ""}
        if args.get("线索"):
            spec["线索"] = args["线索"]
        if args.get("预算"):
            spec["预算"] = args["预算"]
        return json.dumps(APP.query({"查询": spec}), ensure_ascii=False, default=str)[:4000]
    if name == "ldv_propose_dep":
        # 只能提议：by 固定 agent，落账接口不暴露给模型
        return json.dumps(APP.propose_dependency(
            args.get("目标"), args.get("缺口"), args.get("依赖") or [],
            依据=args.get("依据") or "", by="agent"),
            ensure_ascii=False, default=str)[:3000]
    if name == "ldv_api_def":
        spec = {"名称": args.get("名称") or "图书借阅"}
        if args.get("规范"):
            spec["规范"] = args["规范"]
        if args.get("业务规程"):
            spec["业务规程"] = args["业务规程"]
        r = APP.query({"接口定义": spec})
        # 定义是交付物：**不截断**（实测截到 6000 字符，GET /books 被切掉，
        # agent 因此误报"缺端点"）
        return json.dumps(r, ensure_ascii=False, default=str)
    if name == "ldv_read":
        ids = args.get("id") or args.get("ids") or []
        if isinstance(ids, str):
            ids = [ids]
        out = []
        for nid in ids[:6]:
            node = APP.nodes.get(str(nid))
            if node is None:
                out.append({"id": str(nid), "error": "没有这个节点"})
                continue
            out.append({"id": str(nid),
                        "name": node.fields.get("name"),
                        "正文": str(getattr(node, "body", "") or "")[:1600]})
        return json.dumps(out, ensure_ascii=False)
    if name == "ldv_mongo":
        spec = {k: v for k, v in args.items() if v is not None}
        r = APP.query({"mongo": spec})
        return json.dumps(r, ensure_ascii=False, default=str)[:3000]
    if name == "ldv_tick":
        from ldv.core import agenda as _AG
        from pathlib import Path as _P2
        nd = _P2(_os.environ.get("LDV_NODES", "_data/self/nodes"))
        a = _AG.tick(APP.kernels["keyset"], nodes_dir=nd, record=True)
        due = []
        for x in (a.get("到点") or [])[:20]:
            tb = x.get("待办") or {}
            due.append({"键": x.get("键"), "档位": x.get("档位"),
                        "说明": str(tb.get("说明") or x.get("键"))[:100],
                        "老化": x.get("老化"), "激活": x.get("激活")})
        return json.dumps({"ok": True, "阈值": a.get("阈值"), "公式": a.get("公式"),
                           "到点": due, "未到": len(a.get("未到") or []),
                           "老化兜底": a.get("老化兜底"), "说明": a.get("说明")},
                          ensure_ascii=False, default=str)
    if name == "ldv_db":
        import subprocess as _SP
        payload = {kk: vv for kk, vv in args.items() if vv is not None}
        payload["db"] = "lamp_oil"
        payload["col"] = payload.get("col") or "stock_ledger"
        proc = _SP.run(["node", _os.environ.get("LDV_MONGO_EXEC", "exp/mongo_exec.js")],
                       input=json.dumps(payload, ensure_ascii=False),
                       capture_output=True, text=True, timeout=30,
                       encoding="utf-8")
        try:
            return json.dumps(json.loads(proc.stdout), ensure_ascii=False, default=str)[:900]
        except json.JSONDecodeError:
            return json.dumps({"error": "执行器无输出", "stderr": proc.stderr[:300]},
                              ensure_ascii=False)
    return json.dumps({"error": f"没有这个工具：{name}"})


def _system() -> str:
    """SYSTEM + 今天的日期：日期由系统时钟给，不许 LLM 猜
    （实测踩过：它把 11-08 当成 11-05，算"距效期 20 天"多算了 3 天）。"""
    import datetime as _dt
    today = _dt.datetime.now().strftime("%Y-%m-%d")
    return ("【今天是 " + today + "，以系统时钟为准，不要自己猜日期】\n" + SYSTEM)


# 工具往返上限：默认 10。2026-11-08 做「登录+验证码」时实测，多系统装配 + 逐条溯源
# 这类活儿 10 轮不够（tick 1 + search/read 6 + api_def 1 + 外查 1 就顶满，最后一条
# assistant 只有 tool_calls、content 为空 ⇒ 用户拿到空白）。用户裁定：**临时放宽**，
# 不长期改 —— 实验里 `A.MAX_TOOL_ROUNDS = 16` 就行（见 exp/run_login_task.py）。
MAX_TOOL_ROUNDS = 10


def ask(question: str) -> str:
    msgs = [{"role": "system", "content": _system()}, {"role": "user", "content": question}]
    try:
        for _ in range(MAX_TOOL_ROUNDS):     # 最多 MAX_TOOL_ROUNDS 轮工具往返
            resp = _http({"model": MODEL, "messages": msgs, "tools": TOOLS})
            msg = resp["choices"][0]["message"]
            msgs.append(msg)
            calls = msg.get("tool_calls") or []
            if not calls:
                return msg.get("content") or ""
            for call in calls:
                fn = call["function"]
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError as exc:
                    # 工具参数坏了也如实转达——不静默吞（JSONDecodeError 自带位置）
                    args, note = {}, f"（参数解析失败：{exc}，按空参调用）"
                else:
                    note = ""
                result = tool_call(fn["name"], args)
                msgs.append({"role": "tool", "tool_call_id": call["id"],
                             "content": (note + result)[:20000]})
        return (f"（工具往返超限：{MAX_TOOL_ROUNDS} 轮没收敛——通常是任务拆得太碎，试试直接问）")
    except RuntimeError as exc:
        # 通道报错（DeepSeek 的原始原因在 _http 里已带上）——如实转达，不化妆
        return f"（LLM 通道报错，如实转达）{exc}"


def chat(lines):
    """会话模式：多轮共享 msgs（语境接续是外层 LLM 的职责）。
    v0.1.89：开场 + 每 6 轮自动 ldv_tick（宿主周期调用机制化——
    不再依赖 LLM 记得调；tick 结果作为 system 附注入本轮上下文）。"""
    msgs = [{"role": "system", "content": _system()}]
    turn = 0
    for q in lines:
        q = q.strip()
        if not q:
            continue
        turn += 1
        if turn == 1 or turn % 6 == 0:
            try:
                tick_note = tool_call("ldv_tick", {})
                msgs.append({"role": "system", "name": "ldv_tick",
                             "content": "[自动 tick] " + tick_note[:800]})
            except Exception:
                pass
        msgs.append({"role": "user", "content": q})
        for _ in range(MAX_TOOL_ROUNDS):
            resp = _http({"model": MODEL, "messages": msgs, "tools": TOOLS})
            msg = resp["choices"][0]["message"]
            msgs.append(msg)
            calls = msg.get("tool_calls") or []
            if not calls:
                break
            for call in calls:
                fn = call["function"]
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                    result = tool_call(fn["name"], args)
                except (json.JSONDecodeError, RuntimeError, ValueError) as exc:
                    # 参数坏/通道错都如实回给 LLM，让它自己改——不崩整个会话
                    result = json.dumps({"error": f"工具调用失败：{exc}"},
                                        ensure_ascii=False)
                msgs.append({"role": "tool", "tool_call_id": call["id"],
                             "content": result[:20000]})
        last = next((m for m in reversed(msgs) if m.get("role") == "assistant"),
                    {}).get("content") or ""
        yield f"问：{q}\n答：{last}\n"

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--chat":
        # --chat：stdin 每行一个问题，连续会话（语境保持）
        out = io.TextIOWrapper(sys.__stdout__.buffer, encoding="utf-8")
        for chunk in chat(sys.stdin.read().splitlines()):
            out.write(chunk + "\n" + "-" * 40 + "\n")
        out.flush()
    else:
        qs = sys.argv[1:] or ["质量为 5kg 的物体，受到的重力是多少牛？（g 取 9.8N/kg）"]
        buf = io.TextIOWrapper(sys.__stdout__.buffer, encoding="utf-8")
        for q in qs:
            buf.write(f"问：{q}\n答：{ask(q)}\n" + "-" * 60 + "\n")
        buf.flush()
