"""生成 S1 基线报告的**精简版**，与完整版 `S1-baseline.md` 并存。

精简原则：**不删有效内容，只压展开度**。
- 保留：全部结论、全部汇总数字、全部入选标准、全部风险与效度威胁
- 压缩：模块级明细（每项目 top 6 而非 12）、四象限（汇总计数 + A 清单，去掉逐象限释义）
- 完整版仍保留逐模块全量明细，需要查细节时去那里看

用法：
    python scripts/gen_condensed_report.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from gen_baseline_report import (  # noqa: E402
    REPORTS,
    conclusion,
    cost_table,
    load_all,
    load_overhead,
    mitigation_table,
    module_plan_section,
    module_rows,
    overview_table,
    quadrant,
    validity_threats_table,
)

TOP_MODULES = 4
QUAD_LABEL = {
    "A_高覆盖_弱断言": "A 高覆盖/弱断言",
    "B_高覆盖_强断言": "B 高覆盖/强断言",
    "C_低覆盖_弱断言": "C 低覆盖/弱断言",
    "D_低覆盖_强断言": "D 低覆盖/强断言",
}

# 完整版与精简版章节编号不同，共用文案里的「第 N 节」引用需要重映射：
#   完整版 1 环境 / 2 总览 / 3 模块明细 / 4 四象限 / 5 成本 / 6 选型结论 / 7 实验范围
#   精简版 1 结论 / 2 总览 / 3 四象限 / 4 模块明细 / 5 成本 / 6 实验范围 / 7 选型结论
SECTION_REMAP = (
    ("第 3 节", "第 4 节"),   # 模块明细
    ("第 4 节", "第 3 节"),   # 四象限
    ("第 6 节", "第 7 节"),   # 选型结论
    ("第 7 节", "第 6 节"),   # 实验范围
)


def remap_sections(text: str) -> str:
    """改章节引用。用占位符做一次性替换，避免 3↔4 互换时被二次替换回原样。"""
    for i, (old, _new) in enumerate(SECTION_REMAP):
        text = text.replace(old, f"\x00{i}\x00")
    for i, (_old, new) in enumerate(SECTION_REMAP):
        text = text.replace(f"\x00{i}\x00", new)
    return text


def combined_module_table(all_rows: dict[str, list[dict]], top: int) -> str:
    """把 5 个项目的模块明细合成一张表，省掉重复的表头与分隔行。"""
    head = (
        "| 项目 | 模块 | NCLOC | 语句 | 覆盖率 | 覆盖测试数 | 断言密度 |\n"
        "|---|---|---|---|---|---|---|"
    )
    lines = [head]
    for name, rows in all_rows.items():
        if not rows:
            lines.append(f"| `{name}` | _数据缺失_ | | | | | |")
            continue
        for i, r in enumerate(rows[:top]):
            lines.append(
                f"| {f'`{name}`' if i == 0 else ''} | `{r['module']}` | {r['ncloc']} | "
                f"{r['num_statements']} | {r['pct']}% | {r['n_tests']} | {r['assert_density']} |"
            )
    return "\n".join(lines)


def quadrant_summary(name: str, rows: list[dict]) -> str:
    _body, meta = quadrant(rows)
    if not meta:
        return f"| `{name}` | — | — | — | — | — | 样本不足 |"
    c = meta["counts"]
    a = "、".join(m["module"] for m in meta["a_modules"][:3]) or "无"
    return (
        f"| `{name}` | {meta['n_modules']} | {c['A_高覆盖_弱断言']} | {c['B_高覆盖_强断言']} | "
        f"{c['C_低覆盖_弱断言']} | {c['D_低覆盖_强断言']} | {a} |"
    )


def main() -> int:
    data = load_all()
    if not data:
        print("未找到基线数据")
        return 1
    overhead = load_overhead()
    scale_tbl, test_tbl = overview_table(data)
    any_d = next(iter(data.values()))

    all_rows: dict[str, list[dict]] = {}
    metas: dict[str, dict] = {}
    for name, d in data.items():
        rows = module_rows(d)
        all_rows[name] = rows
        _, meta = quadrant(rows)
        metas[name] = meta

    total_full = sum(o["cpu_hours_full_run"] for o in overhead.values())
    sel = module_plan_section()

    p: list[str] = []
    p.append("# S1 基线报告（精简版）\n")
    p.append(
        f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}　"
        f"完整版见 [`S1-baseline.md`](S1-baseline.md)。\n"
        f"> 本版**保留全部结论与汇总数字**，压缩的是逐模块的展开明细；"
        f"需要查某个模块的具体数字时去完整版。\n"
    )

    p.append("\n## 1. 一页纸结论\n")
    p.append(
        "**选了什么**：5 个 Python 项目，锁定 release tag，"
        f"Python `{any_d['subject']['python']}` / `{any_d['subject']['platform']}`。\n\n"
        "**核心发现**：覆盖率会骗人，变异分数不会——本平台要用变异分数替代覆盖率。"
        "为两周工期内的可迭代性，实验范围收敛到**每项目 2–3 个模块**（第 6 节）。\n\n"
        f"**成本**：整项目一轮 {total_full:.1f} CPU 小时（16 核约 {total_full / 10:.1f} 小时）；"
        f"模块级一轮按第 6 节约 1 小时，可反复迭代。\n\n"
        "**最大的坑（也是最有价值的发现）**：每判定一个变异体都要启动一次 pytest 进程，"
        "固定开销 2.9–4.6 秒、占单变异体成本 47%–83%。覆盖率导向调度的收益被它吃掉一大半，"
        "实测加速比只有 1.4x–4.5x，不是理论上的 5x–20x。\n\n"
        "**下一步（S2）**：先在 marshmallow 上数出 mutmut 的真实变异体数，修订成本预算。\n"
    )

    p.append("\n## 2. 被测项目总览\n")
    p.append("\n**代码规模**\n")
    p.append(scale_tbl + "\n")
    p.append("\n**测试与覆盖**\n")
    p.append(test_tbl + "\n")
    p.append(
        "\n> NCLOC = SLOC 扣除 docstring；AST 语句数 ≈ 变异算子落点规模。"
        "**测试函数**（源码 `def test*` 个数，参数化未展开）≠ **用例数**"
        "（collect-only 条数，参数化已展开）；**断言数（源码）** ≠ **断言数（全套件执行）**"
        "（后者按参数化实例重复计入）。"
        "**断言/用例**＝执行数÷用例数，**断言/测试函数**＝源码数÷函数数，"
        "两个比值都可用表内数字相除验算。\n"
    )

    p.append("\n## 3. 覆盖率 vs 断言强度：四象限\n")
    p.append(
        "> 「断言密度」是**断言专注度**不是断言条数：一个测试的断言按"
        "「它在本模块执行的行数 / 它在被测包内执行的总行数」摊到各模块。"
        "**只在项目内部做相对排序，不跨项目比较。**\n"
        f"> 切分口径：覆盖率 ≥ 85%（固定阈值）/ 断言密度 ≥ 项目内中位数。\n"
    )
    p.append(
        "\n| 项目 | 入样模块 | A | B | C | D | A 象限代表模块（按规模） |\n"
        "|---|---|---|---|---|---|---|"
    )
    for name in data:
        p.append(quadrant_summary(name, all_rows[name]))
    p.append(
        "\n> A = 覆盖到了但没断言住，**首选**：变异分数大概率低、补测增益空间最大。"
        "B = 测试扎实，作对照。C/D 作多样性补充。\n"
        "> 覆盖率用固定阈值而非中位数，是因为 jinja 的模块覆盖率挤在 89–96%，"
        "用中位数会让 filters.py 只差 0.27 个百分点就翻象限（刀刃效应）。\n"
    )

    p.append("\n## 4. 模块级明细（每项目取前 4）\n")
    p.append(combined_module_table(all_rows, TOP_MODULES) + "\n")
    p.append("\n> 完整明细（每项目前 12 个模块，含覆盖行数与缺失行）见 `S1-baseline.md` 第 3 节。\n")

    p.append("\n## 5. 变异分析成本\n")
    p.append(cost_table(data, overhead) + "\n")
    p.append(
        "\n> naive = 每个变异体都跑全量套件；调度后 = 只跑覆盖该变异行的测试子集。"
        "变异体数按「每语句 2 个」保守估算，S2 用 mutmut 实测替换。\n"
        "> **RQ1 不要写死「加速比 > 4x」**——实测只有 click 达标。改为报告实测值，"
        "并把「固定开销吃掉调度收益」本身作为一个发现来写。\n"
    )
    p.append("\n### 压缩路径（可叠加）\n")
    p.append(mitigation_table(overhead) + "\n")

    p.append("\n## 6. 实验范围：模块级\n")
    p.append(remap_sections(module_plan_section()) + "\n")
    p.append(
        remap_sections(
            validity_threats_table(
                overhead.get("marshmallow", {}).get("cpu_hours_full_run", 0.0)
            )
        )
    )

    p.append("\n## 7. 选型结论\n")
    p.append(remap_sections(conclusion(data, all_rows, metas)) + "\n")

    out = REPORTS / "S1-baseline-condensed.md"
    out.write_text("\n".join(p), encoding="utf-8")
    full = (REPORTS / "S1-baseline.md").read_text(encoding="utf-8")
    print(f"精简版已生成: {out}")
    print(f"  精简版 {len(chr(10).join(p).splitlines())} 行 / "
          f"完整版 {len(full.splitlines())} 行 "
          f"（压缩到 {100 * len(chr(10).join(p).splitlines()) / len(full.splitlines()):.0f}%）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
