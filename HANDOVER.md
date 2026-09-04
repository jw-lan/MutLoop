# MutLoop 交接文档

> 最后更新：2026-09-03　阶段：**S1、S2、S3、S4、S5、S6 全部完成**
> 面向：接手本项目的人 / 其他会话。读完这份即可接手，不必重走一遍弯路。
>
> **过程与试错**（被推翻的假设、踩过的坑、方法学推导）见
> [`reports/DEVELOPMENT-LOG.md`](reports/DEVELOPMENT-LOG.md)，本文件只保留最终状态。
>
> **三条口径纪律（全文遵守）**：
> 1. 分清「实测」与「预估／推算」，凡标 ⚠️ 或「推算」的数字不要当结论引用。
> 2. 引用比率时带上分母（本项目至少有 5349 / 5107 / 3549 / 2981 四个不同分母在混用）。
> 3. 跨阶段比分数必须用「共同可判定集」（5109 个）。

---

## 1. 一句话概况

**MutLoop** 是一个课程设计项目，核心主张是：**用变异分数替代覆盖率来度量测试质量**，
并把「存活变异体 → 定向补测 → 重评分」做成自动闭环。

已跑通"S1 选型与基线采集 → S2 十二模块全量变异分析 → S3 覆盖率导向调度全量实测 →
S4 增量模式与存活变异体分级 → S5 LLM 定向补测（能力基线）"。
**5349 个变异体在 5 个真实开源项目上跑完，朴素全套件口径变异分数 69.49%。**

三个研究问题的进展：

| RQ | 内容 | 状态 |
|---|---|---|
| **RQ1** | 调度能加速多少、代价多大 | **已有实测答案**：首次运行 **3.47x**（重复运行 4.65x），漏杀 13 个、假杀 2 个。见 §5 |
| RQ2 | 定向补测效果（LLM 三臂对照） | **已有实测答案**：三臂杀死率 **55.0% / 10.0% / 2.5%**——增益主要来自变异信息本身。见 §5 |
| RQ3 | 与真实缺陷的相关性 | **已探索（S6）**：闭环方法成立，但"变异分数→抓真实 bug"转化率低（4 样本抓 1），根因是盲区变异体与真实 bug 语义错位。见 §5 |

---

## 2. 五分钟上手

### 环境（唯一可用）

```bash
PY="C:/Users/lenovo、/.workbuddy/binaries/python/envs/mutbench/Scripts/python.exe"
cd C:/Users/lenovo、/WorkBuddy/2026-08-30-16-49-19/MutLoop
```

> venv 目录名仍叫 `mutbench`（项目目录已改名 MutLoop，venv 没跟着改），写 `envs/mutloop` 会失败。

### 三条验证命令（不跑测试，几秒钟）

```bash
$PY scripts/audit_report.py        # 校验 S1 报告数字与数据一致 → 21 项应全 OK
$PY scripts/verify_error_origin.py # 校验 error_origin 判定 → 应"全部通过"
$PY -m mutloop list                # 列出 5 个被测项目与锁定的 tag
```

### 常用操作

```bash
$PY scripts/count_mutants.py                      # 重新枚举 12 模块变异体数 → 应为 5349
$PY scripts/run_all_modules.py                    # 批量跑（不带 --schedule 即朴素全套件，S2 口径）
$PY scripts/run_all_modules.py --schedule line    # S3 覆盖率导向调度，跑完约 1h15m
$PY scripts/rescore_all.py                        # 按最终口径重算 summary（不重跑测试）
```

调度对账脚本（只读 JSON，几秒钟）：

```bash
$PY scripts/compare_schedule.py --all --table   # S2/S3 逐变异体对账 ← 口径权威，引用数字先跑它
$PY scripts/s3_import_time.py                   # 漏杀按 import-time / func 归因
$PY scripts/s3_miss_breakdown.py                # 漏杀按成因分类 + 机时构成
```

跑单个模块：`$PY scripts/run_parallel.py --subject click --target parser.py`
（`--workers` 默认 **8**，不要降到 4——`click/types.py` 曾因此跑了 94 分钟）

S4 增量模式与 triage（几秒钟到几分钟）：

