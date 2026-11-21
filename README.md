# 执事（ldv）

**独自办事，有据而答；办过的每一步，账上都有。**

执事是一个**判定内核**：把语料装成分层划分树，把规矩写成规程节点，
对外提供 20+ 个结构化通道。接到任务它独自走完全程——量差距、装配、
调端口、走推演——每一步带依据；办不成，它给你一张说清缺什么的单子。

**A steward for LLM agents: it runs the errand end-to-end, answers with
citations, and leaves an auditable trail.**

**当前版本 v0.2.78** · 判据 **1252 条全绿** · 零第三方依赖 · [Apache-2.0](LICENSE)
**成果展示**：[`showcase/`](showcase/README.md) —— 真实参数、真实回执、真实账本

## 当前状态：v0.2.78（2026-11-21）

★★ **本版（v0.2.78，2026-11-21）** —— 值入形态：句子我出，值你给。
推演边上的值由外面给，**只换值、不换措辞**；换完走不通就明说。
逐版叙事见 [`docs/它是怎么长出来的.md`](docs/它是怎么长出来的.md)。

---

## 独自办事的四个场面

每一个场面，都有对应的机制、语料规程与真实回执（见 [`showcase/`](showcase/README.md)）：

| 场面 | 它怎么做 | 展示 |
|---|---|---|
| 工具返回空 | 回执自带三分类标签（无此记录 / 无权限 / 查询失败），并回查集合字段自证 | [`showcase/runs/02`](showcase/runs/02_空结果_无此记录.json) |
| 调用被枚举校验拒绝 | 记失败码与已尝试参数，**回查对方允许值**，落 `mongo_ledger` 挂账，同一参数不重试 | [`showcase/runs/04b`](showcase/runs/04b_第2步_枚举越界.json) |
| 评测满分、生产崩盘 | 按维度逐条归因——权限 / 数据新鲜度 / 跨系统聚合 / 延迟，结论落工程层 | [`showcase/runs/06`](showcase/runs/06_断层归因_成文.json) |
| 查不到就报查不到 | 交付三分类，不许用一段流畅的解释替代 | [`showcase/corpus/规程/conc__526.md`](showcase/corpus/规程/conc__526.md) |

---

## 与 LLM 的分工

```
LLM  管「怎么问」：读话、定意图、把话翻成方言、组织成回答
执事 管「怎么办」：独自执行、判定对错、核算代价——每条结论带可复算的依据
```

合作三要素：**有历史**（账本只追加，507 轮对话、255 条答题、104 条决策都在账上）、
**有规程**（每条路由一个语料节点，教过才会，没教就报缺单）、
**有止线**（缺口报到人，值由人给，它只换值不换措辞）。

桥接接口（五工具契约 + 密钥环境变量）：
[`docs/LLM桥接接口.md`](docs/LLM桥接接口.md) · 参考实现 [`exp/ldv_agent.py`](exp/ldv_agent.py)

---

## 快速开始

```bash
git clone https://github.com/aic-123/ldv.git && cd ldv
python -m ldv.cli a          # 三个插件上各建一棵划分树并跑检索（零依赖）
```

⚠️ Windows 先 `set PYTHONIOENCODING=utf-8 & set PYTHONUTF8=1`。

```python
from entry import App
app = App(root="_data/<语料>/nodes")

app.query({"require": ["name=声速"]})                       # 检索：带出处的定位
app.query({"derive": {"运算": "*", "左": "name=声速",
                      "右": "name=三", "字段": "数值"}})      # 推算：340×3=1020，带依据
app.query({"装配": {"名称": "订单退款空结果审计"}})           # 装配：判据逐条找规程
```

---

## 能力总览（全部有判据守着）

| 能力 | 一句话 |
|---|---|
| **分层划分树内核** | 语料 → 划分树；检索**无假阴**是判据（§K8），答案带完备性声明 |
| **教导式路由 ×8** | 装配 / 过程 / 差距 / 建议 / 报缺 / 重发 / 推演 / 值入——规矩在语料节点、算子在代码，教过才会 |
| **值入形态** | 推演边上的值由外面给：只换值、不换措辞，前提结论一起换；换完走不通就明说 |
| **装配单** | 判据逐条找零件，三态有/弱/缺，不排顺序；规程类目标只认规程当零件 |
| **MongoDB 直连** | 自写 BSON + OP_MSG 连 27017（零驱动）；空结果自证、写失败报允许值并挂账 |
| **HTTP 服务壳** | 多租户 / 认证 / 配额 / healthz 与 readyz 分开 / 异步任务 |
| **留痕账本** | dialog · answers · decision · assemble · mongo_ledger——只追加，可审计 |
| **使用学习** | 曝光不算有用、下钻才算、判错只能人授；只排先看哪支，答案集逐字不变 |
| **LLM 桥接** | 五工具契约（search / converse / expr / quantities / dims），密钥走环境变量 |

