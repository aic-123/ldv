# 真人知识填充：人的类别与层级行为（V17 前的先验库）

日期：2026-11-02。目的：V17（出声思维）的预期与编码表不能是拍的——
本文把两个方向的实证先验落下来：**人默认在哪个层级切分**（Rosch），
**真人实际建/用多深的层级**（PIM 实证研究）。

---

## 1. Rosch 基本层级：人的第一刀切在哪

**出处**：Rosch et al. 1976（基本层级加工优势）；Rosch 1978（原型理论）。

- 类别层级三水平：**上位**（家具）→ **基本层级**（椅子）→ **下位**（用餐椅）。
- **基本层级是认知最优的**：类内相似性高、类间区分度高，且是
  "最大数量的信息性范畴"——类内共享属性最多、类外共享最少。
- **成人在自然命名里绝大多数落在基本层级**（问"你坐在什么上面"，答"椅子"，
  不答"用餐椅"也不答"家具"）；儿童最先习得的也是基本层级名称。
- **分得过细耗认知资源，不分又看不出重要特征**——人是在这个权衡里
  默认停在基本层级。
- 例外：**经验丰富时下位类优先**（专家对自己领域的细分更敏感）。
  ⇒ 对"自己的"120 条个人语料，被试（=库的主人）可能比陌生人切得更细。

**对 V17 的可检验预测**：
- P-R1：真人的第一刀落在**最大的自然类别**（对应语料卡里的 `type` 层：
  读书/健身/食谱/……）——即基本层级，而不是需求字段。
- P-R2：第二刀才落需求字段（标记/部位/口味/…）。
- P-R3：库主人 vs 陌生人：主人可能多切一层（下位优先效应）——
  单被试（库主人）时此条作为解释备选，不作判据。

## 2. PIM 实证：真人建/用多深的层级

**出处**：Bergman, Whittaker et al.（EU PIM 项目 248997 总报告；JASIST 2010）；
Boardman & Sasse 2004（CHI）；Ducheneaut & Bellotti 2001；Henderson & Srinivasan 2009；
Whittaker et al. 2010；CHI 2019《The Scale and Structure of Personal File Collections》；
Balter 2000（模型）。

### 关键数字（必须区分两个口径）

| 口径 | 数字 | 出处 |
|---|---|---|
| **检索时的深度**（找文件走的层数） | **均值 2.86 层**（296 人、5035 步导航、94% 成功、均值 14.76s） | Bergman et al. 2010 |
| 文件夹大小 | 平均 11.82 个文件/夹；子夹 10.64 个 | Bergman et al. 2010 |
| 电子文件层级深度（全库） | 3.3；邮件 1.7；书签 1.3 | Boardman & Sasse 2004 |
| 邮件层级 | "generally shallow, typically having only **two levels**"（人要即时取用） | Ducheneaut & Bellotti 2001 |
| 全库最大深度 vs **最近访问深度** | 全库 ~6±1；**最近访问 1.81–3.7**——常用区浅、归档区深 = **适应性行为** | CHI 2019 |
| 手动检索最优文件夹数 | 4–20 个；>30 不划算 | Balter 2000 |

### 文献的核心结论（原文转述）

> "Folder structures were found to be **shallow** … **the users' decision to avoid both
> deep structure and large folders to be adaptive**." —— Bergman et al. 2010

> 层级宽而浅、文件夹小而定义良好，是多个研究的共识；尽管有搜索和标签，
> 人仍然主要用文件夹组织，且组织方式多年稳定（CHI 2019）。

### 对我们的三个直接映射

1. **"人 1–2 层"有了实证底**：活跃/检索深度 1.8–3.7（CHI 2019）、2.86（Bergman）。
   m2 里拍的"人 3.2 步"与文献一致，但从"拍的"变成"有出处的预期"。
2. **区分两个口径**：全库深度（≈LDV 的"到叶"5–10）vs **检索深度**（≈一次回答要讲的层数）。
   文献里人自己就把"不常用的放深处、常用的放浅处"——LDV 的链没有这个区分，
   每条链都讲到叶。⇒ **修复方向不是砍层数，是分"检索深度/归档深度"两个数**。
3. **文件夹大小 ≈ 8–22 个/夹、分支 ≈ 5–10**：与人"一眼能扫一屏"一致——
   停层判据里 `规模≤7` 的直觉数（米勒数）在 PIM 数据里得到旁证。

## 3. 两个先验放在一起，对 V17 的综合预期

- **顺序**：第一刀 = 基本层级（type 层），第二刀 = 需求字段，然后**收进具体条目**（不再分层）。
  即预测的字段决策序列 ≈ `type → 需求字段`（2 个字段决策），与 LDV 的相干字段数一致。
- **步数**：检索口径 1–3 步（文献），字段决策口径 2 步（预测）。
- **个体差异**：库主人可能比文献均值多一层（专家下位效应）；对需求外信息
  （如日期、地点）的反应是**跳过**（文献：导航时人按类别归组跳读）——
  编码表里要单设"过路/跳读"码，预期过路占比显著低于 LDV 链（LDV 过路 ≈ 50%）。

## 参考

- Rosch, E., et al. (1976). Basic objects in natural categories. *Cognitive Psychology* 8(3), 382–439.
- Rosch, E. (1978). Principles of categorization. In *Cognition and Categorization*. Erlbaum.
- Bergman, O., Whittaker, S., Sanderson, M., Nachmias, R., & Ramamoorthy, A. (2010).
  The effect of folder structure on personal file navigation. *JASIST* 61(12), 2426–2441.
- Boardman, R., & Sasse, M. A. (2004). "Stuff goes into the computer and doesn't come out":
  a cross-tool study of personal information management. *CHI 2004*, 583–590.
- Ducheneaut, N., & Bellotti, V. (2001). E-mail as habitat. *Interactions* 8(5), 30–38.
- Henderson, S., & Srinivasan, H. (2009). Filing, piling & structuring: everyday use of
  folders on the computer. *HCI International 2009*.
- Whittaker, S., et al. (2010). Am I wasting my time organizing email? *CHI 2010*.
- Dinneen, J. D., & Julien, C. A. (2019). The scale and structure of personal file collections. *CHI 2019*.
- Balter, O. (2000). Seven strategies for organizing e-mail. *HICSS 2000*.
