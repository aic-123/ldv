# LIT_M2 —— 判据的文献依据（以及文献推翻了我哪几条推理）

> 写于 2026-11-02，在 `PREREG.md` 定稿之前。目的：把「通过线」从**我拍的数**换成
> **文献给的数**；并且诚实记下**文献改掉了原方案哪几条**。
>
> ⚠️ 本文档不提供「人就是这样」的结论。心理学大部分效应是**分布性**的（平均有差、
> 个体重叠），把它当硬阈值用会系统性高估。§1 因此先做分级，§4 专门写三个陷阱。

---

## 0 怎么读

三条规矩：

1. **每条判据必须指到具体一行文献**，指不到的，判据降级为「观察」，不计分。
2. **效应量优先于显著性**。p < .05 在 n 很大时几乎必然成立，不能当判据。
3. **必须写争议**。凡是有失败重复、有理论分歧的，写在同一条里，不藏到脚注。

分级（本文档自用，不外推）：

| 级 | 含义 | 在本实验里的用法 |
|---|---|---|
| **A** | 多项独立重复 + 元分析 + 跨实验室 | 可作**定量**通过线 |
| **B** | 有元分析但存在未决的理论分歧，或效应依赖测量方式 | 只作**方向**判据，不给数值阈值 |
| **C** | 单研究、样本小，或处于可重复性争议中 | 只作**观察**，不进判据表 |

---

## 1 判据与文献的对照（总表）