**关键反向对照与缺口**（实体在本机 `evidence/`，按资产分级不上传；逐件被文档引用，不可变）：
语料 front matter 三种坏形状当场拒收 `evidence/_probe_loader_contract_reverse.py` ｜
持久化白名单与资源上限 `evidence/_probe_persist_untrusted_reverse.py` ｜
使用学习接入探针（W1–W10，含「答案集逐字不变」）`evidence/_probe_usage_wiring.py` ｜
首值时间实测（36 项 0.51 s / 7115 项 51.8 s）`evidence/_probe_first_value.py` ｜
接线矩阵唯一半链路缺口（`ldv.recognize`，冻结待外生 spec）`evidence/wiring_gaps.json`

完整通道口径：[`docs/端口与接口标准格式.md`](docs/端口与接口标准格式.md)。

---

## 验证

```bash
python -m ldv.tests.run_tests     # 1252 条（单元/对照/守卫/注入）
python -m ldv.run_checks          # 97 条构建断言
python -m ldv.tools.version_spots check   # 版本落点 7 处一致
python e2e_agent.py               # 端到端：真进程、真 HTTP、27 步
```

预注册实验（E1–E4 · m0 · m1 · m2）统一入口：[`exp/INDEX.md`](exp/INDEX.md)。

---

## 语料与资产分级

**仓库带的是样本，不是全部语料。** 教学与生产语料（`_data/self/`：节点、账本、
对话记录）、历史归档、探针证据、实验运行数据都**留在本地**——仓库只带骨架、判据、
教学路径与 [`showcase/`](showcase/README.md) 成果样本。

给执事当工程师的入门材料：

- [`docs/员工手册_AI应用开发新人.md`](docs/员工手册_AI应用开发新人.md) —— 岗位 / 合作三要素 / 权限边界 / 工作流 / 考核
- [`docs/端口与接口标准格式.md`](docs/端口与接口标准格式.md) —— 全通道格式 / HTTP 端点 / Mongo 报文 / 调用纪律
- [`docs/路由的教法.md`](docs/路由的教法.md) —— 教一条路由的七段骨架

---

## 设计文档

| 文档 | 是什么 |
|---|---|
| [分层方向视图-设计文档](docs/分层方向视图-设计文档.md) | **权威**：不变量 §K1–K9 · 接口 §I1–I7 · 检查 §B1–B20 |
| [保证矩阵](docs/保证矩阵.md) | 53 条承诺 × 守它的判据（生成物，零判据 0 条） |
| [形式化断言集](docs/形式化断言集.md) | 33 条断言脱离系统可读（拿纸就能验） |
| [它是怎么长出来的](docs/它是怎么长出来的.md) | 设计怎么被押定、哪些判断被数据推翻（版本长文） |
| [五个困境实测](docs/五个困境实测_2026-11-09.md) | 独自办事的四场硬仗，逐条实测 |

---

## 仓库结构

```
entry.py            查询入口（检索 / 推算 / 对话 / 差距 / 建议 / 装配 / 过程 / 端口 / mongo / 推演 / 值入）
ldv/core/           划分树内核 + mongowire（MongoDB 27017 线协议，零驱动）
ldv/plugins/        keyset（合取键）· reach（图可达）· sequence（前缀树）
ldv/tests/          1252 条判据 · 注入 71 条
ldv/tools/          evidence_index · version_spots · guarantee_matrix · usage_store …
serve.py            HTTP 服务壳（多租户 / 认证 / 配额）
ldv-mcp/            MCP 镜像（server.py 把原话递给内核路由链）
exp/                实验脚本 · LLM 桥接参考实现 · INDEX/CLAIMS
showcase/           成果展示：真实参数、真实回执、真实账本
docs/               设计文档 · 教学路径 · 员工手册 · 接口标准
```

---

**License**：Apache-2.0 · Copyright 2026 AIC-123
