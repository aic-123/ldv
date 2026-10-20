# ldv 待裁定项 · 成熟答案印证

**日期** 2026-10-06 ｜ **对应** `outputs/ldv-待裁定清单.md` ｜ **设计文档版本** v0.1.9

---

## 怎么读

**目的**：给待裁定清单里**给了推荐的每一项**，找成熟系统的现成答案，逐字核对 —— 看它**印证 / 修正 / 反驳**推荐。

**口径**

| 标记 | 含义 |
|---|---|
| **已核** | 读过该文献**全文**（PDF 抽文或 HTML 正文），引文逐字照抄 |
| **未核** | 只见到摘要或搜索片段 ⇒ **不得作为依据**（`C8` §4 的两栏纪律） |

**引文规则**：逐字照抄。凡 PDF 抽文与原文排版有出入的地方，**在引文后另注**（`[抽文]`），**不静默改字**。

抽取器的产物（**不是**原文的排版）。两批文献、两类产物：

**（一）排版产物** —— 数字排版换行/连字造成：

| 抽文里长这样 | 原文是 | 例 |
|---|---|---|
| `ﬁ` `ﬂ` `ﬀ` `ﬃ` `ﬄ` | `fi` `fl` `ff` `ffi` `ffl` | `satisﬁability` → `satisfiability` |
| `ir-` + 换行 + `relevant` | `irrelevant` | GiST 1995 p.4 |
| `poten-` + 换行 + `tially` | `potentially` | GiST 1995 p.4 |
| `N ,` `N )` `P ):` | `N,` `N)` `P):` | 标点前多一个空格 |
| `/n28` `/n29` | `(` `)` | GiST 1995 的接口一节 |
| `/_` `/!` `/$` | `∨` `→` `≡` | 同上 |
| `/:` `/;` | `.` `,` | `E/:p` → `E.p` |
| `ptr` + 换行 + `/1` | `ptr₁`（下标被拆行） | 同上 |
| `w h e r e` | `where`（斜体被逐字拆开） | GiST 1995 p.4 |
| `onN` | `on N`（词间空格被吃掉） | GiST 1995 p.4 |
| `keyk` | `key k`（同上） | Bε-tree 2015 |
| `non-` + 换行 + `leaf` | `non-leaf`（复合词被断在连字符处，归一化**过度合并**成 `nonleaf`） | R\*-tree 1990 §4.1 |
| `Checkability:.` | `**Checkability.**`（标题带句点） | McConnell 2011 §5.5 |

**（二）字形混淆产物** —— **扫描件**（OCR）把形近的字认错。第二批五篇里有**四篇**是扫描件
（R\*-tree / Guttman 84 / X-tree 96 / Driscoll 89），只有 Bε-tree 那篇是数字原生：

| 抽文里长这样 | 原文是 | 认错在哪 | 例 |
|---|---|---|---|
| `remsert` `msertion` `mserted` | `reinsert` `insertion` `inserted` | `n` 被读成 `m` | R\*-tree 1990 §4.1 |
| `entrles` `entnes` `Entnes` | `entries` `entries` `Entries` | `i`→`l`、`i`→`n` | Guttman 84 / R\*-tree |
| `supemodes` `ehmmatron` | `supernodes` `elimination` | `rn`→`m`、`ti`→`tt` | X-tree 96 / Guttman 84 |
| `1s` `IS` | `is` | `i`→`1`、小写→大写 | R\*-tree 1990 |
| `0( 1)` `Q(n)` `G(i)` | `O(1)` `Θ(n)` `Θ(i)` | 数学符号被认成字母 | Driscoll 89 §2 |
| `l Forced remsert` | `• Forced reinsert` | 项目符号被读成 `l` | R\*-tree 1990 §4.1 |
| `linked date structure` | `linked data structure` | `a`→`e` | Driscoll 89 §2.2 |

⇒ 引文按**原文的读法**写；抽文原样附在 `[抽文]` 注里，**两样都逐字**。
HTML 正文（PostgreSQL 文档、ESLint、rustc book）无此问题；网页另标 `[网页]`。
⇒ **两类产物分开计数**（`OK~抽` / `OK~扫`）—— 合并成一个数就等于把「还原了符号」和
「还原了字形」当成一件事，而后者对「引文到底对不对」的证明力更弱，必须能单独看见。

**这份文档的引文是**核过的**，不是抄完就算**：

```bash
python outputs/_verify_blocks.py    # 本地：51 条 [抽文] 注 + 51 个引文块，整块比对
python outputs/_verify_web.py       # 网上：16 条，比对 .webcache/ 里的页面缓存
```

两条都会**红**（退出码 1），且都**逐档区分**「原样中」/「抽文产物还原后中」/「扫描件字形还原后中」——
降级一律**上报**，不静默。网页的正文缓存在 `.webcache/`（`curl -sL` 取，带状态码），
**缓存随文档一起留着**，否则那两条引文无法回查。

⚠️ 引文是**整块**核的，不是核片段 —— 片段核会放过「多一个句点」「少一个逗号」这类错。
本稿就是这么抓出**三处**的：`Checkability:.`、`Simplicity:.`、`3.4 Removing instances`（后者是**多加**了一个句点）。

⚠️ 挂在引文后的 `` `[抽文]` `` 注**不参与**整块比对（注里是抽文原样，本来就不该匹配），所以注**单独**当引文核。
**除了一个例外**：出处在 `sources/gist1995.txt` 的块**整块跳过** —— 那份是旧数学排版，
花括号/下标/逻辑符全被换成码，读法**不能机械还原**，逐字比对只会产假红；这类块的证据由它自己的注承担。
其余带注的块（含本批四篇扫描件）**只剔掉注那一行**，块本身照常判 —— **不因为「这块带注」整块放过**。
跳过**逐块打印原因**，**不当成通过**。

**出处写法**：`文件:行号`，可直接回查。

---

## 速览

**第一批**（本地语料 + 网页文档）：

| # | 项 | 推荐 | 成熟答案（出处） | 结论 |
|---|---|---|---|---|
| 1 | 健全性 · `reach` 维护路径 | (1) 根记账 ~~+ (3) 子 payload 从父派生~~ | GIN pending list ｜ GiST `AdjustKeys` ｜ Godin Galois 格 | (1) **印证 + 修正**；<br>(3) **印证机制、修正方向**，但 **2026-10-07 裁定不采纳**（靶子按定义不存在，见设计文档 §10.2 A′）—— 下表同 |
| 2 | 健全性 · `sequence` 维护路径 | (3) 拒绝 + 记账 | SP-GiST `allTheSame` ｜ `PathShrink` 三档 | **印证**，且**多出一条出路 (4)** |
| 3 | `B16` 判据对 `reach` 过强 | (c) 外生 oracle 定义覆盖 | McConnell §5.5 **排除的两种极端** | **逐字印证**（两条出路各对应一种被排除的极端） |
| 4 | 「覆盖不漏」硬要求无检查 | (2) 先做成度量 | ESLint bulk suppressions ｜ Rust compatibility lint group | **印证 + 修正**（→ baseline + 里程碑） |
| 6 | 进步量守卫 | 「进步」= payload 细化度，N = 10 | SP-GiST `longValuesOK` | **逐字印证**，且**答掉了「待确认」** |
| 7 | 删除流程 | 做，排在 1–4 之后 | ART `Delete` ｜ SP-GiST `Tree Shrink` ｜ Godin §3.4 | **印证**（含**次序**） |

**第二批**（**主动去邻域找的** —— 存储引擎 / 空间索引 / 持久化，见 §七）：

| # | 项 | 补充的成熟答案（出处） | 结论 |
|---|---|---|---|
| 1 | 根记账 | **Bε-tree** 的**节点内 buffer** | **修正**：(1) 的选择从「物化 vs 不物化」变成**三选** |
| 2 | `sequence` 拒绝 + 记账 | **X-tree supernode** ｜ **R\*-tree forced reinsert** | **印证 (4)**（且给出触发条件的写法）；另**排除**一条不适用的候选 |
| 7 | 删除流程 | **Bε-tree tombstone** ｜ **Guttman `CondenseTree`** | **印证**（含**次序**）；**新发现一条待定口径** |
| 8 | 持久化 / 并发 | **Driscoll 89** ｜ Overmars 三条老办法 | **前例已核**（清单 §11 原写「未核」）⇒ 现在能定形态 |

**一句话**：七项推荐里 **没有一项被反驳**；**五项被修正**（1 的 (1) 与 (3)、2 多一条出路、4 换形态、8 定形态），而**修正全部来自成熟答案比推荐更强的地方**，不是来自它不同意推荐。

---

## 一、`reach` 维护路径（清单 §1）

**推荐原文**：(1) `insert` 的项必须落在根覆盖内，否则记一条事件、不塞进结构；**做根这一层** + (3) 子方向的 payload 从父的 payload 派生；**做内部**。

### 成熟答案 A —— 「暂时放不进主结构的项」有名字、可计数、可开关

GIN 把「一时放不进主结构的项」放进一份**有名字的旁挂清单**，而不是塞进主结构或丢掉：

