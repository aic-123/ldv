# PREREG —— 阶段一：缓冲区与遗忘两个机制的存在性验证

> 纪律 0.2：本文件在**任何 B/D/X 组数据产生之前**写完。跑完只许在 RESULT.md 记读数，
> **不许改本文件的判据**。末尾附本文 sha256 与写定时间（不含末节的正文哈希）。

## 0. 被测对象（纪律 0.4）

| 项 | 值 |
|---|---|
| 被测 | 内核 `ldv` @ commit `291dad58697cc877f04fcee9f5ead5ec2af1d97b`（v0.1.52） |
| 不测 | `ldv-mcp`（钉在 v0.1.47，落后 4 个版本） |
| `ldv/tools/usage_store.py` sha256 | `196dae63ce4092e78d125bf28791fd06187a3e75bde04c78e16cd36562fd3d36` |
| Python | 3.13.14 |
| 写入位置 | 仅 `tempfile.mkdtemp()` 下的 `<tmp>/_data/<corpus>/nodes`（usage 启用的唯一形态） |
| 禁改 | `ldv/` 下任何文件（发现不符文档 → 记 FAIL + 存证据，不许顺手修） |

## 1. 前置事实（执行前记录，非判据）

1. **本地 HEAD 与清单所述 sha 不同**：清单写 `291dad5…`，本地仓库 HEAD 为
   `8f4c4c6`（同内容、不同谱系的 cherry-pick 版本）。`291dad5` 提交对象在本地存在，
   其树与 `8f4c4c6` 的树差 3 个 0 字节怪名文件、内容零差异。**已 checkout 到
   `291dad5` 后执行全部验证**，与清单对象一致。
2. **三个既有探针全部崩溃**（非语料缺失）：
   - `_probe_forgetting.py` → `KeyError: 'nodes'`（query 返回形态漂移）
   - `_probe_usage_wiring.py` → `KeyError: 'n_answer'`
   - `_probe_buffer_real.py` → `FileNotFoundError: ...\repo\repo`（`os.chdir(REPO)` 多套了一层）
   原始 stdout/err 全文落盘 `probe_runs/*.txt`。**不重写、不修探针**（纪律 0.1 的镜像：
   探针属于测量工具，改它等于改量具）。
3. **`_data/ext-wiki-vote` 实际存在**（13 MB，`MANIFEST.json` + `nodes/` + `raw/`），
   与清单"没有它"的假设相反。因此 M0-B（改 CORPUS 常量）**不需要执行**；
   探针崩溃与语料无关。
4. 语料实有：`arxiv-taxonomy / cora / cora-labeled / ext-wiki-vote / openalex-citations /
   snap-email-eu / snap-hepth / snap-hepth-dag`。

## 2. M0 判据

**M0-A**：三个探针的 stdout/err 全文已落盘，且逐条列出"哪些判据真实执行、哪些被跳过"。
PASS = 三份文件存在且非空 + 本节列出执行/跳过清单；FAIL = 任一探针无输出文件。

**M0-B**：仅当 W 系列整段因语料缺失跳过时适用。当前 `ext-wiki-vote` 存在 ⇒ **不适用（N/A）**。
若实跑发现 W 系列仍然全跳，则按清单改 `CORPUS` 常量重跑一次，判据同 M0-A。

## 3. B 组判据（缓冲区，全用 `ldv.tools.usage_store`，tmp 目录）

所有用例的 `tmp` 均为 `mkdtemp()` 新建空目录，`corpus_dir = <tmp>/_data/<name>/nodes` 的父目录
（即 `stage()` 直接可用的语料根）。**B 组不读任何真实语料。**

