# 拿真实数据跑整个流程 —— 问题清单

**日期** 2026-10-08 ｜ **版本** v0.1.13 ｜ **HEAD** `0e4d4b2`
**语料** `_data/` 下 13 份 OpenAlex 切片（40 → 3907 项）+ Cora 2708 项
**解释器** `C:/Users/19253/.workbuddy-ai/binaries/python/versions/3.13.12/python.exe`
（所有命令都前置 `PYTHONIOENCODING=utf-8 PYTHONUTF8=1`）

> ⚠️ 本文里的「红」都是**退出码**意义上的红（`cli` 的护栏 / `run_checks` 的断言），
> 不是我自己判的。每条都附**可复现的调用式**与**逐字输出**。

---

## 摘要：真实数据上暴露 8 条，其中 4 条是真缺陷

| # | 是什么 | 性质 | 严重度 |
|---|---|---|---|
| 1 | `cli all --remove N` 在**任何真实语料**上退出码 1（`D2 覆盖漏`） | **真缺陷**（代码与文档冲突） | 高 |
| 2 | `run_checks` 的两条「路径」**都没走 `cli` 实际用的那条** ⇒ 全绿不蕴含 `cli` 绿 | **真缺陷**（守卫覆盖洞） | 高 |
| 3 | 抽象层 E 在真实语料上**七条全跳过**（外生声明只覆盖 36 项） | 结构性（声明缺失） | 中 |
| 4 | `§A3`（核心定理的验证）在**视图数 ≥ 11** 时必然跳过 | 结构性（oracle 指数级） | 中 |
| 5 | `reach` 方向数 = **2n−1**（3907 项 → 7813 方向 → 声明 285 KB） | 规模（手工不可行） | 中 |
| 6 | `sequence` 全量建内核 **386 s** | 性能 | 低 |
| 7 | **`remove` 只从 `Cone(x)` 摘项** ⇒ 重试展开后的子方向留下**幽灵成员** ⇒ `B4` 在删除路径上报「子方向多出父没有的项」（4 语料 **49 项**） | **真缺陷**（已修） | **高** |
| 8 | `cli` 的 B′ `D2 覆盖漏` 判据用的是**裸漏**（含论域外 + 有证明的滞留两类）⇒ **基线就红**，而那条红与删除**毫无关系** | **假红**（已修） | 高 |

**一句话**：36 项那套玩具语料是**唯一**能让「全绿」成立的地方；
真实语料上，要么检查**跳过**（3/4），要么 `cli` **退出码 1**（1/2/8），
要么**读数看着对、其实在报一件没发生的事**（7）。

---

## 问题 1（真缺陷）：`cli` 的 reach 根 payload 用了**全语料**

### 症状

```bash
LDV_CORPUS=openalex-small python -m ldv.cli all --remove 12   # 退出码 1
```

```
── 流程 B′ · 删除（§10.2 C）──
  · 删 W3201073812｜锥 9 层｜支持集空了 1：['D526']｜失效记账 9 条｜D2 漏 8｜D3 越界 0｜‼ D2 覆盖漏 8
  ...（12 条全同）
  D1–D6 断言：违规 12 处 ‼ 违规
  ‼ 护栏：B′ D2 覆盖漏 8；...（×12）
  护栏（M3 锥外变动 / F3 外生项被改 / 空转 / D1–D6）：‼ 有违规
  退出码 1
```

**在所有真实语料上都成立**（`openalex-tiny` 100 项 / `n150` / `b280` / `small` 281 项），
而 36 项那套是 `D2 漏 0 / 违规 0 处 / 退出码 0`。

### 根因（单变量对照）

`ldv/cli.py:86`：

```python
elif name == "reach":
    plug = ReachPlugin(edges)
    root = frozenset(ids)          # ← 全语料 281 个 id
```

而建树时只有 `init_items`（269 项）已知 —— 那 12 项是留给流程 B 的。

`ldv/checks/_fixtures.py:274`（`make_builder`，`run_checks` 走的那条）**逐字写着**：