> GIN is capable of postponing much of this work by inserting new tuples into a **temporary, unsorted list of pending entries**. When the table is vacuumed or autoanalyzed, or when gin_clean_pending_list function is called, or if the pending list becomes larger than gin_pending_list_limit , the entries are moved to the main GIN data structure using the same bulk insert techniques used during initial index creation. This greatly improves GIN index update speed, even counting the additional vacuum overhead.
> — `.prior-art/gin.txt:249`（PostgreSQL 18 文档 §65.4.4.1 GIN Fast Update Technique）**已核**

代价**明确记在性能上**，不记在正确性上：

> The main disadvantage of this approach is that **searches must scan the list of pending entries in addition to searching the regular index**, and so a large list of pending entries will slow searches significantly.
> — `.prior-art/gin.txt:251` **已核**

而且它是**可关的**，关掉不影响正确性：

> If consistent response time is more important than update speed, use of pending entries can be disabled by turning off the fastupdate storage parameter for a GIN index.
> — `.prior-art/gin.txt:253` **已核**

⇒ **印证 (1)**：成熟系统确实把「一时落不进主结构的项」做成一个**有名字、可计数、可开关**的旁挂状态，并且**把代价记在性能栏**（查询要多扫一份）—— 与推荐 (1) 的三点（承认合法 / 给它记账 / 代价是性能）逐条同形。

⚠️ **修正（一处，必须写进裁定）**：GIN 的 pending list 是**物化**的（真存了那些项），推荐 (1) 是**只记事件、不物化**（沿用 §K2 判空「判定 + 事件，不物化」的形态）。
- 好处：不物化 ⇒ **查询不用扫**，上面那条「searches must scan」的代价直接消失。
- 代价：**失去「事后还能补进去」的能力** —— GIN 的清单会在 vacuum 时被并回主结构，本设计的账**不会**。
⇒ 二者不可兼得。这与设计文档 §K2 那句「**代价不会消失，只会换地方**」（`分层方向视图-设计文档.md:310-313`）是同一句判断，只是换了一层。**裁定要选的是「可数」还是「可补」**，不是「要不要记账」。

### 成熟答案 B —— 父的键在**维护路径上重算**

GiST 的 `AdjustKeys` 在**每次插入**后沿父链上溯，把父的键**重算成子们的并**：

> **Algorithm AdjustKeys(R, N)** … Sketch: ascend parents from N in the tree, making the predicates be accurate characterizations of the subtrees. **Stop after root, or when a predicate is found that is already accurate.**
> **PR1:** If N is the root, or the entry which points to N has an already-accurate representation of the Union of the entries on N, then return.
> **PR2:** Otherwise, **modify the entry E which points to N so that E.p is the Union of all entries on N.** Then AdjustKeys(R, Parent(N)).
> — `sources/gist1995.txt:744-757`（Hellerstein / Naughton / Pfeffer, *Generalized Search Trees*, VLDB 1995）**已核**
> `[抽文]` 抽文原样：`Algorithm AdjustKeys/n28 R/; N /n29` · `N so that E/: p is the Union of all entries onN .` · `Then AdjustKeys(R , Parent(N ).)`（`E/: p` = `E.p`，`onN` = `on N`）

⇒ **修正（重要，这条改的是推荐的理由，不是推荐本身）**：
GiST 的答案是「**把父的键重算成子们的并**」，所以**GiST 里根本不会出现「根盖不住后来添的项」** —— 它的根键也是推导出来的。
本设计的根 payload 是**人声明的锚点集**（`分层方向视图-设计文档.md:383`：`根 rank = 1，**外生**`）⇒ 这个现象是**外生根的直接后果**，不是实现缺陷。
⇒ **反过来加固了「不选 (2)」**：让根的 payload 随插入加宽，在 GiST 里是**正常做法**（PR2 就是这个），但在本设计里等于**替人改声明** ⇒ 撞 §K4（方向不可变），并连带 §8.2「外生项一旦允许自我优化，会同时失去可复现性与可解释性」（`…设计文档.md:776-780`）。
⇒ 结论：**「根记账」不是本设计不得已的补丁，而是「外生根」这一条决定的必然形态**。裁定时这句话可以拿来回答「为什么不学 GiST 直接加宽」。

### 成熟答案 C —— 子从父**派生**，是「序」的定义，不是一条要额外守的性质

Godin / Missaoui / Alaoui 1995 把「谁是谁的子」**定义成**包含关系本身：

> The couple of functions (f, f') is a **Galois connection** between P(E) and P(E') and the Galois lattice G for the binary relation is the set of all complete pairs … with the following partial order: given C1=(X1,X'1) and C2=(X2,X'2), **C1 < C2 <=> X'1 ⊂ X'2** . There is a **dual relationship** between the X and X' sets in the lattice, i.e., **X'1 ⊂ X'2 <=> X2 ⊂ X1** and therefore, C1 < C2 <=> X2 ⊂ X1. The partial order is used to generate the lattice graph in the following way: **there is an edge (C1,C2) if C1 < C2 and there is no other element C3 in the lattice such that C1 < C3 < C2 . C1 is called parent of C2 and C2 child of C1.**
> — `.prior-art/godin95.txt:138-148`（*Incremental concept formation algorithms based on Galois (concept) lattices*, Computational Intelligence 11(2) 1995）**已核**

新节点也是**从父的 X' 出发**、用一个**收窄**运算算出来的：

> These new pairs are always in the form **(Y ∪ {x*}, Y' ∩ f*({x*}))** for some pair (Y, Y') ∈ G.
> — `.prior-art/godin95.txt:171` **已核**

⇒ **印证 (3) 的机制**：成熟答案把「子比父细」做成**序的定义**，于是它不是一条要额外守的性质，而是**构造**。
⇒ 而且这条对偶关系 **`X'1 ⊂ X'2 <=> X2 ⊂ X1` 正是本设计 §1 的第一行与第二行**：

| 本设计 §1 | Godin 的对偶 |
|---|---|
| 第一行 `members(子) ⊆ members(父)`（**硬要求**，由内核构造保证） | `X2 ⊂ X1` |
| 第二行 `覆盖(子) ⊆ 覆盖(父)`（**不是**硬要求） | `X'1 ⊂ X'2` |

⇒ 即：**本设计把 Godin 里「同一个序」的两侧拆成了「一硬一软」**。Godin 那边两侧都是序的定义（都硬）；本设计只硬了成员侧。这个拆法**有代价**，而代价正好就是 item 1 / item 2 / item 4 这三条 —— **软的那一侧没有构造保证，就一定会有人漏**。

⚠️ **修正（方向相反，不能含糊）**：Godin 的新 intent 是 `Y' ∩ f*({x*})` —— **收窄**。推荐 (3) 是**加宽**子的 payload（`payload(子) ⊇ payload(父)`），因为 `reach` 的 `覆盖 = reachable(payload)` 对 payload **单调不减**。
- ⇒ 用 Godin 只能印证「**派生**」这个机制，**不能**印证「加宽」这个做法。
- ⇒ 加宽的后果设计文档已记（`…设计文档.md:1010`：`子方向覆盖 ⊇ 父方向覆盖，树在覆盖意义上不再变细`）。**这里补一句 Godin 侧的对照**：走了 (3)，本设计就**不再是 Godin 意义上的格** —— 会变成「成员变细、覆盖变粗」的**两向**结构。
- ⇒ 这是 (3) 的真实代价，**裁定时要按这个说**，而不是说「与成熟做法一致」。

**本项结论**

| 推荐的一半 | 成熟答案 | 结论 |
|---|---|---|
| (1) 根记账 | GIN pending list | **印证**（物化 vs 不物化要选，见上） |
| (1) 为什么不能改成「加宽根」 | GiST `AdjustKeys` | **印证**（加宽是 GiST 的正常做法，但在本设计里撞 §K4 / §8.2） |
| (3) 子 payload 从父派生 | Godin Galois 格 | **印证机制、修正方向**（Godin 收窄，本设计要加宽；加宽后不再是格）<br>⬜ **2026-10-07 裁定不采纳** —— 靶子按定义不存在，见设计文档 §10.2 A′ |

---

## 二、`sequence` 维护路径（清单 §2）

**推荐原文**：(3) `insert` 遇到收不住的项就**拒绝 / 记事件**，与第 1 项共用同一个机制。

### 成熟答案 A —— 插件分不开时，内核**不静默通过**，而是**覆盖决定 + 打显式标记 + 改下游契约**

> If more than one leaf tuple is supplied, it is expected that the picksplit function will classify them into more than one node; **otherwise it is not possible to split the leaf tuples across multiple pages, which is the ultimate purpose of this operation. Therefore, if the picksplit function ends up placing all the leaf tuples in the same node, the core SP-GiST code will override that decision and generate an inner tuple in which the leaf tuples are assigned at random to several identically-labeled nodes. Such a tuple is marked allTheSame to signify that this has happened. The choose and inner_consistent functions must take suitable care with such inner tuples.**
> — `.prior-art/spgist.txt:427`（PostgreSQL 18 文档 §65.3.4.2）**已核**