```bash
$PY scripts/run_parallel.py --subject attrs --target validators.py --lines "175,450"  # 增量：只变异指定行
$PY scripts/run_parallel.py --subject attrs --target validators.py --diff             # 增量：git diff 改动行
$PY scripts/triage_survivors.py --top 20         # 存活变异体分级（写 data/s4/survivor_triage.json）
```

---

## 3. 成果清单

### 数字速查

| 项 | 值 |
|---|---|
| 被测项目 | attrs 25.3.0、click 8.1.8、dateutil 2.9.0.post0、jinja 3.1.6、marshmallow 3.26.2 |
| 模块 | 12 个，4516 语句 |
| 变异体 | 5349（1.18 变异体/语句） |
| 可判定 | 5107（killed 3400 / survived 1558 / timeout 149） |
| **变异分数（传统，主用）** | **69.49%** |
| 变异分数（严格，辅助） | 42.26% |
| killed 含金量 | 断言命中 1580（44.5%）/ 廉价 1391（39.2%）/ 业务异常 578（16.3%） |
| 存活变异体分级（S4） | A 覆盖未断言 910 / B 未覆盖 432 / C import-time 216（合计 1558） |

### 文件地图

| 路径 | 是什么 | 能否重建 |
|---|---|---|
| `data/baseline/*.json` | S1 采集的 5 项目基线 | 能（跑 baseline，约几分钟） |
| `data/s2/*.json` | **S2 十二模块逐变异体结果（核心资产）** | **不能**（朴素全套件一轮约 45.9 CPU 小时） |
| `data/s3/*-sched.json` | **S3 十二模块调度结果（核心资产）** | 能（`--schedule line` 约 1h15m） |
| `data/s3/line_index_<项目>.json` | 行级「每测试覆盖」索引 | 能（`s3_coverage_probe.py`，~110s） |
| `data/s3/compact_index_<项目>.json` | 上者的紧凑版，调度实际读这个 | 能（由明文转换） |
| `data/s3/_deadloop/*.json` | 死循环结果缓存（修复二） | 能（`s3_seed_deadloop.py`） |
| `data/s4/survivor_triage.json` | S4 存活变异体分级结果（S5 抽样依据） | 能（`triage_survivors.py`） |
| `data/module_selection.json` | 12 个选中模块及其象限 | 不能（S1 决策产物） |
| `reports/S2-mutation-analysis.md` | **S2 最终结论（S2 数字引这个）** | — |
| `reports/S2-slice-marshmallow-utils.md` | 切片阶段的方法学推导过程 | — |
| `reports/S3-scheduling.md` | S3 覆盖率导向调度（RQ1 答案） | — |
| `reports/S4-incremental-triage.md` | S4 增量模式 + 存活变异体分级 | — |
| `reports/S5-llm-patch.md` | S5 LLM 定向补测（RQ2 答案） | — |
| `reports/S6-external-validity.md` | S6 真实缺陷外部效度（RQ3 结论） | — |
| `reports/FINAL-REPORT.md` | **整体最终报告（入口，S1–S6 证据链）** | — |
| `reports/DEVELOPMENT-LOG.md` | 开发过程与试错记录 | — |
| `reports/S1-baseline.md` | S1 选型与基线（成本数字是估算） | — |

### 数据字段（`data/s2/*.json`）

每个变异体一条记录，关键字段：

- `status`：`killed` / `survived` / `timeout` / `stillborn` / `compile_error`
- `kill_class`：`assertion`（断言命中）/ `crash`（崩溃+超时，廉价）/ `business`（业务异常）
- `error_origin`：`tests`（测试侧抛出）/ `package`（被测代码内部冒出）
- `first_error_type` / `first_error_line`：首个失败的错误类型与原始 traceback 行
- `retries`：异常 rc 的重试次数（0 或 1）

> `first_error_line` 是特意存的——事后想重新归类时不必重跑（一轮约 45.9 CPU 小时）。

---

## 4. 关键决策（含理由，改动前请先看）