| 判据 | 文献锚点 | 级 | 文献给的数 | 我原来的写法 | 改成 |
|---|---|---|---|---|---|
| **P1** 遗忘形状 | Wixted & Ebbesen 1991；Rubin & Wenzel 1996；Anderson & Schooler 1991 | A/B | 两参数幂律 > 指数；**三参数指数打平** | R² 谁高二选一 | 同参数个数对比 + 报渐近线 |
| **P2** 间隔效应 | Cepeda et al. 2006（839 项）；Cepeda et al. 2008（n>1350） | **A** | 分散 47.3% vs 集中 36.7%；最优 ISI ≈ RI 的 20–40%→5–10% | 两组，判比值 1.38 | **18 格网格**，判三条形状 |
| **P3** 启动 | Anderson, Bjork & Bjork 1994；Murayama et al. 2014（g=0.35） | B | 提取**同时**启动目标、压制同侪 | 差集非空 | 报增/减/**净**三个数，允许「不可分辨」 |
| **P4** 组块/压缩 | Cowan 2001；Chase & Simon 1973 | B | 容量约 4 块（非 7±2）；专家优势在**结构**不在容量 | 中位数下降 | 加「叶大小分布」而非只看支数 |
| **P5** 迁移 | Barnett & Ceci 2002 | **A**（作为分类学） | 9 维；**禁单一效应量** | 单一命中率单调升 | 按维度分层报，不合成 |
| **P6** 不一致驱动重构 | Posner et al. 1982；Nussbaum & Novick 1982 | B | 四条件；17 人里仅 1 人完成重构 | 分裂率显著高于对照 | 判**部分**重构 + 报中间态比例 |
| **P7** 顺序效应 | Murdock 1962；Glenberg et al. 1980 | **A** | U 形（首因+近因） | 不计分（"价值低"） | **升级计分**，判 U 形 |
| **P9** 时间跨度 | Cepeda 2008（同上） | B | 同次数不同跨度结果不同 | 差集非空 | 保留，但并入 P2 解释 |
| **P10** 倾向的倒 U | Bornstein 1989；**Montoya et al. 2017** | B | r=0.26；倒 U，峰值 ~36 次 | **原方案没有** | **新增** |
| **P11** fan effect | Anderson & Reder 1999 | **A** | 线索关联越多越难检索 | **原方案没有** | **新增，预注册为不成立** |
| **P8** 离线巩固 | Rasch & Born 2013 | A | 巩固需**主动重放**，非时间副作用 | 预注册不成立 | 不变（有文献支持这个"不成立"） |

---

## 2 逐条展开：文献说什么、我改了什么

### 2.1 P1 —— 幂律 vs 指数：**我原来的判据是错的**

Wixted & Ebbesen (1991) 用三个实验（词回忆、面孔再认、鸽子延迟匹配）比了六种函数
（线性、指数、指数-幂、双曲、对数、幂），结论是**两参数幂函数最优**，连 Ebbinghaus
1885 的 savings 数据重析后也是幂律。

Rubin & Wenzel (1996) 把 210 个保持数据集放在一起拟，同样指向幂函数。

**但同一批作者后来自己拆了这个结论**（Wixted 在 Jost 定律那篇里写得很直白）：
给指数加一个**非零渐近线**（第三个参数），指数拟合得"一样好"（every bit as well），
代价只是渐近线高得离谱 —— 高学习条件 56%、低学习条件 39% 正确率。

⇒ **只用 R² 比"幂律 vs 指数"，比的是自由参数个数，不是形状。**
我原来的 P1「二选一，必须裁决」在方法上站不住。

**改后的判据（三组，同参数个数）**：

| 模型 | 参数 | 报什么 |
|---|---|---|
| `R = a·t^-b` | 2 | R²、AIC、b |
| `R = a·e^-bt` | 2 | R²、AIC、b |
| `R = c + a·e^-bt` | 3 | R²、AIC、**渐近线 c** |

裁决口径：**只有 3 参数指数也输**，才能写「幂律更优」；若 3 参数指数打平而 c > 0.3，
则写「本数据无法区分，且指数需要一个不合常理的高渐近线」——这是诚实的结论，
不是"幂律胜"。

**还有一处文献直接支持「两支衰减并存是自相矛盾」这个判断**：
ACT-R 用 `B = ln(Σ t^-d)`（每次呈现各自幂律衰减后**相加**），这一个机制**同时**
产生「幂律遗忘」与「幂律练习曲线」（Anderson, Fincham & Douglass 1999）。
也就是说：文献里的做法是**一个**求和式幂律，不是「指数管使用、幂律管议程」两套。
本仓库 `usage_store` 用半衰期指数、`agenda` 用 `t^-0.5` 求和 —— 这个分裂在文献里
没有对应物。这条写进 RESULTS 的"自相矛盾"一节。

### 2.2 P2 —— 间隔效应：**判据必须从 2 组改成 18 格**

Cepeda et al. (2006) 的规模：184 篇 / 317 实验 / **839 项**分散练习评估；
分散 47.3% vs 集中 36.7%；271 项直接比较里 259 项（96%）偏向分散。

但真正可当判据的是**形状**，不是"分散更好"：

- **非单调**：ISI 超过某个最优点后，保持率**轻微下降**（Cepeda 2006 明确报告）。
- **最优点随保留间隔移动**：Cepeda et al. (2008)，1350+ 被试，测得最优 ISI 约为
  保留间隔的 **20–40%**（1 周后测）降到 **5–10%**（1 年后测）。
  ⚠️ 具体表格数字来自二手综述，原表待核；但"比值随 RI 下降"这个方向在原文摘要里。

⇒ 我原来只做「集中 vs 分散」两组、判比值 1.38，**测不出非单调**，也测不出
「最优点随 RI 移动」——而后者才是文献里最硬的那条。两组设计的检验力浪费了。

**改后**：ISI ∈ {0, 1, 3, 7, 14, 30} 天 × RI ∈ {7, 30, 90} 天 = 18 格，
每格 6 次接触、末次对齐。判三条：

1. 每个 RI 内，保持率随 ISI **先升后降**（存在内部极大）；
2. 最优 ISI 随 RI **单调增**；
3. 最优 ISI / RI 的比值随 RI **单调降**。

三条全中才算「呈现间隔效应」。只中第 1 条 ⇒ 写「有间隔效应，但缺时间尺度不变性」。

**关于我上一轮的"1.38 必然反号"**：那个推算本身没错（`6×0.5^1 = 3.00` vs `≈2.18`），
但它只是**一个 RI=30 的格**。在 18 格网格里，模型会给出「ISI 越大越差」的**单调**
曲线，也就是第 1 条就不过。这个结论比"比值 1.38"强得多，而且是**结构性的**：
求和式指数衰减在任何 RI 下都不可能产生内部极大。

### 2.3 P3 —— 启动：**同侪既可能被启动，也可能被压制**

我原来的 P3 是「碰 A 之后，与 A 共享谓词的邻居更易到点」。文献里这只是一半。

Anderson, Bjork & Bjork (1994) 的检索练习范式：练 `FRUIT–or___` 之后，
**同类的未练项（Rp−）比完全没碰过的类（Nrp）更难回忆**。Murayama et al. (2014)
的元分析：512 样本 / 759 效应量，g = 0.35，95% CI [0.32, 0.38]，
fail-safe N = 56,111；**控制输出干扰后 g = 0.22（4.5 个百分点）**。

⇒ 人的系统里，一次提取对同侪是**两个相反效应叠加**：启动（正，短延迟）
+ 提取诱发遗忘（负，可持续一周以上）。净效应可能接近零。

**改后判据**：报三个数 —— 新增到点 / 掉出到点 / 净变化，并判：

- 净变化显著为正 ⇒ 「启动占优」；
- 净变化显著为负 ⇒ 「压制占优」；
- 落在噪声内 ⇒ 写「**不可分辨**」，**既不判通过也不判失败**。

这条改了之后，P3 从"弱判据"变成"能区分两种机制的判据"。原来那个"差集非空"
版本其实什么都没测——差集非空几乎是必然的。

### 2.4 P5 —— 迁移：**不允许合成一个命中率**

Barnett & Ceci (2002) 的核心不是某个效应量，而是一句警告：

> 在这么复杂的维度组合下，**估算单一的 far-transfer 效应量是误导的**
> （"Estimation of a single effect size for far transfer is misguided"）。

他们给 9 个维度：知识域、物理情境、时间情境、功能情境、社会情境、模态……
"近迁移/远迁移"是**在这 9 维上走了多远**，不是一个标量。

⇒ 我原来的「命中率随材料量单调升」正好犯了这个错，而且和本仓库自己的
「禁合成标量」原则撞车。

**改后**：T-改写 / T-组合 / T-跨域 三类题**分开报**，每类给"学习情境与测验情境
在哪几维上不同"。判据：只在**近迁移**（≤2 维不同）上要求命中率上升；
远迁移**不给通过线**，只报数 —— 文献的共识是远迁移很难、常常不发生，
拿它当通过线等于预设一个大概率失败的判据。

### 2.5 P6 —— 不一致驱动重构：**预期"部分重构"，不是"分裂"**

Posner, Strike, Hewson & Gertzog (1982) 给的是**四个必要条件**：

1. 对现有概念**不满**（dissatisfaction）
2. 新概念**可理解**（intelligible）
3. 新概念**看似可行**（plausible）
4. 新概念**有产出**（fruitful）

这四条的用处不是"打个勾"，而是：**只有四条都满足才可能发生顺应（accommodation），
少一条就只会发生同化（assimilation）** —— 也就是"把新材料硬塞进旧结构"。

而实证那一半更冷：Nussbaum & Novick (1982) 用认知冲突教六年级气体概念，
17 个学生里**只有 1 个**达到目标概念，其余停在五个中间态之一，约三分之二
带着若干迷思概念结束。作者的结论是概念改变"**不是革命式的，本质上是渐进的**"。

⇒ 我原来的「矛盾支的分裂率显著高于对照」预期了一个太强的结果。按文献，
正确预期是：**大部分矛盾只会被同化（结构不变），少数产生部分重构，几乎不会完成**。

**改后判据**（三层，分开报）：

| 层 | 读数 | 判据 |
|---|---|---|
| 同化 | 子方向数不变、只加成员 | 预期占多数（≥60%），**不算失败** |
| 部分重构 | 出现分裂 / 新增 `mustnot` / 约束收紧 | 显著高于无 `notice` 的配对对照 |
| 完成重构 | 矛盾消解且复检通过 | 预期 < 20%，**不设为通过线** |

并把 Posner 四条件映射到 `notice` 的字段上（有没有写"为什么不满""新支凭什么
可理解/可行"），作为**观察**项 —— 这一项能看出系统有没有"论证"，还是只是"记账"。

### 2.6 P7 —— 顺序效应：**我把它降级是错的，应该升级**

序列位置效应（Murdock 1962）是最稳的记忆现象之一：U 形，首因 + 近因。
Glenberg et al. (1980) 的两过程解释：首因靠复述进 LTM，近因靠短时存储。

我原来写「这条容易变成验证公式，价值低，不计分」。理由部分成立（`Σ t^-d`
确实会机械地产生 U 形），但结论不对：**正因为公式会机械地产生它，它才是一个
好的校准项** —— 如果连 U 形都出不来，说明时间戳或求和有 bug。

**改**：升级为计分项，判"5 次等间隔接触，首/中/尾的 `aged_tendency` 呈 U 形
（两端 > 中间）"，并加一条**区分性检验**：把 5 次的跨度从 9 天拉到 90 天，
首因应**减弱**（首项年龄变大）——这一条公式会给，人也会给，但两者衰减速率不同，
所以同时报"实测首因/近因比"与"公式预测比"，**差值本身是读数**。

### 2.7 P10 —— 倾向的倒 U：**新增，这是"情感/倾向"最直接的文献**

这条原本缺，而它是用户问的"人类情感或倾向"最贴的一处。

- Zajonc (1968) 确立单纯曝光效应；
- Bornstein (1989) 元分析：134 篇 / 208 项独立结果，总体 **r = 0.26**；
- **Montoya et al. (2017)** 重析：81 篇 / **268 条曝光-偏好曲线**，
  拟合 `y = .661 + .00191x − .000026x²`，线性项与二次项都显著
  ⇒ **倒 U**。峰值 **x = .00191/(2×.000026) ≈ 36.7 次**曝光（这个数是我按它给的
  系数算的；二手摘要里写 35.73，差在四舍五入还是别的口径，待核原文）。
  该形状**只见于视觉刺激，不见于听觉**。
  作者的原话是：**现有模型都不能充分解释这些结果**。

⇒ 对 LDV：`aged_tendency` 随接触次数是**单调递增且无上限**的（对数压缩，但单调）。
人的偏好是倒 U。这是"倾向"这一维上一条**方向明确的分界**。

**判据**：同一方向接触 1/3/10/36/80 次（其余条件固定），测 `aged_tendency` 与
"被选为答案的概率"。判：

- 单调增 ⇒ 记为「**与人的倒 U 反号**」（按 Montoya 的峰值位置，36 次之后应见下降，
  系统不会）；
- 出现下降 ⇒ 说明有某个我们没意识到的饱和机制在起作用，**当缺陷查**。

⚠️ 争议必须写进去：Montoya 自己说机制未定；Bornstein 的 r=0.26 是**小到中等**效应，
个体差异大。所以这条只能作 **B 级**：判方向，不判"差多少"。

### 2.8 P11 —— fan effect：**新增，预注册为不成立**

Anderson & Reder (1999)：一个线索关联的项越多，检索越慢越易错，
关联强度 `S_ji = S − ln(fan_j)`。

**代码事实**（已核）：`ldv/core/agenda.py` 的激活只有 base-level 一项，
**没有 spreading activation，也没有 fan 项**。所以：

**预注册**：同一谓词下方向数从 3 增到 30，命中率**不会**下降（或下降幅度与
`1/ln(fan)` 不符）⇒ **不成立**。

写一条注定失败的，理由和 P8 一样：它是诚实性的锚。而且它比 P8 更有信息量 ——
P8 守的是"有没有东西偷改状态"，P11 直接指出**架构缺了一个文献里标准的机制**。

### 2.9 P8 —— 离线巩固：这条"不成立"现在有文献支持

Rasch & Born (2013) 的综述：睡眠期间的巩固是**主动的系统重放**（海马→皮层），
不是"没人动它就自己变强"。没有重放机制的系統，隔夜不会更好。

⇒ 本系统 `clock` 只提供"现在几点"，不触发任何计算 ⇒ **预注册不成立**，与文献一致。
这一条从"我猜"变成了"有依据的阴性预期"。

---

## 3 被文献改掉的判据（一页看完）

| 原判据 | 错在哪 | 依据 |
|---|---|---|
| P1「幂律 vs 指数 二选一」 | 比的是参数个数，不是形状 | 3 参数指数打平（Wixted, Jost 那篇） |
| P2「两组，判 1.38」 | 测不出非单调，也测不出尺度不变性 | Cepeda 2006 的非单调；2008 的 ridgeline |
| P3「差集非空」 | 差集几乎必然非空，等于没测；且漏了 RIF | Murayama 2014, g=0.35 |
| P5「命中率单调升」 | 违反 Barnett & Ceci 明说的"禁单一效应量" | Barnett & Ceci 2002 |
| P6「分裂率显著高」 | 预期过强；文献里完成重构是少数 | Nussbaum & Novick 1982（17→1） |
| P7「不计分」 | 校准价值被低估 | Murdock 1962；Glenberg 1980 |

**另外三条是原本缺的**：P10（倾向倒 U）、P11（fan effect）、以及 P1 里的
"求和 vs 单条"区分。

---

## 4 三个陷阱（防止把"抄对了"读成"像人"）

### 4.1 抄了 ACT-R ≠ 呈现人类规律

`agenda.py` 的 `B = ln(Σ age^-0.5)` 就是 ACT-R 的 base-level activation，
`d = 0.5` 是社区默认值（"0.5 has emerged as the default value for the time-based
decay parameter d over a large range of applications"）。

⇒ 如果议程层的排序"看起来像人的记忆"，那**首先**说明公式抄对了。
这一项**不能**计入"像人"的任何一条。要写就写在"实现正确性"里，不写在"行为相似"里。

### 4.2 间隔效应不是人类特有的

Cepeda et al. (2006) 的综述里，间隔效应在海兔（约 2 万神经元）、线虫（**302 个
神经元**）、果蝇、蜜蜂、鸽子、鼠、猴、4–76 岁的人身上都成立。

⇒ 「LDV 出现间隔效应」**不能**当"像人"的证据，它是生物保守机制。
这一条恰恰是最容易被写成"看，它像人"的地方。§2.2 的判据只判**形状**，
不判"有间隔效应"——就是为了堵这个口子。

### 4.3 形状接近 ≠ 机制相同

`aged_tendency` 下降、人的保持率也下降，两条曲线都"先快后慢"。但这不能推出
机制相同：人的遗忘里有**抑制**成分（RIF，g=0.35）、有**线索依赖**（习惯随情境
断裂，Wood & Rünger 2016），而 LDV 的曲线是纯算术结果，没有任何"竞争"。

⇒ 判据只能判**行为形状**，不能判"所以它也在遗忘"。RESULTS 里凡出现"遗忘"二字，
必须在同段说明这是"读数下降"，不是"记忆痕迹消退"。

---

## 5 参考文献（含核验状态）

> 核验状态：`原PDF` = 我读了论文 PDF/正文片段；`摘要` = 读到期刊页摘要；
> `二手` = 只据综述或检索摘要，**页码与具体数字需核原文**。

### 记忆强度与遗忘

1. Anderson, J. R., & Schooler, L. J. (1991). Reflections of the environment in
   memory. *Psychological Science*, 2(6), 396–408.
   doi:10.1111/j.1467-9280.1991.tb00174.x —— `摘要`
2. Anderson, J. R., Bothell, D., Byrne, M. D., Douglass, S., Lebiere, C., & Qin, Y.
   (2004). An integrated theory of the mind. *Psychological Review*, 111(4),
   1036–1060. —— `二手`（公式经 Taatgen 章节 PDF 核对，见 3）
3. Taatgen, N., Lebiere, C., & Anderson, J. R. (2006). Modeling paradigms in ACT-R.
   In *Cognition and Multi-Agent Interaction*. —— `原PDF`（base-level 式与 d=0.5 默认值）
4. Wixted, J. T., & Ebbesen, E. B. (1991). On the form of forgetting.
   *Psychological Science*, 2(6), 409–415. —— `原PDF`
5. Wixted, J. T. On common ground: Jost's (1897) law of forgetting and Ribot's
   (1881) law of retrograde amnesia. —— `原PDF`（三参数指数打平那段）
6. Rubin, D. C., & Wenzel, A. E. (1996). One hundred years of forgetting: A
   quantitative description of retention. *Psychological Review*, 103(4), 734–760.
   —— `二手`

### 间隔效应

7. Cepeda, N. J., Pashler, H., Vul, E., Wixted, J. T., & Rohrer, D. (2006).
   Distributed practice in verbal recall tasks: A review and quantitative
   synthesis. *Psychological Bulletin*, 132(3), 354–380.
   doi:10.1037/0033-2909.132.3.354 —— `摘要`（839 项 / 317 实验 / 184 篇、
   47.3% vs 36.7% 来自二手统计，方向可信、小数待核）
8. Cepeda, N. J., Vul, E., Rohrer, D., Wixted, J. T., & Pashler, H. (2008).
   Spacing effects in learning: A temporal ridgeline of optimal retention.
   *Psychological Science*, 19(11), 1095–1102. —— `二手`（20–40% → 5–10% 的比值趋势）

### 提取的双面

9. Anderson, M. C., Bjork, R. A., & Bjork, E. L. (1994). Remembering can cause
   forgetting: Retrieval dynamics in long-term memory. *Journal of Experimental
   Psychology: Learning, Memory, and Cognition*, 20(5), 1063–1087. —— `二手`
10. Murayama, K., Miyatsu, T., Buchli, D., & Storm, B. C. (2014). Forgetting as a
    consequence of retrieval: A meta-analytic review of retrieval-induced
    forgetting. *Psychological Bulletin*, 140(5), 1383–1409.
    doi:10.1037/a0037505 —— `二手`（g=0.35；控制输出干扰后 g=0.22）
11. Stramaccia, D. F., et al. (2021). Think/no-think 元分析，d = 0.28（可能有发表偏倚）
    —— `二手`，仅用于说明"主动抑制"版本效应更小

### 迁移 / 概念改变

12. Barnett, S. M., & Ceci, S. J. (2002). When and where do we apply what we learn?
    A taxonomy for far transfer. *Psychological Bulletin*, 128(4), 612–637.
    doi:10.1037/0033-2909.128.4.612 —— `摘要`（9 维 + "禁单一效应量"原话）
13. Posner, G. J., Strike, K. A., Hewson, P. W., & Gertzog, W. A. (1982).
    Accommodation of a scientific conception: Toward a theory of conceptual
    change. *Science Education*, 66(2), 211–227. —— `二手`（四条件）
14. Nussbaum, J., & Novick, S. (1982). Alternative frameworks, conceptual conflict
    and accommodation. *Instructional Science*, 11, 183–200. —— `二手`
    （17 名学生仅 1 人达标；这段经 ERIC 文献综述转述，原始样本描述待核）

### 倾向 / 习惯

15. Zajonc, R. B. (1968). Attitudinal effects of mere exposure. *Journal of
    Personality and Social Psychology*, 9(2, Pt.2), 1–27. —— `二手`
16. Bornstein, R. F. (1989). Exposure and affect: Overview and meta-analysis of
    research, 1968–1987. *Psychological Bulletin*, 106(2), 265–289. —— `二手`
    （208 实验，r = 0.26）
17. Montoya, R. M., Horton, R. S., Vevea, J. L., Citkowicz, M., & Lauber, E. A.
    (2017). A re-examination of the mere exposure effect: The influence of
    repeated exposure on recognition, familiarity, and liking.
    *Psychological Bulletin*, 143(5), 459–498. —— `二手`（268 曲线 / 81 篇；倒 U；
    峰值 ~36；"现有模型都不能解释"）
18. Wood, W., & Rünger, D. (2016). Psychology of habit. *Annual Review of
    Psychology*, 67, 289–314. doi:10.1146/annurev-psych-122414-033417 —— `原PDF`
19. Lally, P., van Jaarsveld, C. H. M., Potts, H. W. W., & Wardle, J. (2010). How
    are habits formed: Modelling habit formation in the real world. *European
    Journal of Social Psychology*, 40(6), 998–1009. —— `二手`（中位 66 天，18–254）

### 序列位置 / 容量 / 巩固

20. Murdock, B. B. (1962). The serial position effect of free recall. *Journal of
    Experimental Psychology*, 64(5), 482–488. —— `二手`
21. Glenberg, A. M., Bradley, M. M., Stevenson, J. A., & Kraus, T. A. (1980). A
    two-process account of long-term serial position effects. *JEP: LMC*, 6(4),
    355–369. —— `二手`
22. Cowan, N. (2001). The magical number 4 in short-term memory: A reconsideration
    of mental storage capacity. *Behavioral and Brain Sciences*, 24(1), 87–114.
    —— `二手`（与 Miller 1956 的 7±2 冲突，见 §6）
23. Chase, W. G., & Simon, H. A. (1973). Perception in chess. *Cognitive
    Psychology*, 4(1), 55–81. —— `二手`
24. Rasch, B., & Born, J. (2013). About sleep's role in memory. *Physiological
    Reviews*, 93(2), 681–766. —— `二手`

### 可重复性（用来约束我自己）

25. Open Science Collaboration (2015). Estimating the reproducibility of
    psychological science. *Science*, 349(6251), aac4716. —— `二手`
    （100 项重复，36% 显著，效应量约减半）
26. Hagger, M. S., et al. (2016). A multilab preregistered replication of the
    ego-depletion effect. *Perspectives on Psychological Science*, 11(4), 546–573.
    —— `二手`（d ≈ 0.04）

---

## 6 没引的东西，以及为什么

| 想引但没引 | 原因 |
|---|---|
| **社会启动效应**（Bargh 等） | 大样本重复失败（Doyen et al. 2012），C 级，不入门 |
| **自我损耗 / ego depletion** | 23 实验室联合重复 d ≈ 0.04，等于零 |
| **7±2 工作记忆** | 已被 Cowan 的 ~4 取代；引它等于引一个过时的数 |
| **单一"综合认知能力"分数** | 本仓库禁合成标量，任何这类构念都进不来 |
| **"AI 像人"类论文** | 判据全部是主观评分或对话偏好，无刻度，过不了"四关"第一关 |

⚠️ 另有一条自查：上面所有 `二手` 条目，**在 RESULTS 里引用时必须标明
"数字未核原文"**。预注册的价值一半在诚实，一半在可核查；把二手数字写成
实测数字，比不引还糟。

---

## 7 这份文献工作的一个副产品

写到这里发现一件事，比任何一条判据都值得说：

**"像人"的判据里，最强的几条（间隔效应、序列位置、幂律遗忘）都不是人类特有的。**
间隔效应在线虫身上成立，序列位置效应在鸽子身上成立，幂律遗忘在鸽子身上也成立
（Wixted & Ebbesen 的实验三就是鸽子）。

真正有区分度的，反而是**那些"人做得不好"的地方**：

- 偏好是倒 U 的（会腻），系统不会；
- 概念改变是渐进的、常失败的（17 里 1 个），系统要么不重构要么重构得过于干净；
- 习惯强依赖情境线索，系统的倾向不看情境；
- 有 fan effect（线索越多越乱），系统没有。

⇒ 如果最后要说"像人"，靠的不会是"它也表现出间隔效应"，而是"
**它也在该出错的地方出错了**"。
这个方向比原方案的主轴更难做假，也更难通过。写在这里，供 RESULTS 时回看。
