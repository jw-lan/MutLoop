# MutLoop

变异测试驱动的测试质量度量与自动增强平台。

核心主张：**用变异分数替代覆盖率来度量测试质量**，并把「存活变异体 → 定向补测 → 重评分」做成自动闭环。

当前进度：**S1、S2、S3、S4、S5、S6 全部完成**（RQ1、RQ2 有实测答案；RQ3 已探索）

- S1 选型与基线采集 ✅
- S2 十二模块全量变异分析（5349 变异体，传统变异分数 69.49%）✅
- S3 覆盖率导向调度 ✅（RQ1 有实测答案，见下）
- S4 增量模式（PR 级只变异改动行）+ 存活变异体分级（triage）✅
- S5 LLM 定向补测 ✅（能力基线 58.0%；三臂对照 55.0%/10.0%/2.5%，RQ2 有答案）
- S6 真实缺陷外部效度 ✅（闭环方法成立，但"变异分数→抓 bug"转化率低，RQ3 已探索）

> **新接手请先读 [`HANDOVER.md`](HANDOVER.md)** —— 它讲清了做了什么、关键决策的理由、
> 以及当前状态。想了解过程中的踩坑与被推翻的假设，读
> [`reports/DEVELOPMENT-LOG.md`](reports/DEVELOPMENT-LOG.md)。

---

## 目录结构

```
MutLoop/
├── mutloop/             # 平台代码
│   ├── subjects.py      # 被测项目注册表（锁定 release tag、deselect 列表）
│   ├── metrics.py       # 静态度量：代码规模 + 测试断言强度
│   ├── baseline.py      # S1 采集器：规模/用例/耗时/覆盖率/per-test 索引
│   ├── mutator.py       # S2 变异引擎：枚举变异体 + 算子归类
│   ├── runner.py        # S2 执行与判定：状态模型、kill 含金量、汇总
│   ├── schedule.py      # S3 行级覆盖率索引与测试选择
│   ├── select_plugin.py # S3 pytest 插件：按选择文件筛选用例
│   ├── import_time.py   # S3 AST 判定「只在 import 时执行一次」的行
│   ├── deadloop_cache.py# S3 死循环结果缓存（修复二）
│   └── __main__.py      # CLI: python -m mutloop
├── scripts/
│   ├── gen_baseline_report.py   # 由 data/baseline/*.json 渲染 S1 报告
│   ├── audit_report.py          # 校验 S1 报告数字与数据一致（21 项）
│   ├── count_mutants.py         # 枚举全部模块的变异体数（不出测试）
│   ├── run_parallel.py          # 并行跑单个模块（--schedule {none,line}；--lines/--diff 增量）
│   ├── run_all_modules.py       # 按变异体数从小到大批量跑 + 逐模块体检
│   ├── compare_schedule.py      # S3 S2/S3 逐变异体对账（口径唯一权威）
│   ├── s3_seed_deadloop.py      # 用历史结果离线预置死循环缓存
│   └── triage_survivors.py      # S4 存活变异体分级（A/B/C 三层）
├── subjects/            # 被测项目源码（按 tag 克隆，不入库）
├── data/
│   ├── baseline/        # S1 采集结果
│   ├── s2/              # S2 逐模块变异分析结果（12 个 JSON，核心资产）
│   ├── s3/              # S3 行级索引 + 12 个 -sched.json 调度结果
│   ├── s4/              # S4 存活变异体分级结果（survivor_triage.json）
│   ├── s5/              # S5 补测结果（能力基线 + 三臂 arm_*.json）
│   └── s6/              # S6 候选样本与验证结果
└── reports/
    ├── FINAL-REPORT.md             # 整体最终报告（S1–S6 证据链，入口）
    ├── S1-baseline.md             # S1 报告：选型与基线（完整版）
    ├── S1-baseline-condensed.md   # S1 精简版
    ├── S2-mutation-analysis.md    # S2 报告：12 模块全量结果（最终结论以此为准）
    ├── S2-slice-marshmallow-utils.md  # S2 切片过程记录（方法学推导）
    ├── S3-scheduling.md           # S3 报告：覆盖率导向调度（RQ1）
    ├── S4-incremental-triage.md   # S4 报告：增量模式 + 存活变异体分级
    ├── S5-llm-patch.md            # S5 报告：LLM 定向补测（RQ2）
    ├── S6-external-validity.md    # S6 报告：真实缺陷外部效度（RQ3）
    └── DEVELOPMENT-LOG.md         # 开发过程与试错记录
```