| 决策 | 理由 |
|---|---|
| **ARG 只保留「实参置 None」，剔除「删实参」** | 删实参让参数个数对不上，58% 的 killed 是 TypeError——测试只是"碰巧"崩了。置 None 的廉价率与 CR 持平，是有效算子 |
| **跳过 `__all__` 导出列表** | 变异导出名会让 `import *` 失败、pytest 收集崩溃，产生大量无意义 stillborn。它测的是导入机制不是业务逻辑 |
| **stillborn 与 compile_error 分离** | rc=4 是收集期崩溃，语法完全合法。叫"编译错误"会误导 |
| **timeout 计入 killed，归为廉价** | 死循环是变异产生的可观察影响，等价被杀死；但与崩溃同属"程序异常"，不算真实检测 |
| **主用传统口径（69.49%）** | 严格口径会给测试改进设虚假上限——崩溃/超时类变异体本身难以用测试用例解决。严格口径作辅助指标完整保留 |
| **每项目独立 workspace 目录** | 共用一个目录时，其他项目的残留副本会污染 `import` |
| **import-time 变异体退回全套件** | 类体/模块级/签名默认值只在 import 时执行一次，覆盖率归属不可靠（漏杀主因）。纯 AST 判定，无项目硬编码 |

---

## 5. 当前状态与下一步

### 已完成

- **S1**：5 项目选型、基线采集、12 模块实验集
- **S2**：变异引擎、执行判定、算子集定稿、12 模块全量分析（5349 变异体，69.49%）
- **S3**：行级覆盖率索引、覆盖率导向调度、12 模块全量实测、**两处修复均已实现并重跑验证**
- **S4**：增量模式（`--lines`/`--diff` 只变异改动行）、存活变异体分级（A/B/C 三层）
- **S5（第一步）**：LLM 定向补测能力基线（100 个 Tier A，杀死率 58.0%，见下）
- **S6**：真实缺陷外部效度（修复前后对照 + AI 补测闭环，见下）

### S4 成果

**增量模式**：`run_parallel.py` 的 `--lines`（指定行）与 `--diff`（`git diff` 工作树取改动行），
复用 mutator 的 `covered_lines` 过滤，只变异改动行。

> 已知局限：增量过滤会改变 `mutant_id`（根因是 ID 含全局枚举序号），增量结果与全量结果
> 需按（文件、行、算子、描述）对账而非 ID。详见 DEVELOPMENT-LOG 的 S4 一节。

**存活变异体分级**（`scripts/triage_survivors.py`，结果在 `data/s4/survivor_triage.json`）：
1558 个真实存活变异体按可检测性分三层——

| 层级 | 数量 | 含义 |
|---|---|---|
| A 覆盖到了但没断言住 | 910（58.4%） | 行被测试执行、变异仍存活，S5 最高优先级 |
| B 根本没覆盖 | 432（27.7%） | 行从未执行 |
| C import 时执行 | 216（13.9%） | 类体/模块级，需内省式测试 |

### S3 的 RQ1 答案（实测）

覆盖率导向调度的最终数字（`scripts/compare_schedule.py --all --table` 可复现）：

| 口径 | 加速比 |
|---|---|
| **首次运行**（无历史，可泛化） | **3.47x** |
| 重复运行（带死循环缓存） | 4.65x |
| 纯加速（配对 5200 个非超时变异体） | 5.31x |

代价（与 S2 真值对比）：漏杀 **13** 个、假杀 **2** 个（修复前是 167 + 90）。

**两处修复**：

| 项 | 实现 | 效果 |
|---|---|---|
| 修复一：import-time 变异体改跑全套件 | `mutloop/import_time.py` | 漏杀 167→13，假杀 90→2，代价 +70.6 CPU 分钟 |
| 修复二：死循环结果缓存 | `mutloop/deadloop_cache.py` | 命中 100 个，省 200.3 CPU 分钟 |

修复前的 12 份结果备份在 `data/s3/_pre_fix_backup/`，`data/s3/*-sched.json` 已是修复后数据。

**S3 遗留的已知不完美（刻意保留，未修）**：

- 严格口径有 ±0.5 pp 跑间噪声（`error_origin` 取第一个失败的测试，并行下不确定）
- 剩余 13 个漏杀（click-types 5 / marshmallow-validate 4 / marshmallow-schema 3 / relativedelta 1）与 2 个假杀（dateutil-win）
- 死循环缓存收益对首次运行为 0（跨运行复用），而 S6 检验的正是首次运行场景
- `--verify` 只复核幸存者，找不出假杀（假杀躺在被判 killed 的那批里）

