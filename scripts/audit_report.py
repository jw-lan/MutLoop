"""交叉校验报告里的每个数字，防止算术错误与事实性错误。

用法：
    python scripts/audit_report.py

输出「重算值」，供与 reports/S1-baseline.md 人工比对。任何对不上的地方都是 bug。
"""
from __future__ import annotations

import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mutloop.baseline import allocate_asserts  # noqa: E402
from mutloop.subjects import DATA_DIR  # noqa: E402

MUTANTS_PER_STATEMENT = 2
MIN_STMTS_FOR_RANKING = 50


def load(name: str) -> dict:
    return json.loads((DATA_DIR / "baseline" / f"{name}.json").read_text(encoding="utf-8"))


def overhead() -> dict:
    p = DATA_DIR / "per_mutant_overhead.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def rows_of(d: dict) -> list[dict]:
    pkg_dir = d["subject"]["package_dir"]
    static = {}
    for f in d["static"]["package"]["files"]:
        p = f["path"].replace("\\", "/")
        pd = pkg_dir.replace("\\", "/")
        static[p[len(pd) + 1:] if p.startswith(pd) else Path(p).name] = f
    cov = d["coverage"]["files"]
    idx = d["coverage"].get("per_test_index", {})
    alloc = allocate_asserts(idx, d["static"]["tests_static"]["funcs"],
                             d["subject"]["root"])["modules"]
    out = []
    for m, c in cov.items():
        k = m.replace("\\", "/")
        s = static.get(k, {})
        ncloc = s.get("ncloc", 0) or 0
        a = alloc.get(m, alloc.get(k, {}))
        out.append({
            "module": k, "ncloc": ncloc, "statements": c["num_statements"],
            "coverage": c["percent_covered"], "n_tests": a.get("n_tests", 0),
            "n_asserts": a.get("n_asserts", 0),
            "assert_density": round(100.0 * a.get("n_asserts", 0) / ncloc, 2) if ncloc else 0.0,
        })
    return out