> **从哪读起**：整体结论看 [`reports/FINAL-REPORT.md`](reports/FINAL-REPORT.md)；
> 各阶段细节看对应报告；过程与踩坑看 [`DEVELOPMENT-LOG.md`](reports/DEVELOPMENT-LOG.md)。

> **数字以哪个为准**：S2 分数引 `reports/S2-mutation-analysis.md`；
> S3 调度数字一律以 `scripts/compare_schedule.py --all --table` 的输出为准（直接读 JSON，
> 唯一同时给出「含超时加速比／共同可判定集偏差／漏杀」的口径）。

## 常用命令

```bash
PY="C:/Users/lenovo、/.workbuddy/binaries/python/envs/mutbench/Scripts/python.exe"

$PY -m mutloop list                              # 列出被测项目与锁定 tag
$PY -m mutloop baseline --per-test               # 采集全部候选项目基线
$PY -m mutloop baseline --subject click          # 只采集一个
$PY scripts/count_mutants.py                     # 重新枚举变异体数 → 应为 5349
$PY scripts/run_all_modules.py --schedule line   # S3 覆盖率导向调度，约 1h15m
$PY scripts/compare_schedule.py --all --table    # S2/S3 对账，几秒钟
$PY scripts/run_parallel.py --subject attrs --target validators.py --lines "175,450"  # S4 增量：只变异指定行
$PY scripts/run_parallel.py --subject attrs --target validators.py --diff             # S4 增量：git diff 改动行
$PY scripts/triage_survivors.py --top 20         # S4 存活变异体分级（写 data/s4/）
```

## 复现环境

```bash
# 1) 新建隔离 venv（注意：venv 目录名是 mutbench，与项目名 MutLoop 不一致）
"C:/Users/lenovo、/.workbuddy/binaries/python/versions/3.13.12/python.exe" -m venv \
  "C:/Users/lenovo、/.workbuddy/binaries/python/envs/mutbench"

# 2) 依赖（pytest 必须锁 8.3.5）
$PY -m pip install "pytest==8.3.5" coverage pytest-cov mutmut libcst numpy scipy \
    hypothesis freezegun six simplejson packaging pytest-mock markupsafe \
    urllib3 certifi charset-normalizer idna tzdata trio

# 3) 克隆被测项目到锁定 tag
git clone --depth 1 --branch 25.3.0       https://github.com/python-attrs/attrs.git            subjects/attrs
git clone --depth 1 --branch 8.1.8        https://github.com/pallets/click.git                 subjects/click
git clone --depth 1 --branch 3.1.6        https://github.com/pallets/jinja.git                 subjects/jinja
git clone --depth 1 --branch 3.26.2       https://github.com/marshmallow-code/marshmallow.git  subjects/marshmallow
git clone --depth 1 --branch 2.9.0.post0  https://github.com/dateutil/dateutil.git             subjects/dateutil

# 4) editable 安装（变异直接作用于工作副本）
for p in attrs click jinja marshmallow dateutil; do
  (cd subjects/$p && $PY -m pip install --no-deps -e .)
done
```

> 环境相关的坑（pytest 版本、沙箱拦截删除、zoneinfo 数据缺失、editable install 改名失效等）
> 见 [`reports/DEVELOPMENT-LOG.md`](reports/DEVELOPMENT-LOG.md) 的「环境坑」一节。

## 两个关键设计决策

**per-test 覆盖率上下文：不用 coverage 内置的 `dynamic_context=test_function`。**
它靠"函数名以 test 开头"做启发式命名，且不含参数化信息。改为由 pytest 插件在每条用例
`logstart` 时调用 `Coverage.switch_context(nodeid)`，上下文名即精确 nodeid，对齐率 89.86%–100%。
S3 的调度层直接依赖这份数据。

**每个被测项目独占一个进程。**
`pytest.main()` 在同一进程内跑第二个项目会受前一次的 `sys.modules` / conftest 残留影响。
`mutloop baseline` 因此为每个项目 spawn 子进程；S3 的并发调度同样依赖这个隔离前提。

## S1 结论摘要

正式实验集：**click + jinja（主力） / dateutil（弱断言对照） / attrs（上限对照）**，
marshmallow 作为 S2 的工程验证载体。requests 因测试依赖 pytest-httpbin（本地 HTTP 服务）、
破坏变异判定的确定性而排除。

| 项目 | 覆盖率 | 用例 | 全量(s) | 断言/用例 |
|---|---|---|---|---|
| attrs | 99.94% | 1346 | 12.85 | 1.88 |
| click | 82.21% | 648 | 27.51 | 2.45 |
| dateutil | 90.61% | 2091 | 7.50 | 1.01 |
| jinja | 90.76% | 909 | 8.44 | 1.57 |
| marshmallow | 97.28% | 1239 | 5.57 | 3.03 |