⇒ **逐条印证**推荐 (3) 的三点：

| 推荐 (3) 的要素 | SP-GiST 的做法 |
|---|---|
| 不静默通过 | 内核 **override** 插件的决定 |
| 显式记账 | 打 **`allTheSame`** 标记 |
| 下游必须知道 | **`choose` 与 `inner_consistent` 必须对这类元组另行处理** |

⇒ 并且它**不动「三态」**：`allTheSame` 是**节点上的一个标志位**，不是第四种查询答案。
⇒ **这直接印证了清单 §2 那条「待一起定」的子问题**：

> 若它算**第四态** ⇒ `Tri` 要动 ⇒ **要重验**；若它只是**一条账本事件**（不进 `Tri`）⇒ 不动 §K6，**不用重验**
> — 本清单 §2

**成熟答案的做法就是后者** —— 标记挂在**元组**上，答案仍是三态。⇒ 推荐成立，且**不用重验**。

⚠️ **修正（一处）**：SP-GiST **不拒绝**这个项 —— 它把项**放进去**，只把「这次放置**没有信息**」标出来。推荐 (3) 是**项不塞进结构**。两者对正确性等价（都不会让「否」变成假阴），差别在**可恢复性**：SP-GiST 的项还在结构里，本设计的只在账里。
⇒ 这正是上面 GIN 那条「物化 vs 不物化」的**同一个岔路**。

### 成熟答案 B —— 出路其实还有第 (4) 条：**不新增子方向，让项留在父这一层**

SP-GiST 把「展开到多深」做成三档**声明式策略**，中间那一档的原文就是本设计 §K2 的字面：

> **Leaf Shrink:** Data is inserted at the ﬁrst available leaf node. Decomposition will not depend on the maximum possible resolution. In this strategy, **no index node will have one leaf node as we decompose only when there is no room for the newly inserted data item.**
> — `.prior-art/spgist01.txt:219-222`（Aref & Ilyas, *SP-GiST: An Extensible Database Index for Supporting Space Partitioning Trees*, JIIS 17:2/3 2001）**已核**

⇒ **修正（有价值的第 (4) 条）**：成熟答案对「这一层分不开」的处置是 **「项留在这一层，不再往下分」** —— 而这**正是本设计 §K2 的字面已经规定的做法**（`…设计文档.md:304-313`：「不建这一层」⇒ 那些项只能留在这个叶里 ⇒ 叶容量变大、分辨率下降，且**代价必须成对报**）。
⇒ 所以清单 §2 的三条出路之外，**还有第 (4) 条**：**不新增 catch-all 子方向、也不拒绝 —— 让项留在父这一层**。
- 它**不动 §K2**（§K2 本来就要求这样）
- 它**不动 §I4**（不需要 `None`）
- 它的代价**已经记在叶容量里**，而叶容量**已经是一个度量**
- ⚠️ 待确认：内核是否允许一个方向**既有子层、又是叶**（`run_tests.test_emergence` ② 断言的是「方向 = 有子层 + 叶」，需要对着内核确认这条能不能覆盖 item 2 的情形）

⚠️ **但有一条差别必须说清**：SP-GiST 的「不再分解」是**装不下**触发的（物理），C 的「分不开」是**语言形状**触发的（无穷字母表）。后者**开局就成立**，不是一个可以「等装不下再说」的档位。⇒ 这正是清单 §2 里那句「对 C 来说，(3) 不是『选择』，是**承认不可能**」的成熟侧对照。

### 成熟答案 C —— `PickSplit` 直接返回一个布尔 = 「要不要继续分」

> **PickSplit(P , level, splitnodes, splitpredicates): Returns Boolean**, where P is a set of BucketSize+1 entries that cannot ﬁt in a node. PickSplit deﬁnes a way of splitting the entries into a number of partitions equal to NumberOfSpacePartitions and **returns a Boolean value indicating whether further partitioning should take place or not.**
> — `.prior-art/spgist01.txt:273-277` **已核**

⇒ **印证 §I4 的 `None`**：插件**被允许**说「这一层分不开」，而且这是**接口的正常用法**，不是违约。设计文档 `§I4`（`:199`、`:226-228`）已经引了这一条，本次核到的是**布尔的确切含义**（"whether further partitioning should take place or not"）—— 逐字对得上。

**本项结论**

| 推荐 | 成熟答案 | 结论 |
|---|---|---|
| (3) 拒绝 + 记账 | SP-GiST `allTheSame` | **印证**；且**印证「不进 `Tri`」⇒ 不用重验** |
| — | `PathShrink = Leaf Shrink` | **修正**：多出第 (4) 条出路（项留在父层） |
| — | `PickSplit → Boolean` | **印证** `§I4` 的 `None` 是正常用法 |

---

## 三、`B16` 的判据对 `reach` 过强（清单 §3 / §5）

**推荐原文**：(c) `B16` 改成按**外生 oracle** 判覆盖（同 `B17` 的做法：`_fixtures` 里按方向定义，**不调插件、不调内核**）。

McConnell / Mehlhorn / Näher / Schweitzer 2011 §5.5 用**两个被排除的极端**把「什么算检查」夹出来：

> The “deﬁnition” above **rules out some obviously undesirable situations**:
>
> 1. Assume we have a complicated program P for I/O-behavior (ϕ, ψ). We could take w as the record of the computation of P on x and deﬁne W (x,y,w) as “w is the computation of P on input x and y is the output of this computation”. If P is correct, then W is a witness predicate. This predicate certainly satisﬁes the checkability requirement. **However, it does not satisfy the simplicity requirement, since a proof of the witness property is tantamount to proving the correctness of P.**
> 2. Another extreme is to deﬁne W (x,y,w) as “ψ (x,y)”. For this predicate simplicity is trivially given. **However, deciding W amounts to a solution of the original problem.**
>
> — `.prior-art/mcconnell11.txt:1063-1071`（*Certifying Algorithms*, Computer Science Review 2011）**已核**

⇒ **逐字印证：清单 §3 的三条出路，恰好是「两个被排除的极端 + 中间那条」**

| 清单 §3 的出路 | 对应 McConnell 的哪一条 | 原文判定 |
|---|---|---|
| `B16` 今天的样子：按 `§I3 代价`**重算**归属 | **极端 1**（把程序的运算记录当见证） | **不算 certifying** —— 「证明这条见证性质等于证明 P 正确」 |
| 出路 (b)：让 `reach.penalty` 改成 `0 ⟺ 可达`（插件自报） | **极端 2**（`W := ψ`） | **不算检查** —— 「判定 W 等于解原问题」 |
| **出路 (c)：外生 oracle** | **中间那条** | —— |

⇒ 即：**清单里那三条出路不是三个并列选项，是「两个已知的坏答案 + 一个剩下的答案」。** 这条把 (c) 从「我推荐」升级成「**前两个已被成熟文献明确排除**」。

并且 §5.5 给了 Simplicity 与 Checkability 的**判据本身**，可以直接拿来当验收条件：

> **Checkability.** Given x, y, and w, we require that it is trivial to determine whether W (x,y,w) holds. … • There is a decision algorithm for W that runs in **linear time**. …
> **Simplicity.** The witness property is easily veriﬁed, i.e., the (equivalent) implications ∀x,y,w W (x,y,w)→ ψ (x,y) … have an **elementary proof**.
> — `.prior-art/mcconnell11.txt:1028-1046` **已核**
> `[抽文]` 抽文把两个小标题写成 `Checkability:.` 与 `Simplicity:.`（标题自带句点）。

⇒ 落地含义：外生 oracle 必须**比内核简单**（不调插件、不调内核，按方向定义重算覆盖）—— 与 `B17` 的 `equiv_classes` 同形，正是设计文档已经用过的那条路（`…设计文档.md:180-181` 已把这条理由写进推荐）。

⚠️ **必须两句话都说，不能只说一句**（否则 (c) 会被做成「换个人说同一句话」）：

| 病 | 判据来自 | 内容 |
|---|---|---|
| **代理量不等价** | 本设计的实测 | `代价 = 0` 只对**锚点本身**成立，`覆盖 = reachable(payload)` 大得多 ⇒ 代理只在 `keyset` 上恰好等价 |
| **复算不算检查** | McConnell §5.5 极端 1 | 重放被检查对象的运算 ⇒ 与被检查对象**共享同一个盲点**（`false-green` 形状 3） |

**本项结论**：**逐字印证**。推荐 (c) 是三条出路里**唯一没有被成熟文献排除**的那条；而 `§3` 与 `§5` 合流（`B16` checker 重写）的判断也成立 —— 两条出路指向同一个改动。

---

## 四、「覆盖不漏」是硬要求却无检查（清单 §4）

**推荐原文**：(2) 做成**第 8 个度量**（像健全性那样只报不判），C 修完再升级成判据。

### 成熟答案 A —— 基线就红的判据，成熟做法是**冻结既有违规 + 对新增违规照样红**