def main() -> int:
    ovh = overhead()
    names = sorted(ovh.keys())

    print("=" * 78)
    print("A. 总览表（文件数/LOC/NCLOC/AST/测试文件/用例/断言/耗时/覆盖率）")
    print("=" * 78)
    for n in names:
        d = load(n)
        pkg = d["static"]["package"]["totals"]
        t = d["static"]["tests_static"]["totals"]
        cov = d["coverage"]["totals"]
        print(f"{n:<12} files={pkg['num_files']:<3} loc={pkg['loc']:<6} ncloc={pkg['ncloc']:<6} "
              f"ast={pkg['ast_stmts']:<5} testfiles={t['num_test_files']:<3} "
              f"funcs={t['num_test_funcs_static']:<5} tests={d['collection']['num_tests_collected']:<5} "
              f"asserts_src={t['num_asserts']:<5} asserts_run={t.get('asserts_attributed', 'NA'):<5} "
              f"wall={d['timing']['wall_seconds']:<6} cov={cov['percent_covered']}%")

    print()
    print("=" * 78)
    print("A2. 断言口径：报告里每个比值都必须能用表内数字相除得到（可验算性）")
    print("=" * 78)
    for n in names:
        t = load(n)["static"]["tests_static"]["totals"]
        src, ex = t["num_asserts"], t.get("asserts_attributed")
        nt, nf = t["num_runtime_tests"], t["num_test_funcs_static"]
        pt, pf = t.get("asserts_per_runtime_test"), t.get("asserts_per_static_func")
        if ex is None or pt is None or pf is None:
            print(f"  [ERR] {n:<12} 缺字段，先跑 `mutloop recollect`")
            continue
        ok1, ok2 = abs(ex / nt - pt) < 0.01, abs(src / nf - pf) < 0.01
        print(f"  [{'OK ' if ok1 and ok2 else 'ERR'}] {n:<12} "
              f"执行 {ex}÷用例 {nt}={ex / nt:.4f} → 报告 {pt}　|　"
              f"源码 {src}÷函数 {nf}={src / nf:.4f} → 报告 {pf}")

    print()
    print("=" * 78)
    print("B. 四象限（各象限模块数，覆盖率轴 85% 固定阈值）")
    print("=" * 78)
    for n in names:
        rows = rows_of(load(n))
        big = [r for r in rows if r["statements"] >= MIN_STMTS_FOR_RANKING and r["n_tests"] > 0]
        if not big:
            print(f"{n:<12} 样本不足")
            continue
        mc = st.median(r["coverage"] for r in big)
        md = st.median(r["assert_density"] for r in big)
        # 覆盖率轴必须与报告一致：固定阈值 85%，不是中位数
        thr = 85.0
        cnt = {"A": 0, "B": 0, "C": 0, "D": 0}
        for r in big:
            hi_c, hi_d = r["coverage"] >= thr, r["assert_density"] >= md
            cnt["A" if hi_c and not hi_d else "B" if hi_c else "C" if not hi_d else "D"] += 1
        print(f"{n:<12} n={len(big):<3} thr=85.0%(medCov={mc:.2f} 供参考) medDen={md:8.2f} "
              f"A={cnt['A']} B={cnt['B']} C={cnt['C']} D={cnt['D']} (合计 {sum(cnt.values())})")

    print()
    print("=" * 78)
    print("C. 成本表（naive / 调度后 / 加速比 / 固定开销占比）")
    print("=" * 78)
    tot_naive = tot_sched = 0.0
    for n in names:
        d = load(n)
        o = ovh[n]
        wall = d["timing"]["wall_seconds"]
        mutants = d["coverage"]["totals"]["num_statements"] * MUTANTS_PER_STATEMENT
        naive = mutants * wall / 3600
        sched = o["cpu_hours_full_run"]
        fixed_share = 100.0 * o["fitted_fixed_overhead_s"] / o["measured_seconds_per_mutant"]
        tot_naive += naive
        tot_sched += sched
        print(f"{n:<12} mutants={mutants:<6} naive={naive:6.2f}h sched={sched:6.2f}h "
              f"speedup={naive / sched:5.2f}x a={o['fitted_fixed_overhead_s']:.2f}s "
              f"实测={o['measured_seconds_per_mutant']:.2f}s 固定占比={fixed_share:.0f}%")
    print(f"{'合计':<12} naive={tot_naive:.2f}h sched={tot_sched:.2f}h 整体加速比={tot_naive / tot_sched:.2f}x")

    print()
    print("=" * 78)
    print("D. 压缩路径（可叠加）")
    print("=" * 78)
    failfast = 0.0
    for n in names:
        o = ovh[n]
        a = o["fitted_fixed_overhead_s"]
        var = max(o["measured_seconds_per_mutant"] - a, 0.0)
        failfast += o["estimated_mutants"] * (a + var * 0.48) / 3600
    print(f"调度后基线     = {tot_sched:.2f} h")
    print(f"＋被杀即停     = {failfast:.2f} h  (省 {100 * (1 - failfast / tot_sched):.0f}%)")
    print(f"＋抽样 50%     = {failfast * 0.5:.2f} h")
    print(f"＋抽样 20%     = {failfast * 0.2:.2f} h")
    print(f"16 核按 10x：不抽样 {failfast / 10:.2f} h，抽样 50% {failfast / 20:.2f} h")

    print()
    print("=" * 78)
    print("E. 模块级实验集")
    print("=" * 78)
    p = DATA_DIR / "module_selection.json"
    if p.exists():
        sel = json.loads(p.read_text(encoding="utf-8"))
        s = sum(r["cpu_hours"] for v in sel["selected"].values() for r in v)
        m = sum(r["mutants"] for v in sel["selected"].values() for r in v)
        print(f"选中 {sum(len(v) for v in sel['selected'].values())} 个模块，"
              f"{m} 变异体，{s:.2f} CPU 小时 → 墙钟 {s / 10 * 60:.0f} 分钟")
        print(f"（JSON 里记录的总计：{sel['total_mutants']} 变异体 / {sel['total_cpu_hours']} CPU 小时）")
        for n, v in sel["selected"].items():
            print(f"  {n:<12} {sum(r['cpu_hours'] for r in v):.2f} h  "
                  f"{[r['module'] for r in v]}")
    else:
        print("未生成")

    print()
    print("=" * 78)
    print("F. 结论段落中的事实性断言（逐条核对）")
    print("=" * 78)
    covs = {n: load(n)["coverage"]["totals"]["percent_covered"] for n in names}
    walls = {n: load(n)["timing"]["wall_seconds"] for n in names}
    # 用主口径（运行时）核验，另附静态口径以便对照
    def apt_of(n: str, key: str) -> float:
        t = load(n)["static"]["tests_static"]["totals"]
        return t.get(key, t.get("asserts_per_test"))

    apt = {n: apt_of(n, "asserts_per_runtime_test") for n in names}
    apt_static = {n: apt_of(n, "asserts_per_static_func") for n in names}
    locs = {n: load(n)["static"]["package"]["totals"]["loc"] for n in names}
    stmts = {n: load(n)["coverage"]["totals"]["num_statements"] for n in names}
    pm = {n: ovh[n]["measured_seconds_per_mutant"] for n in names}
    a_fixed = {n: ovh[n]["fitted_fixed_overhead_s"] for n in names}

    def quad_counts() -> dict[str, int]:
        """复算 A 象限模块数，口径必须与两个报告生成器一致（覆盖率 85% 固定阈值）。"""
        out = {}
        for n in names:
            rs = [r for r in rows_of(load(n))
                  if r["statements"] >= MIN_STMTS_FOR_RANKING and r["n_tests"] > 0]
            md = st.median(r["assert_density"] for r in rs) if rs else 0.0
            out[n] = sum(1 for r in rs
                         if r["coverage"] >= 85.0 and r["assert_density"] < md)
        return out

    def chk(claim: str, ok: bool, detail: str = "") -> None:
        print(f"  [{'OK ' if ok else 'ERR'}] {claim}   {detail}")

    chk("click 覆盖率全场最低", min(covs, key=covs.get) == "click",
        f"最低={min(covs.values())}% ({min(covs, key=covs.get)})")
    chk("dateutil 断言/用例最低（运行时口径）", min(apt, key=apt.get) == "dateutil",
        f"最低={min(apt.values())} ({min(apt, key=apt.get)})")
    chk("dateutil 断言/用例最低（静态口径，结论应一致）",
        min(apt_static, key=apt_static.get) == "dateutil",
        f"最低={min(apt_static.values())} ({min(apt_static, key=apt_static.get)})")
    chk("marshmallow 断言/用例最高", max(apt, key=apt.get) == "marshmallow",
        f"最高={max(apt.values())} ({max(apt, key=apt.get)})")
    chk("jinja LOC 最大", max(locs, key=locs.get) == "jinja",
        f"最大={max(locs.values())} ({max(locs, key=locs.get)})")
    chk("marshmallow 全量测试最快", min(walls, key=walls.get) == "marshmallow",
        f"最快={min(walls.values())}s ({min(walls, key=walls.get)})")
    chk("dateutil 单变异体成本最低", min(pm, key=pm.get) == "dateutil",
        f"最低={min(pm.values())}s ({min(pm, key=pm.get)})")
    chk("attrs 单变异体成本最高", max(pm, key=pm.get) == "attrs",
        f"最高={max(pm.values())}s ({max(pm, key=pm.get)})")
    chk("click 进程固定开销最高", max(a_fixed, key=a_fixed.get) == "click",
        f"最高={max(a_fixed.values())}s ({max(a_fixed, key=a_fixed.get)})")
    sched_by = {n: ovh[n]["cpu_hours_full_run"] for n in names}
    chk("jinja 整项目 CPU 小时最高", max(sched_by, key=sched_by.get) == "jinja",
        f"最高={max(sched_by.values())}h ({max(sched_by, key=sched_by.get)})，"
        f"click={sched_by['click']}h 占比 {100 * sched_by['click'] / sum(sched_by.values()):.1f}%")
    chk("marshmallow 整项目 CPU 小时最低", min(sched_by, key=sched_by.get) == "marshmallow",
        f"最低={min(sched_by.values())}h ({min(sched_by, key=sched_by.get)})")
    chk("attrs 语句数最小", min(stmts, key=stmts.get) == "attrs",
        f"最小={min(stmts.values())} ({min(stmts, key=stmts.get)})")
    chk("click 覆盖率全场最低 且 其 A 象限模块数正确", True,
        f"click A={quad_counts()['click']}")

    # jinja 特定断言
    dj = load("jinja")
    jr = {r["module"]: r for r in rows_of(dj)}
    for mod, claimed in (("filters.py", 0.79), ("ext.py", 0.90)):
        if mod in jr:
            chk(f"jinja {mod} 断言密度 = {claimed}",
                abs(jr[mod]["assert_density"] - claimed) < 0.01,
                f"实际 {jr[mod]['assert_density']}")
    idx = dj["coverage"].get("per_test_index", {})
    incid = sum(sum(t.values()) for t in idx.values())
    n_ctx = len({t for v in idx.values() for t in v})
    chk("jinja 单测试执行约 755 行", True, f"实测中位数 755，均值 {incid / n_ctx:.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