> ⚠️ **根 payload 必须跟着 subset 走**，不能偷偷用全集：
> `reach` 的根是「全部项当锚点」，增量时它只该是「**当时已知的**锚点」。
> **用全集就等于作弊** —— 那一侧就不是增量了。

它用的是 `k.build(frozenset(sub))`。

**对照实验**（`outputs/_probe_root_ab.py`，唯一变量 = 根 payload）：

| 根 payload | 批建后漏 | 维护 12 项后漏 |
|---|---|---|
| `frozenset(全部 281 个 id)` | **8** | **8** |
| `frozenset(已知的 269 个 id)` | 0 | 0 |

⇒ **成因唯一**：根声明覆盖 281 项，而建树时只有 269 项进了结构，
子方向盖不住那 12 项 ⇒ 漏 8。

### 为什么一直没被发现（两条叠加）

1. **`D2` 只挂在删除路径上**，而漏是**批建**阶段就有的 ——
   `cli all`（不删）在同一语料上**退出码 0**：
   ```
   cli all --remove 12 (281) 退出码 = 1
   cli all（不删）    (281) 退出码 = 0
   cli all --remove 12 (36)  退出码 = 0
   ```
   即：**守卫挂在了「症状出现的那条路」上，而病根在另一条路上。**
2. **`run_checks` 用的是诚实那条路**（`make_builder`），
   它在同一语料上对这个量报的是：
   ```
   · 覆盖不漏（reach）：一次建完 0 漏 / 280 对、维护 0 漏 / 24 对（两条路径都成立）
   ```

### 与文档的冲突

`docs/分层方向视图-设计文档.md:2118`：

> ⚠️ **`D2` / `D3` 在真 `Kernel` 上按构造恒绿**（理由见上：删除既不碰 `payload`
> 也不碰语料）⇒ 它们不是「会开火的判据」，是**守卫**。

这句话**在 36 项上成立、在真实语料上不成立**。
（另一半仍然对：`D2` 确实不是被删除打破的 —— 删除前后都是 8，
见 `outputs/_probe_d2_stage.py`。）

### 修法（一行）

`cli.py` 的 `reach` 分支改成用**当时已知的**锚点：

```python
# 现在
root = frozenset(ids)                     # 第 86 行
# 改成（把 root 的计算挪到 init_items 之后）
root = frozenset(init_items)
```

**验收判据**：`outputs/_probe_root_ab.py` 的对照表（8/8 → 0/0）。
⚠️ 改之前先确认这不是**有意的**声明 —— 设计稿说 reach 的根是「外生锚点集 = 覆盖全部项」，
但 `_fixtures` 明说增量侧只能声明**已知**的。**两条路必须先统一口径**，
否则就是把红从 `cli` 挪到 `run_checks`。

---

## 问题 2（真缺陷）：`run_checks` 的「两条路径」漏掉了 `cli` 实际用的那条

`run_checks` 声称「健全性与覆盖不漏都要**两条路径**」（`MAINT_INIT = 6`）：

```
· 覆盖不漏（reach）：一次建完 0 漏 / 280 对、维护 0 漏 / 24 对（两条路径都成立）
```

但 `cli` 用的是**第三种切分**（先建 269、维护 12），而漏的读数对切分**敏感**：

```
── reach ──（outputs/_probe_leak_pair.py）
  先建   6 再维护 275：漏  256 项 /  1 对  D0 漏 256 项
  先建  12 再维护 269：漏  142 项 /  1 对  D0 漏 142 项
  先建  50 再维护 231：漏   83 项 /  1 对  D0 漏  83 项
  先建 269 再维护  12：漏    8 项 /  1 对  D0 漏   8 项   ← cli 用的切分
  先建 281 再维护   1：漏    1 项 /  1 对  D0 漏   1 项
```

⚠️ 而且 `run_checks` 的「维护」路径用的是**诚实根**（子集），
`cli` 用的是**全集根** —— 两条路**连根都不一样**，
所以「同一读数两边都是 0/8」这件事**不可能**发生。

**结论**：`run_checks` 全绿**不蕴含** `cli` 全绿。验收层（`test_intake`）
在 281 上也是 `过 13 · 红 0 · 跳过 0 · exit 0` —— 它跑的是 `run_checks`，
**根本不经过 `cli`**。