> Enabling a new lint rule as "error" can be challenging when the codebase has many violations and the rule isn’t auto-fixable. Unless the rule is enabled during the early stages of the project, it becomes harder and harder to enable it as the codebase grows. **Existing violations must be resolved before enabling the rule**, but while doing that other violations may occur. To address this, ESLint provides a way to **suppress existing violations** for one or more rules. **While the rule will be enforced for new code, the existing violations will not be reported.** This way, you can address the existing violations at your own pace.
>
> **Only rules configured as "error" are suppressed. If a rule is enabled as "warn", ESLint will not suppress the violations.**
> — https://eslint.org/docs/latest/use/suppressions（ESLint 官方文档 *Bulk Suppressions*）**已核**（2026-10-06 读全文，正文缓存于 `.webcache/eslint-suppressions.html`）
> `[网页]` 页面把 `"error"` / `"warn"` 包在 `<code>` 里（引号是直的）；`isn’t` 用的是弯撇号。

⇒ **印证**：原文第一句 **「Existing violations must be resolved before enabling the rule」** 就是清单那条「**基线就红的判据过不了注入验证**」的成熟版 —— 而且它把这件事当成**一个有名字的、已知的阶段问题**，不是特例。

⇒ **修正（这一条很值钱）**：清单 §4 只给了三条出路 ——(1) 写检查（过不了注入验证）/(2) 先做度量 /(3) 降级。ESLint 给了**第 (4) 条**：

| | 会红吗 | 代价 |
|---|---|---|
| (2) 度量 | **永远不红** | 只能靠人看输出 |
| **(4) baseline** | **新增违规会红** | 多一份要提交进仓库的清单（`eslint-suppressions.json`） |

⇒ **baseline 严格强于纯度量**：它**保留了「能红」**，而度量把「能红」丢掉了。而清单 §4 自己写过一句警告 ——「**一个『声称是硬的、实际没人管』的要求，比一个明说是软的要求危险得多**」—— baseline 正是对这个警告的直接回应。

⇒ 而且 ESLint 配了一条**只减不增的纪律**，与本设计的账本纪律方向相反、目的相同：

> If you run ESLint again you will notice that it **exits with a non-zero exit code and an error is reported about unused suppressions**. … `There are suppressions left that do not occur anymore. Consider re-running the command with --prune-suppressions.`
> — 同上 **已核**
> `[网页]` 那句是终端消息，页面把开关渲染成带反引号的 `` `--prune-suppressions` ``。

⇒ 落地要求：若走 baseline，**必须同时规定「baseline 里的条目一旦不再发生就报错」**。否则 baseline 会退化成**永久豁免** —— 那正是「空转与通过长得一模一样」。
（注意与 `§B9`「历史记录只增不改」的区别：`§B9` 管的是**方向的失效历史**，baseline 管的是**判据的豁免清单**，两者纪律相反，不能混。）

### 成熟答案 B —— 「先报后判」要配一个**里程碑**，不是「修完再说」

> **rust-2024-compatibility** — Lints used to **transition code from the 2021 edition to 2024**
> **future-incompatible** — Lints that detect code that has **future-compatibility problems**
> — https://doc.rust-lang.org/rustc/lints/groups.html（The rustc book, *Lint Groups*）**已核**（2026-10-06 读全文）

⇒ **印证 + 修正**：成熟答案给「先报后判」配的是**一个具名的里程碑**（下一个 edition），不是一个开放的「等修完」。
⇒ 并且注意：`rust-2024-compatibility` 这个**过渡组里三种严重度混装**（`warn-by-default` / `deny-by-default` / `allowed-by-default`）—— 即**「过渡组」本身是一个容器，不要求组内一致**。
⇒ 这正好是本设计 §1 硬要求表的现状：**四行现在处于四种不同的守备状态**（第一行内核构造守 / 第二行明说不是硬要求 / 第三行度量 + 只跑一条路的判据 / 第四行只有文档）。⇒ **不必强行让四行一致**，要的是**每一行的状态都被写明**（而 §1 的 ⚠️ 已经在做这件事）。

**本项结论**：**印证 + 修正**。建议把推荐 (2) 改成 **(2′) 度量 + baseline（冻结既有违规、对新增红）+ 一个具名的升级里程碑**。

---

## 五、进步量守卫（清单 §6）

**推荐原文**：「进步」= **payload 的细化度**（不是叶容量），**N = 10**（照前例）。

> When longValuesOK is true, it is expected that successive levels of the SP-GiST tree will absorb more and more information into the prefixes and node labels of the inner tuples, **making the required leaf datum smaller and smaller**, so that eventually it will fit on a page. To prevent bugs in operator classes from causing infinite insertion loops, **the SP-GiST core will raise an error if the leaf datum does not become any smaller within ten cycles of choose method calls.**
> — `.prior-art/spgist.txt:554` **已核**

⇒ **逐字印证两项**：「进步」= **leaf datum 变小** = **payload 细化度**（**不是**叶容量）；**N = 10**。

辅证（同一份文档，讲 `picksplit` 被反复调用的那一支）：

> In this case the point of the operation is to **strip off a prefix and produce a new, shorter leaf datum value**. **The call will be repeated until a leaf datum short enough to fit on a page has been produced.**
> — `.prior-art/spgist.txt:429` **已核**

⇒ 即「反复调用直到 payload 变短到装得下」与「十次没变小就报错」是同一件事的两面。

物理预算那一半（清单 §6 说的「终止判据 = 物理预算 + 进步量守卫」两件套）：

> **Individual leaf tuples and inner tuples must fit on a single index page (8kB by default).**
> — `.prior-art/spgist.txt:548` **已核**

### ★ 顺带答掉了清单 §6 里那个「待确认」

清单 §6 写着：**「这一项是全表里唯一可能要重验的，做之前必须先答这个问题」** ——「细化度」要不要**插件自己报**？

原文那句话给了答案：

> **the SP-GiST core will raise an error** if the leaf datum does not become any smaller within ten cycles of choose method calls.

⇒ **内核计数、内核报错**。原文算的是 **leaf datum 的大小**，一个**内核看得见**的量，插件不需要新报任何东西。
⇒ **答案：不需要动接口。**（本设计的对应物：`payload` 没有「字节大小」，可算的候选是 payload 的**项数**或**覆盖的收紧量** `|覆盖(父)| − |覆盖(子)|`。叶容量已被推荐排除，理由照旧：叶容量是**后果**、会滞后。）

⚠️ **一个连带的依赖（必须写进裁定）**：要算「覆盖的收紧量」，内核得能拿到 `覆盖` —— 而那正是 `§3` 要建的**外生 oracle**。
⇒ **`§3` 是 `§6` 的前置**：外生 oracle 定义不了覆盖，进步量守卫就没有量可算。清单 §5「依赖顺序」那一节没有这条边，**要补上**。

**本项结论**：**逐字印证**，并且**把唯一的「可能要重验」答成了「不用重验」**；同时**新增一条依赖边 `§3 → §6`**。

---

## 六、删除流程（清单 §7）

**推荐原文**：**做**，但**排在 1–4 之后**。

### 成熟答案 A —— 删除会造出单子节点，所以要配 Path Compression 那一半

> **Delete:** The implementation of deletion is symmetrical to insertion. The leaf is removed from an inner node, which is shrunk if necessary. **If that node now has only one child, it is replaced by its child and the compressed path is adjusted.**
> — `.prior-art/art13.txt:636-639`（Leis / Kemper / Neumann, *The Adaptive Radix Tree*, ICDE 2013）**已核**

> **Path compression**, the second technique, **removes all inner nodes that have only a single child.**
> — `.prior-art/art13.txt:496-498` **已核**

⇒ **逐字印证**清单 §7 那句「ART 说删除会造出**单子节点**，所以要配 `Path Compression` 那一半」。

### 成熟答案 B —— 独立第二源，同一答案、同一祖先

> **Tree Shrink:** The internal nodes are merged together to **eliminate all single child internal nodes**. This strategy is adapted from structures like the **Patricia trie** that aim at reducing the height of the tree as much as possible.
> — `.prior-art/spgist01.txt:223-225` **已核**

⇒ **印证（不是孤例）**：ART 与 SP-GiST 两个独立系统给出同一个答案，都指向同一个祖先（Patricia）。
⚠️ **但本设计比它们麻烦一处**：ART / SP-GiST 里单子节点是**浪费**（路径变长），合并**只赚不赔**；本设计合并会**改变层数**，而层数是 `§K5` 良基秩的载体。
⇒ 不过设计文档已经指出：`Tree Shrink` 那一档「把区分信息**并进父节点**（ART 的 `Path Compression`，代价是『partial key 不能就这么丢掉』，所以它要**存下来**），要不要走这一档是 §10 的留白」（`…设计文档.md:330-332`）。⇒ 落地时**不是新增规则**，而是让 `§K2` 的字面（「不建这一层」）在**删除路径**上也生效。

### 成熟答案 C —— 两条判据逐字对得上，并且**印证了次序**