完整分析见 [`reports/S1-baseline.md`](reports/S1-baseline.md)（完整版）与
[`reports/S1-baseline-condensed.md`](reports/S1-baseline-condensed.md)（精简版）。

## 实验范围：模块级（不是整项目）

每轮整项目分析约 4.6 小时墙钟，两周内几乎没有试错空间。收敛为**每个项目选 2–3 个代表性模块**
后，一轮约 64 分钟，可反复迭代。

入选标准（先定标准再看结果，避免挑对自己有利的样本）：

- 语句数 ≥ 150、覆盖率 ≥ 80%
- **A/B 象限分层**：每个项目必须同时含弱断言与强断言模块——对比本身才是结论
- 象限内取**语句数最多**且预算装得下的（不用"最便宜"，那会系统性偏向低分模块）
- 稳定性是**软约束**（由 `scripts/measure_module_churn.py` 实测前 2 个 release 的文件哈希）

选中的 12 个模块、实测 5349 个变异体，明细见 `data/module_selection.json` 与
[`reports/S1-baseline.md`](reports/S1-baseline.md) 第 7 节。

> marshmallow 整项目分析作为"模块级能否推广到项目级"的锚点，**尚未跑**，该结论暂无证据。

## S2 结果：状态模型与算子集

结果模型（数据契约）：`{mutant_id, file, line, operator, status}`，
status ∈ `killed / survived / timeout / stillborn / compile_error`。

**五个状态的口径（很多比率的口径差别就在这）**：

| 状态 | 含义 | 进分数分母？ |
|---|---|---|
| `killed` | 至少一条测试失败 | 是 |
| `survived` | 测试全过 | 是 |
| `timeout` | 死循环，跑不完 | **是，且计入 killed** |
| `stillborn` | 收集阶段就崩，一条用例都没跑 | **否** |
| `compile_error` | 语法不合法 | **否**（实测 0 个） |

`stillborn` 与 `compile_error` 必须分开：前者语法完全合法，是 pytest 收集期崩溃（rc=4），
叫"编译错误"会误导。

**算子集（2026-09-01 定稿）**：

| 算子 | 含义 | mutmut 对应 |
|---|---|---|
| AOR 算术运算符替换 | `+ - * /` 等互换 | `operator_swap_op`（BinaryOperation） |
| ROR 关系运算符替换 | `< <= == != > >=` 互换 | `operator_swap_op`（ComparisonTarget） |
| COR 条件运算符替换 | `and` / `or` 互换 | `operator_swap_op`（BooleanOperation） |
| CR 常量替换 | 数字 / 字符串 / 标识符 | `operator_number` + `operator_string` + `operator_name` |
| ARG 实参替换 | **仅「实参置 None」** | `operator_arg_removal` |

「删实参」形态已剔除、`__all__` 导出列表已跳过，理由与证据见
[`reports/DEVELOPMENT-LOG.md`](reports/DEVELOPMENT-LOG.md) 的 S2 一节。

**真实变异体数（最终值）**：12 个选中模块共 **5349 个 / 4516 语句 = 1.18 比值**。
实测机时 **2754.0 CPU 分钟 = 45.9 CPU 小时**。

## S3 结果：RQ1 答案

覆盖率导向调度的实测结论（`scripts/compare_schedule.py --all --table` 可复现）：

| 口径 | 加速比 |
|---|---|
| **首次运行**（无历史，可泛化） | **3.47x** |
| 重复运行（带死循环缓存） | 4.65x |
| 纯加速（配对 5200 个非超时变异体） | 5.31x |

代价：漏杀 **13** 个、假杀 **2** 个（修复前是 167 + 90）。

> 跨阶段比分数必须用「共同可判定集」（5109 个），详见 [`HANDOVER.md`](HANDOVER.md) §5。

## S4 结果：增量模式 + 存活变异体分级

**增量模式（PR 级只变异改动行）**：`run_parallel.py` 加了 `--lines`（指定行）和 `--diff`
（`git diff` 工作树自动取改动行）两个入口，只变异改动行而非整个文件。
底层复用 mutator 预留的 `covered_lines` 过滤。

> 注意：增量过滤会改变 `mutant_id`（根因是 ID 含全局枚举序号），增量结果无法和全量结果
> 按 ID 对账，需按（文件、行、算子、描述）对账。详见 [`DEVELOPMENT-LOG.md`](reports/DEVELOPMENT-LOG.md) 的 S4 一节。