### 泛化性审查（改动前必看）

**硬约束**：任何针对某个文件／项目特制的、无法泛化的优化都不被允许；除非能扩展到适配所有项目，
否则不要优化，保留不完美，如实报告。

审查结论：

1. **没有发现需要回退的项目特制优化。** 已落地的逻辑要么是语言／平台级原理，要么是不可避免的按项目枚举数据（deselect 清单）。
2. **真正需要警惕的不是"项目特制"，而是"证据单薄"**：ARG 剔除、跳过 `__all__`、`window=5` 三条规则
   的实测证据各自只来自一个模块/切片，写报告时不能说是"已验证"。
3. **超时阈值常数（×`workers//2`）是本机拟合**，换机器必须重新标定。
4. **死循环缓存是跨运行优化**，首次测量收益为零，不能算通用加速手段。

**已知但刻意保留的不完美**：

| 不完美 | 影响 | 为什么不修 |
|---|---|---|
| `dateutil/tz/win.py` 4 条基线失败用例被 deselect | 该模块是在少了 TzWinTest 13 个方法中 4 个（31%）的条件下测的，与 Linux 环境不可直接比较 | 不能修：留着会让每个变异体都被预存失败"杀死" |
| marshmallow 项目级锚点未跑 | "模块级结论能推广到项目级"没有证据 | 属补数据，时间预算内未做 |

### S5 成果（第一步：LLM 补测能力基线）

流程：对 Tier A 存活变异体，让 LLM「生成测试 → 跑 → 反馈错误/存活 → 重试」最多 5 轮，
5 轮仍存活标「疑似等价」（视为 LLM 无法判定，如实计入未杀死）。

**四个质量指标（100 个 Tier A 分层抽样，`data/s5/sample_run_100_v2.json`）**：

| 指标 | 值 |
|---|---|
| 编译通过率 | 100.0%（终版测试都能跑通原代码） |
| 杀死率 | 58.0%（58/100） |
| 平均尝试轮次 | 1.9（仅统计 killed） |
| 作弊率 | 1.2%（重言式断言轮次占比） |

**杀死率差异来自「可测试性」而非「等价性」**：marshmallow/utils 89%、dateutil/rrule 82%、
relativedelta 80%（纯数据运算易写测试）；jinja/nodes、click/shell_completion 仅 25%
（深层机制难写触发测试）。按算子 COR 66% > AOR 62% > CR 60% > ROR 52% > ARG 51%。

脚本：`scripts/s5_probe_one.py`（探针，`--arm` 选臂）、`scripts/s5_sample_run.py`
（`--from`/`--limit` 保证三臂同批）、`scripts/s5_cross_validate.py`（pro 交叉验证，
已弃用——时间成本高，结论改为"如实写杀死率"）。

### S5 三臂对照（RQ2 核心，40 个同批变异体）

控制"给 LLM 的信息量"，三臂跑**完全同一批**变异体（已验证坐标一致），prompt 严格隔离：

| 臂 | 给 LLM 的信息 | 杀死率 | 编译通过率 | 平均轮次 | 作弊率 |
|---|---|---|---|---|---|
| 臂 1 `directed` | 完整变异 diff + 算子 + 描述 | **55.0%** | 100% | 2.0 | 2.2% |
| 臂 2 `coverage` | 覆盖该行的测试列表（前 20） | **10.0%** | 100% | 2.0 | 3.7% |
| 臂 3 `blind` | 仅模块名 + 行号 | **2.5%** | 100% | 4.0 | 0.5% |

**RQ2 答案**：杀死率单调递减，臂 1 是臂 2 的 5.5 倍、臂 3 的 22 倍。
LLM 补测能力**主要来自变异信息本身**；覆盖率信息只能替代约 1/5；纯盲测基本无效。
作弊率同样随信息量单调递减（2.2% → 3.7% → 0.5%）。

结果：`data/s5/arm_{directed,coverage,blind}.json`；对比：`python scripts/s5_arm_compare.py`。

> 局限：① 每模块仅 1–4 个样本，按模块/算子的分解值噪声大，只看趋势。
> ② 臂 2 的测试名自带语义（如 `test_weekday_zero_raises_value_error`），
> 会提示"该行在测什么"，故臂 2 并非"纯覆盖率信息"。
> ③ 三臂均为 flash 单模型结论，未做 pro 交叉验证（时间成本考量）。

