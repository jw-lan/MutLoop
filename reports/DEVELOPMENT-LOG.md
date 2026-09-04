# 开发过程与试错记录（MutLoop）

> 本文汇集了项目各阶段**被推翻的假设、踩过的坑、以及方法学的中间推导过程**。
> 成果文档（`README.md` / `HANDOVER.md` / `reports/S1-*.md` / `reports/S2-*.md`）
> 只保留最终结论与理由；要理解"我们是怎么一步步走到最终方案的"，读这里。
>
> 按阶段与时间先后组织。每条都尽量写明「当时的错误判断 → 证据 → 修正」，
> 数据均来自当时的实测，可与 `data/` 下的 JSON 对账。

---

## 目录

1. [S1：选型与基线](#s1选型与基线)
2. [S2：变异引擎与判定](#s2变异引擎与判定)
3. [S3：覆盖率导向调度](#s3覆盖率导向调度)
4. [S4：增量模式与存活变异体分级](#s4增量模式与存活变异体分级)
5. [S5：LLM 定向补测](#s5llm-定向补测)
6. [S6：外部效度](#s6外部效度)
7. [环境坑（跨阶段）](#环境坑跨阶段)

---

## S1：选型与基线

### 成本模型的两处低估（6.28 → 45.9 CPU 小时，7.3 倍）

**当时的错误**：S1 报告把 `data/mutant_counts.json` 的 `estimated_cpu_hours`
（字段名就叫 estimated）写成「**S2 实测**」。这是错的——它是 S1 成本模型的推算。

**证据**：实测机时是 **2754.0 CPU 分钟 = 45.9 CPU 小时**（`data/s2/*.json` 的
`duration_s` 求和，`python scripts/compare_schedule.py --all --table` 可复现），
比预估高 **7.3 倍**。

**根因**：成本模型只用了「**未变异基线的全套件耗时**」，漏算了
「**变异本身会把测试拖慢**」这一项——实测中位放大 **1.3–8.6 倍**。
模块级变异体数也高估了（假设 2.0 变异体/语句，实测 1.18）。

**修正**：S1/S2 报告与 README 的顶部都已加 WARNING，引用成本一律用实测 45.9，
不再引预估 6.28 / 8.17 / 10.73。

### 固定开销的发现（初版只算测试执行时间）

**初版错误**：只按「测试执行时间」估算调度收益，声称加速比可达 5.2x–21.6x。

**证据**：实测每判定一个变异体都要**启动一次 pytest 进程**，固定开销 2.9–4.5 秒，
占单变异体成本的 **47%–83%**。计入后真实加速比降到 **1.4x–4.5x**。

**结论（后来被 S3 实测证实）**：固定开销确实省不掉，覆盖率导向调度的收益被它
吃掉一大半。这也解释了为什么 marshmallow（全套件 6–7 秒）只有 1.3x，
而 click（73–161 秒）能到 16–32x。

### RQ1 验收口径的演变

- 原计划「全量变异分析耗时 < 全量测试耗时的 25%」按字面**不成立**。
- 改为「单个变异体平均判定成本 < 全量测试耗时的 25%」（等价加速比 > 4x）后，
  也只有 click 达标。
- 最终决定：**不写 4x 硬性验收**，改为报告实测值，并把「固定开销占比」本身
  作为一个发现来写。（S3 实测：整体 3.81x 含超时 / 5.44x 纯加速，逐模块 1.17x–22.48x。）

### per-test 覆盖率的上下文方案

coverage.py 内置的 `dynamic_context=test_function` 靠「函数名以 test 开头」做
启发式命名，实测只识别 **10/38** 条用例，且不含参数化信息。改用 pytest 插件在
每条用例 `logstart` 时调用 `Coverage.switch_context(nodeid)`，对齐率 89.86%–100%。

---

## S2：变异引擎与判定

### 算子集的两次收窄

mutmut 的 `Mutation` 不携带算子名，AOR/ROR/COR/CR/ARG 是 `mutator.classify()`
从节点形态反推的。切片阶段据此做了两次收窄。

#### ① 剔除 ARG「删实参」形态，保留「置 None」

`arg_removal` 有两种形态，性质差别很大（以 marshmallow/utils.py 切片计）：

| 形态 | 变异体 | kill 率 | TypeError | 廉价率 |
|---|---|---|---|---|
| 删实参 | 91 | 69.5% | 33（占其 killed 58%） | **78.9%** |
| 实参置 None | 75 | 86.8% | 15 | 64.4% |
| CR（对照） | 118 | 57.8% | 0 | 63.5% |

**结论**：删实参让调用参数个数对不上，大量产生 TypeError——测试只是"碰巧"崩了。
置 None 的廉价率与 CR 持平、kill 率还更高，是有效算子。剔除删实参后变异体
312 → 253（−19%），分数 69.23% → 68.49%（几乎不变），ARG 廉价率 71.5% → 67.1%。

**遗留证据单薄**：这条判据的实测只来自 utils.py 一个切片（166 个 ARG）。

#### ② 跳过 `__all__` 导出列表

attrs/validators.py 首跑出现 **40.8% 的 stillborn**，查明是 CR 算子把 `__all__`
里的导出名字符串（`"and_" → "XXand_XX"`）变异，导致 `from ... import *` 失败、
pytest 收集崩溃。这些是"合法"死胎，但测的是导入机制不是业务逻辑。

过滤后：attrs/validators 98 → **60**，stillborn 40.8% → **3.3%**，分数不变。

**遗留证据单薄**：这条判据的实测只来自 attrs/validators.py 一个模块。

### stillborn 的排查（为什么不是「编译错误」）

数据文件里 26 个变异体字段名最初是 `compile_error`，但实测**语法完全合法**
（`compile()` 全部通过）。真实成因是变异改变了被测代码在**模块导入期**的行为，
`tests/base.py` 一 import 就抛异常，pytest 一条用例都没收集到，rc=4 退出。

证据：returncode 全部 4（26/26）、耗时中位 1.69s（是 killed 的 1/3，说明收集阶段就退出）。

**修正**：`runner.py` 新增 `STATUS_STILLBORN`，与真正的 `compile_error`（语法不合法）
拆开。另加防线：rc=5（一条用例都没收集到）单独报警——那是环境问题不是变异体问题。

### error_origin 判定的两个坑

- **不能用包名匹配**：被测项目根往往就叫 `marshmallow`，`subjects/marshmallow/tests/`
  里也含 `marshmallow\`，用包名匹配会把所有测试侧失败误判成崩溃（第一版就这么错的，
  廉价率虚报成 97.5%）。
- **必须统一分隔符**：pytest 输出 `/`，`Path.resolve()` 给 `\`，不统一则 `startswith`
  恒为 False。
- 用工作副本绝对路径 + 分隔符归一化后修正，`scripts/verify_error_origin.py` 固化验证。

### 崩溃型变异体的三分类论证（含 ValidationError 灰色地带）

观察 killed 的失败原因，发现相当一部分不是测试检测到差异，而是**代码直接崩了**
（删实参 → TypeError、改字典键 → KeyError）。这类 killed 既不能算 killed 也不能算 survived。

**三分类**（`runner.classify_kill`）：

| 类别 | 判据 | 算真实检测？ |
|---|---|---|
| crash 崩溃 | 崩溃类异常且从被测代码内冒出 | 不算 |
| business 业务异常 | 被测库设计内抛出（如 ValidationError） | 算 |
| assertion 断言命中 | AssertionError / pytest `Failed` | 算 |

**关键边界**：ValidationError 是 marshmallow 设计内行为，测试常 `pytest.raises` 显式
断言它，算真实检测——所以**只排除程序崩溃、保留业务异常**。

### 6 个工程 bug（其中 3 个会静默产生错误数据）

| 问题 | 症状 | 根因与修正 |
|---|---|---|
| 并行丢变异体 | 60 个结果只有 56 个唯一 ID | 按 `(line, description)` 还原 Mutant，描述相同的被合并。改按 `mutant_id` |
| hypothesis 随机性 | 同一变异体两次判定不同 | 加 `--hypothesis-seed=0` |
| hypothesis 库污染 | 前一变异体影响后一 | 隔离粒度改为每个变异体一个库（每个 worker 一个库试过，无效） |
| 子目录模块路径 | dateutil/tz/win.py 启动即崩 | `Path(file).name` 吃掉 `tz/` 子目录。新增 `in_package_path()` |
| workspace 遮蔽标准库 | marshmallow 对照失败 | 残留 `types.py` 遮蔽标准库 `types`。清理残留 |
| 超时阈值未随并行放宽 | click 96.6% 被误判 timeout | 阈值 × `workers // 2` |

**超时排查的教训**：不能只看超时比例就归因。click 的 96.6% 是并行拖慢（串行 38s 能跑完），
rrule 的 10.2% 是真实死循环。用 `probe_timeout_one.py` **按 mutant_id** 抽样才区分开
（第一次按行号抽样抽错了对象，串行 9 秒完成，得出"并行减速"的错误结论）。

### 口径选择的再讨论（传统 vs 严格）

决定主用传统口径的理由：严格口径会给测试改进设虚假上限（崩溃/超时类变异体本身
难以用测试用例解决）。但数据显示了反向考量：

1. 传统口径与覆盖率中度相关（r=0.736），相当程度重复了覆盖率已有的信息；
2. 严格口径与覆盖率弱相关（r=0.455），区分度是传统口径的 1.69 倍，更能捕捉覆盖率看不到的东西。

**当前处理**：尊重决定，主用传统口径，同时完整保留严格口径数据。两种口径的差距
（69.49% vs 42.26%）本身就是一个发现：约四成的"被杀死"其实是廉价的。

---

## S3：覆盖率导向调度

### 漏杀归因：import-time 代码的盲区

类体 / 模块级 / 函数签名默认值只在 import 时执行一次，覆盖率只能把它们归给
"碰巧第一个触发导入"的测试，而真正检测改动的测试（如 `to_info_dict()` 内省）根本不执行
那一行。于是调度必然选错。

**修法**（`mutloop/import_time.py`，纯 AST）：判定变异行是否只在 import 时执行，
是则退回全套件。实测漏杀 167 → 13（其中 import-time 贡献了 154/167 = 92%）。

### 双向误差的发现（推翻「误差单向」）

文档原声称"覆盖率导向的误差是**单向**的，只会 killed→survived（漏杀），分数是下界"，
并称反向只有 2 个。重跑后实测发现：

| 方向 | 修复前 | 修复后 |
|---|---|---|
| 漏杀（S2 杀 / S3 活） | 167 | 13 |
| **假杀（S2 活或死胎 / S3 杀）** | **90** | 2 |

90 个假杀里 **82 个**在 `dateutil/rrule.py`，全是「只跑 1 条测试时收集就崩、被记成 killed」，
而真值是 stillborn（真值里它们跑全套件时在收集阶段就崩，rc=2 被排除出分母）。

**教训**：减少测试反而能把死胎变成"被杀"，抬高分数——「分数是下界」不成立。
原来的"反向 2"之所以错，是只统计了 `survived→killed`，漏掉了 `stillborn→killed`。
连带结论：`--verify` 只复核幸存者**不足以**得到精确结果，假杀躺在被判 killed 的那批里。

### 修复一/二的推算 → 实测

| 项 | 推算 | 实测 |
|---|---|---|
| 修复一代价 | +121.1 m | **+70.6 m**（推算偏保守） |
| 修复一回收漏杀 | 154/167 | **154 个（完全一致）** |
| 修复二节省 | −200.3 m | **−200.3 m（一条不差）** |

推算脚本 `s3_import_time.py` 用含超时的模块均值，被 rrule 的 120 秒超时污染，算出
rrule "修复后反而更快"的失真结果；`s3_fix_projection.py` 改用非超时中位数仍低估了 rrule。

### 死循环缓存的方法论陷阱

缓存（修复二）的收益依赖"已知哪些是死循环"：**首次运行**新项目时没有清单，收益 = 0；
只有**重复运行**（如 S5 补测后重评分）才有那 200.3 分钟。所以含缓存的 4.65x
把"上一轮跑出来的知识"算进了这一轮，属跨运行信息复用，**不能当成 RQ1 的加速比**。

### 严格口径的 ±0.5 pp 跑间噪声

`error_origin` 取"第一个失败的测试"，并行下哪个测试先失败不完全确定。
实测 click-shell_completion 有 3 个变异体的 kill_class 发生翻转。所以**小于 1 pp 的
严格口径差异不能当结论用**。

### 缓存预置的一次倒退

`s3_seed_deadloop.py` 只拿「S2 + 当前 S3」做种子时，被缓存跳过的死循环在当前 S3 里是
`from_cache`（`record()` 会跳过），只剩 1 次运行记录，从已确认掉回未确认（100 → 31）。
修正：种子必须包含修复前的备份那次运行。

---

## S4：增量模式与存活变异体分级

### 增量过滤会改变 mutant_id（方案 A 保留，未修）

`enumerate_mutants(covered_lines=...)` 按行过滤后，`mutant_id` 与全量枚举**完全对不上**
（实测 0/11 一致）。根因是 `mutant_id` 的 sha1 里含**全局枚举序号 `i`**，过滤后序号整体前移。

**本质**：ID 本来就"绑定具体枚举上下文"（连算子集变了 ID 也会变），增量模式只是让它更明显。
**决策（用户拍板方案 A）**：不改 ID 公式（改成组内序号会要求迁移全部 12+12+12 个 JSON 的 ID，
风险是错位破坏核心数据）。需要跨模式对账时，按（文件、行、算子、描述）匹配而非 ID。

### 等价变异体：TCE 启发式实测 0 命中

想借成熟的 TCE（Trivial Compiler Equivalence，Papadakis 2014，字节码等价）先筛一轮等价变异体。
实测 `compile()` + 递归 code object 比较，对我们的算子集（AOR/ROR/COR/CR/ARG）**0 命中**
（attrs/validators 60 个、marshmallow/utils 253 个全 0）。

**根因**：TCE 只在"变异编译成相同字节码"时有效，主要针对**语句删除（SDL）**类算子——那正是
等价变异体的大头，而我们当初已剔除 SDL。我们的值替换算子字节码必然变，TCE 用不上。

**结论**：无廉价启发式可借用，等价判定只能靠 LLM 语义判断，或用"生成测试→跑→杀不死"作反向证据。

### 等价变异体要不要单独判定：建议不做

- 收益小：SDL 已剔除，严格等价变异体本来就少。
- 成本高：单独一轮 LLM 判断 = 多花 token。
- 有误判风险：LLM 误判"等价"会漏掉本可杀死的变异体。
- 等价信息会在 S5 的"生成→跑→杀不死"里自然浮现，如实报告即可。

### 存活变异体分级的由来

triage 用「行级覆盖索引（判覆盖）+ `is_import_time`（判 import-time）」把 1558 个存活变异体
分 A/B/C 三层。分层依据是"可检测性"——覆盖了但没断言住（A）才是 S5 最该补的，未覆盖（B）和
import-time（C）补测收益更低。

---

## S5：LLM 定向补测

### 三次方案迭代（等价出口 → 强制生成 → 5 轮循环）

1. **首版（有等价出口）**：prompt 里给了 EQUIVALENT 出口，让 LLM 遇到疑似等价就回答等价。
   结果：LLM 偷懒——把"可杀但难写"的变异（如 `allow_extra_args=True→False`）误判为等价，
   等价比例虚高到 37.5%（抽查 3 个有 2 个是可杀的）。
2. **最终版（用户拍板）**：去掉首轮等价出口，改成「生成测试 → 跑 → 反馈错误/存活 → 重试」
   最多 5 轮，5 轮仍存活才标「疑似等价」（唯一等价出口）。

### 关 thinking 是必须的（DeepSeek V4 的坑）

DeepSeek V4 flash/pro 默认开 thinking（推理），content 被 reasoning 吃光：
一个 ARG 变异曾 reasoning 16797 token 仍返回空。`{"thinking":{"type":"disabled"}}` 后
token 从 ~16000 降到 ~100，content 直接出。生成测试不需要深度推理，关掉才快。

### 关 thinking 后必须强化 prompt

只给局部 diff 会让模型猜 import（`from datetime import weekday`、`from your_module import ...`
占位符），7/12 生成有 bug 的测试。加「被测模块 import 路径 + 禁止占位符」后 error 归零。

### 重言式断言检测（纯 AST，不依赖 LLM）

`tautological_asserts()` 拦 `assert True/False`、`x == x`、`1 == 1`、`x is x` 等。
单测通过，作弊率指标（重言式轮次占比）由此计算。

### 反复 unlink 触发沙箱 safe-delete

每个变异体跑完删测试文件，累积到 50 次触发 safe-delete 批量确认，批量脚本崩。
改法：测试文件固定名 `test_s5_probe_tmp.py` 覆盖写，批量结束统一清理一次。

### pro 交叉验证被弃用（时间成本考量）

曾用 pro 对疑似等价交叉验证（`s5_cross_validate.py`），pro 只多杀 2/13，时间却翻倍。
用户拍板：不做交叉验证，flash 的疑似等价直接视为"LLM 无法判定"，如实写杀死率。

### 核心洞察：杀死率差异来自「可测试性」而非「等价性」

100 个 Tier A 实测（改进 prompt 后重跑），按模块杀死率：marshmallow/utils 89%、
dateutil/rrule 82%、relativedelta 80%（纯数据运算好测）；jinja/nodes、shell_completion 仅 25%
（深层机制：shell 补全/AST 内省，LLM 较难写出触发测试）。
按算子 COR 66% > AOR 62% > CR 60% > ROR 52% > ARG 51%。
这是 RQ2 的重要发现，也是"分类技巧减工作量"的依据。

> 注：早期用旧 prompt（unified_diff 只给 7 行、裁掉方法签名）跑时，nodes/shell_completion/tz_win
> 是 0%，一度被归因为"库机制复杂补不了"。改进 prompt（方法签名+类名+真实行号）后它们翻到
> 25–44%，说明"信息太少"是主要瓶颈，"库机制复杂"只占次要。

### 一个被自己证伪的结论：小样本偏差的教训

三臂对照时踩过一次坑，值得记下来：

- 先跑了**各 10 个独立抽样**的小样本，臂 2 作弊率高达 **29.2%**，臂 1 4.1%、臂 3 0%。
  据此得出"作弊率随信息量**非单调**，臂 2 最焦虑、最投机"的结论。
- 换成**40 个同批样本**后，臂 2 作弊率只有 **3.7%**——"非单调"结论**被证伪**。

原因：10 个独立小样本碰巧撞进几个高投机变异体（click/types L391、click/parser L438/L460，
单变异体 token 达 6 万，反复用重言式断言充数），把整体作弊率拉了上去。等间隔从臂 1 那批取
40 个后分布均匀，数值回归正常。

**教训**：小样本 + 独立抽样足以造出假结论。配对设计（三臂跑同一批）+ 足够样本量，
才能暴露这种偏差——如果三臂各抽各的，这个错误根本发现不了。这与 S3 泛化性审查里
"证据单薄"的教训是同一类问题。

---

## S6：外部效度

### Docker/WSL 探测（否决 Docker 依赖数据集）

```
docker --version → command not found（未安装）
wsl --status     → 被沙箱安全策略拦截（Program Blacklist，不可绕过）
```

这一条直接否决了 BugsInPy / SWE-bench / SWT-Bench（都依赖 Docker）。

### Defects4J 作废（选型失误）

Defects4J 是 **Java**，而 MutLoop 整条链路（mutmut + pytest + coverage.py）只对 Python
成立。原计划"Defects4J 走另一条链路"无法达成"程序能在真实 bug 环境下起到真实检出作用"
这个目标——换语言重写一整套两周内不现实。**这是选型失误，不是实现失误。**

### 数据集候选对比

| 候选 | 规模 | 本机可行性 |
|---|---|---|
| BugsInPy | 493 bugs / 17 项目 | ❌ 需要 Docker |
| PyBugHive | 149 bugs / 11 项目 | ⚠️ 离线版或可绕开 Docker，但 Linux venv 在 Windows 上要重建 |
| SWE-bench / SWT-Bench | 数百实例 | ❌ 依赖 Docker |
| 自建（现有 5 项目挖历史 bug） | 完全可控 | ✅ 零成本，但不构成"没见过的新项目"的检验 |

### PyBugHive 标签稀疏（「预测式」不可行的证据）

实测下载 PyBugHive 数据（149 bug，全部 manuallyChecked）后，分布极度倾斜：
pandas 43 / black 38 / jax 17 / freqtrade 15 / spaCy 11 / poetry 11 / salt 7 /
cookiecutter 2 / scrapy 2 / discord.py 2 / numpy 1。

关键负面发现：
1. black 38 个 bug 只触及 **8 个**源文件，其中 **29 个（76%）在单个 `black.py`**
   （2018 年还是几千行单体文件）；poetry 11 个 bug 中位只有 1 个/文件。
2. **负标签不可靠**："没被收录" ≠ "没有 bug"（black 实际有几百个 bug，只采样了 38 个）。
3. 模块身份不稳定（black.py → src/black/__init__.py；poetry/ → src/poetry/）。
4. testSteps 太粗：black 38 个 bug 里 36 个的 testSteps 是 `python setup.py test`（跑全量）。

### GitHub 通道探测 + SZZ 方案量化

| 通道 | 结果 |
|---|---|
| `git clone https://github.com/...` | ❌ 代理拦断（CONNECT 502） |
| `codeload.github.com` tarball | ✅ 可拉任意 commit 的源码树 |
| `api.github.com` | ⚠️ 未认证仅 60 次/小时 |

SZZ 式挖 bug 标签（5 个现有项目，共 13488 commits）全流程约 3627 次请求：
未认证需 60.5 小时，认证后 43.5 分钟。曾建议用户提供只读 GitHub token，方案待定。

### 检出式 vs 预测式的讨论

检出式（让 LLM 读完 bug 程序判断位置）与项目初衷相悖——变异测试缺席了。
用户明确要求**预测式**：变异得分预测哪里会出 bug，让变异测试保持核心地位。
但 PyBugHive 标签稀疏使预测式难做，最终方案仍在探索。

### 最终方案定稿：修复前后对照（用户澄清本意）

用户澄清 S6 真正要验证的：找项目"修复前/修复后"版本对，以**修复前测试套件（弱测试）**
为基础，在**修复后正确代码**上做变异测试找盲区，AI 补测，再把新测试拿到**修复前 bug 代码**
上跑——若新测试在正确版 PASS、bug 版 FAIL，即证明"变异分数提升换来了抓 bug 能力提升"。

此前"文件级 bug 数 vs 覆盖率/变异分数"的预测式走偏了：被"文件重要性"混杂变量主导
（覆盖率 rs 反而最强 +0.613），且文献里外部效度验证的是 coupling effect，不是"预测哪个文件 bug 多"。

### 关键突破与走通的链路

- **`git fetch --unshallow` 成功**（此前以为浅克隆拿不到历史）：5 项目 11870 commits，
  自建 SZZ 无需 Docker/token。
- **worktree 版本切换**：早期直接 checkout 主工作区曾被中断破坏（marshmallow 丢 16 个
  测试文件），改用 `git worktree` 独立副本后根治。
- **control 剔除 baseline 失败**：修复会同步改测试断言，导致"修复前测试在修复后代码上
  FAIL"，`run_one` 不跑 baseline 会误判 killed。必须先跑 control 剔除。
- **prompt 补全上下文**：`unified_diff(n=3)` 只给 7 行、裁掉方法签名（flash 曾用错类
  `_ClassBuilder` 而非 `Attribute`），改为「真实行号代码 ±20 行 + 标注 class/def 边界」后，
  flash 补测能力从 0 提升到杀 7 个变异体。

### 最终结论（诚实负结果为主）

批量 4 个合格样本：AI 杀死 7 个变异体，**抓到真实 bug 仅 1 个**（attrs/_make.py 错误消息 bug）。
click 杀 2 抓 0、jinja 杀 3 抓 0——**"杀死变异体 ≠ 抓到 bug"**，根因是盲区变异体与真实 bug
语义错位：盲区是"测试没覆盖的任何行为"，bug 是"特定的错误行为"，只有落在同一可观察维度
（如错误消息）时，补测测试才能既杀变异体又抓 bug。

详见 [`S6-external-validity.md`](S6-external-validity.md)。

---

## 环境坑（跨阶段）

| 坑 | 症状 | 对策 |
|---|---|---|
| venv 目录名 | 项目目录已改名 MutLoop，venv 仍叫 `mutbench`，写 `envs/mutloop` 直接失败 | 用 `envs/mutbench` |
| pytest 版本 | pytest 9.x 的弃用告警被 `filterwarnings=error` 升级成错误，5 项目收集失败 | 锁 8.3.5 |
| 沙箱拦截删除 | pip 卸载把包装成"半删除"（coverage 只剩 `__pycache__`） | 绝不在已装好的 venv 上 force-reinstall；重装就新建 venv |
| zoneinfo 数据缺失 | `dateutil-zoneinfo.tar.gz` 未提交，3 个测试模块收集失败 | 从官方 wheel 取该文件 |
| editable install 改名失效 | MutBench → MutLoop 后 `.pth` 指向旧路径，5 项目 import 失败 | 直接改 `.pth` 文本，不能 pip 重装 |
| `data/baseline/*.json` 旧绝对路径 | JSON 里仍是 `MutBench` 路径 | **有意保留**，内部自洽；只改 root 不改 funcs[].file 反而破坏对齐 |