---

## 问题 3（结构性）：抽象层 E 在真实语料上**七条全跳过**

```bash
LDV_CORPUS=openalex-small python -m ldv.run_checks --no-probes
```

```
  [跳] A1 ... 外生声明是在**另一份语料**上写的（{'项数': 36, '边数': 47} vs 现在 {'项数': 281, '边数': 1008}）
  ...（A2–A7 同）
  断言 69 条：红 0，跳过 12
  跳过的是：['B18', 'B18', 'B20', 'B18', 'B20', 'A1', 'A2', 'A3', 'A4', 'A5', 'A6', 'A7']
  退出码 0
```

`ldv/checks/view_spec.json` 的 `语料` 字段冻死在 `{项数: 36, 边数: 47}`，
`spec_for` 要求**精确相等** ⇒ 换语料全跳。**仓库里没有任何工具能产出新声明。**

### 但 E 本身是好的 —— 缺的只是声明

`outputs/_probe_real_viewspec.py` 机械生成一份（281 项 **1357 字节**，
36 项那份是 5862 字节），七条判据立刻跑起来：

```
语料 {'项数': 281, '边数': 1008}；方向 keyset
方向数 39｜树边 38 条
spec_for ⇒ OK

═══ 插件 (视图-探针) ═══
  [过] A1 ... 10 张视图、1072 个成员项全部落在各自的具体化里
  [过] A2 ... 10 块全部稳定（38 条外生边）
  [过] A3 ... 10 块已是最粗（枚举了 115975 个粗化，没有一个稳定）
  [过] A4 ... 3 条读数声明全部实测成立
  [跳] A5 ... 账为空 ⇒ 判不了，**不是通过**
  [过] A6 ... 10 张视图落盘-读回
  [过] A7 ... 从 10 张旧视图出发 ⇒ 传播后 14 块，稳定
  ── 断言 7 条：过 6，红 0，跳过 1
视图（keyset）：39 个方向 ⇒ 10 张视图（最大一块 20 个方向）
```

把这份声明**真的写进** `view_spec.json` 再跑 `run_checks`（用完已 `git checkout` 还原）：

```
  [红] A4 视图类别 ... 2/5 条读数声明与实测不符：
       ['中位数：见证**不成立**：`计数与和` 在 A / B 上本来就不同（(3, 472.0) vs (3, 350.0)）',
        '覆盖计数：见证**不成立**：`计数与和` 在 A / B 上本来就不同（(2, 191.0) vs (2, 63.0)）']
  断言 69 条：红 1，跳过 6
```

⇒ 跳数 **12 → 6**，且**设计自己的预言成立**：换语料后过期见证会让 `§A4` **报红**
（而不是静默通过）。这一条**没有问题**，是设计按预期工作。

---

## 问题 4（结构性）：`§A3` 在真实规模上必然跳过

`§A3` 用**暴力枚举**验证「`Q` 就是那个唯一的解」（刻意与被判对象不共用代码，
避免共享盲点）。候选数 = `∏ Bell(组内块数)`，`P = {U}` 时就是 `Bell(|Q|)`，
上限 `MAX_COARSENING_CANDIDATES = 200_000`。

**`Bell(11) = 678570 > 200000`** ⇒ **视图数 ≥ 11 就必然跳过。**

```bash
python ../outputs/_probe_a3_scope.py openalex-tiny ... openalex-small
```

```
上限 MAX_COARSENING_CANDIDATES = 200000
语料                  项数 方向       方向数  视图数      Bell(|Q|)    §A3
openalex-tiny        100 keyset      29     8           4140      跑
openalex-tiny        100 reach      199    11         678570     跳过   ← 连 100 项都跳
openalex-n50          50 reach       99    10         115975      跑
openalex-n150        150 reach      299    13       27644437     跳过
openalex-small       281 reach      561    16    10480142147     跳过
openalex-small       281 keyset      39    10         115975      跑   ← 只剩一步
```

`reach` **从 100 项（11 张视图）起就跳过**，而 `§A3` 正是
「**视图不用挑，因为它唯一**」这条主张的验证 —— 也就是这层设计最强的那一条。
⇒ 它在**真实规模上没被验过**，而且跳过（不是红），看起来像没问题。