| ID | 判据（PASS 条件） | FAIL 的含义 |
|---|---|---|
| **B1** | `stage(tmp, qfp="q1", shown=["D1","D2"])` 后：`load(tmp) == []` **且** `aged_tendency(tmp) == {}` | 曝光被当成有用——两层记忆设计的地基塌了 |
| **B2** | 同 qfp 再 `stage(shown=["D3"])` 后：`len(stage_load(tmp)) == 1` **且** 该批 `shown == ["D3"]` **且** 返回值 `overwrote == True` | 同一查询重跑堆积批次 ⇒ 缓冲不是"状态"而是"账本" |
| **B3** | 三个独立 tmp 分别写 `STAGE_CAP-1 / STAGE_CAP / STAGE_CAP+1` 批，缓冲数分别为 `cap-1 / cap / cap`，第三档返回 `evicted` 长度恰为 1 | 边界写错（多削/少削），随使用漂移 |
| **B4** | 连续写 `STAGE_CAP+5` 批（qfp `q0000…q0054`，批间 `time.sleep(0.002)` 保证 `at` 单调）：剩余 qfp 集合 == `{q0005…q0054}` | 淘汰顺序错——数量对但语义错 |
| **B4-NOTE** | 若 `at` 相同导致排序退化为按文件名：如实记为 NOTE，不算 FAIL，但必须写明"at 相同时淘汰顺序与新旧无关" | — |
| **B5** | 构造 3 批同 `at` 的缓冲，连跑两次 `_evict`，两次删除文件名集合**完全相同** | 自动淘汰不可复现 = 系统里埋了随机性 |
| **B6** | `stage(shown=["D1","D2","D3"])` 后依次 promote D1/D2/D3：`remaining` 依次为 `2/1/0`；末次后 `len(stage_load(tmp)) == 0`；`load(tmp)` 恰 3 条且 outcome 全为 +1.0 | 缓冲泄漏（批次不删）或重复计分 |

## 4. D 组判据（遗忘，纯函数）

约定：`NOW = datetime(2026, 11, 1, 12, 0, 0)`；`d(days)` = `(NOW - timedelta(days=days)).isoformat()`。

| ID | 判据（PASS 条件） | FAIL 的含义 |
|---|---|---|
| **D2** | 两条记录（D1 @ at=NOW，D2 @ at=NOW−300d，均 outcome=1.0、propensity=0.5）：`t["D1"] ≈ 2.0`（±1e-6）且 `t["D2"] < 0.01` 且 `t["D1"]/t["D2"] > 1000` | 若 `t["D2"] == 2.0` ⇒ 实现是"平均"而非"累计" ⇒ 遗忘对单条方向完全无效（重大发现，不许调通） |
| **D3** | 手写一条无 `at` 的记录：`decay_weight("", now=NOW) == 1.0` **且** `status(tmp)["长期隐性记忆（稳定区）"]["无时间戳（不衰减）"] == 1` | 系统在"不知道时间"时凭空惩罚或假装很新 |
| **D4** | `decay_weight((NOW+5d).isoformat(), now=NOW) == 1.0` **且** `decay_weight("这不是时间", now=NOW) == 1.0`，两条均不抛异常 | 时钟回拨会让权重 >1，次序被污染 |
| **D5** | 5 项精确值：`at=NOW`→1.0；`NOW−30d`（hl=30）→0.5；`−60d`→0.25；`−90d`→0.125；`−90d, hl=0`→1.0。全部误差 < 1e-9 | 公式实现错误 |
| **D6** | 对 tmp 内 `usage/*.md` 取 md5 清单 → 调 `aged_tendency` 三次 → 再取 md5：文件数/文件名/md5 逐字相同 | 计算过程改写了账本 ⇒ 不是"只追加" |
| **D7** | `append(did="D1", outcome=-1.0, propensity=0.5, at=NOW−300d)`：`aged_tendency(tmp)["D1"] < 0` 且 `abs(...) < 0.01` | 判错信号不衰减，永久压住某方向 |

## 5. X 组判据（耦合，端到端）

**语料与输入定义（先定死）**：
- 语料：`_data/openalex-citations` 复制到 `<tmp>/_data/<corpus>/`（4037 节点 / 77 方向 / 16 层）。
- 查询：`{"require": []}`（实测 39 支、4037 项答案；空需求是"全部方向"的可复现入口）。
- `ans_of(r) = (sorted(r["answer_ids"]), r["n_answer"], r["n_branches"])`。

