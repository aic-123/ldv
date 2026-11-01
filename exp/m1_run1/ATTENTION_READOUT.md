# 接口提案：`attention` —— 把注意力作为**只读派生读数**说出来

> 状态：**已实现**（`entry.py::App._attention`）。依据 `exp/m1/` 的 V10–V13 读数；
> 验收见 §8：9/9 通过，全量 906 条判据红 0。
> 目标：回答「视角能不能不压在 agent 上」——能，但 attention 必须是**算出来的**，不是**存着的**。

---

## 0. 一句话

`query()` 现在只回 `ordered_by="usage"` 一个词，读者被迫从"谁排第一"反推什么叫重视。
把这一词扩展成**事实清单**即可：**哪一支用过几次、最近多少天前**，外加一条公开的半衰期。
**零分数、零排名、不可写、不落盘。**

---

## 1. 三层职责（这是全部安全性的来源）

| 层 | 内容 | 可写？ | 落盘？ |
|---|---|---|---|
| **权威** | `usage/*.md`（`did` / `outcome` / `propensity` / `at` / `qfp`） | ✅ 唯一入口 `drill` → `promote` | ✅ 只追加 |
| **派生** | `aged_tendency` → `_tendency` → `ordered` → **`attention.rows`** | ❌ 只读 | ❌ 每次现算 |
| **参数** | `half_life_days = 30` | ❌ 外生（常量 / spec） | — |

`attention` 落在**派生层**。它现在就已经存在（`entry.py:265` 每次现算），
本提案只是**把它说出来**，不新增任何状态。

> 若把它改成"存着的状态"，会出现两个来源说同一件事（账本 vs attention），
> 而 V13 的 A1/A2/A5 三条当场失效（凭空 promote、重复写、曝光算分全部可以绕过）。

---

## 2. 字段（只读，随 `query()` 返回）

```json
"attention": {
  "basis": "usage",              // "usage" | "default"（与 ordered_by 同域）
  "half_life_days": 30,          // 外生常量的回显；不接受传参
  "n_with_records": 6,           // 本次 nodes 里有记录的方向数
  "rows": [
    {"did": "D7",  "n_records": 1, "last_used_days": 0,   "span_days": 0},
    {"did": "D11", "n_records": 1, "last_used_days": 300, "span_days": 0}
  ]
}
```

字段约定：

| 字段 | 含义 | 类型 |
|---|---|---|
| `basis` | 这份读数的来源。无任何记录 ⇒ `"default"`，`rows` 为空 | 枚举（同 `ordered_by` 值域） |
| `half_life_days` | 遗忘半衰期（公开机制参数） | 数字，只读 |
| `n_with_records` | 落在本次 `nodes` 里、且有记录的方向数 | 整数 |
| `rows[].did` | 必须是 `nodes[].did` 中的某一个 | 字符串 |
| `rows[].n_records` | 该方向的记录条数（不区分正负） | 整数 |
| `rows[].last_used_days` | 最近一条记录距今天数（`max(0, …)`） | 整数 |
| `rows[].span_days` | 首末记录间隔（单条 ⇒ 0） | 整数 |

**不含**：`score` / `weight` / `priority` / `rating`（协议禁词，`ldv/protocol.py:65`）、
任何排名、任何跨方向可比的标量。

---

## 3. 五条不变式（可直接写成断言）

| ID | 不变式 | 守什么 |
|---|---|---|
| **I1** | `rows` 的顺序与 `nodes` 中同一 `did` 的先后**一致** | 读者能对上号 |
| **I2** | 所有 `rows[].did` ⊆ `nodes[].did` | 不给"本次没出现的方向"编故事 |
| **I3** | 无截断时 `n_with_records == len(rows)` | 账平 |
| **I4** | `basis == "default"` ⇔ `rows == []` | 没有 ≠ 0（§K6） |
| **I5** | **删掉整个 `attention` 字段，`nodes`/`ordered_by`/`n_answer`/`answer_ids` 逐字不变** | **可移除性：attention 不参与行为** |

I5 是这份提案的**验收总闸**：它保证 attention 是纯读数，删了不影响任何计算结果。
若将来有人让排序去读 `attention`，I5 立刻红。

---

## 4. 与现有字段的关系（兼容，不改语义）

| 现有 | 关系 |
|---|---|
| `ordered_by` | 保留原样。`attention` 是它的**解释**，不是它的替代：`ordered_by` 说"按什么排"，`attention` 说"每支凭什么" |
| `max_branches` / `branches_hidden` | `attention.rows` 跟随 `nodes` 的截断；截断时 `n_with_records` 报被截掉的数量，与 `branches_hidden` 同款 |
| `skipped_rejected` / `rejected_items` | 互不影响：那是负事件跳过的账，这是曝光/使用的账 |
| `tendency_after`（`drill` 返回） | 保留为一个数（现有调用方依赖）。`attention` 只在 `query` 上给，不动 `drill` 的返回 |

