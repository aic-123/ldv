# ldv

**一个先学会「不回答」的回答系统。**

ldv 是一个**判定内核**：把语料装成分层划分树，把规矩写成规程节点，
对外提供 20+ 个结构化通道。它只做三件事——**查得到就带出处地答，
答不了就说明缺什么，做过的每一步都留账**。它不产内容、不猜、不装会。

**A judgment kernel for LLM agents: answer with citations, refuse with
reasons, and leave an auditable trail.**

**当前版本 v0.2.78** · 判据 **1252 条全绿** · 零第三方依赖 · [Apache-2.0](LICENSE)

---

## 它解决什么

LLM 应用有四个反复出现、且**不靠换模型能解决**的问题：

| 问题 | ldv 的做法 |
|---|---|
| 空结果被补成完整答案 | 空是显式结构：`结论: 空结果 / 任务标签: 无此记录`，并回查集合字段自证「真没有」 |
| 失败被静默重试 | 失败即停：报失败码与已尝试参数，**回查对方允许值**，落 `mongo_ledger` 挂账，同一参数不许重试 |
| 评测满分、生产崩盘 | 《评测维度对照》：权限 / 数据新鲜度 / 跨系统聚合 / 延迟，逐条写明「评测里 vs 真实使用里」的差别 |
| 长任务失忆 | 账本只追加（对话/答题/决策/装配），检索按需取，半衰期可配可关 |

每一条都有真实回执级别的实测，见 [`docs/五个困境实测_2026-11-09.md`](docs/五个困境实测_2026-11-09.md)。

---

## 与 LLM 的分工（最重要的口径）

```
LLM  管「怎么问」：读话、定意图、把话翻成方言、组织成回答
ldv  管「答不答」：命不命中、缺不缺、允不允许——每条答案带可复算的依据
```

三条纪律（agent 违反任意一条，产物作废）：

1. **不造数** —— 所有数值必须来自 ldv 的「算得」；
2. **不替判** —— 允不允许、缺不缺，由 ldv 说，agent 只执行；
3. **不藏翻译** —— 把话翻成 ldv 方言要交出整句 spec，不许心里翻译直接给答案。

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
app.query({"对话": {"说": "声速乘三等于几？"}})               # 对话：路由到已学通道
```

---

## 能力总览（全部有判据守着，不是路线图）

| 能力 | 一句话 |
|---|---|
| **分层划分树内核** | 语料 → 划分树；检索**无假阴**是判据（§K8），答案带完备性声明 |
| **教导式路由 ×8** | 装配 / 过程 / 差距 / 建议 / 报缺 / 重发 / 推演 / 值入——规矩在语料节点、算子在代码，教过才有，没教就弃答 |
| **装配单** | 目标判据逐条找零件，三态有/弱/缺，**不排顺序**；规程类目标不接受词汇当零件 |
| **推演 + 值入** | X→Y 是一条边（前提/规则/结论/依据，缺一件不算边）；边上的值由外面给，**只换值不换措辞** |
| **MongoDB 直连** | 自写 BSON + OP_MSG 连 27017（零驱动）；空结果自证、写失败报允许值并挂账 |
| **HTTP 服务壳** | 多租户 / 认证 / 配额 / healthz 与 readyz 分开 / 异步任务 |
| **留痕账本** | dialog · answers · decision · assemble · mongo_ledger——只追加，可审计 |
| **使用学习** | 曝光不算有用、下钻才算、判错只能人授；只排先看哪支，答案集逐字不变 |
| **LLM 桥接** | 五工具契约（search / converse / expr / quantities / dims），密钥走环境变量 |

完整口径见《端口与接口标准格式》：[`docs/端口与接口标准格式.md`](docs/端口与接口标准格式.md)。

---

## 验证

```bash
python -m ldv.tests.run_tests     # 1252 条（单元/对照/守卫/注入）
python -m ldv.run_checks          # 97 条构建断言
python -m ldv.tools.version_spots check   # 版本落点 7 处一致
python e2e_agent.py               # 端到端：真进程、真 HTTP、27 步
```

预注册实验（E1–E4 · m0 · m1 · m2）的统一入口与读数：
[`exp/INDEX.md`](exp/INDEX.md)；能说什么/不能说什么的诚实清单：[`exp/CLAIMS.md`](exp/CLAIMS.md)。

---

## 语料与资产分级

**仓库不带语料。** 教学与生产语料（`_data/self/`：节点、账本、对话记录）、
历史归档、探针证据、实验运行数据都**留在本地**——仓库只带骨架、判据与教学路径。
`_data/` 里随仓库的只有外部基准的视图规格（view_spec），没有数据本身。

给 ldv 当工程师的入门材料：

- [`docs/员工手册_AI应用开发新人.md`](docs/员工手册_AI应用开发新人.md) —— 岗位 / 铁律 / 权限边界 / 工作流 / 考核
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
| [五个困境实测](docs/五个困境实测_2026-11-09.md) | 四个问题之外，再加归因与分流的逐条实测 |

---

## 明确不声称

语义理解 · 内容生成 · 相似度匹配 · 任务正确性（建树质量）· 线程安全（`--locked` 是可选壳）·
标量评分（协议禁词 `score/weight/priority/rating`）。
完整边界与已收缩的主张：[`exp/CLAIMS.md`](exp/CLAIMS.md)。

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
docs/               设计文档 · 教学路径 · 员工手册 · 接口标准
```

---

**License**：Apache-2.0 · Copyright 2026 AIC-123
