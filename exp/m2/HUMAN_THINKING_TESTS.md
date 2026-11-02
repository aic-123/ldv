# 关于「人的思考」的权威测试：调研与映射

日期：2026-11-02。动机：m2 的「人 1–2 层 / 2–3 个字段决策 / 人 3.2 步」目前是
结构化自我报告（`HUMAN_STEPS` 表），不是权威口径。本文调研四个成熟的测量传统，
并把每一个映射回我们的问题。

---

## 1. 四个权威传统（按与我们的相关度排序）

### ① Protocol Analysis（出声思维 / 口语记录分析）——测"人怎么想"的唯一实证方法

- **出处**：Duncker 1945 首创；Newell & Simon 1972 用于问题解决一举成名；
  Ericsson & Simon 1980/1993《Protocol Analysis》是方法论圣经。
- **方法**：被试做事时**并发**出声思维（concurrent protocol），录音转写，
  按**停顿点分段**（一段 = 一个思维单元），逐段编码成「问题行为图」
  （知识状态 + 改变状态的操作，方框加箭头）。
- **关键有效性结论**（Ericsson & Simon 实证）：Level 1–2 言语化（直接说出
  注意到的东西）**不改变思维的结构与进程**，只略降速度；Level 3（解释性报告）
  才会污染。⇒ 并发出声思维是合法的测量，不是反应法。
- **对我们**：`HUMAN_STEPS` 表里"人 3 步"属于**事后追述**（retrospective）——
  文献明确指出追述会变成"合理化的故事"（rationalised story），不反映真实序列。
  要权威口径，必须让真人在 120 条语料上做那 8 个需求时**并发**出声思维。

### ② Adaptive Planning Depth（人计划多深）——正面理论锚点

- **出处**：Eluchans, Lancia, Maselli, Gordon & Pezzulo 2025,
  *Adaptive planning depth in human problem-solving*, **Royal Society Open Science 12:241161**（PMC11978448）。
- **核心发现**：给需要 1–8 个子目标的计划题，人**按"最小必需深度"选择初始计划**，
  而不是所有题用同一深度——有界理性（bounded rationality）的资源自适应。
- **旁证**（同文引用的经典）：国际象棋高手平均算 3.6–5.4 步、上限 6.8–9.1 步。
- **对我们**：两个直接映射：
  1. `STOP_PROBE.md` §7 实测 LDV 的**字段决策数恒为 2**——这正好是
     "最小必需深度"的行为形态。**LDV 的思考深度落在人的自适应范围内**，
     这是对 LDV 有利的理论锚点（不再是拍的）。
  2. "人 1–2 层"可以被重述为"最小必需深度"，且**随需求变化**——
     简单需求 1 层、复杂需求 3 层。我们 m2 里人步数 3–4 恒定的写法需要修正：
     应该测"该需求的最小必需深度"。

### ③ Cognitive Dimensions of Notations——给"LDV 的讲述单位"一个权威名字

- **出处**：Green & Petre 1996, *Usability Analysis of Visual Programming
  Environments: A "Cognitive Dimensions" Framework*（VL'96）；Green 1989 起源。
- **定位**：专门评估"一个信息结构/记法是否贴合人的认知策略"的轻量框架，
  14 个维度，不比"好不好"，而是暴露权衡（tradeoffs）。
- **命中我们的维度**：

| 维度 | 定义 | 对应我们的发现 |
|---|---|---|
| **Viscosity – repetition viscosity** | "用户概念模型里的**一个动作**，需要多次重复的设备操作" | **逐字命中**：LDV 一个字段决策 = N 次二元切分（`type◆→type◆→type◆`）。字段折叠 = 针对它的 **design maneuver**（框架自带术语） |
| **Closeness of mapping** | 记法与问题世界的贴近程度 | `相干=相干字段` 的标注（V16）就是在补这个 |
| **Diffuseness** | 表达同一意思要多少符号/篇幅 | 链长 5–10 vs 人 3 步 = 高 diffuseness |
| **Hard mental operations** | 记法层面要求的高负荷心理操作 | `¬(a ∨ b ∨ c)` 否定链（V3 被判"最像机械"的形态来源） |
| **Hidden dependencies** | 实体间依赖不可见 | `ordered_by="usage"` 不带 `attention` 事实时，"为什么排前"是隐藏依赖——attention 字段就是在解它 |

- **对我们**：不需要再发明判据。把 `chain()` 按 14 维度做一遍 checklist 评估，
  每个问题都能拿到一个成熟名字；而"字段折叠"在框架里的合法性是现成的
  （design maneuver against repetition viscosity，文献中已有先例分析）。

### ④ Menu breadth vs depth——"层级多深合适"有 40 年实验数据