---

## 5. 已知的三个工具层口子（V13 实测，服务层未暴露）

| 口子 | 位置 | 影响 | 收紧建议 |
|---|---|---|---|
| `append(..., at=...)` | `usage_store.py:92` | 可写任意历史时间 ⇒ 伪造"很久没用" | 保留（判据要固定 now），但加**静态检查**：`serve*.py` 不得出现 `append(` / `usage_now` |
| `aged_tendency(..., half_life_days=)` | `usage_store.py:192` | 可改遗忘速度 ⇒ 让旧的看起来像新的 | 同上；`entry.py` 已不传 |
| `App(usage_now=...)` | `entry.py:122` | 只改读侧"现在"，账本 md5 不变（V13-B3 已验） | 服务层构造时不传（现状已满足） |

> 结论：**伪造面不在 `attention`，在这三个低层参数**。砍掉 attention 的可读性
> 并不能堵它们；把它们从服务层排除才有效。

---

## 6. 验收（实现后必跑的判据）

| ID | 判据 |
|---|---|
| A-1 | I1–I5 全部成立（含 I5 的逐字比较） |
| A-2 | 无记录时 `basis="default"`、`rows=[]`，`ordered_by="default"` |
| A-3 | 协议：`check_envelope` 通过（无禁词、无跨系统标量） |
| A-4 | 重复 `query` 两次，`attention` 逐字相同（确定性，§8.2） |
| A-5 | 沿用 V13 的 A1–A6（对外接口不可伪造）仍全 PASS |

---

## 7. 不做什么

- ❌ 不新增存储、不新增内核方法、不改 `split` / `P` / `E`（§K9、`B14` 不动）
- ❌ 不让排序读 `attention`（I5）
- ❌ 不提供任何"设置 attention"的接口
- ❌ 不在 `attention` 里放"结论"（如 `"top": "review"`）——那是判断，属于读者

---

## 8. 实现记录（2026-11-01）

**改动面**：`entry.py` 三处——`from datetime import datetime`、
一个私有方法 `App._attention(shown)`、`query()` 返回里一行 `"attention": self._attention(shown)`。
**未改**任何内核算法、插件、`split`、`P`/`E`、协议、服务层。

**验收（`exp/m1/scripts/v14_accept.py`，读数 `raw/v14_accept.txt`）**：

| ID | 判据 | 结果 |
|---|---|---|
| A-2 | 无记录 ⇒ `basis="default"`、`rows=[]`、`ordered_by="default"` | PASS |
| I1 | `rows` 与 `nodes` 同序 | PASS（位置 `[0,1]`） |
| I2 | `rows[].did ⊆ nodes[].did` | PASS（2 / 39） |
| I3 | 无截断 ⇒ `n_with_records == len(rows)` | PASS |
| I4 | `basis=="default"` ⇔ `rows==[]` | PASS |
| I5 | 动态：删掉 `attention` 后其余字段逐字相同；静态：源码无任何读取点 | PASS |
| A-3 | 协议信封收（禁词检查通过） | PASS |
| A-4 | 连续两次 `query`，`attention` 逐字相同 | PASS |
| A-5 | 复用 V13-A（对外接口 6/6 仍守住） | PASS |

**回归**：`python -m ldv.run_checks` 断言 97 条（红 0）；
`python -m ldv.tests.run_tests` **906 条全过、红 0**。

### 实现时被既有判据当场抓到的一次红（值得记）

首版 `basis` 用了 `"none"`，`§S1`（「查询输出里每个字符串都能指回材料」）
立刻报 `无出处 2 个：['none','none']`。这条判据的注释写着：
「一旦有人加自动摘要 / 模型润色，第一个被抓到的就是这里」——它按设计起作用了。

**处理方式**：不把 `"none"` 加进协议白名单，而是**复用 `ordered_by` 的值域**
（`default` / `usage`）。理由：白名单的闸门是为「新增文案」设的，
而 `basis` 本来就在说「这份读数按什么来的」，与 `ordered_by` 同域既语义一致，
又不扩张协议词表。改后 906 条全过。

### 已知未做

- 每次 `query` 多读一遍 `usage/*.md`（记录数大时需要缓存；当前量级 <10ms）。
- HTTP 层（`serve.py` 的 `/api/query`）未加专门断言，靠 `run_tests` 覆盖。