| ID | 判据（PASS 条件） | FAIL 的含义 |
|---|---|---|
| **X1 ★★** | 取默认排序前两支 A、B，各 drill 一次（均 promote）；把 A 的记录 `at` 改为 300 天前；重建 App 后 query：`ordered_by == "usage"` 前后都有；`t(A) < t(B)`；`ordered` 中 **B 的位置 < A 的位置**；且 `ans_of(r)` 与首次**逐字相同** | 倾向变了但排序没跟上（或排序不读倾向）⇒ "学习"只有账面没有行为。**硬 FAIL，不许解释** |
| **X2 ★★** | drill 某方向 D 后把它的记录 `at` 改为 3000 天前（w→0），重建后 query：D **仍在** `nodes` 中；`n_branches` 与无记录时（39）相同；D 的位置**后移**（不再是首支） | 衰减被实现成过滤，或影响了候选集 ⇒ "遗忘"与"筛选"混为一谈 |
| **X3** | 同一 App 连续 query 100 次（同 query）：`aged_tendency` 仍为 `{}` 或空（正式记录 0 条）；随后 drill 一支 ⇒ 正式记录**恰好 1 条**、`outcome == +1.0` | 曝光被刷成有用；排序会被"看得多的"而非"真有用的"驱动 |
| **X4** | 会话内：query → drill(did) → query，第二次 `ordered` 与第一次**相同**；重建 App 后 query，`ordered` **不同** | 巩固时机与文档不符（"长期隐性记忆"的语义是跨会话）。⚠️ 预读源码显示 `_record_use` 会**当场**刷新 `_tendency`（entry.py:300-304）⇒ 预期本判据 **FAIL**，如实记录，不算"调通" |

**判定规则**（照抄清单）：X1、X2 任一 FAIL ⇒ 阶段一不通过（"机制有效果"未成立）。
B/D 组 FAIL ⇒ 记"实现与设计不符"，写进报告，**不许改代码**。

## 6. 每条读数的留证方式

- 每条判据打印：`[ID] PASS|FAIL 读数=<json>`；单条失败不中断，全部跑完退出码 = 有 FAIL 则 1。
- 全部 stdout 由外层重定向落盘 `exp/m0/probe_mechanisms.txt`。
- 结构涉及 tmp 路径的，打印 `tmp=<path>` 以便复现。

## 7. 冻结

- 写定时间（UTC）：见下方哈希行。
- 判据一旦落盘，跑数据期间**不再修改本文件**。
- **正文 sha256（本节之前）**：`5fdd2d94e2a2495780f2f3b9bfecff4ab51ca61465029a01870a1df69057005d`
- **写定时间**：2026-11-01T11:46:04.038622+08:00

## V2 增补（写于 X 组任何数据之前）—— X2 的可观测构造

预读 `entry.py:614-618` 发现排序键为 `(-tendency, did)`：任何一个**正值**倾向都排在
所有 0 值之前。因此"只有 D 有记录、再把 D 变老"的构造下，D 的 0.00195 仍大于其余
0.000 ⇒ D 依然是首支，`位置后移`**不可观测**（这不是实现问题，是构造问题）。

X2 构造定为：同语料中 drill **两支** —— D（随后把它的记录 `at` 改为 3000 天前）
与对照 E（保留为"现在"）。判据本体不变，落成三条：
1. D 仍在 `nodes`（未被过滤）；
2. `n_branches == 39`（与无记录时相同，候选集不变）；
3. `t(D) < t(E)` 且 `ordered` 中 `index(D) > index(E)`（位置后移）。

- **V2 后整文件 sha256**：`104cb344098ab6808955c84e92fa535a55b12a1db1ea06b1bb896a7535f7f658`｜2026-11-01T11:46:28.573801+08:00

## V3 增补（用户口径裁定，2026-11-01 12:27，写于 W/F/BR 新探针数据之前）

### 裁定原文（统一口径）

> 会话内 drill 是先记录，然后进入缓冲区，进入到方向树后才会改变倾向；
> "记完刷新倾向"指的是进入方向树后刷新。