⚠️ `keyset` 在 281 项上是 10 张视图（115975，**差一步就撞上限**）。
再加几十项就会掉出去。

---

## 问题 5（规模）：`reach` 方向数 = 2n−1

```
语料                  项数 方向       方向数   树边   声明字节   建内核 s
openalex-n50          50 reach       99     98       2995      0.01
openalex-n100        100 reach      199    198       6364      0.02
openalex-n200        200 reach      399    398      13152      0.10
openalex-small       281 reach      561    560      18649      0.18
openalex-b280        280 reach      559    558      18581      0.21
openalex-citations  3907 reach     7813   7812     291884    103.72
```

严格 **2n−1**。3907 项 ⇒ **7813 个方向**，机械生成的声明 **285 KB**。

⇒ 问题 3 的「补一份声明」这条路，在 `reach` 上**手工写不可行**。
（`keyset` 79 方向 / 2401 字节、`sequence` 118 方向 / 3613 字节都还好，
 但 `sequence` 在 281 项上给 **183 漏**、3907 上更多。）

---

## 问题 6（性能）：`sequence` 全量建内核 386 s

同一次测量的最后一列：`keyset 3.23 s`、`reach 103.72 s`、**`sequence 386.29 s`**。
三个方向加起来 ~8.2 min **只是建内核**。README 记的「全量 `cli all` 466 s」与这个量级一致。

---

## 问题 7（真缺陷，已修）：`remove` 只从 `Cone(x)` 摘项 ⇒ 子方向留下**幽灵成员**

### 怎么发现的

`cli all --remove 12` 修好根口径之后（问题 1），`openalex-n100` **仍然**退出码 1。
把 `cli` 不跑的那条判据（`B4`）接上去逐段量，看到**删除路径**上 `B4` 红：

```
openalex-n50 / keyset / B′ 删除: NO｜1 处破划分：["D18 的子方向多出父没有的项：['W2156098321', ...]"]
openalex-n50 / reach  / B′ 删除: NO｜4 处破划分：["D10 的子方向多出父没有的项：['W2161374186']", ...]
openalex-n100 / reach / B′ 删除: NO｜1 处破划分：["D170 的子方向多出父没有的项：['W2970971581']"]
openalex-n150 / reach / B′ 删除: NO｜1 处破划分：["D237 的子方向多出父没有的项：['W3006961006']"]
openalex-n150 / sequence / B′ 删除: NO｜1 处破划分：["D26 的子方向多出父没有的项：['W3007497549', ...]"]
```

⚠️ 注意这句红**不是**「漏」，是**「多」**：`∪members(子) − members(父) ≠ ∅`。

### 根因（单变量定位）

`_probe_extra_defect.py`（cwd = repo）把每个待删项在**删之前**的三样东西并排打出来：

```
删  7 W2156098321
    Cone(x)（插入路径，3 层）：['D0', 'D1', 'D18']
    x 实际在的成员方向（4 个）：['D0', 'D1', 'D18', 'D20']
    ★ 在成员里、**不在锥上**：['D20']  ‼ 新增 extra
       → D18 的子方向多出 W2156098321；D18 是 x 的成员吗：False；D18 在锥上吗：True
```

    `_path[x]`（= `Cone(x)`）**只在 `insert` 结尾写一次**（当时的下降路径）
    而 `expand()` **会重试**（叶不是终态：成员集变了就重判）
    重试时 `_expand_inner` 把 `x` 分桶分进一个**新的**子方向 `D20`
    —— 那一笔**没有记到 `_path[x]` 上**
    ⇒ `x` 是 `D20` 的成员，却不在自己的锥上
    ⇒ `remove(x)` 只从 `['D0','D1','D18']` 上摘 ⇒ `D20` 留着 x ⇒ 「子方向多出父没有的项」

**规模**（`_probe_cone_defect.py`，4 份语料 × 3 方向 × 2 段）：