**存活变异体分级（triage）**：`scripts/triage_survivors.py` 把 1558 个真实存活变异体
（S2 真值）按「可检测性」分三层：

| 层级 | 数量 | 含义 | S5 优先级 |
|---|---|---|---|
| A 覆盖到了但没断言住 | 910（58.4%） | 行被测试执行、变异仍存活 | 最高 |
| B 根本没覆盖 | 432（27.7%） | 行从未执行 | 低 |
| C import 时执行 | 216（13.9%） | 类体/模块级，需内省式测试 | 中 |

结果写 `data/s4/survivor_triage.json`，供 S5 定向补测做抽样与优先级依据。

## S5 结果：LLM 定向补测

**补测流程**：对 Tier A 存活变异体，让 LLM（deepseek-v4-flash，关 thinking）
「生成测试 → 跑 → 反馈错误/存活 → 重试」最多 5 轮；5 轮仍存活标「疑似等价」
（视为 LLM 无法判定，如实计入未杀死）。

### 第一步：能力基线（100 个 Tier A 分层抽样）

| 指标 | 值 | 含义 |
|---|---|---|
| 编译通过率 | **100.0%** | 终版测试都能跑通原代码 |
| 杀死率 | **58.0%**（58/100） | 最终 killed 占比 |
| 平均尝试轮次 | **1.9** | 仅统计 killed，取值 1–5 |
| 作弊率 | **1.2%** | 重言式断言轮次占比 |

**杀死率的差异来自「可测试性」而非「等价性」**：marshmallow/utils 89%、dateutil/rrule 82%、
relativedelta 80%（纯数据运算，易写测试）；jinja/nodes、click/shell_completion 仅 25%
（深层机制，LLM 较难写触发测试）。按算子 COR 66% > AOR 62% > CR 60% > ROR 52% > ARG 51%。

### 第二步：三臂对照（RQ2 核心，40 个同批变异体）

控制"给 LLM 的信息量"，三臂跑**完全同一批**变异体，prompt 严格隔离：

| 臂 | 给 LLM 的信息 | 杀死率 |
|---|---|---|
| 臂 1 定向 | 完整变异 diff + 算子 + 描述 | **55.0%** |
| 臂 2 覆盖率 | 只给覆盖该行的测试列表（截断前 20，测试名自带语义） | **10.0%** |
| 臂 3 盲测 | 只给模块名 + 行号 | **2.5%** |

| 指标 | 臂 1 | 臂 2 | 臂 3 |
|---|---|---|---|
| 编译通过率 | 100% | 100% | 100% |
| 杀死率 | 55.0% | 10.0% | 2.5% |
| 平均尝试轮次 | 2.0 | 2.0 | 4.0 |
| 作弊率 | 2.2% | 3.7% | 0.5% |

**RQ2 答案**：杀死率单调递减，臂 1 是臂 2 的 5.5 倍、臂 3 的 22 倍——
LLM 补测能力**主要来自变异信息本身**；覆盖率信息只能替代约 1/5；纯盲测基本无效。

结果与脚本：`data/s5/arm_{directed,coverage,blind}.json`、`scripts/s5_probe_one.py`（`--arm`）、
`scripts/s5_sample_run.py`（`--from`/`--limit` 保证同批）、`scripts/s5_arm_compare.py`。

> 两个局限：① 每模块仅 1–4 个样本，按模块/算子的分解值噪声大，只能看趋势。
> ② 臂 2 的测试名自带语义（如 `test_weekday_zero_raises_value_error`），
> 会提示"该行在测什么"，因此臂 2 并非"纯覆盖率信息"。

## S6 结果：真实缺陷外部效度（RQ3）

用「修复前/修复后版本对」验证"提升变异分数能否换来抓真实 bug"。闭环方法成立：
前提验证 → 找盲区 → AI 补测 → 补测测试在 bug 版验证。**批量 4 个合格样本，
AI 杀死 7 个变异体、抓到 1 个真实 bug**（`attrs/_make.py` 的错误消息 bug）。

关键结论：① 闭环方法走通（1 个样本完整抓到 bug）；② prompt 补全上下文
（方法签名+类名+真实行号）后 flash 补测能力从 0 升到杀 7 个；③ "变异分数→抓 bug"
转化率低，根因是**盲区变异体与真实 bug 语义错位**。

详见 [`reports/S6-external-validity.md`](reports/S6-external-validity.md)。

---

改报告后**务必重跑审校**：

```bash
$PY scripts/audit_report.py          # 21 项断言核验，必须全 OK
$PY scripts/gen_baseline_report.py    # 完整版
$PY scripts/gen_condensed_report.py   # 精简版
```
