# S5 报告：LLM 定向补测（RQ2）

> 代码：`scripts/s5_probe_one.py`（探针，`--arm` 选臂）、`scripts/s5_sample_run.py`（抽样／`--from` 同批）、
> `scripts/s5_arm_compare.py`（三臂对比）
> 数据：`data/s5/sample_run_100_v2.json`（能力基线）、`data/s5/arm_{directed,coverage,blind}.json`（三臂）
> 过程中的踩坑、方案迭代与一个被证伪的结论见 [`DEVELOPMENT-LOG.md`](DEVELOPMENT-LOG.md) 的 S5 一节。

## 1. S5 要回答什么

**RQ2：LLM 定向补测的效果如何？增益来自哪里？**

闭环的最后一环：存活变异体 → LLM 生成测试 → 杀死它 → 重评分。

## 2. 补测流程

对每个存活变异体，LLM 反复生成测试直到杀死或放弃（**最多 5 轮**，多轮对话）：

```
生成测试 → 跑原代码 + 跑变异代码
├─ 杀死（原 PASS + 变异 FAIL）→ 保留测试，结束
├─ 程序报错／重言式断言     → 把错误信息反馈给 LLM，进入下一轮
└─ 存活（两边都 PASS）      → 告诉 LLM"没杀死"并把测试还给它，进入下一轮
                              5 轮后仍存活 → 标「疑似等价」（LLM 无法判定，如实计入未杀死）
```

**关键设计**：首轮**不提供等价出口**，强制 LLM 先尝试生成测试；只有"确实杀不死"才允许判等价。

LLM 配置：DeepSeek `deepseek-v4-flash`，**关闭 thinking**（`{"thinking":{"type":"disabled"}}`），
max_tokens 4000，temperature 0.2，密钥走环境变量 `DEEPSEEK_API_KEY`。

## 3. 四个质量指标

用于综合衡量"LLM 生成测试对抗变异测试的质量"：

| 指标 | 定义 |
|---|---|
| 编译通过率 | 终版测试能跑通原代码的比例（= 1 − error 率） |
| 杀死率 | killed 占样本的比例 |
| 平均尝试轮次 | 仅统计 killed 的轮次均值（1–5）；5 轮未 killed 不计入，靠杀死率反映 |
| 作弊率 | 生成重言式断言的轮次占比 |

其中**重言式断言检测**（`assert True`、`x == x`、`1 == 1` 等）是**纯 AST 程序判定**，
不依赖 LLM，用于拦截"投机取巧"。

## 4. 第一步：能力基线（100 个 Tier A 分层抽样）

| 指标 | 值 |
|---|---|
| 编译通过率 | **100.0%**（100/100） |
| **杀死率** | **58.0%**（58/100） |
| 平均尝试轮次 | 1.9 |
| 作弊率 | 1.2% |

**杀死率的差异来自「可测试性」而非「等价性」**：

| 模块 | 杀死率 |
|---|---|
| marshmallow/utils、dateutil/rrule、relativedelta | **80–89%**（纯数据运算，易写测试） |
| marshmallow/schema、jinja/environment | 71–75% |
| click/types | 67% |
| attrs/validators | 50% |
| dateutil/tz/win | 44% |
| click/parser | 40% |
| **jinja/nodes、click/shell_completion** | **25%** |
| marshmallow/validate | 20% |

按算子：COR 66% > AOR 62% > CR 60% > ROR 52% > **ARG 51%**。

也就是说，nodes、shell_completion 这类模块不是"变异等价"，而是需要理解深层机制
（shell 补全、AST 内省）才能构造触发测试——LLM 较难写出。

## 5. 第二步：三臂对照（RQ2 核心）

控制"给 LLM 的信息量"，三臂跑**完全同一批** 40 个变异体，prompt 严格隔离：

| 臂 | 给 LLM 的信息 | 杀死率 | 编译通过率 | 平均轮次 | 作弊率 |
|---|---|---|---|---|---|
| 臂 1 `directed` | 完整变异 diff + 算子 + 描述 | **55.0%** | 100% | 2.0 | 2.2% |
| 臂 2 `coverage` | 覆盖该行的测试列表（截断前 20） | **10.0%** | 100% | 2.0 | 3.7% |
| 臂 3 `blind` | 仅模块名 + 行号 | **2.5%** | 100% | 4.0 | 0.5% |

### RQ2 的答案

**杀死率单调递减 55.0% → 10.0% → 2.5%**：臂 1 是臂 2 的 **5.5 倍**、臂 3 的 **22 倍**。

- **变异信息本身是增益主力**：给完整 diff 能杀 55.0%，撤掉只剩 10%。
- **覆盖率信息的替代作用有限**：约臂 1 的 1/5。
- **盲测基本无效**：2.5%，且那唯一一个是碰巧。

作弊率同样随信息量单调递减（2.2% → 3.7% → 0.5%）——信息越少，
LLM 连"投机"都无从投起。

按算子（臂 1）：COR 71% > AOR 67% > CR 56% ≈ ROR 56% > ARG 33%。

## 6. 局限（如实报告）

1. **样本量**：三臂各 40 个；按模块／算子分解后每组仅 1–4 个，**分解值噪声大，只看趋势**。
2. **臂 2 并非纯覆盖率信息**：测试名自带语义（如 `test_weekday_zero_raises_value_error`），
   会提示"该行在测什么"，因此臂 2 比纯覆盖率偏强。
3. **单模型结论**：三臂均为 flash，未做 pro 交叉验证（时间成本考量）。
   LLM 判定的"疑似等价"是**能力边界**的近似，严格等价比例可能更低。
4. **被测代码的可测试性是主导因素**：杀死率高低更多取决于模块 API 是否直观、
   能否独立触发，而非变异本身的性质。

## 7. 复现

```bash
export DEEPSEEK_API_KEY=sk-...
$PY scripts/s5_sample_run.py --n 100 --out data/s5/sample_run_100_v2.json        # 能力基线
$PY scripts/s5_sample_run.py --from data/s5/sample_run_100_v2.json --limit 40 --arm coverage \
    --out data/s5/arm_coverage.json                                              # 臂 2（同批 40 个）
$PY scripts/s5_sample_run.py --from data/s5/sample_run_100_v2.json --limit 40 --arm blind \
    --out data/s5/arm_blind.json                                                 # 臂 3
$PY scripts/s5_arm_compare.py                                                    # 三臂并排对比
```