- **出处**：D. Miller 1981（2⁶/4³/8²/64¹ 四结构）；Snowberry, Parkinson & Sisson
  1983（复现+分类条件）；Kiger 1984（4×16 vs 16×4）；Jacko & Salvendy 1996（深度→感知复杂度）。
- **稳定结论**：
  - **深度增加 ⇒ 时间与错误都变差**：错误率从 1 层的 4% 涨到 6 层的 **34%**（Snowberry 1983）。
  - 广而浅 + **分类归组**最优（64 项分类单层 = 最快，3.0s vs 无序 7.3s）。
  - 二叉（2⁶，即深度 6 广度 2）**在所有研究里都是最差或次差结构**。
  - Jacko & Salvendy 1996：深度增加 ⇒ 感知复杂度显著上升。
- **对我们**：LDV 链 = 二叉树路径，恰好是文献里性能最差的形状；
  "人 1–2 层"的直觉有实验支撑（深度惩罚陡峭）；**字段折叠正是
  "分类归组"的移动——它把有效的广度还给读者**，这是有数据背书的修复方向。

---

## 2. 四者怎么组合成我们的下一步

| 我们的问题 | 用哪个 | 具体做法 |
|---|---|---|
| "人到底几步" | ① Protocol analysis | 找 1–2 个真人（含你本人），在 120 条语料上做 8 个需求，**并发出声思维**、录音、按停顿分段、编码成步。产出权威口径的 HUMAN_STEPS 替换自报值 |
| "LDV 想得深不深" | ② Planning depth | 不再比"层数"，比"**字段决策数 vs 最小必需深度**"。LDV=2，人的最小必需深度大概率也是 1–2 ⇒ 结论可能是"思考形状一致" |
| "怎么定性链长问题" | ③ Cognitive Dimensions | 对 `chain()` 写一份 14 维评估（重点 viscosity / diffuseness / hard mental operations），字段折叠作为 design maneuver 记档 |
| "折叠有没有依据" | ④ Menu studies | 引 Snowberry 1983 / Miller 1981 支撑"减深度、增有效广度、分类归组"；给 PREREG_V16 的结论加文献锚 |

## 3. 明确不适用的权威测试（查过，排除）

- **GOMS / KLM**（Card, Moran & Newell）：测专家**执行**序列的操作时间，我们测的是
  **理解与讲述**，任务类型不符。
- **NASA-TLX / SUS**：主观负荷与可用性量表，输出是分数不是过程结构，回答不了
  "分几步"。
- **OntoClean**：本体概念一致性（刚性/本质属性），评的是分类学**正确性**，
  与"讲述单位"无关。
- **Wason 选择任务 / 认知反思测试（CRT）**：测推理偏差，不是测组织信息的步骤。

## 4. 建议的落地顺序（待定）

1. **先做 ①（出声思维，N=1 起步）**——成本最低（一次会话），且直接替换
   我们最不可靠的那个数（HUMAN_STEPS）。按 Ericsson & Simon 纪律：
   预训练出声 → 不打断（除非停顿超时）→ 录音逐字转写 → 停顿分段 → 编码。
2. **同时把 ③ 的 checklist 写进 `ROLES.md` 的附录**——纯文档工作，给团队
   一套共享词汇（以后讨论"太深/太碎"不再是形容词）。
3. **② ④ 作为 V17 的理论锚引用**，不单独做实验。

## 参考（关键文献清单）

- Ericsson, K. A., & Simon, H. A. (1993). *Protocol Analysis: Verbal Reports as Data* (rev. ed.). MIT Press.
- Newell, A., & Simon, H. A. (1972). *Human Problem Solving*. Prentice-Hall.
- Eluchans, M., et al. (2025). Adaptive planning depth in human problem-solving. *R. Soc. Open Sci.* 12:241161. https://doi.org/10.1098/rsos.241161
- Green, T. R. G., & Petre, M. (1996). Usability analysis of visual programming environments: a 'cognitive dimensions' framework. *J. Visual Languages & Computing* 7(2), 131–174.
- Miller, D. P. (1981). The depth/breadth tradeoff in hierarchical computer menus. *Human Factors* 23(6), 683–695.
- Snowberry, K., Parkinson, S. R., & Sisson, N. (1983). Computer display menus. *Ergonomics* 26(7), 699–712.
- Kiger, J. I. (1984). The depth/breadth tradeoff in the design of menu-driven user interfaces. *Int. J. Man-Machine Studies* 20(2), 201–213.
- Jacko, J. A., & Salvendy, G. (1996). Hierarchical menu design: breadth, depth, and item count. *Int. J. Human-Computer Studies* 45(3), 365–371.
