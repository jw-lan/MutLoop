"""从 data/baseline/*.json 生成 S1 基线报告（Markdown）。

用法：
    python scripts/gen_baseline_report.py
"""
from __future__ import annotations

import json
import statistics as st
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mutloop.baseline import allocate_asserts  # noqa: E402

BASE_DIR = ROOT / "data" / "baseline"
DATA_DIR = ROOT / "data"
REPORTS = ROOT / "reports"

# 只有规模足够大的模块才进入"薄测试"排序，避免 __init__.py 之类的噪声
MIN_STMTS_FOR_RANKING = 50
# 四象限的「高覆盖」判据，必须与 scripts/plan_module_budget.py 保持一致
HIGH_COVERAGE_THRESHOLD = 85.0


def load_overhead() -> dict:
    p = DATA_DIR / "per_mutant_overhead.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def load_all() -> dict[str, dict]:
    data = {}
    for p in sorted(BASE_DIR.glob("*.json")):
        data[p.stem] = json.loads(p.read_text(encoding="utf-8"))
    return data


def rel_static(path: str, pkg_dir: str) -> str:
    pp = Path(path).as_posix()
    pd = Path(pkg_dir).as_posix()
    return pp[len(pd) + 1 :] if pp.startswith(pd) else Path(path).name


def _k(path: str) -> str:
    """统一路径分隔符。

    Windows 下 coverage 的 relative_to() 产出反斜杠（parser\\_parser.py），
    而静态分析用 as_posix()；不统一会导致子包模块的规模指标匹配不上。
    """
    return path.replace("\\", "/")


def module_rows(d: dict) -> list[dict]:
    """把静态规模 + 覆盖率 + per-test 断言分配 合成模块级明细。"""
    pkg_dir = d["subject"]["package_dir"]
    static = {_k(rel_static(f["path"], pkg_dir)): f for f in d["static"]["package"]["files"]}
    cov = d["coverage"]["files"]
    # 断言分配基于已落盘的 per-test 索引重算，改算法无需重跑测试套件
    idx = {_k(k): v for k, v in d["coverage"].get("per_test_index", {}).items()}
    alloc = allocate_asserts(idx, d["static"]["tests_static"]["funcs"], d["subject"]["root"])[
        "modules"
    ]

    rows = []
    for raw_rel, c in cov.items():
        rel = _k(raw_rel)
        s = static.get(rel, {})
        ncloc = s.get("ncloc", 0)
        a = alloc.get(rel, {})
        n_asserts = a.get("n_asserts", 0)
        rows.append(
            {
                "module": rel,
                "ncloc": ncloc,
                "sloc": s.get("sloc", 0),
                "ast_stmts": s.get("ast_stmts", 0),
                "num_statements": c["num_statements"],
                "covered": c["covered"],
                "missing": c["missing"],
                "pct": c["percent_covered"],
                "n_tests": a.get("n_tests", 0),
                "n_asserts": n_asserts,
                "assert_density": round(100.0 * n_asserts / ncloc, 2) if ncloc else 0.0,
            }
        )
    rows.sort(key=lambda r: -r["ncloc"])
    return rows


