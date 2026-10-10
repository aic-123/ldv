# 引文源档索引 —— **怎么取、怎么核**

本目录是 `docs/分层方向视图-成熟方案与跨领域文献.md`、`docs/分层方向视图-设计文档.md` §11、
`docs/分层方向视图-多层抽象-前作核验.md` 与 `ldv/C8-成熟方案对照.md` 里
**每一条英文引文的取法与页码偏移**。
它是「引文**已核**」这句话的**唯一可复核入口**。

## ⚠️ 先读这一段：**版权正文不随仓库发布**

    PDF 与抽出的正文          **不进仓库**（版权归原作者）
    本目录里的四份文件          **是我们自己的东西**，不含任何版权正文：
                              `README.md`（本文件：URL + 页码偏移 + 抽取脚本用法）
                              `extract.py`（抽 PDF 正文，带页码标记）
                              `verify_quotes.py`（把交付物里的英文引文回源文逐字比对）
                              `render_pages.py`（把**没有文字层**的扫描件渲成 PNG —— 那种源只能**读图**）

⇒ 要逐字复核某条引文，**按下面的 URL 自己取一份**，用 `extract.py` 抽出正文，
   再按 `## 页号约定` 换算页码 —— 引文在正本里的位置与页码偏移都写在下面。
   **这条路径是完整的**：从本文件出发，不需要任何未公开的东西。

⚠️ 工作副本（作者本机）在仓库**外面**：`<工作区根>/.prior-art/`（PDF + 抽文）
与 `<工作区根>/sources/`（第二份语料）。`verify_quotes.py` 默认去那里找源文，
可用 `LDV_PRIOR_ART` / `LDV_SOURCES` 覆盖 —— **在干净的 clone 里它找不到源文，
会明确报错，不会静默报「全过」**。

---

# （以下为原索引正文）

**本目录随仓库发布**（`README.md` + `extract.py` + `verify_quotes.py`，都是我们自己的东西）。
它是 `repo/docs/分层方向视图-成熟方案与跨领域文献.md`、
`outputs/ldv-成熟答案印证.md`、`repo/docs/分层方向视图-多层抽象-前作核验.md`
与 `repo/ldv/C8-成熟方案对照.md` 里**每一条引文的索引**。
**版权正文**（PDF 与抽文）**不在这里、也不在仓库里** —— 见上面那段。

⚠️ **正本在 `repo/docs/`，不在 `outputs/`。** `outputs/` 里那几份 `分层方向视图-*.md`
是 `2026-10-05` 的**旧副本**（设计文档 73 KB vs 正本 120 KB），**别照着它核引文**。

## 内容