| 语料 | 方向 | A 批建（持有集≠锥） | B 维护（持有集≠锥） |
|---|---|---|---|
| openalex-n50 | keyset / reach / sequence | 0 / 0 / 0 | 0 / 0 / **2** |
| openalex-n100 | keyset / reach / sequence | 0 / 0 / 0 | 0 / **6** / 0 |
| openalex-n150 | keyset / reach / sequence | 0 / 0 / 0 | 0 / **7** / **11** |
| openalex-b280 | keyset / reach / sequence | 0 / 0 / 0 | **2** / **4** / 0 |
| **合计** | | **0** | **49** |

⇒ **批建 0、维护 49**。这不是巧合：`B15` 保证批建时「每个方向第一次被展开时成员集
已经完整」⇒ **展开一次就定形、重试永不触发**（`kernel.expand` 的 docstring 自己写着）。
维护路径正相反 —— 成员是后来才长起来的，重试才会触发。

⚠️ 同一份探针还量了**另一条**同源但**没被触发**的形态（记为未爆的洞）：
「滞留项 ∈ 某个子方向的成员」在 4 份语料 × 3 方向 × 2 段上**全为 0**。
它的机制是：`insert` 在叶上把项落进 `members(d)` **之后**再试一次 `expand(d)`，
那一次分桶会把它分进某个子方向；若随后所有子方向都 `_refuses` 它 ⇒
它**停在父**（记 `stayed`）却**已经在子的成员里** ⇒ `B4` 的 `漏` 里没有它、账上有它。
本次没触发，但机制在。

### 修法

**改代码，不改检查器** —— `B4` 报的是对的，错的是 `_path` 的维护。

    ① `_expand_inner`：写 `_members[kid]` 的**同时**把那一笔追加到每个被分桶的项的锥上
       （`_path.get(iid) is None` 时**跳过** —— 那说明它还在被插入的路上，
        由 `insert` 先记路径、再让这一笔追加进去）
    ② `insert`：第二次 `expand(d)` **之前**先记下当时的路径（否则 ① 的那一笔没有落点）
    ③ `insert` 结尾：**合并**「走过的路径」与「①追加的子方向」，不是覆盖

③ 的**合并**不是可选的：分桶按 `代价` 选，下降还过一遍 `_refuses`，
两者可以选到**不同**的子方向 ⇒ 覆盖会丢掉「被分桶进去但没下降进去」的那个。
合并之后得到的就是**支撑锥的定义**：

    `_path[x]` == 「当前真的持有 x 的方向」

### 验收（改前 / 改后）

| 量 | 改前 | 改后 |
|---|---|---|
| 持有集 ≠ 锥（4 语料 × 3 方向 × 2 段） | **49 项** | **0 项** |
| `B4` 在删除路径上（4 语料 × 3 方向 × 3 段 = 36 行） | **红 5 / 36** | **红 0 / 36** |
| `run_tests` | 463 过 / 红 0 | **466 过 / 红 0** |
| `run_checks` | 69 条 红 0 跳 4 | **69 条 红 0 跳 4**（不变） |
| `test_injections` | 43/43 | **43/43**（不变） |
| `cli all --remove 12`（n50/n100/n150/b280/small） | 退出码 1 | **退出码 0** |

**反向判据（防改过头）**：`test_lock_scope` 的「作用域 = 锥」
（`insert 36/36、remove 8/8`、`公共交 == {根}`、`路径锁 max < 扇出锁 max`）
**改前改后逐项相同** ⇒ 改的不是锁的作用域，只是把锥**补齐到它本来就该有的定义**。

---

## 问题 8（假红，已修）：`cli` 的 B′ `D2 覆盖漏` 拿**裸漏**当判据

### 两个口径量的不是一件事

    `cover_leak_profile`   覆盖(父.payload) − ∪覆盖(子.payload)        ← **语义**（插件 payload）
    `B4` / 成员审计         members(父) − ∪members(子) 对 `stayed_of`   ← **归属**（内核分配）

`_probe_d4_leak.py`（n100 / sequence）把 D4 那一对父子逐项摊开：

```
══ 父 D4（rank 2） 子 ['D12', 'D13'] ══
   members(父)=9  ∪members(子)=9  归属漏=0  stayed_of=0
   cover(父)=10  ∪cover(子)=9  语义漏=1
   · W2995022099: 在语料；**是holdout**；不在members(父)；不在stayed_of；**不在结构items**
     子的覆盖里: **一个都没有**
```