def overview_table(data: dict[str, dict]) -> tuple[str, str]:
    """拆成「代码规模」与「测试与覆盖」两张表。

    拆表是为了让**每个比值都能用表内数字直接相除验算**——指标一多挤在一行里，
    读者（包括写报告的人）很容易拿错分子分母，这个项目已经踩过一次这个坑。
    """
    a_head = (
        "| 项目 | 锁定 tag | 源码包 | 文件数 | LOC | NCLOC | AST 语句 |\n"
        "|---|---|---|---|---|---|---|"
    )
    a = [a_head]
    for name, d in data.items():
        p = d["static"]["package"]["totals"]
        a.append(
            f"| `{name}` | `{d['subject']['tag']}` | `{d['subject']['package']}` | "
            f"{p['num_files']} | {p['loc']} | {p['ncloc']} | {p['ast_stmts']} |"
        )

    b_head = (
        "| 项目 | 测试文件 | 测试函数 | 用例数 | 断言数（源码 / 全套件执行） | "
        "断言/用例 | 断言/测试函数 | 全量测试(s) | 语句覆盖 |\n"
        "|---|---|---|---|---|---|---|---|---|"
    )
    b = [b_head]
    for name, d in data.items():
        t = d["static"]["tests_static"]["totals"]
        cov = d["coverage"]["totals"]
        wall = d.get("timing", {}).get("wall_seconds", "n/a")
        b.append(
            f"| `{name}` | {t['num_test_files']} | {t['num_test_funcs_static']} | "
            f"{d['collection']['num_tests_collected']} | "
            f"{t['num_asserts']} / **{t.get('asserts_attributed', 'n/a')}** | "
            f"**{t.get('asserts_per_runtime_test', 'n/a')}** | "
            f"{t.get('asserts_per_static_func', t.get('asserts_per_test', 'n/a'))} | "
            f"{wall} | {cov['percent_covered']}% |"
        )
    return "\n".join(a), "\n".join(b)


def module_table(rows: list[dict], top: int = 12) -> str:
    head = (
        "| 模块 | NCLOC | 语句数 | 覆盖行数 | 缺失 | 覆盖率 | 覆盖测试数 | 断言数 | 断言密度(/100 NCLOC) |\n"
        "|---|---|---|---|---|---|---|---|"
    )
    lines = [head]
    for r in rows[:top]:
        lines.append(
            f"| `{r['module']}` | {r['ncloc']} | {r['num_statements']} | {r['covered']} | "
            f"{r['missing']} | {r['pct']}% | {r['n_tests']} | {r['n_asserts']} | {r['assert_density']} |"
        )
    return "\n".join(lines)


def cost_table(data: dict[str, dict], overhead: dict) -> str:
    """变异分析成本实测——S3 调度层的验收目标能不能达到，由这张表说了算。

    口径：
    - 行均测试 = Σ(测试在某模块覆盖的行数) / 被覆盖行数，即"一条被覆盖的语句
      平均被多少条测试覆盖"，这是覆盖率导向调度后每个变异体要跑的测试数。
    - 单变异体成本 = 固定开销 a + 单条测试耗时 b × 行均测试。
      **a 是本表的关键**：每判定一个变异体都要启动一次 pytest 进程，这笔开销
      与跑多少条测试无关，调度层省不掉。
    - 实测/变异体 = 直接按行均测试数跑一批、3 次取最快，作为预算依据。
    - 预估变异体数 = 语句数 × 2（5 个算子下的保守下限，S2 用 mutmut 实测替换）。
    """
    if not overhead:
        return "_尚未实测，请先运行 `python scripts/bench_per_mutant_overhead.py`_"

    head = (
        "| 项目 | 行均测试 | 固定开销 a(s) | 单测试 b(s) | 实测/变异体(s) | 预估变异体 | "
        "naive(h) | 调度后(h) | **实际加速比** |\n"
        "|---|---|---|---|---|---|---|---|---|"
    )
    lines = [head]
    for name, d in data.items():
        o = overhead.get(name)
        if not o:
            continue
        wall = d["timing"]["wall_seconds"]
        mutants = o["estimated_mutants"]
        naive = mutants * wall / 3600
        sched = o["cpu_hours_full_run"]
        fixed_share = 100.0 * o["fitted_fixed_overhead_s"] / o["measured_seconds_per_mutant"]
        lines.append(
            f"| `{name}` | {o['avg_tests_per_line']:.0f} | {o['fitted_fixed_overhead_s']:.2f} | "
            f"{o['fitted_per_test_s']:.4f} | {o['measured_seconds_per_mutant']:.2f} "
            f"（固定开销占 {fixed_share:.0f}%） | {mutants} | {naive:.1f} | {sched:.2f} | "
            f"**{naive / sched if sched else 0:.1f}x** |"
        )
    return "\n".join(lines)