### S6 成果（真实缺陷外部效度）

用「修复前/修复后版本对」验证"变异分数提升能否换来抓真实 bug"。方法闭环全通：
前提验证（修复前测试@bug版全过 + 修复后测试@bug版失败）→ 在修复后代码上找盲区 →
AI 补测 → 补测测试在 bug 版验证。

**结果**：批量 5 个功能类 bug 样本，4 个合格，AI 杀死 7 个变异体，**抓到真实 bug 1 个**。

**三个递进结论**：① 闭环方法成立（1 个样本完整走通抓到 bug）；② prompt 补全上下文
（方法签名+类名+真实行号）后 flash 补测能力从 0 提升到杀 7 个；③ "变异分数→抓 bug"
转化率低（click 杀2抓0、jinja 杀3抓0），根因是**盲区变异体与真实 bug 语义错位**。

详见 [`reports/S6-external-validity.md`](reports/S6-external-validity.md)。

### 后续阶段

| 阶段 | 内容 | 产出 |
|---|---|---|
| ~~S4~~ | ~~增量模式、存活变异体分级~~ | ✅ 已完成 |
| ~~S5~~ | ~~LLM 定向补测三臂对照~~ | ✅ 已完成（**RQ2 有答案**） |
| ~~S6~~ | ~~真实缺陷外部效度~~ | ✅ 已完成（**RQ3 已探索**，结论见上） |

### 给 S5 三臂对照的输入（已备好）

- **存活变异体分级**：`data/s4/survivor_triage.json`，1558 个按可检测性分 A（910）/B（432）/C（216）三层；S5 优先抽样 Tier A
- **crash/timeout 共 1391 个**属廉价 killed，不优先补测
- **jinja 两个模块**（nodes 严格分 12.05%、environment 30.10%）是最典型的"高覆盖低质量"样本
- **LLM 依赖**：DeepSeek API（`deepseek-v4-flash` 优先，不行换 `deepseek-v4-pro`），密钥走环境变量 `DEEPSEEK_API_KEY`，不硬编码

---

## 6. 最值得记住的一个发现

**覆盖率几乎无法区分模块。**

12 个模块的覆盖率全部在 87.5% 以上、标准差只有 4.8——从覆盖率看"都很好"。但变异分数把它们拉开了
（标准差 10.7 / 严格口径 18.1）。最典型：**jinja/nodes 覆盖率 89.5%，严格口径下只有 12.05% 的变异体
被真正检测到。**

这是整个项目立论最有力的证据。（详见 `reports/S2-mutation-analysis.md` 第 4 节）

---

## 7. 引用规范

- **整体结论** → 引 `reports/FINAL-REPORT.md`（入口，串起 S1–S5 证据链）
- **S2 最终数字** → 引 `reports/S2-mutation-analysis.md`
- **S2 方法学推导过程** → 引 `reports/S2-slice-marshmallow-utils.md`（顶部已标注哪些数字被取代）
- **S1 选型与基线** → 引 `reports/S1-baseline.md`（成本数字是估算不是实测）
- **S3 调度数字** → 引 `reports/S3-scheduling.md`，或先跑 `python scripts/compare_schedule.py --all --table` 复现
- **S4 增量与分级** → 引 `reports/S4-incremental-triage.md`
- **S5 补测与三臂** → 引 `reports/S5-llm-patch.md`
- **S6 真实缺陷外部效度** → 引 `reports/S6-external-validity.md`
- **踩坑与试错** → 引 `reports/DEVELOPMENT-LOG.md`

### 三条引用纪律

1. **分清「实测」与「预估／推算」。** 本项目曾把 6.28 CPU 小时（预估）写成"S2 实测"，真实值是 45.9。
2. **引用比率时带上分母。** 5349（全部）／5107（S2 可判定）／3549（killed+timeout）／2981（可反查覆盖率的 killed）。
3. **跨阶段比分数必须用「共同可判定集」。** 调度会改变分母（stillborn↔killed 双向流动），
   干净的值是共同可判定集上的 -3.23 pp（修复前）／+2.98 pp（修复后）。