> **3.4 Removing instances**
> Removing an instance from the lattice might also be considered for some applications. **This problem is much easier than adding an instance.** One simple method is to **start from the lowest pair containing the instance to remove and to search upwards recursively, verifying if the parents are still necessary. When deleting a parent, care must be taken for relinking nodes, if necessary.** The number of pairs to examine is limited to pairs (X, X') where X' ⊆ f({x}), x being the instance. **This process will therefore be much more efficient than adding an instance.**
> — `.prior-art/godin95.txt:475-481` **已核**

⇒ **印证两条判据**（向上递归验证父是否仍必要 + 必要时重新连接）—— 与清单 §7 写的两条**逐字对得上**。

⇒ **并且印证了「排在 1–4 之后」**：原文两次说删除**比增加容易**（"much easier than adding an instance" / "much more efficient than adding an instance"）⇒ 把删除排在插入路径（§1 / §2 / §4）之后，是**成熟答案支持的次序**，不是排期上的偷懒。

**本项结论**：**印证**（两条判据 + 次序）；落地时要处理的是 `§K2` 在删除路径上生效，而不是新增规则。

---

## 七、第二批文献：跨领域前例（Bε-tree / R\*-tree / Guttman 84 / X-tree 96 / Driscoll 89）

**为什么要另开一批**：第一批（§一–§六）用的语料全部来自**检索结构同一支**（GiST / SP-GiST / GIN / ART / 概念格 / certifying algorithms）。同一条支流里的做法**共享同一批默认假设**，所以「几篇都这么说」的证明力，弱于「**另一个邻域独立地撞上同一件事**」。

这一批就是去**别的邻域**找的：

| 邻域 | 文献 | 解决什么 |
|---|---|---|
| **写优化的存储引擎** | Bε-tree（USENIX ;login: 2015） | 「放不进主结构的项」放在哪 |
| **空间索引** | R\*-tree（SIGMOD 1990） | 溢出时先重插还是先分裂 |
| **空间索引** | Guttman 84（R-tree 原文） | 删除后欠满节点怎么办 |
| **空间索引** | X-tree（VLDB 1996） | 没有好分裂时怎么办 |
| **持久化数据结构** | Driscoll / Sarnak / Sleator / Tarjan（JCSS 1989） | 多版本怎么存 |

**核验状态**：五篇**全部已核**（本地 PDF 抽文，逐字）。其中**四篇是扫描件** ⇒ 引文块会带 `[抽文]` 注说明字形混淆（见「怎么读」第二张表）。

**本批要回答的四件事**：§1 的 (1) 到底在选什么 ｜ §2 的出路表是不是漏了 ｜ §7 的处置菜单有几条 ｜ 以及**清单 §11 里那条卡住 §8 的「Driscoll 1989 未核」**。

---

### 7.1 §1：Bε-tree 把待处理的项**放进了节点自己** —— (1) 的选择其实是**三选**

Bε-tree 的内部节点划一块空间当 buffer，存的是「将来要应用到这片叶子底下的更新」：

> The distinguishing feature of a B ε-tree is that internal nodes also allocate some space for a buﬀer, as shown in Figure 1. The buﬀer in each internal node is used to store messages, which encode updates that will eventually be applied to leaves under this node. **This buﬀer is not an in-memory data structure; it is part of the node and is written to disk, evicted from memory, etc., whenever the node is.**
> — `.prior-art/betree.txt:64-71`（Bender / Farach-Colton / Jannen / Johnson / Kuszmaul / Porter / Yuan / Zhan, *An Introduction to Bε-trees and Write-Optimization*, USENIX ;login: 2015）**已核**
> `[抽文]` `buﬀer` 的 `ﬀ` 是连字（= `ff`）；抽文里 `B ε-tree` 的 `ε` 前多一个空格（原文是 `Bε-tree`）。

插入就是往**根的 buffer** 里放一条消息，不是直接改叶子：

> **Inserts and deletes.** Insertions are encoded as “insert messages”, addressed to a particular key, and added to the buﬀer of the root node of the tree.
> — `.prior-art/betree.txt:76-78` **已核**

⇒ **修正（§1 的 (1) 从二选变成三选）**。§一 把选择框成「**物化**（GIN pending list）vs **不物化**（只记事件）」。Bε-tree 给的是**第三种**：**物化，但物化在结构自己身上** —— 不是一份旁挂清单，而是**节点的一块空间**。

| 形态 | 项在哪 | 查询要多扫吗 | 事后能补吗 |
|---|---|---|---|
| GIN pending list | **旁挂**清单（物化） | 要（"searches must scan the list of pending entries"） | 能（vacuum 时并回主结构） |
| **Bε-tree buffer** | **节点内部**（物化，随节点落盘） | 要（见下） | **能**（消息最终投递到叶） |
| 本设计推荐 (1) | **只在账里**（不物化） | **不要** | **不能** |

⇒ 三行的差别**不在「要不要记」，在「记在哪」**：旁挂清单在**结构外**，节点 buffer 在**结构内**。
⇒ 对裁定的实际含义：如果选「可补」那一边，**不必新增一份旁挂结构** —— 可以让**方向节点自带一块待处理区**。这个形态是 Bε-tree 给的，GIN 没给。

Bε-tree 靠一条不变量把「待处理的项不许丢」钉住：

> **Point and range queries.** Messages addressed to a key k are guaranteed to be applied to k’s leaf or in some buﬀer along the root-to-leaf path towards key k. **This invariant ensures that point and range queries in a B ε-tree have a similar I/O cost to a B-tree.** … However, in a B ε-tree, answering a query also means **checking the buﬀers in nodes on this path for messages, and applying relevant messages before returning the results of the query.**
> — `.prior-art/betree.txt:148-158` **已核**
> `[抽文]` 抽文原样：`guaranteed to be applied to k’s leaf or in` / `some buﬀer along the root-to-leaf path towards keyk.`（`keyk` = `key k`，词间空格被吃掉）· `returning the re-` / `sults of the query.`（断行连字符）

⇒ **这条不变量的形状要单独抄下来**：**待处理的项必须落在查询路径上**。
⇒ 本设计的对应物：查询是从根沿 `劈开` 往下走的 ⇒ **「根记账」这条推荐 (1) 恰好满足这个形状**（根在每一条查询路径上）。
⇒ 所以推荐 (1) 不只是「与本家族的空值纪律同源」—— 它还**满足了存储引擎那边独立给出的路径条件**。这是**跨邻域的印证**，比第一批的「GIN 也这么做」强一档。

---

### 7.2 §2：X-tree 的 supernode **印证 (4)**；R\*-tree 的 forced reinsert 是一条**不适用**的候选

#### A. X-tree：没有好分裂时，把节点**变大**，而不是造一个坏分裂

> The X-tree therefore **avoids overlap whenever it is possible without allowing the tree to degenerate; otherwise, the X-tree uses extended variable size directory nodes, so-called supernodes.**
> — `.prior-art/xtree96.txt:305-308`（Berchtold / Keim / Kriegel, *The X-tree*, VLDB 1996）**已核**

> The basic goal of supernodes is **to avoid splits in the directory that would result in an inefficient directory structure.** The alternative to using larger node sizes are highly overlapping directory nodes which would require to access most of the son nodes during the search process.
> — `.prior-art/xtree96.txt:350-355` **已核**

触发条件写得很死，而且明说是**最后手段**：

> **Supernodes are created during insertion only if there is no other possibility to avoid overlap.** In many cases, the creation or extension of supernodes may be avoided by choosing an overlap-minimal split axis
> — `.prior-art/xtree96.txt:379-382` **已核**

代码里就是那个分支（**这一块按抽文原样引** —— 花括号被 OCR 认错，属**结构性**破坏，不是词形，改写反而失真）：

> else // there is no good split  
> t  
> *new-node = new X-SuperNode();  
> (*new-node)->s.et-mbrs(mbrs);  
> return SUPERNODE;  
> — `.prior-art/xtree96.txt:494-498` **已核**
> `[抽文]` 读法：`t` = `{`（被认成 `t`），`s.et-mbrs` = `set_mbrs`，块末的 `1 )` = `}`。抽文里还有 `1` 与 `I` 混用（`I` = `}`）。

⇒ **印证 (4)**，而且补上了**触发条件的写法**：§二 的 (4) 只说「让项留在父这一层」，**没说什么时候才该这么做**。X-tree 给的是「**only if there is no other possibility**」—— (4) **不是默认档，是最后手段**。
⇒ 这与 §K2 的字面**同形**：§K2 也是「**不建这一层**」，即**先试建、建不出来才不建**。两者都是「**能细分就细分，分不了才留在这一层**」。
⇒ ⚠️ **只取「允许超容」，不取优先级函数**：X-tree 用 `c_t · type + c_l · level + c_s · size` 决定哪些 supernode 留在内存（`xtree96.txt:390-391`）—— 那是**存储层**的事，本设计没有存储层。

#### B. R\*-tree：第一次溢出**不分裂**，先把远处的项**重插一遍**

> **Algorithm OverflowTreatment**
> **OT1** If the level is not the root level and this is the **first call** of OverflowTreatment in the given level during the insertion of one data rectangle, then **invoke Reinsert** else invoke Split
> — `.prior-art/rstree.txt:566-573`（Beckmann / Kriegel / Schneider / Seeger, *The R\*-tree*, SIGMOD 1990）**已核**
> `[抽文]` 扫描件原样：`OTl If the level 1s not the root level and this IS the first` / `call of OverflowTreatment m the given level` / `durmg the Insertion of one data rectangle, then` / `mvoke Reinsert` / `else` / `mvoke Split`（`OTl`→`OT1`、`1s`→`is`、`IS`→`is`、`m`→`in`、`durmg`→`during`、`mvoke`→`invoke`）

> **Algorithm Reinsert** … Remove the first p entries from N and adjust the bounding rectangle of N
> — `.prior-art/rstree.txt:574-581` **已核**
> `[抽文]` 步骤标签 `RI1` `RI2` `RI3` `RI4` 在抽文里被排到正文**之后**（第 585-587 行），所以引文按正文顺序写、标签另注。

> The experiments have shown that **p = 30% of M** for leaf nodes as well as for non-leaf nodes yields the best performance
> — `.prior-art/rstree.txt:596-598` **已核**
> `[抽文]` 抽文里 `non-` 与 `leaf` 被断行拆开（`non-` 换行 `leaf nodes yields the best performance`）。

> Summarizing, we can say … **Forced reinsert changes entries between neighboring nodes and thus decreases the overlap** … As a side effect, storage utilization is improved … **Due to more restructuring, less splits occur** … the cpu cost will be higher now since the insertion routine is called more often
> — `.prior-art/rstree.txt:604-613` **已核**
> `[抽文]` 扫描件原样：`Summarizmg. we can say` · `l Forced remsert changes entries between neighboring` · `storage utlllzatton IS unproved` · `less sphts occur` · `smce the` / `msertlon routme 1s called more often`（`l` = 项目符号、`remsert`→`reinsert`、`utlllzatton`→`utilization`、`unproved`→`improved`、`sphts`→`splits`、`smce`→`since`、`msertlon`→`insertion`、`routme`→`routine`）

⇒ 这一条的形状是「**先别急着判定收不住，把项挪到别的节点去试试**」，代价记在 **CPU**（"the cpu cost will be higher"），收益是**更少的分裂** ⇒ 代价仍是**性能**，与 §K8 同向。

⚠️ **但它对 §2 不适用 —— 排除它的理由比采纳它更值钱**：

| | R\*-tree 要治的病 | §2 要治的病 |
|---|---|---|
| 病根 | **重叠**，根因是**分裂轴选得不好** | **无穷字母表** |
| 为什么重插有效 | **别的节点接得住**那些项 | **没有任何子方向接得住**（§2 实测：一改就 `IndexError`） |
| 结论 | 采纳 | **排除** —— 无对象可挪 |

⇒ 这个排除本身**加固了 §2 的判断**：§2 的问题**不是「分裂分得不好」，是「分不了」**。凡是「换一种分法 / 挪个地方」的办法（X-tree 的 overlap-minimal split、R\*-tree 的 reinsert）**都够不着它**。

---

### 7.3 §7：两条**相反**的成熟做法 —— Bε-tree 把删除**做成插入**，Guttman 立刻收拾

#### A. Bε-tree：删除也是一条消息

> Bε-trees delete items by inserting “tombstone messages” into the tree. These tombstone messages are ﬂushed down the tree until they reach a leaf. When a tombstone message is ﬂushed to a leaf, the B ε-tree discards both the deleted item and the tombstone message. **Thus, a deleted item, or even entire leaf node, can continue to exist until a tombstone message reaches the leaf. Because deletes are encoded as messages, deletions are algorithmically very similar to insertions.**
> — `.prior-art/betree.txt:126-135` **已核**

⇒ **印证 §7 的「排在 1–4 之后」**：删除**复用插入那条路**（都是消息、都进 buffer）⇒ 先把插入路径做对，删除是**同一条路**上的第二种消息 ⇒ 次序不是排期上的偷懒。
⇒ 而且**删除后的项可以继续存在**（直到 tombstone 到叶）—— 与 §B9「只追加账本」同向：**不回头改历史，用一条新记录把它盖掉**。

#### B. Guttman 84：欠满就**压掉节点 + 把项重插回去**

> **Algorithm CondenseTree** Given a leaf node L from which an entry has been deleted, … **eliminate the node If it has too few entries and relocate its entries** … Propagate node elimination upward as necessary.
> — `.prior-art/guttman84.txt:328-332`（Guttman, *R-Trees: A Dynamic Index Structure for Spatial Searching*, SIGMOD 1984）**已核**
> `[抽文]` 扫描件原样：`node L from whch an entry has been` / `deleted, ehnnnate the node If it has too few` / `entnes and relocate its entnes Propagate` / `node ehmmatron upward as necessary.`（`whch`→`which`、`ehnnnate`→`eliminate`、`entnes`→`entries`、`ehmmatron`→`elimination`）

> **CT6 [Re-insert orphaned entries]** Re-insert all entries of nodes in set Q
> — `.prior-art/guttman84.txt:358-359` **已核**

> The procedure outlined above for disposing of under-full nodes differs from the corresponding operation on a B-tree, in which two or more adjacent nodes are merged … **We chose re-insertion instead for two reasons** … it accomplishes the same task and is easier to implement because the Insert routine can be used …
> — `.prior-art/guttman84.txt:368-382` **已核**
> `[抽文]` 扫描件原样：`for dlsposmg of under-full nodes dflers from` / `the correspondmg operation on a B-tree, m which two or more adlacent nodes are merged` · `We chose re-msertlon` / `mstead for two reasons first., it accom` / `phshes the same t* and 1s easier to` / `rmplement because the Insert routme can` / `be used`（`dlsposmg`→`disposing`、`dflers`→`differs`、`correspondmg`→`corresponding`、`adlacent`→`adjacent`、`re-msertlon`→`re-insertion`、`mstead`→`instead`、`accom phshes`→`accomplishes`、`t*`→`task`、`rmplement`→`implement`、`routme`→`routine`；`reasons` 后的冒号与 `used` 后的句点在抽文里没有 —— 所以引文在这两处用 `…` 断开，**没有替原文补标点**）

⇒ **§7 的处置菜单从「一条」变成「三条」**：

| 办法 | 谁用 | 代价 |
|---|---|---|
| **把项重插回树**（`CT6`） | Guttman 84 | 复用 `Insert`，实现最省；但**要重走一次下降** |
| **与兄弟合并** | B-tree | 需要「邻接」概念 —— R-tree 里没有（原文："there is no adjacency in the B-tree sense"） |
| **压掉单子节点**（Path Compression） | ART ｜ SP-GiST | 要把区分信息**并进父**（所以要**存下来**） |

⇒ 本设计 §六 已经有第三条（ART / SP-GiST）。Guttman 补的是**第一条** —— 那是**「不压节点」**的出路。
⇒ ⚠️ 但第一条在本设计里**不是免费的**：重插要走一次下降 ⇒ **可能改变层数**，而层数是 `§K5` 良基秩的载体（与 §六 成熟答案 B 的注意事项同源）。

★ **而且本次发现一条新的待定口径**：Guttman 的重插之所以「省」，是因为它复用的是 `Insert`；本设计的重插要经过 `§I3 代价` 与 **`§I4 劈开`** —— 而删完只剩一项时，`劈开` 会说 **`None`**。

⇒ **`§I4` 的 `None` 在删除路径上是什么含义？** 与插入路径上（「这一层分不出下层表达不出的东西」）**是同一件事吗**？

| 若… | 含义 | 后果 |
|---|---|---|
| **同一件事** | 删除路径上的 `None` 也是「分不出」 | 保留这一层 ⇒ 与 (4) 合流，**不用新规则** |
| **不同** | 插入的 `None` 是「分不出」，删除的 `None` 是「**没必要**」 | **`§I4` 的语义要分路径** ⇒ 接口层的事，**要写进裁定** |

⇒ 这一条**清单 §7 里没有**，本次新发现。它与 §六 的「让 §K2 在删除路径上生效」是**两条不同的口径**，不能合并。

---

### 7.4 §8：清单 §11 里那条卡住排期的「Driscoll 1989 未核」—— **已核**

清单 §8 写的是：「接口已验证，**现在才允许做**。前例 Driscoll 1989 **未核** ⇒ 下一轮核完再动」。本批把它核了。

#### A. 两条通用方法（原文："applies to any ephemeral linked structure"）

> We shall propose two methods. **The first and simpler is the fat node method, which applies to any ephemeral linked structure and makes it persistent at a worst-case space cost of O(1) per update step and a worst-case time cost of O(log m) per access or update step. More complicated is the node-copying method, which applies to an ephemeral linked structure of constant bounded in-degree and has an amortized time and space cost of O(1) per update step and a worst-case time cost of O(1) per access step.**
> — `.prior-art/driscoll89.txt:203-210`（Driscoll / Sarnak / Sleator / Tarjan, *Making Data Structures Persistent*, JCSS 38:86-124, 1989）**已核**
> `[抽文]` 扫描件原样：`cost of 0( 1) per update step` · `cost of 0( 1) per access` / `step.`（`0( 1)` → `O(1)`，`O` 被认成 `0`）

fat node 的两条缺点（原文自己列的）：

> The fat node method applies to any linked data structure and uses only O(1) space per ephemeral update step in the worst case, **but it has two drawbacks. First, the fat nodes must be represented by linked collections of fixed-size nodes.** This poses no fundamental difficulty but complicates the implementation. Second, … **which pointer in a fat node to follow when simulating an access step takes more than constant time.** If the values of a field within a fat node are ordered by version stamp and stored in a binary search tree, simulating an ephemeral access or update step takes O(log m) time.
> — `.prior-art/driscoll89.txt:296-314` **已核**
> `[抽文]` 抽文里是 `linked date structure`（`data` 被认成 `date`）；`Second, choos-` 后面**插进了图 1 的题注**（`FIG. 1. A partially persistent search tree built using the fat node method, ...`），再回到 `ing which pointer in a fat node to follow` —— 所以引文在这一处用 `…` 断开。

定义（partial / full）：

> The structure is **partially persistent** if all versions can be accessed but only the newest version can be modified, and **fully persistent** if every version can be both accessed and modified.
> — `.prior-art/driscoll89.txt:45-47` **已核**

#### B. Overmars 的三条老办法 —— 其中第二条**就是本设计现在的形态**

> One method is to **store every version explicitly, copying the entire ephemeral structure after each update operation.** This costs Θ(n) time and space per update. An alternative method is to **store no versions but instead to store the entire sequence of update operations, rebuilding the current version from scratch each time an access is performed.** If storing an update operation takes O(1) space, this method uses only O(m) space, but **accessing version i takes Θ(i) time** even if a single update operation takes O(1) time.
> — `.prior-art/driscoll89.txt:212-219` **已核**
> `[抽文]` 扫描件原样：`This costs Q(n) time and space per update.` · `accessing ver-` / `sion i takes G(i) time`（`Q(n)`→`Θ(n)`、`G(i)`→`Θ(i)`）

★ **这一条最贴**：本设计的持久化形态**就是 Overmars 的第二条** —— 「只追加账本（§B9）+ 规范重建（已是一个度量）」。
⇒ 所以 §8 不是「没有前例」，而是**有前例、并且前例已经量出了代价**：

| 形态 | 空间 | 访问版本 i | 本设计现状 |
|---|---|---|---|
| 显式存每版（Overmars 1） | Θ(n)/次更新 | O(1) | 否 |
| **只存账 + 重放（Overmars 2）** | **O(m)** | **Θ(i)** | **是** |
| 每隔 k 版存一份（Overmars 3，混合） | O(m) + 快照 | O(i/k + k) | 否 —— **可选的升级** |
| fat node（Driscoll §2.2） | **O(1)/步** | O(log m) | 否 |
| node-copying（Driscoll §2.3） | **O(1)/步**（摊还） | **O(1)** | 否 |

⇒ **§8 的「纯工程，接口已冻结」现在有了具体形态**：

- **只要「能回看旧版本、不要求快」⇒ 现状就够**，但**必须把代价写进文档**：访问版本 i 是 **Θ(i)**。
  不写，读者会把「能回看」读成「回看很快」—— 那是「空转与通过长得一模一样」的另一种形态：**能力在，代价没写**。
- **要访问快 ⇒ Driscoll 的两条是通用的**（原文："applies to any ephemeral linked structure"）⇒ **不动 §I1–§I7** ⇒ 与「接口已冻结」不冲突。
- ⚠️ **一条限制，落地前必须答**：node-copying 要求底层结构**入度常数**：

> Nevertheless, if we assume that the underlying ephemeral structure has nodes of **constant bounded in-degree** and we allow sufficient extra space in each node of the persistent structure, then we can derive an O(1) amortized bound on the number of nodes copied and the time required per update step.
> — `.prior-art/driscoll89.txt:325-329` **已核**
> `[抽文]` 扫描件原样：`then we can derive an 0( 1) amortized bound on the number of nodes copied`（`0( 1)`→`O(1)`）

⇒ **方向节点的入度是不是常数**要实测：

| 若… | 入度 | 可走的路线 |
|---|---|---|
| 树（每个方向一个父） | 1，**常数** | node-copying（O(1) 访问） |
| **DAG**（一个方向被多个父共享） | **无界** | 只能 fat node（O(log m) 访问） |

⇒ **这是 §8 的第一件事，排在写代码之前** —— 它决定用哪条路线。

---

### 7.5 本批对 §11 引用栏的影响

新增「已核」五条（见 §九 的表）。**同时 §11 的「未核」栏少了三条**：`R\*-tree forced reinsert`、`X-tree supernode`、`Driscoll 1989` —— 清单 §11 那七条里，**本批清了三条**。

**仍「未核」**：机制设计 ｜ ANN-Benchmarks ｜ CRDT / join-semilattice ｜ Cousot 那一支（抽象解释）。

⚠️ **一条要澄清的**（与 §九 里 `dred1993.txt` 那条同类）：`.prior-art/nncost01.*` **不是** X-tree。
它是 ICDT 2001 的 *On Optimizing Nearest Neighbor Queries in High-Dimensional Data Spaces*（`nncost` = nearest-neighbor cost model），里面只在**二手引用**里提到 supernode。**引 X-tree 要引 `xtree96.*`**（VLDB 1996，`P028.PDF`）。

**本批结论**

| 项 | 成熟答案 | 结论 |
|---|---|---|
| §1 (1) 根记账 | Bε-tree 节点内 buffer | **修正**：三选（旁挂 / 节点内 / 不物化）；且「待处理项必须在查询路径上」**独立印证了「根」这个位置** |
| §2 (4) 项留在父层 | X-tree supernode | **印证**，并补上**触发条件**（"only if there is no other possibility"） |
| §2 出路表 | R\*-tree forced reinsert | **排除**（重插要有「别的节点接得住」，无穷字母表下无对象可挪） |
| §7 处置菜单 | Bε-tree tombstone ｜ Guttman `CondenseTree` | **印证次序**；菜单补到**三条**；★ **新发现一条待定口径**（`§I4` 的 `None` 在删除路径上的含义） |
| §8 持久化 | Driscoll 89 ｜ Overmars 三条 | **前例已核** ⇒ 形态可定；★ **新发现一条前置问题**（方向节点入度是否常数） |

---

## 八、顺带核到的三条（不属于任何一项裁定，但会改变依据强度）

### 8.1 GiST 的 `Consistent` 契约 —— `§K8` 有 1995 年的独立前例

> Note that **an accurate test for satisﬁability is not required here: Consistent may return true incorrectly without affecting the correctness of the tree algorithms. The penalty for such errors is in performance**, since they may result in exploration of irrelevant subtrees during search.
> — `sources/gist1995.txt:358-363` **已核**

⇒ **印证 `§K8` 的不对称**（假阳允许、代价在性能、不影响正确性），而且**比 McConnell 2011 早 16 年** —— 说明这条契约在检索结构里有**独立**的来源，不是从 certifying algorithms 借来的。
⇒ **对 `§3` 的含义（加固）**：GiST 明说 `Consistent` **允许不精确**。所以 `B16` 拿 `代价 = 0`（一个**精确性**代理）去判 `覆盖`（一个**只要求不许假阴**的量）—— **在原理上就是过强的**，不只是 `reach` 的实现问题。

### 8.2 GiST 的 `Union` / `Decompress` 都是**单向**的 —— `§1` 第二行「不是硬要求」有前例

> **Union(P):** given a set P of entries … returns some predicate r that holds for all tuples stored below ptr₁ through ptrₙ. This can be done by ﬁnding an r such that **(p₁ ∨ … ∨ pₙ) → r** .
> — `sources/gist1995.txt:373-383` **已核**
> `[抽文]` 抽文原样：`returns some predicate r that holds for all` / `tuples stored below ptr` / `/1` / `through ptr` / `/n` / `/29 . This can be done by ﬁnding an r such that /n28 p /1 /_ /:/:/: /_ p /n /29 /! r .`（下标被拆行，`∨` `→` 被换成 `/_` `/!`）

> Note that this is a potentially **“lossy” compression, since we do not require that p ≡ r** .
> — `sources/gist1995.txt:387-391` **已核**
> `[抽文]` 这一整条 `Decompress` 在抽文里是 `given a compressed representation E /=/n28 /n19/; ptr/n29 ,w h e r e/n19 /= Compress/n28 p /n29 , returns an en-⏎try /n28 r /; ptr/n29 such that p /! r .` —— 斜体的 `where` 被逐字拆成 `w h e r e`，`en-⏎try` 是断行连字符（`⏎` = 换行）。**内容与上面那句一致**（`p /! r` 即 `p → r`）。

⇒ **印证 `§1` 第二行**：`Union` 只要求**单向蕴含**（子们的并 → 父的键），**不要求相等** ⇒ 父的键**允许比子们的并更大**。这与「`覆盖(子) ⊆ 覆盖(父)` 不是硬要求，越界只赔假阳」**同向**。
⇒ 也**印证 `§I5`**（`…设计文档.md:271`：「解码结果**覆盖**编码前（可以更粗，不可以更细）」）—— 与 `p → r`、`不要求 p ≡ r` 逐字同形。

### 8.3 `AdjustKeys` 没有出现在设计文档里（本次新核）

见 §一 · 成熟答案 B。它是**维护路径上父键重算**的原文依据，`§10.2` 讲「维护路径」时可以直接引。

---

## 九、对设计文档 `§11 引用` 的影响

### 新增「已核」

**第一批**（§一–§六 用到的）：

| 文献 | 新支撑什么 | 出处 |
|---|---|---|
| **PostgreSQL 18 文档 · GIN（§65.4.4.1）** | `§10.2` 出路 (1) 的前例：「放不进主结构的项」进**有名字、可计数、可开关**的旁挂清单；代价记在**性能**栏 | `.prior-art/gin.txt` |
| **Hellerstein / Naughton / Pfeffer 1995 · `AdjustKeys`** | `§10.2` 的**维护路径**依据：父的键在每次插入后**重算成子们的并**；并解释**为什么本设计不能照做**（根是外生的） | `sources/gist1995.txt:744-757` |
| **McConnell 等 2011 · §5.5「两个被排除的极端」** | `§7.0` / `B16` 的**重写理由**：重放程序运算**不算检查**；`W := ψ` **也不算**。比现有的「Simplicity」引用更贴 `B16` 的病灶 | `.prior-art/mcconnell11.txt:1063-1071` |
| **ESLint 文档 · Bulk Suppressions** | `§7.4` / `§7.5` 的**形态依据**：基线就红的判据用 **baseline** 处置，且 baseline 条目**不再发生时必须报错** | eslint.org（2026-10-06） |
| **The rustc book · Lint Groups** | 同上：「先报后判」要配**具名里程碑**；过渡组**允许组内严重度不一致** | doc.rust-lang.org（2026-10-06） |

**第二批**（§七 用的，全部跨邻域）：

| 文献 | 新支撑什么 | 出处 |
|---|---|---|
| **Bender 等 2015 · Bε-tree** | `§10.2` 出路 (1) 的**第三条形态**：待处理项可以**物化在节点自己身上**（不是旁挂清单）；并给出「**待处理项必须在查询路径上**」这条不变量 —— 独立印证「根」这个位置 | `.prior-art/betree.txt:64-71, 76-78, 148-158` |
| **Berchtold / Keim / Kriegel 1996 · X-tree** | §2 出路 (4) 的**跨邻域印证** + **触发条件**（"only if there is no other possibility"）；「没有好分裂时允许节点超容」 | `.prior-art/xtree96.txt:305-308, 350-355, 379-382, 494-498` |
| **Beckmann 等 1990 · R\*-tree** | **反例/排除项**：`OverflowTreatment` 先重插后分裂 —— 对 §2 **不适用**，而排除它的理由加固了「§2 是分不了、不是分不好」 | `.prior-art/rstree.txt:566-573, 574-581, 596-598, 604-613` |
| **Guttman 1984 · R-tree** | §7 处置菜单的**第一条**：`CondenseTree` 压掉欠满节点 + **把项重插回树**（复用 `Insert`，实现最省） | `.prior-art/guttman84.txt:328-332, 358-359, 368-382` |
| **Driscoll / Sarnak / Sleator / Tarjan 1989** | §8 的**前例**：fat node 与 node-copying 两条通用方法（含入度常数这条限制）；**Overmars 三条老办法**里第二条正是本设计现状，代价是**访问版本 i 为 Θ(i)** | `.prior-art/driscoll89.txt:45-47, 203-210, 212-219, 296-314, 325-329` |

### 仍然「未核」（**不得作为依据**）

清单 §11 那七条里，**第一批动了一条、第二批清掉三条**：

| 线索 | 状态变化 |
|---|---|
| 抽象解释 / Galois 连接（Cousot） | **部分**：Galois 连接在**概念格**这一支已核到（`godin95.txt:138`，且 Godin 本来就在「已核」栏）⇒ 本设计要的「父子序 = 包含关系」**已有着落**；**Cousot 那一支仍未核**，且**不再必要** |
| R\*-tree forced reinsert | **已核**（§7.2 B）⇒ 移出本栏 |
| X-tree supernode | **已核**（§7.2 A）⇒ 移出本栏 |
| Driscoll 1989 | **已核**（§7.4）⇒ 移出本栏 |
| 机制设计 · ANN-Benchmarks · CRDT / join-semilattice | **仍未核** |

⚠️ **两条要澄清的**（都是**文件名会误导**，不是引用归属错）：

| 文件 | 容易读成 | 实际是 |
|---|---|---|
| `sources/dred1993.txt` | **Driscoll 1989** | Gupta / Mumick / Subrahmanian, *Maintaining Views Incrementally*, SIGMOD 1993（`dred` = 文中算法 **DRed**） |
| `.prior-art/nncost01.*` | **X-tree** | ICDT 2001, *On Optimizing Nearest Neighbor Queries in High-Dimensional Data Spaces*（`nncost` = nearest-neighbor cost model）；X-tree 在 `xtree96.*` |

设计文档 §11 已核栏的归属是对的，**只有文件名会误导**。

---

## 十、一句话收

**七项推荐，零项被反驳；五项被修正，而修正全部是把推荐往上抬，不是推翻它。**

    1  根记账 ← GIN pending list 印证；「不能学 GiST 加宽根」← GiST AdjustKeys 解释（外生根）
       ★ Bε-tree 补第三条形态：物化在**节点自己**身上（不是旁挂清单）；且「待处理项须在查询路径上」独立印证「根」这个位置
       子从父派生 ← Godin 的格印证机制，但 Godin 是收窄、本设计是加宽 ⇒ 加宽后不再是格，这是 (3) 的真实代价
    2  拒绝 + 记账 ← SP-GiST allTheSame 印证；且它「标记挂元组、答案仍三态」⇒ 印证「不用重验」
       ★ 多出一条出路 (4)：不新增子方向，让项留在父层 —— 那是 §K2 的字面，也是 SP-GiST 的 Leaf Shrink
       ★ X-tree 从空间索引那边独立印证 (4)，并补上触发条件（"only if there is no other possibility"）
       ★ R\*-tree 的 forced reinsert **排除**（重插要有「别的节点接得住」，无穷字母表下无对象可挪）
    3  外生 oracle ← McConnell §5.5 把另两条出路（重算 / 自报）**明确排除** ⇒ (c) 是剩下那条
    4  先做度量 ← ESLint 印证，但给更强的第 (4) 条：**baseline（冻结既有、对新增红）**；Rust 补上「要配具名里程碑」
    6  细化度 + N=10 ← SP-GiST 逐字印证；★ 并答掉「待确认」：**内核自己算 ⇒ 不用动接口**；新增依赖边 §3 → §6
    7  做，排在 1–4 后 ← ART / SP-GiST / Godin 三源印证，且 Godin 两次说删除**比增加容易** ⇒ 次序有据
       ★ Bε-tree 的 tombstone 也是「删除复用插入那条路」；Guttman 的 CondenseTree 补上菜单的第三条出路（重插回树）
       ★ 但新发现一条待定口径：`§I4` 的 `None` 在**删除路径**上是什么含义
    8  持久化 / 并发 ← Driscoll 89 把清单 §11 里那条「未核」清了；★ 本设计现状 = Overmars 第二条（代价是访问版本 i 为 Θ(i)）
       ★ 新发现一条前置问题：方向节点**入度是否常数** —— 它决定走 node-copying 还是 fat node

**给裁定的一张最小改动表**

| 清单原推荐 | 改后 | 改在哪 |
|---|---|---|
| §4 (2) 先做成度量 | **(2′) 度量 + baseline + 具名里程碑** | 度量永远不红；baseline 保留「能红」 |
| §6 可能要重验 | **不用重验** | 内核自己算 payload 的细化量 |
| §2 三条出路 | **四条** | 第 (4) 条：项留在父层（§K2 字面），触发条件是「没有别的办法」 |
| §1 (1) 二选 | **三选** | 加一条「物化在节点自己身上」（Bε-tree）；仍然**不选**物化，但理由要写成「要快」而不是「不该记」 |
| §1 (3) | 保留，但**写清「走 (3) 就不再是 Galois 格」** | 成熟答案在这个方向上是**收窄**，不是加宽 |
| §7 处置 | **菜单三条**（重插 / 合并 / 压掉），且**要另定一条口径** | `§I4` 的 `None` 在删除路径上的含义，与插入路径是否相同 |
| §8 持久化 | **先答「方向节点入度是否常数」** | 常数 ⇒ node-copying（O(1) 访问）；无界 ⇒ 只能 fat node（O(log m)） |
| §11 未核栏 | **清掉三条**（R\*-tree / X-tree / Driscoll） | 剩下：机制设计 · ANN-Benchmarks · CRDT / join-semilattice · Cousot 那一支 |
| 依赖顺序 | **补一条 `§3 → §6`** | 没有外生覆盖定义，进步量没有量可算 |
