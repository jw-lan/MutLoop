"""把变异分析成本从「项目级」下钻到「模块级」，据此挑选实验用的稳定子模块。

为什么需要这一步：整项目跑一轮全量变异分析，即使做了覆盖率导向调度也要 4.5~14 CPU 小时，
两周工期内试错空间极小。改为只对选定的若干模块做变异后，成本近似随变异体数线性下降，
一轮可以压到几十分钟，就能承受"跑出来不理想 → 改 → 重跑"的循环。

成本模型（沿用 scripts/bench_per_mutant_overhead.py 的实测 a/b）：

    单变异体成本 = a + b × 该模块的「行均测试数」
    模块 CPU 秒  = 该模块语句数 × 2 × 单变异体成本

注意：成本不是只看模块大小。一个小模块如果被 900 条测试覆盖，它每个变异体依然要跑 900 条测试，
并不便宜。真正的便宜模块是「语句数适中 + 行均测试数低」。

用法：
    python scripts/plan_module_budget.py                 # 全部项目
    python scripts/plan_module_budget.py --budget 0.5    # 按每项目 0.5 CPU 小时选模块
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mutloop.subjects import DATA_DIR  # noqa: E402

MUTANTS_PER_STATEMENT = 2
# 入选门槛：太小则变异体不足、统计上没意义；覆盖太低则低分是"没覆盖"造成的，不是"没断言"
MIN_STATEMENTS = 150
MIN_COVERAGE = 80.0

# 稳定性：由 scripts/measure_module_churn.py 实测（对比前 2 个 release 的文件哈希），
# 不用"下划线开头"这种命名启发式——它会误伤 attrs/_make.py 这类核心模块。
# 只排除无逻辑的样板文件；规模和覆盖率门槛会自然过滤掉其余噪声模块，
# 不要按"叫 utils 就一定是工具集"这种名字去排除——jinja/utils.py 有 318 条语句，
# 是正经的逻辑模块。
EXCLUDE_STEMS = {"__init__", "version", "_version", "compat", "conftest", "setup"}
# 四象限的「高覆盖」判据。用固定阈值而不是中位数，原因见 quadrant_of 的 docstring。
HIGH_COVERAGE_THRESHOLD = 85.0


def _calibrate(rows: list[dict], ovh: dict) -> list[dict]:
    """把逐模块拟合成本的总和，校准到实测成本上。

    两个模型的差异：成本表用「实测/变异体」× 变异体数（在真实行均测试数下直接测的），
    而逐模块成本只能用拟合式 a + b·n 推算，两者在项目级会差 3–15%。
    同一份报告里出现两套总数会自相矛盾。这里按比例缩放，保留模块间的相对顺序
    （这才是选模块要用的），同时让总数与成本表一致。
    """
    target = ovh.get("cpu_hours_full_run")
    fitted = sum(r["cpu_hours"] for r in rows)
    if not target or not fitted:
        return rows
    k = target / fitted
    for r in rows:
        r["cpu_hours"] = round(r["cpu_hours"] * k, 3)
    return rows


def quadrant_of(rows: list[dict]) -> dict[str, str]:
    """划分四象限。

    覆盖率轴用**固定阈值 85%**，断言密度轴用**项目内中位数**。

    为什么覆盖率不用中位数：jinja 的模块覆盖率高度聚集在 89–96%，中位数 90.91%，
    导致 filters.py（覆盖率 90.64%，断言密度 0.79）只差 0.27 个百分点就被划进
    「低覆盖」象限——典型的中位数切分刀刃效应，结论会随样本微调而翻转。
    固定阈值可复现，且不受样本分布影响。

    为什么密度用中位数：各项目断言密度分布差异极大（marshmallow 中位 100、
    jinja 中位 11），统一阈值没有意义，只能项目内相对排序。
    """
    big = [r for r in rows if r["statements"] >= MIN_STATEMENTS and r["n_tests"] > 0]
    md = st.median(r["assert_density"] for r in big) if big else 0.0
    out = {}
    for r in rows:
        if r["n_tests"] <= 0:
            out[r["module"]] = "-"
            continue
        hi_c = r["coverage"] >= HIGH_COVERAGE_THRESHOLD
        hi_d = r["assert_density"] >= md
        out[r["module"]] = (
            "A" if hi_c and not hi_d else "B" if hi_c else "C" if not hi_d else "D"
        )
    return out


def select_modules(rows: list[dict], quads: dict[str, str], budget: float,
                   max_modules: int, churn: dict | None = None) -> tuple[list[dict], bool]:
    """分层挑选：先各取一个 A（弱断言）和一个 B（强断言）保住对比，再用预算填满。

    为什么必须分层：只按「便宜」贪心选会全选到弱断言模块，等于把结论预设成
    "变异分数低"，这是典型的选择偏差。保住 A/B 对比，对比本身才是发现。
    """
    # 稳定性是软约束：优先从稳定模块里选，但 A/B 对比优先于稳定性。
    # 理由：churn 只在"前 2 个补丁版本"这个短窗口上测，一次小改动不代表模块不稳定；
    # 而 A/B 对比是整套实验的科学核心，不能为了稳定性牺牲掉。
    def is_stable(r: dict) -> bool:
        return churn.get(r["module"], {}).get("stable", True)

    def base_pool() -> list[dict]:
        return [
            r for r in rows
            if r["statements"] >= MIN_STATEMENTS
            and r["coverage"] >= MIN_COVERAGE
            and r["n_tests"] > 0
            and Path(r["module"]).stem not in EXCLUDE_STEMS
        ]

    pool_all = base_pool()
    pool_stable = [r for r in pool_all if is_stable(r)]
    relaxed = len(pool_stable) < 2
    pool = pool_stable if not relaxed else pool_all

    picked: list[dict] = []
    spent = 0.0

    def take(q: str) -> None:
        """在象限 q 内，取「语句数最多且预算装得下」的模块。

        为什么按语句数而不是按价格挑：便宜 = 覆盖该模块的测试少，而测试少往往
        意味着变异分数低。按最便宜挑会系统性地偏向"分数低"的模块，等于把结论
        预设进了样本里。语句数只影响统计功效，与测试强度无关，是中性标准。
        """
        nonlocal spent
        for src in (pool_stable, pool_all):
            cands = [
                r for r in src
                if quads.get(r["module"], "-") == q and r not in picked
            ]
            cands.sort(key=lambda r: -r["statements"])
            for r in cands:
                if spent + r["cpu_hours"] <= budget:
                    picked.append(r)
                    spent += r["cpu_hours"]
                    return

    take("A")
    take("B")
    for src in (pool_stable, pool_all):
        for r in sorted(src, key=lambda r: r["cpu_hours"]):
            if len(picked) >= max_modules:
                break
            if r in picked or spent + r["cpu_hours"] > budget:
                continue
            picked.append(r)
            spent += r["cpu_hours"]
    return picked, relaxed


def load(name: str) -> tuple[dict, dict]:
    base = json.loads((DATA_DIR / "baseline" / f"{name}.json").read_text(encoding="utf-8"))
    ovh = json.loads((DATA_DIR / "per_mutant_overhead.json").read_text(encoding="utf-8"))
    return base, ovh.get(name, {})


def module_costs(base: dict, ovh: dict) -> list[dict]:
    cov_files = base["coverage"]["files"]
    idx = base["coverage"].get("per_test_index", {})
    static = {
        f["path"].replace("\\", "/").split("/src/")[-1].split("/", 1)[-1]: f
        for f in base["static"]["package"]["files"]
    }
    pkg = base["subject"]["package"].split("/")[-1]
    static = {
        k[len(pkg) + 1 :] if k.startswith(pkg + "/") else k: v for k, v in static.items()
    }

    from mutloop.baseline import allocate_asserts

    alloc = allocate_asserts(idx, base["static"]["tests_static"]["funcs"],
                             base["subject"]["root"])["modules"]

    a = ovh.get("fitted_fixed_overhead_s", 3.0)
    b = ovh.get("fitted_per_test_s", 0.01)

    rows = []
    for mod, c in cov_files.items():
        key = mod.replace("\\", "/")
        covered = max(c["covered"], 1)
        tests = idx.get(mod, {})
        incidences = sum(tests.values())
        avg_tests = incidences / covered
        mutants = c["num_statements"] * MUTANTS_PER_STATEMENT
        per_mutant = a + b * avg_tests
        s = static.get(key, {})
        ncloc = s.get("ncloc", 0) or 0
        n_asserts = alloc.get(mod, alloc.get(key, {})).get("n_asserts", 0)
        rows.append(
            {
                "module": key,
                "ncloc": ncloc,
                "statements": c["num_statements"],
                "coverage": c["percent_covered"],
                "avg_tests_per_line": round(avg_tests, 1),
                "n_tests": len(tests),
                "n_asserts": n_asserts,
                "assert_density": round(100.0 * n_asserts / ncloc, 2) if ncloc else 0.0,
                "mutants": mutants,
                "sec_per_mutant": round(per_mutant, 2),
                "cpu_hours": round(mutants * per_mutant / 3600, 3),
            }
        )
    rows.sort(key=lambda r: r["cpu_hours"])
    return _calibrate(rows, ovh)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", action="append")
    ap.add_argument("--budget", type=float, default=3.0, help="每项目的 CPU 小时预算")
    ap.add_argument("--max-modules", type=int, default=4, help="每项目最多选几个模块")
    ap.add_argument("--top", type=int, default=14)
    args = ap.parse_args()

    projects = args.project or ["attrs", "click", "dateutil", "jinja", "marshmallow"]
    churn_path = DATA_DIR / "module_churn.json"
    all_churn = json.loads(churn_path.read_text(encoding="utf-8")) if churn_path.exists() else {}
    proposals: dict[str, list[dict]] = {}
    quads_last: dict[str, dict[str, str]] = {}
    churn_last: dict[str, dict] = {}

    for name in projects:
        base, ovh = load(name)
        if not ovh:
            print(f"[跳过] {name}: 缺少实测开销数据")
            continue
        churn = all_churn.get(name, {})
        churn_last[name] = churn
        rows = module_costs(base, ovh)
        total = sum(r["cpu_hours"] for r in rows)
        quads = quadrant_of(rows)
        quads_last[name] = quads

        print(f"\n=== {name} @ {base['subject']['tag']}　整项目 {total:.2f} CPU 小时 ===")
        print(
            f"{'模块':<24}{'象':>3}{'语句':>6}{'覆盖':>8}{'行均测试':>9}{'断言密度':>9}"
            f"{'变异体':>8}{'秒/变异体':>10}{'CPU小时':>9}"
        )
        print("-" * 88)
        for r in rows[: args.top]:
            print(
                f"{r['module']:<24}{quads.get(r['module'], '-'):>3}"
                f"{r['statements']:>6}{r['coverage']:>7.1f}%"
                f"{r['avg_tests_per_line']:>9.1f}{r['assert_density']:>9.1f}"
                f"{r['mutants']:>8}{r['sec_per_mutant']:>10.2f}{r['cpu_hours']:>9.3f}"
            )

        picked, relaxed = select_modules(rows, quads, args.budget, args.max_modules, churn)
        if picked:
            proposals[name] = picked
            spent = sum(r["cpu_hours"] for r in picked)
            cov_sel = sum(r["statements"] * r["coverage"] for r in picked) / sum(
                r["statements"] for r in picked
            )
            qs = "".join(sorted(quads.get(r["module"], "-") for r in picked))
            flag = "　⚠ 稳定模块不足，已放宽" if relaxed else ""
            print(
                f"  → 选中 {len(picked)} 个模块（象限 {qs}），合计 {spent:.2f} h"
                f"（整项目的 {100 * spent / total:.0f}%），加权覆盖 {cov_sel:.1f}%，"
                f"变异体 {sum(r['mutants'] for r in picked)}{flag}"
            )
            for r in picked:
                tag = "稳定" if churn.get(r["module"], {}).get("stable", True) else "有变动"
                print(f"      [{quads.get(r['module'], '-')}/{tag}] {r['module']:<22}"
                      f"{r['statements']:>5} 语句  {r['cpu_hours']:.3f} h")
        else:
            print(f"  → 预算 {args.budget} h 内没有满足门槛的模块，需上调预算或放宽门槛")

    if proposals:
        grand = sum(r["cpu_hours"] for v in proposals.values() for r in v)
        muts = sum(r["mutants"] for v in proposals.values() for r in v)
        print(f"\n{'=' * 78}")
        print(
            f"模块级实验集合计：{grand:.2f} CPU 小时 / {muts} 个变异体\n"
            f"按 16 核、保守 10x 有效加速 → 约 {grand / 10 * 60:.0f} 分钟墙钟／每轮全量分析"
        )

        sel_out = DATA_DIR / "module_selection.json"
        sel_out.write_text(
            json.dumps(
                {
                    "criteria": {
                        "min_statements": MIN_STATEMENTS,
                        "min_coverage": MIN_COVERAGE,
                        "exclude_stems": sorted(EXCLUDE_STEMS),
                        "budget_cpu_hours_per_project": args.budget,
                        "max_modules_per_project": args.max_modules,
                        "quadrant_stratified": "A/B 各至少一个，保住弱断言 vs 强断言的对比",
                        "within_quadrant_rule": "语句数最多且预算装得下（中性标准，不偏向便宜模块）",
                        "stability": "软约束：优先稳定模块，但不牺牲 A/B 对比",
                    },
                    "selected": {
                        n: [
                            {
                                "module": r["module"],
                                "quadrant": quads_last.get(n, {}).get(r["module"], "-"),
                                "stable": bool(
                                    churn_last.get(n, {}).get(r["module"], {}).get("stable", True)
                                ),
                                "statements": r["statements"],
                                "coverage": r["coverage"],
                                "assert_density": r["assert_density"],
                                "mutants": r["mutants"],
                                "cpu_hours": r["cpu_hours"],
                            }
                            for r in v
                        ]
                        for n, v in proposals.items()
                    },
                    "total_cpu_hours": round(grand, 2),
                    "total_mutants": muts,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"选中的模块集已写入: {sel_out}")

    out = DATA_DIR / "module_budget.json"
    out.write_text(
        json.dumps(
            {
                n: [
                    {k: v for k, v in r.items()}
                    for r in module_costs(*load(n))
                ]
                for n in projects
                if load(n)[1]
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"明细已写入: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
