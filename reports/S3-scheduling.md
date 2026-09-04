# S3 报告：覆盖率导向调度（RQ1）

> 数据：`data/s3/*-sched.json`（12 模块，5349 个变异体）
> 代码：`mutloop/schedule.py`（行级索引与测试选择）、`mutloop/select_plugin.py`（pytest 插件）、
> `mutloop/import_time.py`（import-time 判定，修复一）、`mutloop/deadloop_cache.py`（死循环缓存，修复二）
> 过程中的踩坑与被推翻的假设见 [`DEVELOPMENT-LOG.md`](DEVELOPMENT-LOG.md) 的 S3 一节。

## 1. S3 要回答什么

朴素变异分析对每个变异体都跑**全量**测试套件，一轮 45.9 CPU 小时，迭代不动。
**RQ1：覆盖率导向调度能加速多少，代价多大？**

思路（变异测试文献的标准技术）：没执行到某行的测试，不可能发现该行的改动。
所以对每个变异体，只跑**覆盖变异行的测试子集**。

## 2. 方法

| 组件 | 作用 |
|---|---|
| 行级覆盖率索引 | `data/s3/line_index_<项目>.json`，记录「每测试覆盖哪些行」。由 `s3_coverage_probe.py` 采集（~110s） |
| per-test 上下文 | 不用 coverage.py 内置的 `dynamic_context`（靠"函数名以 test 开头"启发式，且不含参数化）。改为 pytest 插件在每条用例 `logstart` 时调用 `Coverage.switch_context(nodeid)`，上下文名即精确 nodeid |
| 测试选择 | `schedule.select(rel, line)` 取覆盖该行的测试；无覆盖数据时退回全套件 |
| 筛选用例的实现 | `select_plugin.py` 用「选择文件」而非超长命令行，绕开 Windows `WinError 206` |

## 3. 两处修复

调度会引入误差，实测发现并修复了两处：

| 项 | 问题 | 修法 | 效果 |
|---|---|---|---|
| 修复一：import-time 变异体 | 类体／模块级／函数签名默认值只在 import 时执行一次，覆盖率归属不可靠（漏杀主因） | `import_time.py` 用纯 AST 判定这类行，**退回全套件**（无项目硬编码） | 漏杀 167→13，假杀 90→2，代价 +70.6 CPU 分钟 |
| 修复二：死循环结果缓存 | 死循环变异体对调度是盲区（选中的正是覆盖它的测试，照样死循环），每个都要等满超时 | `deadloop_cache.py` 跨运行复用判定结果 | 命中 100 个，省 200.3 CPU 分钟 |

修复前的 12 份结果备份在 `data/s3/_pre_fix_backup/`，`data/s3/*-sched.json` 已是修复后数据。

## 4. RQ1 的答案（实测）

`python scripts/compare_schedule.py --all --table` 可复现：

| 口径 | 加速比 | 说明 |
|---|---|---|
| **首次运行**（无历史，可泛化） | **3.47x** | ← **RQ1 主数字** |
| 重复运行（带死循环缓存） | 4.65x | 附加，仅限增量场景 |
| 纯加速（配对 5200 个非超时变异体） | 5.31x | 剔除死循环等待后的上限 |

**代价**（与 S2 真值逐变异体对账）：漏杀 **13** 个、假杀 **2** 个（修复前是 167 + 90）。

> 两个加速比必须分开给：含缓存的 4.65x 把"上一轮跑出来的知识"算进了这一轮，
> 属跨运行信息复用，对全新项目不成立。RQ1 主数字是首次运行的 3.47x。

## 5. 已知不完美（刻意保留，如实报告）

按"任何无法泛化的优化都不做，保留不完美"的硬约束，以下未修：

- 严格口径有 **±0.5 pp 跑间噪声**（`error_origin` 取第一个失败的测试，并行下不确定）
- 剩余 **13 个漏杀**（click-types 5 / marshmallow-validate 4 / marshmallow-schema 3 / relativedelta 1）与 **2 个假杀**（dateutil-win）
- 死循环缓存对**首次运行收益为 0**（跨运行复用）
- `--verify` 只复核幸存者，**找不出假杀**（假杀躺在被判 killed 的那批里）

## 6. 复现

```bash
$PY scripts/s3_coverage_probe.py              # 建行级索引（~110s）
$PY scripts/run_all_modules.py --schedule line # 调度跑 12 模块（约 1h15m）
$PY scripts/compare_schedule.py --all --table   # 与 S2 真值对账（几秒钟，口径唯一权威）
```