| 文件 | 是什么 | 用来支撑 |
|---|---|---|
| `spgist01.pdf` / `.txt` | **Aref & Ilyas, *SP-GiST: An Extensible Database Index for Supporting Space Partitioning Trees*, JIIS 17(2/3):215–240, 2001** | `§K2` 的前例（`PathShrink` 三档 / `NodeShrink` 参数）；`§I4` 的「一组」与布尔返回；`§K8` 的逐字文档串；方法集从 3 长到 5+2 |
| `spgist.html` / `.txt` | PostgreSQL 18 官方文档 65.3（SP-GiST） | `allTheSame`（分不开时**强制建**）；`longValuesOK`（物理预算 + 变细守卫，本设计落地为 `B19`）；`nNodes` |
| `gist.html` / `.txt` | PostgreSQL 18 官方文档 65.2（GiST） | 五必需方法集；`recheck`（有损索引契约） |
| `gin.html` / `.txt` | PostgreSQL 18 官方文档 65.4（GIN） | 两必需方法集；`GIN_TRUE/FALSE/MAYBE` 三值 |
| `art13.pdf` / `.txt` | **Leis / Kemper / Neumann, *The Adaptive Radix Tree*, ICDE 2013** | `Lazy Expansion`（= `§K2`）；`Path Compression`（= `§K2` 的第三个选项）；`Node4/16/48/256`（= 扇出小梯子）；「只改空间不改高度」（= B-4 的区分）；**附录 A 第 15–18 行**：`checkPrefix` 不符时**新建节点**取公共前缀，老节点**有效键不变**（= `§10.2 A′`：加宽已有节点**无前例**）；**§「Path compression」**：「this partial key **cannot simply be ignored**」，两条做法 —— **Pessimistic**（把被压掉的单子节点键存进父的变长前缀向量）/ **Optimistic**（只存个数，**到叶重验**），两条都保证「每个内部节点至少两个孩子」；**§「Delete」**：「deletion is **symmetrical to insertion** … **If that node now has only one child, it is replaced by its child** and the compressed path is adjusted」（= `§10.2 C`：压单子方向的确切定义 + 它**只在删掉节点之后**才出现） |
| `godin95.pdf` / `.txt` | **Godin / Missaoui / Alaoui, *Incremental concept formation algorithms based on Galois (concept) lattices*, Computational Intelligence 11(2):246–267, 1995** | 增量**且顺序无关**；更新的四个方面；会**删边**；**§3.4** 删除流程；**§3.1** 新节点 `(Y ∪ {x*}, Y' ∩ f*({x*}))` = intent **收窄**；**Table 2 第 1 类**「Modified node」的 `X'` 一栏是 `No change`；**Algorithm 1 第 8 行**是全文唯一一次「并 intent」，产出节点 extent = **`Ø`**（格底）（= `§10.2 A′`）；**§3.4（p.15）四条**：「**much easier than adding an instance**」· 从**最低的含该项的 pair** 起**向上递归** · 「**verifying if the parents are still necessary**」+「**care must be taken for relinking nodes**」· ★「The number of pairs to examine is **limited to pairs (X, X') where X' ⊆ f({x})**」（= `§10.2 C` 的 `Cone(x)` 界；Godin **删** pair，我们**不删** —— 只借骨架） |
| `naor01.pdf` / `.txt` | **Naor / Teague, *Anti-persistence: History Independent Data Structures*, STOC 2001 / eprint 2001/036** | history independence 的定义（2.1/2.2）；规模阈值重建的摊还代价；「唯一表示」开放问题 |
| `acar02.pdf` / `.txt` | **Acar / Blelloch / Harper, *Adaptive Functional Programming*, POPL 2002** | `trace` + 变更传播；**求值顺序必须记录并重放**；增量正确性 = 等于全量重算 |
| `mcconnell11.pdf` / `.txt` | **McConnell / Mehlhorn / Näher / Schweitzer, *Certifying Algorithms*, Computer Science Review 2011** | 见证 + 检查器；checker 必须比算法简单；「否」没有小见证；**§6 四条里第 2 条是运行时间**（「Ideally, the running time of a checker is linear in the size of its input」） |
| `mehlhorn05.pdf` / `.txt` | **Mehlhorn, *Certifying Algorithms: An Attempt of a Theory*, 2005**（10 页讲稿） | checker 的定义与「linear running time」；**certifying 程序 Q 的资源只许比 P 大一个常数因子**（p.4 第 3 条）；★ p.9「Cooperation of Verification and Checking」—— 一个性质**难检查但易证明**、另一个**易检查但难证明**，两者分工 |
| `mehlhorn10.pdf` / `.txt` | **Mehlhorn / Schweitzer, *Progress on Certifying Algorithms*, 2010** | ★ **§3 三连通性**：线性算法**有**，但**没有一个是 certifying 的**；最快的 certifying 是 **O(n²)**，且原文说 "It remains a challenge to find a linear time certifying algorithm" —— **「checker 超线性」有前例，且被当成开放问题而不是缺陷** |
| `testoracles15.pdf` / `.txt` | **Barr / Harman / McMinn / Shahbaz / Yoo, *The Oracle Problem in Software Testing: A Survey*, IEEE TSE 2015** | §2.3 soundness / completeness 的**正式定义**（= `§K8` 不对称契约的两个方向）；§2.2「Test oracles are typically computationally expensive」；★ §5「Invariant detection can be computationally expensive, so **incremental** ... analyses ... have been brought to bear」—— oracle 贵的成熟出路是**增量** |
| `purdue.html` | SP-GiST @ Purdue 项目页 | 该框架的定位陈述 |
| `betree.pdf` / `.txt` | **Bender / Farach-Colton / Jannen / Johnson / Kuszmaul / Porter / Yuan / Zhan, *An Introduction to B ε-trees and Write-Optimization*, USENIX ;login: 2015**（数字原生） | 内部节点**带 buffer**、插入/删除都编码成**消息**；「待处理项必须在查询路径上」的不变量；**§「Inserts and deletes」**：`tombstone` 三条可搬形状 —— **逻辑删除 ≠ 物理删除**（「a deleted item, or even entire leaf node, **can continue to exist** until a tombstone message reaches the leaf」）· **删除编码成消息**（「deletions are **algorithmically very similar to insertions**」）· **查询不必更新被查的一方**（「the query **need not update the leaf**」）；⚠️ **不能搬的一条**：它有**冲刷**（墓碑最终落叶、条目才真消失），我们**没有** ⇒ 我们的墓碑是**永久**的（= `§10.2 C`） |
| `xtree96.pdf` / `.txt` | **Berchtold / Keim / Kriegel, *The X-tree: An Index Structure for High-Dimensional Data*, VLDB 1996, pp.28–39**（扫描件） | `supernode`：没有好分裂时**让节点超容**，而非造一个坏分裂；触发条件 "only if there is no other possibility" |
| `rstree.pdf` / `.txt` | **Beckmann / Kriegel / Schneider / Seeger, *The R\*-tree: An Efficient and Robust Access Method for Points and Rectangles*, SIGMOD 1990, pp.322–331**（扫描件） | `OverflowTreatment` + **forced `Reinsert`**（第一次溢出先重插，`p = 30% of M`）；「重叠减少、分裂变少、CPU 变高」 |
| `guttman84.pdf` / `.txt` | **Guttman, *R-Trees: A Dynamic Index Structure for Spatial Searching*, SIGMOD 1984, pp.47–57**（扫描件） | `CondenseTree`：CT3 压掉欠满节点（`fewer than m entries`）+ CT6 **把孤儿项重插回树**（复用 `Insert`，且「entries from higher-level nodes must be placed higher」）；★ **CT4**：「Adjust all covering rectangles on the path to the root, **making them smaller if possible**」（= `§10.2 C`：**`B18` 在删除路径上的对应物** —— 删子之后父的覆盖**必须跟着收缩**）；★ 逐字**排除**「与兄弟合并」：「there is **no adjacency in the B-tree sense**」；重插的两条理由（复用 `Insert` 最省 + 「prevents **gradual deterioration**」） |
| `driscoll89.pdf` / `.txt` | **Driscoll / Sarnak / Sleator / Tarjan, *Making Data Structures Persistent*, JCSS 38(1):86–124, 1989**（扫描件） | partial / full persistence；**fat node**（O(1) 空间 / O(log m) 访问）与 **node-copying**（摊还 O(1)，**要求入度常数**）；转述并否决 Overmars 的三条老办法 |
| `lehman81.pdf` / `.txt` | **Lehman / Yao, *Efficient Locking for Concurrent Operations on B-Trees*, ACM TODS 6(4):650–670, 1981**（21 页，数字原生） | ★ `§10.2 D` 的 P8：**「锁的数目有界」**（p.19 §8「at most a constant number of locks (three) for any process at any time」；p.13「at most three nodes are ever locked simultaneously」）；★ **「死锁自由靠锁的良序」**（p.11「Deadlock freedom is guaranteed by the well-ordering of the locking scheme」）；★ **全局锁是特例不是常态**（p.19「a batch reorganization or an underflow operation which locks the entire tree **can be performed**」）；link pointer 对结构做的那一处修改（split 把项**向右**搬，靠 link 可达） |
| `nbtree.txt` | **PostgreSQL 官方源码文档 `src/backend/access/nbtree/README`**（`master` 分支，2026-10-07 取，1082 行） | ★ `§10.2 D` 的 **P6**：**落盘只有一条边** —— 磁盘格式里**只有 downlink（父→子）**，**没有**子→父指针；要找父页得**重新下降**（「The parent page must be found using the same type of search as used to find the parent during an insertion split.」）；★ 派生边**可以事后补**（「Our approach is to create any missing downlinks on-the-fly, when searching the tree for a new insertion.」）；★ P8：**锁的作用域与顺序**（「we release our lock and pin on a page before attempting to acquire pin and lock on the page we are moving to … This is safe when moving right or up, but not when moving left or down (else we'd create the possibility of deadlocks).」）；★ Lehman–Yao 唯一同时持三把锁的位置（「This is the only point where Lehman and Yao have to simultaneously hold three locks」） |
| `nncost01.pdf` / `.txt` | **Berchtold / Böhm / Keim / Krebs / Kriegel, *On Optimizing Nearest Neighbor Queries in High-Dimensional Data Spaces*, ICDT 2001** | ⚠️ **不是 X-tree**（文件名 `nncost` = nearest-neighbor cost model）。只在**二手引用**里提 supernode，**引 X-tree 请用 `xtree96.*`** |
| `grail10.pdf` / `.txt` | **Yıldırım / Chaoji / Zaki, *GRAIL: Scalable Reachability Index for Large Graphs*, VLDB 2010**（9 页） | ★ **可达性查询的「索引 vs 搜索」两个极端**（Fig. 1）：左 = 全传递闭包（O(1) 查询 / O(n²) 空间）；右 = 每查询一遍 DFS/BFS（无索引 / O(n+m) 每查询，原文判「unacceptable for large graphs」）。★ 摘要写明规模判据：「**more sophisticated methods work better on small graphs**」。★ Table 1 给出谱系（Opt. Tree Cover / GRIPP / Dual Labeling / PathTree / 2HOP / HOPI / GRAIL）的建索引 / 查询 / 空间复杂度 —— **没有一行是「每查询一遍 BFS」**。用来支撑 `项 7` 的裁定（`outputs/ldv-裁定推荐-项2与项7.md`） |
| `grail11.pdf` / `.txt` | **Yıldırım / Chaoji / Zaki, *GRAIL: A Scalable Index for Reachability Queries in Very Large Graphs*, VLDB J 2011**（25 页，期刊版） | 同上的期刊扩展版；引用时**优先 2010 会议版**（页号已核） |
| `popl77.pdf` / `.txt` | **Cousot & Cousot, *Abstract interpretation: a unified lattice model for static analysis of programs by construction or approximation of fixpoints*, POPL 1977, pp.238–252**（15 页） | `docs/分层方向视图-抽象层.md` §2：**抽象 / 具体化两个函数 + 不对称**（6.0「must at least contain the concrete one, (but not only the concrete one)」· 6.2 order-preserving · 6.3 具体化不丢 · 6.4 抽象可丢 · 6.5）—— 本设计 `§K8`（假阴禁止 / 假阳计量）的文献对应。⚠️ **扫描后 OCR**：符号在抽文里是乱的，**只引散文、按节号定位** |
| `pagetarjan.pdf` | **Paige & Tarjan, *Three Partition Refinement Algorithms*, Princeton TR-038, Jan 1986**（25 页） | `docs/分层方向视图-抽象层.md` §3：**relational coarsest partition problem** —— 「find the **coarsest refinement Q of P** …」⇒ **最粗稳定划分唯一** ⇒ 视图集合**不用挑** ⇒ 不需要目标函数（`§A2`/`§A3` 的依据）。⚠️ **纯扫描件、无文字层**（25 页只抽出 416 字符）⇒ **不进语料、`verify_quotes` 核不到**，取法是**读图**（见下） |
| `datacube.pdf` / `.txt` | **Gray / Chaudhuri / Bosworth / Layman / Reichart / Venkatrao / Pellow / Pirahesh, *Data Cube: A Relational Aggregation Operator Generalizing Group-By, Cross-Tab, and Sub-Totals*, MSR-TR-97-32, May 1997**（16 页） | `docs/分层方向视图-抽象层.md` §5：**distributive / algebraic / holistic 三分法** ⇒ 「视图能不能只从下层视图算出来」的判据（`§A4`）。★ 原文对 holistic 的处置：「We know of no more efficient way … than the 2N-algorithm」—— **holistic 要见原始项是结论，不是缺陷** |
| `graphsumm.pdf` / `.txt` | **Liu / Safavi / Dighe / Koutra, *Graph Summarization Methods and Applications: A Survey*, ACM Computing Surveys 51(3), 2018**（34 页） | `docs/分层方向视图-抽象层.md` §1 / §6 / §7：★ **摘要文献的默认框架是「最小化一个目标函数」** ⇒ 与本设计 §4.3（无全局目标函数 / `B12`）**直接冲突**，所以只搬**形状**不搬方法；★ Navlakha 的 **摘要 `S` + 修正项 `C`**（`cost(R) = |ES| + |C|`）＝ 本项目的「视图 + 账」；★ Fan et al. 2012 的 **压缩 / 查询改写 / 结果解释** 三段 + **增量传播**；★ Song et al. 2016 的 **`d`-summary**（参数化有界近似） |
| `hendrickson95.pdf` / `.txt` | **Hendrickson & Leland, *A Multilevel Algorithm for Partitioning Graphs*，Sandia National Laboratories（14 页报告版）** | `docs/分层方向视图-多层抽象-前作核验.md` §1：★★ **「多层」范式的原始出处** —— 算法骨架逐字就是「反复套」：「Until graph is small enough」；★★ **总代价的界**（摘要）：「The entire algorithm can be implemented to execute in time proportional to the size of the original graph.」；★★★ **范式自己写明了它的代价**（p.3）：「The price paid for this reduction in complexity is that only a small number of the possible fine graph partitions are represented and are therefore examinable on the coarse graph.」—— 丢的是「细图上**可检视**的划分空间」，**不是**「可达」（同段紧接着有补救句）；★ 递归深度（p.3）：「All other things being equal, it is preferable to divide into as many sets at once as possible so as to limit the depth of the recursion.」。⚠️ **封面没有报告号** —— 抽文里的 `SAND93-0074` / `SAND94-2692` 是它**引的别人** ⇒ **一律不写 `SAND` 号**；⚠️ **抽文字形缺失**（见下「扫描件（OCR）说明」）⇒ **只引散文** |
| `metis.pdf` / `.txt` | **Karypis & Kumar, *A Fast and High Quality Multilevel Scheme for Partitioning Irregular Graphs*, SIAM J. Sci. Comput. 20(1):359–392, 1998**（34 页） | 同上 §1：★ **三阶段的形式定义**（论文 p.363）：「A multilevel graph bisection algorithm consists of the following three phases.」+ `Coarsening phase` / `Partitioning phase` / `Uncoarsening phase` 三段各自的逐字定义 + 图 1 题注三句；★★ **层数有界的机制**（p.365）：「Since maximal matchings are used to coarsen the graph, the number of vertices in Gi+1 cannot be less than half the number of vertices in Gi」⇒「it will require at least O(log(n/n′)) coarsening phases」；★★★ **阈值的标准位置**（p.365）：「If the ratio becomes lower than a threshold, then it is better to stop the coarsening phase.」**且原文自己交代这个阈值是兜病态的**（「this type of pathological condition usually arises after many coarsening levels, in which case Gi is already fairly small」/「aborting the coarsening does not affect the overall performance of the algorithm」） |
| `dmlgp.pdf` / `.txt` | **Gottesbüren / Heuer / Sanders / Schulz / Seemaier, *Deep Multilevel Graph Partitioning*, arXiv:2105.02022v1**（19 页） | 同上 §1：★ **`k` 大时 MGP 的失效点**（论文 p.3）：「The coarsening phase of MGP usually stops when kC nodes are left.」+「For large k, this breaks the assumption that the coarsest graph is small.」+「Thus, really expensive initial partitioners are infeasible at this level.」；★ **停止阈值的另一种形态**（p.2）：「Once the number of nodes of a coarse graph falls below a certain threshold or the coarsening algorithm converges, initial partitioning computes a partition of the coarsest graph.」；★★ **块数与全局 `k` 解耦**（p.4，不变式 `(P)`）：「A coarse graph Gi is partitioned into ki := ceil2(|Vi|/C) blocks (bounded by 2 and k).」 |
| `reslimit.pdf` / `.txt` | **Fortunato & Barthélemy, *Resolution limit in community detection*, arXiv:physics/0607100v2**（8 页） | 同上 §1.4：★★ **单一全局质量函数内蕴一个尺度**（摘要）：「We find that modularity optimization may fail to identify modules smaller than a scale which depends on the total number L of links of the network and on the degree of interconnectedness of the modules, even in cases where modules are unambiguously defined.」；★ **结论节（p.7）**：「by enforcing modularity optimization, the possible partitions of the system are explored at a coarse level, so that modules smaller than some scale may not be resolved」；★★ **出路是「局部再精化」**（同页）：「constraining modularity optimization on each single module」+「a procedure which is not safe but may give useful indications」；★ 结论句：「have an intrinsic resolution limit calls for a new theoretical framework which focuses on a local definition of community, regardless of its size」 |
| `lsmsurvey.pdf` / `.txt` | **Luo & Carey, *LSM-based Storage Techniques: A Survey*, arXiv:1812.07527**（25 页） | 同上 §1.1 / §1.3：★ **层数由「尺寸比」决定**（论文 p.6）：「Let the size ratio of a given LSM-tree be T, and suppose the LSM-tree contains L levels.」+「In practice, for a stable LSM-tree where the volume of inserts equals the volume of deletes, L remains static.」+「Thus, the number of levels for N entries can be approximated as」（`L = ⌈logT(N/(B·P·T/(T+1)))⌉`，抽文里分式压成一行）；★ **层数封顶的「兜底配置」形态**（p.3）：「If level L is already the configured maximum level, then the resulting component remains at level L.」；★ **阈值不是单向的**（p.6）：写代价 `O(T·L/B)`（leveling）对 `O(L/B)`（tiering）⇒ `T` 更大 ⇒ 层数更少但**每层级联更多** |
| `extract.py` | 抽 PDF 正文（带页码标记） | 见下 |

⚠️ **`sources/` 那一批（第二份语料）里的一篇，本轮首次进仓库文档引用**：

| 文件 | 是什么 | 用来支撑 |
|---|---|---|
| `hnsw.pdf` / `.txt` | **Malkov & Yashunin, *Efficient and robust approximate nearest neighbor search using Hierarchical Navigable Small World graphs*, arXiv:1603.09320** | `docs/分层方向视图-检索器层.md` §14.0：★ **`zoom-out` / `zoom-in` 两阶段**（「The routing can be divided into two phases」）· 上层**只有最长的边**、搜索**从上层开始** · 上层**贪心走到 local minimum**、切下层**从那一点重启** ⇒ 「**整体倾向**在成熟系统里是**一个进入点**，不是一份排序」 |

⚠️ **它不在 `.prior-art/`，在 `<工作区根>/sources/`**（那份是「第二份语料」，
由 `outputs/ldv-成熟答案印证.md` 引用；`verify_quotes` **两份都读**，见下面「第二份语料」一节）。
URL 取法与本文档其余源同一条纪律：

```bash
curl -sL -k --max-time 45 -o hnsw.pdf "https://arxiv.org/pdf/1603.09320"
head -c 5 hnsw.pdf          # 必须是 %PDF-
python extract.py hnsw.pdf hnsw.txt
```

⚠️ **`hnsw` 的抽文有排版缺陷**（本轮人工逐字核时发现 —— **`verify_quotes` 看不见它们**）：

    `zoom -in` / `zoom -out`   连字符**前有空格**（原文抽成了 `zoom -in`）
    `the  elements`            双空格（多处）
    断词                       行末 `illus-` / `pro-` 之类
    弯引号                     `“zoom -in”`（U+201C/U+201D），不是直引号

⇒ **处置（同 `hendrickson95` / `mehlhorn05` 的先例）**：引文**只引「压平空白后逐字」的片段**，
  并**避开**有缺陷的那几处；`docs/分层方向视图-检索器层.md` §14.0 就是这么引的
  （它在正文里显式声明了这条限制）。

⚠️ **为什么这件事得人工做**：`verify_quotes` 的 `norm()` 会压平空白、剥 markdown 标记、
   统一引号 ⇒ **上面四类缺陷它一条都报不出来**（本轮实测：带 `**粗体**` 与直引号的引文
   照样报「全覆盖」）。⇒ **「全覆盖」只说明「归一化后能对上」，不说明「逐字」**。
   这一条是对脚本能力边界的又一次实测，写在这里免得下一个人把它当成「已逐字核过」。

## 来源（下载记录）

第二批五篇（2026-10-06 取）的 URL —— 已落盘，**核验时读本地副本，不必再联网**：

| 文件 | URL | 备注 |
|---|---|---|
| `betree.pdf` | `https://www3.cs.stonybrook.edu/~bender/newpub/2015-BenderFaJa-login-wods.pdf` | 直接 200 |
| `xtree96.pdf` | `https://www.vldb.org/conf/1996/P028.PDF` | 需要**浏览器 User-Agent**，否则 000 |
| `rstree.pdf` | `https://www.cs.albany.edu/~jhh/courses/readings/beckmann.sigmod90.R*.pdf` | URL 带 `*`，`curl` 要加 `-g` |
| `guttman84.pdf` | `https://www.cs.jhu.edu/~misha/ReadingSeminar/Papers/Guttman84.pdf` | 直接 200 |
| `driscoll89.pdf` | `https://cadmo.ethz.ch/education/lectures/HS18/SAADS/papers/persistent.pdf` | 直接 200 |
| `nncost01.pdf` | `https://bib.dbvis.de/uploadedFiles/154.pdf` | **标题不是 X-tree** —— 见上表的警示行 |

第三批三篇（2026-10-06 取，为「检查层的超线性」找前例）—— 已落盘，**核验时读本地副本**：

| 文件 | URL | 备注 |
|---|---|---|
| `mehlhorn05.pdf` | `https://www.mpi-inf.mpg.de/~mehlhorn/ftp/CertifyingAlgorithms2.pdf` | 直接 200；10 页讲稿 |
| `mehlhorn10.pdf` | `https://people.mpi-inf.mpg.de/~mehlhorn/ftp/FAW.pdf` | 直接 200；4 页短文 |
| `testoracles15.pdf` | `https://web.eecs.umich.edu/~weimerw/2024-481F/readings/testoracles.pdf` | 直接 200；31 页综述 |

⚠️ `mehlhorn05` 的抽文**带字形映射缺陷**：pypdf 报 `fontTools is required to fully
parse the encoding of a CFF Type1 font`，正文里混进 **56 个 NUL 与 149 个控制字符**，
`ü` 抽成 `¤`（`f¤ur` = `für`）、`fi` 连字丢失（`certi cation` = `certification`）。
⇒ 已就地清掉控制字符（控制字符换成空格、NUL 删除，`6782 → 6774` 字节）。
**引它的时候按读法写**，与本目录里那四份扫描件同一条纪律。

第四批两篇（2026-10-07 取，为 `项 7`（插件反复重算图遍历）找前例）—— 已落盘，
**核验时读本地副本**。⚠️ 两版抽文都带 `fontTools` 缺失警告（同 `mehlhorn05` 那一类），
引文按读法写：

| 文件 | URL | 备注 |
|---|---|---|
| `grail10.pdf` | `https://cs.rpi.edu/~zaki/PaperDir/VLDB10.pdf` | 直接 200；9 页会议版 |
| `grail11.pdf` | `https://www.cs.rpi.edu/~zaki/PaperDir/VLDBJ11.pdf` | 直接 200；25 页期刊版 |

⚠️ **Cohen / Halperin / Kaplan / Zwick, *Reachability and distance queries via 2-hop labels*
（SODA 2002 / SICOMP 32(5) 2003）未取到全文** —— 试过 `cs.tau.ac.il/~zwick/papers/2hop.pdf`
（返回 HTML）、`cs.technion.ac.il/~haimk/papers/2hop.pdf`（返回 HTML）、
CiteSeerX、SIAM DOI（均非 PDF）⇒ **状态「未核」，不得作为依据**。
引用 2HOP 的复杂度时，只能写「**GRAIL 的 Table 1 所列**」，不能写成「Cohen 等 2003 给出」。

第五批两篇（2026-10-07 取，为 `§10.2 D` 的 P6 / P8 找前例）—— 已落盘，
**核验时读本地副本**：

| 文件 | URL | 备注 |
|---|---|---|
| `lehman81.pdf` | `https://www.cs.utexas.edu/~dsb/cs386d/Readings/ConcurrencyControl/Lehman-Yao.pdf` | 直接 200；**21 页**（含封面页），数字原生，抽文干净（`pypdf` 无字形警告）。⚠️ **页码偏移**：抽文的 `===PAGE n===` 是 **PDF 物理页**，正文印页码要按 `§「页号约定」` 换算 —— 本文引的三处落在 **PDF 第 11 / 13 / 19 页**，正文印页码 **p.660 / p.662 / p.668**（TODS 6(4):650–670） |
| `nbtree.txt` | `https://raw.githubusercontent.com/postgres/postgres/master/src/backend/access/nbtree/README` | 直接 200；**纯文本**（不是 PDF，**不走 `extract.py`**）；1082 行。⚠️ 它是**源码仓库的文档**，随版本变动 —— 引文对应 **2026-10-07 的 `master`**；核验时读本地副本，**不要重新下载**（重下可能已改） |

⚠️ `nbtree.txt` 里的行号**是本文件的行号**，不是 PostgreSQL 的任何版本号；
引用时写「`src/backend/access/nbtree/README`（2026-10-07 的 `master`）」+ 引文，
**不要写行号**（换一版就漂）。

第六批四篇（2026-10-08 取，为 `docs/分层方向视图-抽象层.md`（流程 E）找前例）—— 已落盘：

| 文件 | URL | 备注 |
|---|---|---|
| `popl77.pdf` | `https://homes.cs.washington.edu/~mernst/teaching/6.883/readings/p238-cousot.pdf` | 直接 200；15 页；⚠️ **扫描后 OCR**（见下） |
| `pagetarjan.pdf` | `https://www.cs.princeton.edu/techreports/1986/038.pdf` | 直接 200；25 页；⚠️ **纯扫描件、无文字层**（见下） |
| `datacube.pdf` | `https://arxiv.org/pdf/cs/0701155` | 直接 200；16 页；数字原生，抽文干净 |
| `graphsumm.pdf` | `https://arxiv.org/pdf/1612.04883` | 直接 200；34 页；数字原生，抽文干净。⚠️ **`arXiv:1704.03165` 不是这一篇**（那是 `struc2vec`）—— 别再照那个号取 |

第七批五篇（2026-10-08 取，为「**多层抽象**」找前作 —— `docs/分层方向视图-多层抽象-前作核验.md`）
—— 已落盘，**核验时读本地副本**。URL 全部**按 PDF 字节数核对过**（与本地副本一致）：

| 文件 | URL | 备注 |
|---|---|---|
| `hendrickson95.pdf` | `https://sites.cs.ucsb.edu/~gilbert/cs240a/notes/multilevel.pdf` | 直接 200；158428 字节；14 页。⚠️ **抽文字形缺失**（见下「扫描件（OCR）说明」） |
| `metis.pdf` | `https://www.cs.utexas.edu/~pingali/CS395T/2009fa/papers/metis.pdf` | 直接 200；484442 字节；34 页。⚠️ **同名的另一份副本字节数不同**（`cs.albany.edu` 那份是 227188）⇒ **按字节数认这一份** |
| `dmlgp.pdf` | `https://arxiv.org/pdf/2105.02022` | 直接 200；1229131 字节；19 页 |
| `reslimit.pdf` | `https://arxiv.org/pdf/physics/0607100` | 直接 200；524836 字节；8 页 |
| `lsmsurvey.pdf` | `https://arxiv.org/pdf/1812.07527` | 直接 200；879338 字节；25 页 |

⚠️ **`hendrickson95` 的身份，只写核到的东西**：它的第 1 页末尾逐字写着
`Will appear in Proc. Supercomputing '95.`，**末页印页码 `14`，每页页脚都是本页页码**
⇒ **PDF 页 = 报告页**。但**封面没有报告号** ⇒ **本文不写任何 `SAND` 号**。
⚠️ **METIS 引的是另一份**：`Tech. report SAND 93-1301, Sandia National Laboratories, 1993`
（METIS 参考文献 `[26]`）—— **同名、不同年、不同文档**，**不能拿来给这一份编号**。

⚠️ **`popl77` 是扫描后 OCR 的 PDF**：散文读得通，**数学符号在抽文里是乱的**
（`{Cv ⊑ γ(~)}` 抽成 `{Cv~ ~(~)}`、`α` 抽成 `‘u,`、`γ` 抽成 `y`、`⊑` 抽成 `~`）。
⇒ 引它的时候**只引散文**，符号按 §6.0 / 6.2 / 6.3 / 6.4 / 6.5 的**节号**定位。
实测：本文引的四段散文全部逐字通过 `verify_quotes`；**带符号的那两句一律不引**。

⚠️ **`pagetarjan` 没有文字层**（`extract.py` 跑出 25 页 / **416 字符**）。
⇒ 它**不在 `SRC_DIRS` 的可比范围内** ⇒ `verify_quotes` **核不到它的引文**。
取法是**读图**：

```bash
# 从**工作区根**跑（与 `verify_quotes.py` 的用法同一套相对路径）
python repo/docs/prior-art/render_pages.py .prior-art/pagetarjan.pdf outputs/_ptpages 3 4 130
# 然后看 outputs/_ptpages/p03.png（= 论文 p.1）与 p04.png（= 论文 p.2）
```

⚠️ **渲出来的 PNG 是别人的作品**，与 PDF 同一条规矩：**不随仓库发布** ⇒
`out_dir` 指向仓库**外面**（本仓库的既有做法是工作区根的 `outputs/`）。

⇒ 引用它的地方**必须在正文里显式写出「这一条不经过 `verify_quotes`」**，
否则读者会把「核不到」读成「核过了」。⚠️ 曾试 `core.ac.uk` 的另一份（`403`）
与直接给 PDF 加文字层 —— **都不成**，所以这条限制是**已知的**，不是没试。

⚠️ **`datacube` 与 `graphsumm` 是数字原生**，抽文干净、无需 OCR 修正表。

下载与抽取：

```bash
curl -sL -k --max-time 45 -o betree.pdf "<URL>"     # -k 绕过本机 SSL 问题；不挂 VPN 时部分域名 000
head -c 5 betree.pdf                                 # 必须是 %PDF- —— 有些站 200 但返回 HTML
python extract.py betree.pdf betree.txt
```

## 复现

### ★ 调用式（**跑校验必须连调用式一起抄** —— 只抄数字，范围就漂了）

从**工作区根**跑，**四条全给**：

```bash
V="C:/Users/19253/.workbuddy-ai/binaries/python/versions/3.13.12/python.exe"
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 "$V" repo/docs/prior-art/verify_quotes.py \
    repo/docs/*.md repo/README.md repo/ldv/MEASUREMENTS.md
```

**2026-10-10 实测（最近一次）：引文 272 条｜全覆盖 272｜有缺口 0。**
（同日 265 → **272**：+7 全在 `repo/docs/分层方向视图-抽象层.md` **§7.4** 的文献表 ——
 `acar02` ×2 ／ `lsmsurvey` ×1 ／ `mvselect2026` ×1 ／ `graphsumm` ×1 ／ `betree` ×1，
 以及 §7.4.6 表头那两条的重复引用。**每一句都人工逐字核过**（`norm()` 压平后 `in` 判定）。）
（同日三次累加，每一档都逐条可追：
 253 → **257**：+4 全在 `repo/docs/分层方向视图-检索器层.md` §14.0，
 是本轮首次进仓库文档引用的 **HNSW**（`sources/hnsw.txt`）；
 257 → **265**：+8 全在该文档 §14.7 的「文献印证」表 ——
 `mvselect2026` ×2 ／ `kraska2018` ×1 ／ `unbiased_ltr` ×2 ／ `degenloop` ×2 ／
 `gist1995` ×1（那一条也印证了 `模块化边界 §三` 的「插件允许不准」）。
 ⚠️ 这 8 条**每一句都人工逐字核过**（`norm()` 压平后 `in` 判定），不是只看「全覆盖」。）
（**2026-10-08 是 252 条**；那一次改动之前是 208 条 —— 差的 44 条里 43 条全在
`repo/docs/分层方向视图-多层抽象-前作核验.md` 里，另 1 条是 `repo/ldv/MEASUREMENTS.md`
结果十九 把 METIS p.365 那条原文又引了一次。逐条对得上。
**2026-10-09 的 +1 条**来自新文档 `repo/docs/分层方向视图-检索器层.md` ——
一句 `rewriting the query`（源是 `graphsumm`，**已核**，逐字在
`.prior-art/graphsumm.txt:841`）。⚠️ 那份文档的**骨架稿**里同一句引过 3 次
（⇒ 当时是 255），**正文稿收敛成 1 次** ⇒ 253。**重复计入是脚本的既有行为**，
不是 3 条不同的引文。）

⚠️ **这句话的范围恰好等于上面那条调用式。** 两处**不在**范围里，写在这里：

| 不在范围里的 | 为什么 | 实测 |
|---|---|---|
| `repo/docs/prior-art/README.md`（本文件） | 它含**一条来自无文字层扫描件**的引文（`pagetarjan` 的 `find the coarsest refinement Q of P`）⇒ 机械核验**必然**报缺口。把它塞进调用式就是造一条**常驻的红** —— 而「一条常驻的红等于没人再看红」 | 单独跑：**59 条｜58 全覆盖｜1 缺口**（那 1 条就是 `pagetarjan`；
2026-10-09：本文件新增的 HNSW 登记贡献了 1 条**重复引用** ⇒ 58→59） |
| `outputs/*.md` | 那是**工作副本**，不是仓库的一部分；旧副本的引文与正本不同步（见上面「⚠️ **正本在 `repo/docs/`，不在 `outputs/`**」） | 不适用 |

⚠️ **`repo/docs/prior-art/README.md` 不在范围里这件事，是「已知」不是「已核」** ——
它那 58 条里**可核的 57 条实测全过**，不可核的那 1 条**由本文件自己的 `pagetarjan` 行声明**。

### 抽取

```bash
# venv（pypdf 6.19.0）
V="C:/Users/19253/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe"
"$V" extract.py spgist01.pdf spgist01.txt      # 打印：26 页 / N 字符
```

HTML 类的（PostgreSQL 文档）用同一脚本去标签：

```python
import re, html, io
s = io.open("spgist.html", encoding="utf-8", errors="replace").read()
s = re.sub(r"(?is)<(script|style).*?</\1>", "", s)
s = re.sub(r"(?is)</(p|div|li|h[1-6]|tr|br)>", "\n", s)
s = re.sub(r"(?s)<[^>]+>", " ", s); s = html.unescape(s)
s = re.sub(r"[ \t]+", " ", s); s = re.sub(r"\n\s*\n+", "\n\n", s)
io.open("spgist.txt", "w", encoding="utf-8").write(s)
```

## 页号约定

`.txt` 里的 `===PAGE n===` 是 **PDF 页**。

    Godin 1995      PDF 页 = 论文页脚（已抽查：PDF p.8 页脚为 8）
    SP-GiST 2001    PDF 页 + 214 = 论文页（PDF p.7 → p.221）
    ART 2013        PDF 页 = 论文页
    Naor 2001       PDF 页 = 论文页（eprint 版）
    McConnell 2011  PDF 页 = 论文页
    Bε-tree 2015    PDF 页 = 论文页（;login: 文章，逐页页脚 1..8）
    X-tree 1996     PDF 页 + 27 = 论文页（PDF p.1 页脚 28 … p.12 页脚 39）
    R*-tree 1990    PDF 页 + 321 = 论文页（PDF p.1 页脚 322 … p.10 页脚 331）
    Guttman 1984    PDF 页 + 46 = 论文页（PDF p.1 页脚 47 … p.11 页脚 57）
    Driscoll 1989   PDF 页 + 85 = 论文页（PDF p.1 页脚 86 … p.39 页脚 124）
    Mehlhorn 2005   **讲稿**，每页页脚是 `p.n/10` ⇒ PDF 页 = 页脚 n
    Mehlhorn 2010   **短文**（4 页），PDF 页 = 页脚（用 `===PAGE n===` 指）
    Barr 2015       PDF 页 = 期刊页（PDF p.1 页脚 1 … p.31 页脚 31；双栏，抽文会串行）
    Cousot 1977     PDF 页 + 237 = 论文页（PDF p.1 = p.238；POPL'77 的 238–252 正好 15 页 = PDF 页数）
    Paige–Tarjan 1986  PDF 页 − 2 = 论文页（PDF p.3 = 页脚 `- 1 -`；PDF p.4 = `- 2 -`）
    Gray 1997       PDF 页 − 3 = 报告页（PDF p.7 页眉 `Data Cube 4`；PDF p.14 页眉 `Data Cube 11`）
    Liu 2018 (CSUR) PDF 页 = 期刊页（PDF p.2 = `A:2`；PDF p.3 = `A:3`；版式是 `A:n` 不是 `n`）
    Hendrickson 1995 **报告版**：PDF 页 = 报告页（每页页脚都是本页页码，末页 `14`）——
                     ⚠️ **不写会议版页码**（本机副本不是会议版）
    Karypis 1998     PDF 页 + 358 = 论文页（PDF p.1 = p.359 … PDF p.7 = p.365）
    Gottesbüren 2023 PDF 页 − 1 = 论文页（PDF p.1 是题名页；PDF p.4 页眉 `3`）
    Fortunato 2006   PDF 页 = 论文页（PDF p.7 页首 `7`）
    Luo 2025 (LSM)   PDF 页 = 论文页（PDF p.6 页眉 `6 Chen Luo, Michael J. Carey`）

引用时一律写**论文页**，括号里给 PDF 页。

## 扫描件（OCR）说明

`xtree96` / `rstree` / `guttman84` / `driscoll89` 四篇是**扫描件**，抽文带字形混淆
（`remsert` = `reinsert`、`1s` = `is`、`0( 1)` = `O(1)`、`l` = 项目符号 …）。
引这些文献时**引文按读法写**，抽文原样附在 `[抽文]` 注里 —— 见
`outputs/ldv-成熟答案印证.md` 的「怎么读」，以及 `outputs/_verify_blocks.py` 里的
`OCR_FIX` / `OCR_SOURCES`（**只对这四个源生效**，不去改干净正文）。
`driscoll89` 的抽文里还**把图 1 的题注插进了正文句子中间**（`Second, choos-` 之后），
引那段时要用 `…` 断开。

⚠️ **第六批带进来两种新的「源不干净」，第七批又带进来第三种，各有各的处置**：

| 源 | 症状 | 处置 |
|---|---|---|
| `popl77` | **有文字层，但是 OCR 的** —— 散文可读，**数学符号全乱**（`α`→`‘u,`、`⊑`→`~`） | 进 `SRC_DIRS`（散文可比对）；**引文只引散文**，符号按节号定位。**不进 `OCR_FILES`** —— 它不是字形混淆，是**符号缺失**，加修正表反而会去改干净正文 |
| `pagetarjan` | **没有文字层**（25 页 / 416 字符） | **不在 `SRC_DIRS` 的可比范围内** ⇒ `verify_quotes` 核不到。取法是**读图**（`repo/docs/prior-art/render_pages.py`，见下）⇒ **引用它的正文必须显式声明「不经过 `verify_quotes`」** |
| `hendrickson95` | **字形整类没被映射**：`/` 是**占位符**（2459 处），连字变成控制码（`/\x0c` = `fi`、`/\x0b` = `ff`、`/\x0e` = `ffi`） | 进 `SRC_DIRS`；**加一个按源生效的还原表**（`HENDRICKSON_FILES` / `HENDRICKSON_FIX`）。★ **表放进仓库、不改源文** —— 改源文的话第三方按本文件重抽一遍就对不上，「唯一可复核入口」当场作废。⚠️ **只引散文**：真斜杠只在数字 / 路径 / 报告号里，引到那些句子会报**缺口**（响的），不会静默通过 |

⚠️ **为什么 `hendrickson95` 的还原表不算「猜」**：判据是
「剩下的 `/` 全是产物」这句话**有实测支撑** —— 本源 `/` 出现 **2459** 次，
而 **`字母/字母` 形态 **0** 次**（散文里根本不存在被斜杠连起来的两个词）。
代价明确且方向安全：真要引含数字或路径的句子，那处真斜杠会被并掉 ⇒ **报缺口**，
**不会静默通过**。宁缺勿假绿。
（同 `SOFT_FILES` 的 `gist1995` 先例：那里的 `re.sub(r"\s+/([A-Za-z0-9])", r"\1", t)`
也是「把抽文里的 `/` 当产物」的同一类处置。）

⚠️ **为什么 `pagetarjan` 不进 `SRC_DIRS`**：放一份 416 字符的 `.txt` 进去，
它只会让每条引文都算「没命中」⇒ **全红**，而那个红**指错了地方**
（看起来像「引文写错了」，其实是「源文是空的」）。这正是本项目
「**源文不在」与「引文有问题」必须长得不一样**那条纪律的同一个面。

## 第二份语料：`<工作区根>/sources/`

⚠️ **`verify_quotes.py` 读两份语料，不是一份**（2026-10-07 修）。
⚠️ 两处都是**工作副本**（仓库外面），路径可用 `LDV_PRIOR_ART` / `LDV_SOURCES` 覆盖：

| 目录 | 是谁的源档 |
|---|---|
| `<工作区根>/.prior-art/*.txt` | `repo/docs/分层方向视图-成熟方案与跨领域文献.md`、`repo/ldv/C8-成熟方案对照.md` |
| `<工作区根>/sources/*.txt` | `outputs/ldv-成熟答案印证.md`（13 篇：`gist1995` / `kraska2018` / `unbiased_ltr` / `hnsw` / `mlcs` / `mvselect2026` / `onesize2005` …） |
| `<工作区根>/.webcache/*.html` | **网页来源**的引文（2026-10-07 加）：ESLint *Bulk Suppressions*、rustc *Lint Levels* |

⚠️ **第三份（网页缓存）是 2026-10-07 补的，理由是「源不在范围」与「引文写错了」长得一样。**
   有些引文的源**不是 PDF**、是官方网页，而 v4 只读两份 `.txt` ⇒
   那些引文**全部**被报成「缺口」（实测：`outputs/ldv-成熟答案印证.md` 里那条 ESLint 引文
   覆盖比 **0.64**）—— 读的人会去改**本来是对的**引文。
   可用 `LDV_WEBCACHE` 覆盖路径。

⚠️ **网页源带来的覆盖是*弱*的**：脚本只去标签、去 `script`/`style`、解实体，
   **不抽正文** ⇒ 页脚/导航的样板文字也算「命中」。
   所以网页源上「覆盖比 = 1.0」只说明「这句话在这一页里出现过」，
   **不说明它在正文里** —— 要逐字核仍然得打开那一页。
   （不抽正文是**故意的**：抽正文要写 per-site 选择器，那等于把核验变成
    「按我挑的那段去核」，比不核更糟。**弱但一致**胜过**强但可调**。）

⚠️ **顺带一条已知性质 —— 它的盲区有多大，2026-10-08 实测过**：
判据是「覆盖比 ≥ 0.9 且无 ≥ 25 字符连续缺口」。在 **46 份源文 / 1663876 个 12-gram** 的规模下，
这个判据能抓什么、抓不住什么，是**两类分开**的：

| 编造的类型 | 实测 | 判据表现 |
|---|---|---|
| **异域**编造（整句与语料领域无关） | 4 条全测，覆盖比 **0.00 / 0.00 / 0.00 / 0.67** | ✅ **全红** —— 这一类**抓得住** |
| **同域**编造（措辞像本领域，但整句是编的） | 3 条全测，覆盖比 **0.90 / 0.95 / 0.97** | ❌ **3 条里过 2 条** —— 这一类**抓不住** |
| **一词漂移**（真句里换一个词） | 换两个实义词：**0.94** | ❌ **过** —— 本来就抓不住，见下 |

⇒ **「覆盖比 = 1.0」只说明「这句话的措辞在本领域语料里到处都是」，不说明「这句话有出处」。**
本脚本抓的是**异域编造**，不是**同域编造**，也不是**一词漂移**。

⚠️ **加源必然会扩大这个盲区**（新源的 12-gram 与既有语料同域时会互相「背书」）。
所以**每加一批源，这条边界都要重测一次**，别把「上一批的盲区大小」当成现在的大小。
实测对照（同一批编造句，换不同语料）—— 复现脚本：`outputs/_probe_blind_spot.py`：

| 语料 | k-gram | 同域编造·整句 | 异域编造·整句 |
|---|---|---|---|
| 46 份（含第七批 5 篇） | 1663876 | 0.97（过） | 0.69（红） |
| 去掉第七批那 5 篇 | 1438453 | 0.74（红） | 0.54（红） |

⇒ **第七批把那条同域编造句从「红」推成了「过」。** 这是**已知代价**，
不是「引文都核过了」—— 引文本身仍然要**逐条**回源文看（这正是本文件下面那张内容表存在的理由）。

v3 只读第一份 ⇒ **把一半源文当成不存在**，真引文被报成「缺口」。
实测：GiST 1995 的 `E.p is the Union of all entries on N.` 逐字在
`sources/gist1995.txt:752-757`，v3 却看不见（它当时的「0 缺口」是碰巧，不是证据）。

⚠️ **一份源文都找不到时脚本 `SystemExit(2)`**（2026-10-07 加）——
「源文不在」与「引文有问题」必须长得不一样，否则有人会去改**本来是对的**引文。

### 抽文的**抽取顺序**损伤

`^数字+大写` 形态的页底脚注被插进正文句子中间，多份源档都有
（`hnsw` 17 处、`kraska2018` 9 处、`degenloop` 5 处、`mvselect2026` 4 处…）。
**只在引文跨过它时才需要处理** —— 处置沿用本目录既有先例（同 `mehlhorn05` 就地清控制字符）：

| 源 | 损伤 | 处置 |
|---|---|---|
| `kraska2018.txt` | 脚注 5 插在 `…model [34], importance` 与 `weighting to directly address covariate shift` **之间** | **已就地移到句末**（原句本连续，脚注是页底物） |

其余源档**未动**；若将来有引文跨过某条脚注，按同一做法就地移开，并在此表补一行。

