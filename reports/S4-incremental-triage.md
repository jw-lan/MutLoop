# S4 报告：增量模式与存活变异体分级

> 代码：`scripts/run_parallel.py`（`--lines` / `--diff` 增量）、`scripts/triage_survivors.py`（分级）
> 数据：`data/s4/survivor_triage.json`（1558 个存活变异体的分级结果）
> 过程中的踩坑与被推翻的假设见 [`DEVELOPMENT-LOG.md`](DEVELOPMENT-LOG.md) 的 S4 一节。

## 1. S4 要交付什么

S4 是**工程加分项**（不直接对应某个 RQ），包含两件事：

1. **增量模式**：PR 级只变异改动行，而不是整个文件——让变异测试能进 CI。
2. **存活变异体分级（triage）**：把 1558 个存活变异体按"可检测性"分层，为 S5 定向补测提供优先级。

## 2. 增量模式（PR 级只变异改动行）

`run_parallel.py` 加了两个入口，底层复用 mutator 预留的 `covered_lines` 行过滤：

| 入口 | 作用 |
|---|---|
| `--lines "175,450"` | 手动指定要变异的行（支持范围，如 `100-105`） |
| `--diff` | 用 `git diff`（工作树 vs HEAD）自动取改动行 |

只变异改动行，其余逻辑（执行、判定、调度）与全量完全一致。实测：
`attrs/validators.py` 只变异 2 行 → 11 个变异体，0.2 分钟（全量 60 个变异体）。

> **已知局限**：增量过滤会改变 `mutant_id`（根因是 ID 含全局枚举序号，过滤后序号前移），
> 所以增量结果无法与全量结果按 ID 直接对账，需按（文件、行、算子、描述）匹配。
> 这是"ID 绑定具体枚举上下文"的既有属性（换算子集同样会变），非增量模式独有。

## 3. 存活变异体分级（triage）

### 分层依据

| 信号 | 来源 |
|---|---|
| 该行是否被测试执行 | 行级覆盖索引 `data/s3/line_index_<项目>.json`：有记录且测试列表非空 = 覆盖 |
| 是否只�� import 时执行 | `mutloop/import_time.py` 纯 AST 判定类体／模块级／函数签名默认值 |

### 三层结果（1558 个真实存活变异体，S2 真值）

| 层级 | 数量 | 含义 | S5 优先级 |
|---|---|---|---|
| **A 覆盖到了但没断言住** | **910（58.4%）** | 行被测试执行、变异仍存活 | **最高** |
| B 根本没覆盖 | 432（27.7%） | 行从未被任何测试执行 | 低 |
| C import 时执行 | 216（13.9%） | 类体／模块级，覆盖率归属不可靠 | 中 |

**Tier A 是核心**：它直接证明"覆盖率到了、断言没到"——行被执行过，变异却没被任何测试捕获。
这类变异体既最有补测价值，也最可能通过定向测试杀死。

### Tier A 的构成

按模块（前 6）：dateutil/rrule 241、relativedelta 135、jinja/nodes 107、click/types 101、
marshmallow/schema 85、jinja/environment 70。

按算子：ARG 382、CR 380、ROR 58、AOR 53、COR 37（合计 910）。

## 4. 复现

```bash
$PY scripts/run_parallel.py --subject attrs --target validators.py --lines "175,450"  # 增量：指定行
$PY scripts/run_parallel.py --subject attrs --target validators.py --diff             # 增量：git diff 改动行
$PY scripts/triage_survivors.py --top 20         # 存活变异体分级（写 data/s4/）
```

> 增量模式的"改动行"来自工作树 diff。subjects 是浅克隆（各 1 个 commit），
> 因此可演示"改了行 → 只变异这些行"，但不能做真实 PR 的两-commit 间 diff。