### 分三段看（`_probe_d2_stage2.py`）

| 段 | `members(父)` | `∪members(子)` | 归属漏 | 滞留 | `cover(父)` | `∪cover(子)` | 语义漏 |
|---|---|---|---|---|---|---|---|
| A 批建后（88 项在树） | 9 | 9 | 0 | 0 | 10 | 9 | **1** |
| B 维护 12 项后（100 项在树） | 10 | 9 | **1** | **1** | 10 | 9 | **1** |
| B′ 删 12 项后（88 项在树） | 6 | 5 | **1** | **1** | 10 | 9 | **1** |

⇒ 两个结论：

**① 批建后那个「漏」是 `holdout`**（`W2995022099` **不在结构 items 里**）。
`cover` 是在**整份语料**上算的，而结构的论域是它**真的持有**的项 ⇒ **论域错位**。

**② 维护后那个「漏」是**同一项**，此时它进了树、**是 D4 的成员**、**在 `stayed_of` 里**
⇒ 那是 §10.2 出路 (4) 的**滞留**（每个子方向都**证明**不收它）⇒ **设计如此**。
`B4`（「漏的恰好是账上那些」）在这一段**绿**。

**③ 12 次删除，读数一次都没动。** 这不是「删不掉」，是**这条读数与删除无关**：
`kernel.remove` 的 docstring 自己写着 ③（向上收缩父 payload）**没有做**，
理由是 `payload` 由插件声明、`Direction` 不可变 ⇒ 删一个成员**不改变任何 payload**
⇒ `覆盖(父)` 与 `∪覆盖(子)` **两边都不动**。

### 四种口径的总漏（`_probe_d2_four.py`，4 语料 × 3 方向 × 3 段 = 36 行）

| 口径 | 总漏项数 | `B4` 红行 |
|---|---|---|
| D2 裸（`cli` 原来用的） | **3** | — |
| D2 收域（∩ 树持有的项） | 2 | — |
| D2 记账（− 账上滞留） | 1 | — |
| **D2 收域+记账** | **0** | **0 / 36** |

⇒ 只有最后一个**基线全绿** ⇒ 只有它能当判据（基线就红的判据过不了注入验证）。

### 修法

    `flow.RemoveReport` 拆成两个数：
        `cover_leak`              裸漏        —— **度量**（报趋势，照旧印）
        `cover_leak_unaccounted`  **漏 − 账**  —— **判据**（进退出码）
        `cover_leak_excluded`     被筛掉的     —— **读数**（排除必须看得见）
    `违规` 改判 `cover_leak_unaccounted`；`render()` 两个数印在同一行

### 验收

| 量 | 改前 | 改后 |
|---|---|---|
| `cli all --remove 12`（n50/n100/n150/b280/small） | **退出码 1** | **退出码 0** |
| `D1–D6 断言：违规 N 处` | `12 处` | **`0 处`** |
| n100/sequence 的打印 | `D2 漏 1`（看不出有没有账） | `D2 漏 1（其中无证明 0）` |
| `run_tests` 的 `D2` 行 | 裸漏 | **判据**（`_d2_cover_leak` 改） |

### ★ 已知答案对照组（判据真的在读账目，不是把裸漏换个名字）

`run_tests.test_deletion_path` 新加一格 `_AccountedChild`：
与 `_DropChild` **同一处结构破坏**（父的 `_children` 掉一条边），
唯一区别是**给漏掉的每一项补一条 `stayed` 账目**。

```
· keyset   D2 已知答案 ← _AccountedChild 判据 0（绿）、**裸漏 13**（非 0 ⇒ 两个数确实不同）
· reach    D2 已知答案 ← _AccountedChild 判据 0（绿）、**裸漏 9**（非 0 ⇒ 两个数确实不同）
· sequence D2 已知答案 ← _AccountedChild 判据 0（绿）、**裸漏 25**（非 0 ⇒ 两个数确实不同）
```