def module_plan_section() -> str:
    """渲染模块级实验集（由 scripts/plan_module_budget.py 产出）。"""
    p = DATA_DIR / "module_selection.json"
    if not p.exists():
        return "_尚未生成，请先运行 `python scripts/plan_module_budget.py`_"
    d = json.loads(p.read_text(encoding="utf-8"))
    c = d["criteria"]

    lines = [
        "### 入选标准（先定标准，再看结果）\n",
        f"- 语句数 ≥ **{c['min_statements']}**：变异体数量够，结果才有统计意义\n"
        f"- 覆盖率 ≥ **{c['min_coverage']}%**：保证低分是「断言没打中」，不是「根本没覆盖」\n"
        f"- 象限分层：**{c['quadrant_stratified']}**\n"
        f"- 象限内规则：**{c['within_quadrant_rule']}**\n"
        f"- 稳定性：{c['stability']}（由 `scripts/measure_module_churn.py` 对比前 2 个 release 的文件哈希实测）\n"
        f"- 排除：`{'`, `'.join(c['exclude_stems'])}` 等无逻辑的样板文件\n",
        "\n> **为什么按「语句数最多」而不是「最便宜」挑**：便宜 = 覆盖该模块的测试少，"
        "而测试少往往意味着变异分数低。按最便宜挑会系统性地偏向低分模块，"
        "等于把结论预设进样本里。语句数只影响统计功效，是中性标准。\n",
        f"\n### 选中的模块集（{d['total_mutants']} 个变异体，{d['total_cpu_hours']} CPU 小时）\n",
        "| 项目 | 模块 | 象限 | 稳定性 | 语句 | 覆盖率 | 断言密度 | 变异体 | CPU 小时 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    quad_desc = {"A": "高覆盖/弱断言", "B": "高覆盖/强断言", "C": "低覆盖/弱断言", "D": "低覆盖/强断言"}
    for name, mods in d["selected"].items():
        for i, r in enumerate(mods):
            q = f"{r['quadrant']} {quad_desc.get(r['quadrant'], '')}"
            lines.append(
                f"| {name if i == 0 else ''} | `{r['module']}` | {q} | "
                f"{'稳定' if r['stable'] else '有变动'} | {r['statements']} | "
                f"{r['coverage']:.1f}% | {r['assert_density']:.1f} | {r['mutants']} | "
                f"{r['cpu_hours']:.2f} |"
            )
    wall = d["total_cpu_hours"] / 10
    full = sum(o["cpu_hours_full_run"] for o in load_overhead().values())
    lines.append(
        f"\n按 16 核、保守 10 倍有效加速 → **约 {wall * 60:.0f} 分钟墙钟／每轮全量分析**，"
        f"而整项目方案约 {full:.1f} CPU 小时 → {full / 10:.1f} 小时墙钟。\n"
    )
    return "\n".join(lines)


def mitigation_table(overhead: dict) -> str:
    """把「调度后」的 CPU 小时，按可叠加的优化手段逐级压缩。"""
    if not overhead:
        return ""
    base = sum(o["cpu_hours_full_run"] for o in overhead.values())

    # 被杀即停：假设 65% 的变异体被杀死、且平均跑完 20% 的覆盖测试时就被杀死，
    # 则可变部分的期望系数 = 0.65×0.20 + 0.35×1.00 = 0.48（固定开销 a 不受影响）
    failfast = 0.0
    for o in overhead.values():
        a = o["fitted_fixed_overhead_s"]
        var = max(o["measured_seconds_per_mutant"] - a, 0.0)
        failfast += o["estimated_mutants"] * (a + var * 0.48) / 3600

    rows = [
        ("调度后（基线）", base, "—"),
        ("＋ 被杀即停", failfast, f"省 {100 * (1 - failfast / base):.0f}%"),
        ("＋ 变异体抽样 50%", failfast * 0.5, "统计功效减半，需做敏感性分析"),
        ("＋ 变异体抽样 20%", failfast * 0.2, "必须报告置信区间"),
    ]
    lines = ["| 手段叠加 | 单线程 CPU 小时 | 备注 |", "|---|---|---|"]
    for label, hours, note in rows:
        lines.append(f"| {label} | **{hours:.1f}** | {note} |")
    lines.append(
        f"\n按本机 16 核、保守 10 倍有效加速折算：不抽样约 **{failfast / 10:.1f} 小时**、"
        f"抽样 50% 约 **{failfast / 20:.1f} 小时**／每轮全量分析。"
    )
    return "\n".join(lines)


def _facts(d: dict, meta: dict) -> dict:
    return {
        "cov": d["coverage"]["totals"]["percent_covered"],
        "stmts": d["coverage"]["totals"]["num_statements"],
        "ncloc": d["static"]["package"]["totals"]["ncloc"],
        "tests": d["collection"]["num_tests_collected"],
        "wall": d["timing"]["wall_seconds"],
        "a_per_test": d["static"]["tests_static"]["totals"].get(
            "asserts_per_runtime_test",
            d["static"]["tests_static"]["totals"].get("asserts_per_test"),
        ),
        "a_quad": meta.get("counts", {}).get("A_高覆盖_弱断言", 0),
        "a_modules": ", ".join(
            f"{m['module']}({m['pct']:.1f}%/{m['density']:.2f})"
            for m in meta.get("a_modules", [])[:3]
        )
        or "无",
    }


def conclusion(data: dict[str, dict], rows: dict[str, list[dict]], metas: dict) -> str:
    f = {n: _facts(d, metas.get(n, {})) for n, d in data.items()}

    def pct(n, k):
        return f"{f[n][k]}%" if k in ("cov",) else f[n][k]

    lines = [
        "### 三条判据\n",
        "1. **覆盖率不是越高越好**。覆盖率越接近 100%，变异分数天花板越高，RQ2「定向补测带来的增益」就越没有空间。"
        "因此高覆盖项目只能当对照，不能当主增益对象。\n"
        "2. **看 A 象限（高覆盖 / 弱断言）的模块数量**。这类模块覆盖率虚高、断言没打中，"
        "正是本平台要解决的问题，也是变异分数与覆盖率背离最可能出现的地方。\n"
        "3. **成本必须可控**。全量测试耗时 × 变异体数 × 行均测试数决定了两周内能否跑完；"
        "慢项目只能靠 S3 的调度层救，风险前置。\n",
        "### 结论\n",
        "| 项目 | 角色 | 覆盖率 | 用例 | 全量(s) | 断言/用例 | A 象限模块 | 选型理由 |",
        "|---|---|---|---|---|---|---|---|",
        f"| **click** | 主力 | {pct('click','cov')} | {f['click']['tests']} | {f['click']['wall']} | "
        f"{f['click']['a_per_test']} | {f['click']['a_quad']} | "
        f"**覆盖率全场最低（{pct('click','cov')}）**，A 象限模块 {f['click']['a_quad']} 个"
        f"（{f['click']['a_modules']}），"
        "是唯一「整项目覆盖率不高、但核心模块覆盖已经不低」的项目，"
        "最能体现「覆盖率到了、断言没到」。代价是全量测试 27.5s 全场最慢、"
        "进程固定开销 4.55s 也最高 |",
        f"| **jinja** | 主力 | {pct('jinja','cov')} | {f['jinja']['tests']} | {f['jinja']['wall']} | "
        f"{f['jinja']['a_per_test']} | {f['jinja']['a_quad']} | "
        f"规模最大（14.4k LOC），**A 象限模块 {f['jinja']['a_quad']} 个为全场最多**，"
        f"旗舰是 filters.py（882 NCLOC、覆盖率 90.6%、断言密度仅 0.79——"
        f"覆盖到了却几乎没有断言打中它）；测试只要 8.4s，性价比最高 |",
        f"| **dateutil** | 对照·弱断言 | {pct('dateutil','cov')} | {f['dateutil']['tests']} | {f['dateutil']['wall']} | "
        f"{f['dateutil']['a_per_test']} | {f['dateutil']['a_quad']} | "
        f"断言密度全场最低（{f['dateutil']['a_per_test']}/用例，两个口径都是最低），用来验证「断言弱 → 变异分数低」这条核心假设；"
        "测试最快之一，单变异体判定成本 3.52s 全场最低 |",
        f"| **attrs** | 对照·上限 | {pct('attrs','cov')} | {f['attrs']['tests']} | {f['attrs']['wall']} | "
        f"{f['attrs']['a_per_test']} | {f['attrs']['a_quad']} | "
        "覆盖率 99.94%，测试极度扎实，预期变异分数接近天花板。"
        "作为 RQ2 的**负向锚点**：诚实展示「测试已经很好时，定向补测增益有限」 |",
        f"| marshmallow | 备选 / S2 工程验证 | {pct('marshmallow','cov')} | {f['marshmallow']['tests']} | "
        f"{f['marshmallow']['wall']} | {f['marshmallow']['a_per_test']} | {f['marshmallow']['a_quad']} | "
        "全项目分析最便宜（4.47 CPU 小时，全场最低），**S2 第一天拿它跑通垂直切片**；"
        f"但覆盖率 97.3%、断言密度 {f['marshmallow']['a_per_test']} 全场最高，增益空间小 |",
        "",
        "**正式实验集：5 个项目全进，但每个只做 2–3 个模块**（范围收敛见第 7 节）。"
        "marshmallow 额外承担 S2 垂直切片与「模块级 vs 项目级」锚点两个角色。\n",
        "### 为什么不选 requests\n",
        "requests 的测试依赖 `pytest-httpbin==2.1.0` + `httpbin~=0.10.0`（起本地 HTTP 服务）。"
        "变异测试要求「同一变异体的判定结果可复现、且各变异体之间互不干扰」，"
        "HTTP 服务的时序与端口占用会引入非确定性，也会让并发调度层的隔离保证失效。"
        "这一条同样是我们排除所有网络依赖型项目的通用规则。\n",
        "### 已识别风险与对策\n",
        "| 风险 | 对策 |",
        "|---|---|",
        "| click 全量测试 27.5s 最慢、进程固定开销 4.55s 最高 | S2 只在 click 的小模块（如 formatting.py）上验证加速比，达标后再放大到 core.py |",
        "| jinja 整项目 CPU 小时最高（14.30 h，略高于 click 的 14.21 h） | jinja 的加速比只有 2.1x，靠模块级收敛控制；详见第 7 节 |",
        "| 单变异体固定开销 2.9–4.6s，占成本 47%–83%，调度层省不掉 | 靠「被杀即停 + 16 核并行 + 变异体抽样」压总量，而不是死磕进程启动；详见第 5 节 |",
        "| 变异体数目前按「每语句 2 个」估算，若真实为 3–4 个，成本同比翻倍 | **S2 第一件事**：在 marshmallow 上数出 mutmut 的真实变异体数，据此修订预算 |",
        "| dateutil 含 hypothesis 属性测试，同一变异体的判定可能不稳定 | 上下文对齐率 89.86%（其余 4 项 100%），S2 起记录「同一变异体重复判定一致性」，不稳定则剔除 `tests/property/` |",
        "| jinja 单测试执行约 755 行引擎代码，断言专注度普遍偏低 | 该指标只做项目**内部**相对排序，不做跨项目比较，已在第 4 节注明 |",
        "| attrs 覆盖率 99.94%，变异分数可能已无提升空间 | 这正是它的角色：RQ2 的负向锚点，如实报告零增益/微增益 |",
    ]
    return "\n".join(lines)


def quadrant(rows: list[dict]) -> tuple[str, dict]:
    """覆盖率 vs 断言密度四象限。

    覆盖率轴用**固定阈值 85%**，断言密度轴用**项目内中位数**（理由见
    scripts/plan_module_budget.py 的 quadrant_of，两处定义必须保持一致）。
    """
    big = [r for r in rows if r["num_statements"] >= MIN_STMTS_FOR_RANKING and r["n_tests"] > 0]
    if not big:
        return "_（样本不足，无法计算象限）_", {}
    med_den = st.median(r["assert_density"] for r in big)

    buckets = {"A_高覆盖_弱断言": [], "B_高覆盖_强断言": [], "C_低覆盖_弱断言": [], "D_低覆盖_强断言": []}
    for r in big:
        hi_cov = r["pct"] >= HIGH_COVERAGE_THRESHOLD
        hi_den = r["assert_density"] >= med_den
        key = ("A_高覆盖_弱断言" if hi_cov else "C_低覆盖_弱断言") if not hi_den else (
            "B_高覆盖_强断言" if hi_cov else "D_低覆盖_强断言"
        )
        buckets[key].append(r)

    out = [
        f"- 切分口径：**覆盖率 ≥ {HIGH_COVERAGE_THRESHOLD:.0f}%（固定阈值）** / "
        f"**断言密度 ≥ {med_den:.2f}（项目内中位数）**"
        f"（仅统计 ≥{MIN_STMTS_FOR_RANKING} 语句且被测试覆盖的模块，共 {len(big)} 个）",
        f"- 覆盖率之所以不用中位数：jinja 的模块覆盖率挤在 89–96%，中位数 90.91%，"
        f"会让 filters.py（90.64%）只差 0.27 个百分点就翻到「低覆盖」象限——刀刃效应。"
        f"固定阈值可复现、不随样本微调翻转。",
        "",
        "| 象限 | 模块数 | 含义 | 本项目价值 |",
        "|---|---|---|---|",
        f"| **A 高覆盖 / 弱断言** | {len(buckets['A_高覆盖_弱断言'])} | 覆盖到了但没断言住 | **首选**：变异分数大概率低，补测增益空间最大 |",
        f"| B 高覆盖 / 强断言 | {len(buckets['B_高覆盖_强断言'])} | 测试扎实 | 作为对照组 / 上限参照 |",
        f"| C 低覆盖 / 弱断言 | {len(buckets['C_低覆盖_弱断言'])} | 测试本身不足 | 次选：分数低但故事性弱（覆盖率背锅） |",
        f"| D 低覆盖 / 强断言 | {len(buckets['D_低覆盖_强断言'])} | 断言强但覆盖窄 | 少见于成熟项目 |",
        "",
        "**A 象限模块清单（按 NCLOC 排序）：**",
        "",
    ]
    a_rows = sorted(buckets["A_高覆盖_弱断言"], key=lambda r: -r["ncloc"])[:10]
    if a_rows:
        out.append("| 模块 | NCLOC | 覆盖率 | 覆盖测试数 | 断言密度 |")
        out.append("|---|---|---|---|---|")
        for r in a_rows:
            out.append(
                f"| `{r['module']}` | {r['ncloc']} | {r['pct']}% | {r['n_tests']} | {r['assert_density']} |"
            )
    else:
        out.append("_无_")

    a_modules = sorted(buckets["A_高覆盖_弱断言"], key=lambda r: -r["ncloc"])
    meta = {
        "high_coverage_threshold": HIGH_COVERAGE_THRESHOLD,
        "median_assert_density": med_den,
        "counts": {k: len(v) for k, v in buckets.items()},
        "n_modules": len(big),
        "a_modules": [{"module": r["module"], "ncloc": r["ncloc"],
                       "pct": r["pct"], "density": r["assert_density"]} for r in a_modules],
    }
    return "\n".join(out), meta


def validity_threats_table(marsh_full: float) -> str:
    """范围收敛到模块级之后的效度威胁与处理。完整版与精简版共用。"""
    return (
        "### 这样收敛会不会损害结论\n"
        "\n"
        "| 威胁 | 处理 |\n"
        "|---|---|\n"
        "| 挑模块时偏向有利结果 | 入选标准**先定后看**，且用中性标准（语句数）而非「最便宜」；"
        "报告里公开标准与全部候选的明细（`data/module_budget.json`） |\n"
        "| 只挑弱断言模块，等于预设结论 | 强制 A/B 分层：每个项目必须同时含弱断言与强断言模块，"
        "**对比本身**才是结论，不是单个模块的分数 |\n"
        f"| 模块级结论能否推广到项目级 | 额外做**一次 marshmallow 整项目**分析"
        f"（{marsh_full:.2f} CPU 小时，约 {marsh_full / 10 * 60:.0f} 分钟）"
        "作为锚点，验证模块级与项目级结论一致 |\n"
        "| RQ3 外部效度是否受影响 | 不受影响：Defects4J 是独立的 Java 项目，走另一条链路 |\n"
        "| 可复现性 | 锁定 release tag 已保证，与模块稳不稳定无关；"
        "churn 只影响结论的时效性，因此作为软约束而非硬门槛 |\n"
    )


def main() -> int:
    data = load_all()
    if not data:
        print(f"未找到基线数据：{BASE_DIR}")
        return 1

    REPORTS.mkdir(parents=True, exist_ok=True)
    parts: list[str] = []
    parts.append("# S1 基线报告：被测项目选型与初始判断\n")
    parts.append(
        f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}　"
        f"数据源：`data/baseline/*.json`（由 `python -m mutloop baseline` 生成）\n"
    )

    any_d = next(iter(data.values()))
    scale_tbl, test_tbl = overview_table(data)
    parts.append("## 1. 实验环境\n")
    parts.append(
        f"- Python `{any_d['subject']['python']}` / 平台 `{any_d['subject']['platform']}`\n"
        f"- 覆盖率工具：coverage.py（per-test 上下文由 pytest 插件调用 "
        f"`Coverage.switch_context(nodeid)` 写入，**不是** coverage 内置的 "
        f"`dynamic_context=test_function`，后者实测只识别 10/38 条用例且无参数化信息）\n"
        f"- 断言强度：AST 静态统计（裸 assert / pytest.raises / unittest assert*）\n"
        f"- 用例计数：`pytest --collect-only`（展开参数化后的真实用例数）\n"
        f"- 耗时口径：不带插桩的 `pytest` 墙钟时间（即 RQ1 的分母）\n"
    )

    parts.append("\n## 2. 候选项目总览\n")
    parts.append("\n**代码规模**\n")
    parts.append(scale_tbl + "\n")
    parts.append("\n**测试与覆盖**\n")
    parts.append(test_tbl + "\n")
    parts.append(
        "\n> NCLOC = SLOC 扣除 docstring 行，最接近「真代码量」；"
        "AST 语句数 ≈ 变异算子的潜在落点规模。\n"
        ">\n"
        "> **四个易混的计数，务必分清（都可以用表内数字相除验算）**：\n"
        "> - **测试函数** ＝ 源码里 `def test*` 的个数（参数化**未**展开）\n"
        "> - **用例数** ＝ `pytest --collect-only` 得到的条数（参数化**已**展开）\n"
        "> - **断言数（源码）** ＝ AST 数出来的 assert 语句条数\n"
        "> - **断言数（全套件执行）** ＝ 跑完整个套件实际执行的断言次数 ＝ "
        "Σ 每条用例（其测试函数的断言数）；参数化会重复计入\n"
        ">\n"
        "> 于是：\n"
        "> - **断言/用例** ＝ 全套件执行数 ÷ 用例数\n"
        "> - **断言/测试函数** ＝ 源码断言数 ÷ 测试函数数\n"
        ">\n"
        "> 两个口径的**排序结论一致**，但数值不同（参数化越重差得越多，"
        "attrs 2.24 vs 1.88）。引用时必须说明是哪个。\n"
    )

    parts.append("\n## 3. 各项目模块级明细（按 NCLOC 取前 12）\n")
    all_rows: dict[str, list[dict]] = {}
    for name, d in data.items():
        rows = module_rows(d)
        all_rows[name] = rows
        parts.append(f"\n### {name} @ `{d['subject']['tag']}`\n")
        if not rows:
            parts.append("_覆盖率数据缺失（测试未成功运行）_\n")
            continue
        parts.append(module_table(rows) + "\n")

    parts.append("\n## 4. 覆盖率 vs 断言密度：四象限初判\n")
    parts.append(
        "> **指标口径（务必先读）**：这里的「断言密度」是 **断言专注度**，不是断言条数。\n"
        "> 一个测试的断言会按「它在本模块执行的行数 / 它在被测包内执行的总行数」摊到各模块，\n"
        "> 避免像 `_compat.py` 这种被 979 个测试「顺带覆盖一行」的小模块把别人的断言全算到自己头上。\n"
        ">\n"
        "> 副作用：框架型项目（如 jinja）每跑一个测试就要执行约 755 行引擎代码，\n"
        "> 单模块占比天然被摊薄。**因此该指标只在项目内部做相对排序，绝不做跨项目比较。**\n"
    )
    metas = {}
    for name, rows in all_rows.items():
        parts.append(f"\n### {name}\n")
        body, meta = quadrant(rows)
        metas[name] = meta
        parts.append(body + "\n")

    parts.append("\n## 5. 变异分析成本实测（S3 的前置约束）\n")
    overhead = load_overhead()
    parts.append(cost_table(data, overhead) + "\n")
    parts.append(
        "\n> **读数方式**：naive(h) 是「每个变异体都跑一遍全量套件」的代价，"
        "调度后(h) 是「只跑覆盖该变异行的测试子集」的代价（S3 要实现的就是后者）。\n"
        "> 变异体数按「每语句 2 个」保守估算，S2 接入 mutmut 后用真实计数替换。\n"
        ">\n"
        "> **重要更正**：本报告初版只算了「测试执行时间」，漏掉了"
        "**每判定一个变异体就要启动一次 pytest 进程的固定开销**（实测 2.9–4.5 秒，"
        "占单变异体成本的 47%–83%）。计入后真实加速比从初版声称的 5.2x–21.6x "
        "降到 **1.4x–4.5x**。这是本报告最关键的一个发现：覆盖率导向调度的收益，"
        "被进程启动开销吃掉了一大半。\n"
        ">\n"
        "> **RQ1 验收口径**：原计划「全量变异分析耗时 < 全量测试耗时的 25%」按字面不成立；"
        "改为「单个变异体平均判定成本 < 全量测试耗时的 25%」（等价加速比 > 4x）后，"
        "按上表也只有 click 达标。因此**不要把 4x 写成硬性验收**，建议改为报告实测值 + "
        "把「固定开销占比」本身作为一个发现来写。\n"
    )
    parts.append("\n### 成本压缩路径（手段可叠加）\n")
    parts.append(mitigation_table(overhead) + "\n")

    parts.append("\n## 6. 选型结论\n")
    parts.append(conclusion(data, all_rows, metas) + "\n")

    parts.append("\n## 7. 实验范围收敛：从整项目到模块级\n")
    full_total = sum(o["cpu_hours_full_run"] for o in load_overhead().values())
    parts.append(
        f"整项目跑一轮全量变异分析要 {full_total:.1f} CPU 小时（16 核约 {full_total / 10:.1f} 小时墙钟），"
        "两周工期内几乎没有试错空间。"
        "改为「每个项目选 2–3 个代表性模块」后，一轮压到 1 小时以内，"
        "可以支撑「跑 → 看结果 → 改 → 重跑」的迭代。\n"
    )
    parts.append(module_plan_section() + "\n")
    parts.append(
        validity_threats_table(
            load_overhead().get("marshmallow", {}).get("cpu_hours_full_run", 0.0)
        )
    )

    out = REPORTS / "S1-baseline.md"
    out.write_text("\n".join(parts), encoding="utf-8")
    (REPORTS / "S1-quadrant-meta.json").write_text(
        json.dumps(metas, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"报告已生成: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
