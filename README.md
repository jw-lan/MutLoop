# MutLoop

变异测试驱动的测试质量度量与自动增强平台。

**一句话**：用变异分数替代代码覆盖率来度量测试质量，并把
「存活变异体 → 定向补测 → 重评分」做成自动闭环。

> **只想知道结论？** 读 [`reports/FINAL-REPORT.md`](reports/FINAL-REPORT.md)。
> **想接手项目？** 读 [`HANDOVER.md`](HANDOVER.md)。
> **想看过程中的踩坑与被推翻的假设？** 读 [`DEVELOPMENT-LOG.md`](reports/DEVELOPMENT-LOG.md)。

---

## 核心结果

| 项 | 结果 |
|---|---|
| 实验规模 | 5 个真实开源项目的 12 个模块、4516 条语句、**5349 个变异体** |
| 机时 | 朴素全套件口径 2754 CPU 分钟（45.9 CPU 小时） |
| 变异分数 | 传统口径 **69.49%** ／ 严格口径 **42.26%** |
| RQ1 调度加速 | 首次运行 **3.47x**（不依赖任何历史缓存），代价：漏杀 13、假杀 2 |
| RQ2 LLM 补测 | 能力基线杀死率 **58.0%**；三臂对照 **55.0% / 10.0% / 2.5%** |
| RQ3 外部效度 | 4 个合格样本中抓到 1 个真实缺陷，转化率低，根因已定位 |

三个研究问题：

- **RQ1 调度能加速多少、代价多大** — 覆盖率导向调度首次运行加速 3.47x，
  通过 AST 分析识别「仅在导入期执行」的代码并退回全量运行，把漏杀从 167 压到 13、假杀从 90 压到 2。
- **RQ2 定向补测的增益来自哪里** — 三臂对照（定向 / 覆盖率 / 盲测）杀死率 55.0% / 10.0% / 2.5%，
  证明增益**主要来自变异信息本身**，覆盖率信息只能替代约 1/5，纯盲测基本无效。
- **RQ3 与真实缺陷的相关性** — 闭环方法成立，但「变异分数 → 抓真实 bug」转化率低，
  根因是盲区变异体与真实缺陷语义错位。

---

## 最值得看的一件事

**覆盖率几乎无法区分模块。**

12 个模块的覆盖率全部在 87.5% 以上、标准差只有 **4.8** —— 从覆盖率看这些模块的测试「都很好」，
几乎无法区分。但严格变异分数把它们拉开了（标准差 **18.1**）。

最典型的一例：**`jinja/nodes` 覆盖率 89.5%，严格口径下只有 12.05% 的变异体被真正检测到**
—— 近九成变异体存活，而覆盖率对此完全无感。

严格口径的标准差是覆盖率的 3.8 倍、传统口径的 1.69 倍。这是整个项目立论最有力的证据。

---

## 关于本项目的开发方式

本项目的**方案设计、实验设计、指标口径定义、数据分析与结论由作者完成**；
代码实现采用 LLM 辅助生成、作者审阅验证的方式，重点把控实现是否严格符合所设计的口径。
过程记录见 [`reports/DEVELOPMENT-LOG.md`](reports/DEVELOPMENT-LOG.md)。

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
│   ├── deadloop_cache.py# S3 死循环结果缓存
│   └── __main__.py      # CLI: python -m mutloop
├── scripts/             # 各阶段实验脚本
├── data/                # 实验数据（s2 为不可重建的核心资产）
└── reports/             # S1–S6 完整报告 + 开发日志
```

## 常用命令

```bash
PY="<你的 Python 路径>"

$PY -m mutloop list                              # 列出被测项目与锁定 tag
$PY -m mutloop baseline --per-test               # 采集全部候选项目基线
$PY scripts/count_mutants.py                     # 重新枚举变异体数 → 应为 5349
$PY scripts/run_all_modules.py --schedule line   # S3 覆盖率导向调度，约 1h15m
$PY scripts/compare_schedule.py --all --table    # S2/S3 对账（S3 数字以此为准）
$PY scripts/run_parallel.py --subject attrs --target validators.py --diff   # S4 增量模式
$PY scripts/triage_survivors.py --top 20         # S4 存活变异体分级
```

## 复现环境

```bash
# 1) 新建隔离 venv
python -m venv <venv 路径>

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

> **被测项目源码不随本仓库分发**（版权属于各自原作者）。
> `subjects/` 已写入 `.gitignore`，按上面的命令 clone 即可。

## 两个关键设计决策

**per-test 覆盖率上下文：不用 coverage 内置的 `dynamic_context=test_function`。**
它靠「函数名以 test 开头」做启发式命名，且不含参数化信息。改为由 pytest 插件在每条用例
`logstart` 时调用 `Coverage.switch_context(nodeid)`，上下文名即精确 nodeid，对齐率 89.86%–100%。
S3 的调度层直接依赖这份数据。

**每个被测项目独占一个进程。**
`pytest.main()` 在同一进程内跑第二个项目会受前一次的 `sys.modules` / conftest 残留影响。
`mutloop baseline` 因此为每个项目 spawn 子进程；S3 的并发调度同样依赖这个隔离前提。

---

## 局限

1. **RQ3 结论弱** — 外部效度仅 1 个正面样本，「变异分数 → 抓真实 bug」转化率低，
   根因是盲区变异体与真实缺陷语义错位。
2. **模块级结论，项目级无证据** — marshmallow 项目级锚点未跑，模块级能否推广到项目级目前没有证据。
3. **调度有残余误差** — 漏杀 13、假杀 2；严格口径 ±0.5 pp 跑间噪声。
4. **`dateutil/tz/win` 的 4 条测试被 deselect** — 该模块是在少了 TzWinTest 13 个方法中 4 个（31%）的
   条件下测的，与 Linux 环境不可直接比较。
5. **S5 样本量** — 三臂各 40 个；按模块／算子分解后每组仅 1–4 个，分解值噪声大，只能看趋势。
6. **臂 2 非纯覆盖率信息** — 测试名自带语义，会提示「该行在测什么」。
7. **单模型结论** — S5 三臂均为 flash，未做 pro 交叉验证。

## 三条口径纪律

1. 分清「实测」与「预估／推算」。
2. 引用比率时带上分母。
3. 跨阶段比分数必须用「共同可判定集」（5109 个），因为调度会改变分母。