⚠️ 裸漏那三个数（13 / 9 / 25）与 `_DropChild` 注入格**逐字相同** ⇒ 对照组确实是
「同一处破坏、只多一条账」，不是另造了一个弱化版。

---

## 附：没有问题的部分（也是结论）

- `cli all`（**不删**）在真实语料上 **2 s、退出码 0**，`M3` / `F3` 护栏全过。
- `run_checks --no-probes` 在 281 上 **5 s**、`B1`–`B20` 该过的过该跳的跳（跳过 3 类：`B18` 换语料、`B20` 非标签轴、`A*` 声明过期）。
- `test_intake` 在 281 上 **过 13 · 红 0 · 跳过 0**（它跑的是 `run_checks`，不经过 `cli`）。
- `§A4` 的过期见证**按设计报红** —— 这是设计要的效果，不是缺陷。
- `E′` 传播在真实语料上**跑得通**（10 张 → 14 张，`传播 ⊑ 重算` 为真）。

---

## 复现清单

```bash
cd repo
PY=C:/Users/19253/.workbuddy-ai/binaries/python/versions/3.13.12/python.exe
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1

# 问题 1/2
LDV_CORPUS=openalex-small $PY -m ldv.cli all --remove 12        # 退出码 1
LDV_CORPUS=openalex-small $PY -m ldv.run_checks --no-probes     # 红 0，覆盖不漏（reach）0 漏
LDV_CORPUS=openalex-small $PY ../outputs/_probe_root_ab.py      # 单变量对照：8/8 → 0/0
LDV_CORPUS=openalex-small $PY ../outputs/_probe_d2_stage.py     # 8 漏在批建阶段就有
LDV_CORPUS=openalex-small $PY ../outputs/_probe_leak_pair.py    # 对切分敏感

# 问题 3
LDV_CORPUS=openalex-small $PY ../outputs/_probe_real_viewspec.py keyset
LDV_CORPUS=openalex-small $PY ../outputs/_gen_real_viewspec.py keyset --stale-witness
LDV_CORPUS=openalex-small $PY -m ldv.run_checks --no-probes     # A4 红
git checkout ldv/checks/view_spec.json                          # 还原

# 问题 4
$PY ../outputs/_probe_a3_scope.py openalex-tiny openalex-n50 openalex-n100 openalex-n150 openalex-n200 openalex-small

# 问题 5/6
$PY ../outputs/_probe_scale_dirs.py openalex-tiny openalex-n50 openalex-n100 openalex-n150 openalex-n200 openalex-small openalex-b280 openalex-citations

# 问题 7（幽灵成员）—— 改前 / 改后各跑一次，对比「持有集≠锥」那一列
$PY ../outputs/_probe_cone_defect.py                # 改前合计 49，改后 0
$PY ../outputs/_probe_extra_defect.py openalex-n50 keyset 12 12
$PY ../outputs/_probe_stay_delete.py openalex-n100 sequence 12

# 问题 8（D2 假红）
$PY ../outputs/_probe_d2_universe.py openalex-n100 12
$PY ../outputs/_probe_d2_stage2.py openalex-n100 sequence 12
$PY ../outputs/_probe_d4_leak.py openalex-n100 sequence 12
$PY ../outputs/_probe_d2_four.py                    # 36 行 × 四种口径 + B4
```

**探针脚本**（都在工作区根 `outputs/`，不进仓库）：
`_probe_real_viewspec.py` · `_gen_real_viewspec.py` · `_probe_a3_scope.py` ·
`_probe_scale_dirs.py` · `_probe_cover_leak_real.py` · `_probe_d2_stage.py` ·
`_probe_leak_pair.py` · `_probe_root_ab.py` ·
`_probe_d4_leak.py` · `_probe_d2_universe.py` · `_probe_d2_stage2.py` ·
`_probe_d2_four.py` · `_probe_cone_defect.py` · `_probe_extra_defect.py` ·
`_probe_stay_delete.py`

**留档输出**：`_real_small_cli_all.txt` · `_real_small_remove.txt` ·
`_real_small_run_checks.txt` · `_probe_scale_full.txt` · `_full_suite_after_a7.txt` ·
`_d2_four_measured.txt`