### 由此产生的两条判定变更

1. **X4 原判据作废**（它建立在"巩固是跨会话的事"这一旧理解上；该理解已被裁定否定）。
   原判据读数与 FAIL 记录**保留在 RESULT §2 不删除**，作为口径演进的历史证据。
2. **替代判据 X4R（本增补新增，先于数据）**：
   drill 之后**立即**在同一会话内 query：
   - PASS 条件：`ordered` **改变**（drill 支被提到最前，`ordered_by == "usage"`）；
     重建 App 后仍为 usage 序（说明记录已落盘、不是内存幻觉）；两臂答案集逐字相同。
   - FAIL 含义：倾向在该刷新的时候没刷新（`tendency_after` 与下次 query 的次序脱节）。

### 新增 W / F / BR 三组判据（清单 §2–§3 未执行部分的新建版）

语料现实：旧探针假设的 6 份语料中只有 `arxiv-taxonomy`（155 节点）在本地存在；
`pypi-deps / ext-coauth / snap-email-eu-mini / gh-langs / crossref-journals` 全部缺失。
因此 W/F 组用 `openalex-citations`（4037 节点，`require=[]` ⇒ 39 支），
BR 组用 `arxiv-taxonomy`，**并如实记录语料覆盖 1/6**。

| ID | 判据（PASS） | FAIL 含义 |
|---|---|---|
| W1 | 无记录时 `ordered_by == "default"` | 无历史却按使用排序 |
| W2 | query 后缓冲 1 批且 `n_shown == n_branches` | 曝光没进缓冲（或进错） |
| W3 ★★ | 仅 query 后：正式 0 条、`usage_loaded == 0`、`ordered_by` 仍 default | 曝光被算成有用 |
| W4 | drill 一支：`recorded=True`、正式 1 条、该支从缓冲移除 | promote 通道没打通 |
| W5 ★ | 写记录前后：项数/键数/树/指纹逐字相同 | 使用记录驱动结构（撞 §K9） |
| W6 | 重建 App：`usage_loaded > 0` 且 `ordered_by == "usage"` | 记录没落盘或没读回 |
| W7 ★★ | 排序前后 `answer_ids/n_answer/n_branches` 逐字相同 | 排序改了答案集合 |
| W8 | 同 `(qfp,did)` 再写 ⇒ `written == False` | 重复下钻叠加计分 |
| W9 | 不在缓冲的 did promote ⇒ `written == False` | 凭空造信号 |
| W10 | `clear_usage()` 后 `ordered_by` 回 default | 不可回退 |
| F6 | drill 后把记录改老 300 天：`t` 下降、记录仍在（1 条）、重建后 `usage_loaded ≥ 1` | 衰减不生效或被删 |
| F7 ★★ | 改老前后答案集/项数/支数逐字相同 | 遗忘改了结论 |
| BR-A | 谓词缓冲：候选 = 采用 ∪ 超预算 ∪ 拒收（两两不交） | 三段漏项或重叠 |
| BR-B | staging 暂存 20 个真实节点：缓冲期结构不变；commit 全提交；结构 +20 | 缓冲污染结构 / 提交丢件 |
| BR-C1 | 空缓冲 commit：不报错、返回空 | 空态崩溃 |
| BR-C2 | 10 个全撞 id：0 提交、10 拒收、10 个理由文件 | 静默覆盖 |
| BR-C3 | 100 个真实节点：全提交、结构 +100 | 大批量丢件 |
| BR-D1 | 首次建索引：重读 == 节点数 | 索引未建 |
| BR-D2 | 二次：全命中（重读 0） | 索引未生效 |
| BR-D3 | 再提交 1 个：只重读 1 个 | 索引未失效（吃老数据） |

X4R 单独计：`[X4R] PASS|FAIL`。

- **V3 后整文件 sha256**：`fa7764ad3de4111aad48adc8498421fa9ff8b2e6f0252edbe21ad83c242e5ff0`｜2026-11-01T12:28:09.552801+08:00
