# v0.1.52 —— E1–E4 预注册评测全量 + 服务层 P1 + 内核编码修复

> 本版本包含三块：①服务层 P1 落地（时钟 / 认证 / 多租户 / 调度 / e2e）；
> ②E1–E4 四组预注册实验全量数据与报告；③一个影响所有 Windows 用户的
> MCP stdio 通道编码缺陷修复（E1/E2 时代"server 中途死"的真正根因）。

## Added（新增）

### 服务层 P1
- **内部时钟**（`ldv/core/clock.py`）：给反思层主动性机制提供时间基准
- **认证与多租户**（`serve_auth.py` / `serve_tenants.py`）、调度（`serve_sched.py`）
- **e2e 全链路测试**（`e2e_agent.py`，真实 agent 接入流程验证，"不能只看一段代码通过"）
- 文档：`docs/P1落地.md`、`docs/内部时钟.md`、`docs/壳层选型.md`

### E1–E4 预注册评测（exp/，全部先注册后跑、判分脚本 sha256 冻结、无 LLM 裁判）
- **E1 引导的跨任务保留**：五组对照（R-0/R-R/R-L/L-0/L-L），15 任务递增材料 × N=3
  - 引导丢失崩塌：R-0 **0.319**（陷阱误报 7.5/任务）vs R-R **0.987**；比值跨模型一致（3.1× vs 3.0×）
  - 结构替代重给：R-L 0.864（比值 0.876，但末段 0.72 = **取回税**）
  - 长会话生态位：L-L **1.000** vs L-0 0.495（含 6 次确定性压缩）
- **E2 压缩分水岭**：确定性压缩对裸 agent 无差别摧毁（**0.00**），同压缩下结构可追溯（**0.93**）；
  陷阱 Q4 = 1.00——历史在场 ≠ 历史被用
- **E3 CSV 三臂对照**（专门打自己的实验）：
  852 B 规则 CSV 直给裸 agent **0.972** > ldv 工具取回 **0.864**——
  R-L 的精度是"预先整理"的功劳，取回机制在信息在窗条件下无增值且为负税
- **E4 失效边界**：三类弱键/无键材料上流式 remember 组全线 ≥ 裸组（类 1 反 +0.29），
  预注册的失效方向被证伪——ldv 的优势来自"整理"而非"存储与取回机制"（E1/E3/E4 三角互证）
- **跨模型方向性验证**（glm-4.5-flash 免费档）：引导丢失崩塌方向一致；弱模型长会话裸奔
  全灭（L-0 0.000）；工具循环协议有模型门槛（88% 任务 tool_exhausted）
- 完整报告：`REPORT_E1-E4_20261031.md`；能说/不能说对照表：`exp/CLAIMS.md`

## Fixed（修复）

### ldv-mcp stdio 通道在 Windows（GBK locale）下随机僵死 ★ 影响所有 Windows 用户
- **现象**：server 运行一段时间后"假死"——进程活着，但一切工具调用超时；
  E1/E2 实验中记录的"server 中途死亡（OSError 22 管道断裂）"与此同源
- **根因**：`server.py::_send` 使用 `ensure_ascii=False` 写 stdout，而 Windows 管道下
  子进程 stdout 默认 locale 编码（GBK）。响应中一旦含有 GBK 无法编码的字符
  （如 LLM 生成的要点中的 ⇒ 箭头），即抛 `UnicodeEncodeError`——单响应丢失且
  异常传播杀死 main 循环，此后所有调用永久超时
- **修复**：协议输出改为纯 ASCII（`ensure_ascii=True`，非 ASCII 一律 \uXXXX 转义）；
  实验客户端 `ldv_client.py` 同步加固（双向 `errors="replace"`、每会话独立 server 进程、
  超时触发重启）
- 修复后连续 3 × 84 次 remember 调用零失败复现

## Notes（限制条件，引用结论时必须同时出现）

- 主结论单模型（deepseek-flash）；glm-4.5-flash 仅方向性；付费档第二模型因余额未完成
- "长上下文"指累计意义（L 组 ≤150k token），非单窗 100k+
- 压缩为确定性摘要实现，非 LLM 摘要
- E3/E4 均为单任务小样本协议；E4 N=3 方差较大，"类 1 +0.29"以方向采信
- API 成本 ≈ ¥12；人工（建结构 + 调脚本 + harness 修复）≈ 12h

## 数据

- 原始数据全量入库：`exp/runs/*.jsonl`（17 批，逐次调用一条记录，只增不改）
- 冻结产物（sha256 见 PREREG/RESULTS）：`exp/e3_rule_table.csv`、`exp/e3_rule_table_rules.csv`
- 预注册文件：`exp/PREREG_E1.md`（V3）、`exp/PREREG_E3.md`（V2）、`exp/PREREG_E4.md`（V2–V6）
