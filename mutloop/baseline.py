"""S1 基线采集器。

采集四类数据：
  1. 代码规模      —— LOC / SLOC / NCLOC / AST 语句数（静态度量）
  2. 测试规模      —— pytest --collect-only 的真实用例数 + 断言数与断言密度（静态度量）
  3. 全量测试耗时  —— 不带插桩的 pytest 墙钟时间（RQ1 的分母）
  4. 语句覆盖率    —— coverage.py 聚合覆盖率；可选 per-test 上下文覆盖率

per-context 覆盖率（dynamic_context=test_function）本来是 S3 的内容，
这里提前跑通是为了尽早验证"变异体 → 相关测试子集"这条链路可用，
符合总原则 2：先跑通垂直切片。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from . import metrics
from .subjects import DATA_DIR, Subject

# --------------------------------------------------------------------------
# 通用工具
# --------------------------------------------------------------------------

PYTEST_BASE_ARGS = [
    "-p",
    "no:cacheprovider",
    "--no-header",
    "-q",
    "--tb=no",
]


def _norm(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


class _OutcomeCollector:
    """轻量 pytest 插件：按 nodeid 记录最终 outcome。"""

    def __init__(self) -> None:
        self.outcomes: dict[str, str] = {}

    def pytest_runtest_logreport(self, report):  # noqa: D102 - pytest hook
        prev = self.outcomes.get(report.nodeid)
        if report.outcome == "passed":
            self.outcomes.setdefault(report.nodeid, "passed")
        elif prev != "failed":
            self.outcomes[report.nodeid] = report.outcome


def pytest_args(subj: Subject) -> list[str]:
    """构造 pytest 参数：统一的静音配置 + 剔除基线即失败的用例。"""
    args = [*subj.test_paths, *PYTEST_BASE_ARGS]
    for d in subj.deselect:
        args += ["--deselect", d]
    return args


def _run_subprocess(args: list[str], cwd: Path, timeout: int | None) -> tuple[int, str, str, float]:
    t0 = time.perf_counter()
    proc = subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return proc.returncode, proc.stdout, proc.stderr, time.perf_counter() - t0


# --------------------------------------------------------------------------
# 1/2. 静态度量
# --------------------------------------------------------------------------

def collect_static(subj: Subject) -> dict:
    pkg = metrics.package_metrics(subj.package_dir)
    test_roots = [subj.root / t for t in subj.test_paths]
    tests = metrics.test_suite_metrics([r for r in test_roots if r.exists()])
    return {"package": pkg, "tests_static": tests}


# --------------------------------------------------------------------------
# 3a. 真实用例数（pytest --collect-only）
# --------------------------------------------------------------------------

def collect_test_ids(subj: Subject, timeout: int = 600) -> dict:
    """统计真实用例数（参数化已展开）。

    pytest 8 在 `--collect-only -q` 下输出的是「文件: 条数」的紧凑格式，
    没有 -q 才是逐条 nodeid；这里两种格式都兼容。
    """
    args = [sys.executable, "-m", "pytest", *pytest_args(subj), "--collect-only"]
    rc, out, err, dt = _run_subprocess(args, subj.root, timeout)

    per_file: dict[str, int] = {}
    ids: list[str] = []
    for line in out.splitlines():
        s = line.strip()
        m = re.match(r"^(.+?\.py):\s+(\d+)$", s)
        if m:
            per_file[m.group(1)] = int(m.group(2))
            continue
        if "::" in s and not s.startswith(("-", "=", "<")):
            ids.append(s.split(" ")[0])

    per_file: dict[str, int] = {}
    ids: list[str] = []
    for line in out.splitlines():
        s = line.strip()
        m = re.match(r"^(.+?\.py):\s+(\d+)$", s)
        if m:
            per_file[m.group(1)] = int(m.group(2))
            continue
        if "::" in s and not s.startswith(("-", "=", "<")):
            ids.append(s.split(" ")[0])

    if ids:
        num = len(ids)
        for i in ids:
            per_file[i.split("::")[0]] = per_file.get(i.split("::")[0], 0) + 1
    else:
        # 退化到「文件: 条数」紧凑格式：总数仍可信，但没有逐条 nodeid
        num = sum(per_file.values())

    return {
        "returncode": rc,
        "num_tests_collected": num,
        "per_file": per_file,
        # 完整 nodeid 清单：S3 调度层选测试时直接用，也用于参数化感知的断言密度
        "node_ids": ids,
        "collect_seconds": round(dt, 2),
        "stderr_tail": err.strip().splitlines()[-3:] if err.strip() else [],
    }


# --------------------------------------------------------------------------
# 3b. 全量测试耗时（不带插桩）
# --------------------------------------------------------------------------

def time_full_suite(subj: Subject, timeout: int = 3600) -> dict:
    args = [sys.executable, "-m", "pytest", *pytest_args(subj)]
    rc, out, err, dt = _run_subprocess(args, subj.root, timeout)
    tail = [ln.strip() for ln in out.splitlines() if ln.strip()][-1:] or [""]
    return {
        "returncode": rc,
        "wall_seconds": round(dt, 2),
        "summary": tail[0],
        "stdout_tail": [ln.strip() for ln in out.splitlines() if ln.strip()][-5:],
        "stderr_tail": err.strip().splitlines()[-5:] if err.strip() else [],
    }


# --------------------------------------------------------------------------
# 4. 覆盖率（聚合 + 可选 per-test 上下文）
# --------------------------------------------------------------------------

def run_coverage(
    subj: Subject,
    *,
    per_test: bool = False,
    timeout: int = 3600,
) -> dict:
    """在 coverage 插桩下运行整个测试套件，返回覆盖率与（可选）per-test 索引。

    注意：pytest 在本进程内运行，因此 coverage 能直接拿到 CoverageData 对象，
    从而使用 contexts_by_lineno() 取得"每一行被哪些测试覆盖"——这正是 S3 调度层
    需要的数据结构。

    关于 per-test 上下文：不使用 coverage 内置的 dynamic_context=test_function，
    因为它靠"函数名以 test 开头"启发式命名，得到的上下文既不全（实测只识别出
    10/38 个）也没有参数化信息。这里改为由 pytest 插件在每条用例开始前调用
    cov.switch_context(nodeid)，上下文名即精确的 pytest nodeid。
    """
    import pytest
    from coverage import Coverage

    class _ContextSwitcher:
        """把 coverage 的上下文切到当前用例的 nodeid。"""

        def __init__(self, cov: Coverage) -> None:
            self.cov = cov

        def pytest_runtest_logstart(self, nodeid, location):  # noqa: D102 - pytest hook
            self.cov.switch_context(nodeid)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # 注意：这里刻意不使用 cov.erase()。erase() 会删除已存在的 .coverage 文件，
    # 在受限沙箱里删除操作会被拦截；改为每次生成唯一文件名，做到"只写不删"。
    data_file = DATA_DIR / f".coverage-{subj.name}-{int(time.time() * 1000)}"

    cwd = Path.cwd()
    os.chdir(subj.root)
    sys.path.insert(0, str(subj.root))
    try:
        cov = Coverage(
            source=[str(subj.package_dir)],
            data_file=str(data_file),
            config_file=False,
        )
        if per_test:
            switcher = _ContextSwitcher(cov)

        collector = _OutcomeCollector()
        cov.start()
        t0 = time.perf_counter()
        try:
            rc = pytest.main(
                pytest_args(subj),
                plugins=[collector] + ([switcher] if per_test else []),
            )
        finally:
            wall = time.perf_counter() - t0
            cov.stop()
            cov.save()

        counts: dict[str, int] = {}
        for outcome in collector.outcomes.values():
            counts[outcome] = counts.get(outcome, 0) + 1

        data = cov.get_data()
        pkg_root = subj.package_dir.resolve()
        files: dict[str, dict] = {}
        totals = {"num_statements": 0, "covered": 0, "missing": 0}

        for f in data.measured_files():
            fp = Path(f).resolve()
            try:
                rel = str(fp.relative_to(pkg_root))
            except ValueError:
                rel = str(fp)
            _, statements, _, missing, _ = cov.analysis2(str(fp))
            ns, nm = len(statements), len(missing)
            files[rel] = {
                "num_statements": ns,
                "missing": nm,
                "covered": ns - nm,
                "percent_covered": round(100.0 * (ns - nm) / ns, 2) if ns else 0.0,
            }
            totals["num_statements"] += ns
            totals["missing"] += nm
            totals["covered"] += ns - nm

        totals["percent_covered"] = (
            round(100.0 * totals["covered"] / totals["num_statements"], 2)
            if totals["num_statements"]
            else 0.0
        )

        result = {
            "returncode": int(rc),
            "wall_seconds_with_coverage": round(wall, 2),
            "outcomes": counts,
            "totals": totals,
            "files": files,
            "contexts_enabled": bool(per_test),
        }

        if per_test:
            result["per_test_index"] = _build_per_test_index(cov, data, pkg_root)

        return result
    finally:
        os.chdir(cwd)
        if sys.path and sys.path[0] == str(subj.root):
            sys.path.pop(0)


def _build_per_test_index(cov, data, pkg_root: Path) -> dict[str, dict[str, int]]:
    """把 coverage 的行级上下文反转成「模块 -> {测试 nodeid: 覆盖行数}」。

    上下文名由 pytest 插件写入，形如 tests/test_foo.py::test_bar[param]。
    """
    index: dict[str, dict[str, int]] = {}

    for f in data.measured_files():
        fp = Path(f).resolve()
        try:
            rel = str(fp.relative_to(pkg_root))
        except ValueError:
            continue
        try:
            ctx_by_line = data.contexts_by_lineno(str(fp)) or {}
        except Exception:  # pragma: no cover - coverage 版本差异
            ctx_by_line = {}
        bucket: dict[str, int] = {}
        for _lineno, contexts in ctx_by_line.items():
            for ctx in contexts:
                if not ctx:
                    continue
                key = _norm(ctx)
                bucket[key] = bucket.get(key, 0) + 1
        if bucket:
            index[rel] = bucket

    return index


# --------------------------------------------------------------------------
# 组装
# --------------------------------------------------------------------------

def allocate_asserts(
    per_test_index: dict[str, dict[str, int]],
    test_funcs: list[dict],
    root: Path,
) -> dict:
    """把每个测试的断言数分配到它覆盖的模块上，得到模块级断言强度。

    为什么不做简单求和：像 _compat.py 这种被 979 个测试"顺带执行一行"的模块，
    若把那些测试的全部断言都算到它头上，断言密度会被放大成 5355/100NCLOC 这种
    荒谬值。改用加权分配——一个测试的断言按"它在本模块执行了多少行 / 它总共
    执行了多少行"的比例摊到各模块：

        weighted_asserts(m) = Σ_t  asserts(t) × lines(t,m) / lines(t,*)

    这样"顺带覆盖"自然只摊到极小份额，而真正针对该模块的测试贡献主要份额。

    对齐规则：coverage 的 context 名是精确的 pytest nodeid（形如
    tests/test_x.py::test_y[param]），静态侧不含参数化后缀，按去掉 "[...]"
    后的名字匹配；匹配不上的（如 hypothesis 生成的用例）用所属测试文件的
    "每用例平均断言数"兜底，并如实计入 fallback 比例。
    """
    root = Path(root).resolve()

    def _rel(p: str) -> str:
        try:
            return Path(p).resolve().relative_to(root).as_posix()
        except ValueError:
            return Path(p).name

    lookup: dict[str, int] = {}
    per_file: dict[str, list[int]] = {}
    for tf in test_funcs:
        key = _rel(tf["file"]) + "::" + tf["qualname"]
        lookup[key] = tf["n_asserts"]
        per_file.setdefault(_rel(tf["file"]), []).append(tf["n_asserts"])
    file_avg = {k: (sum(v) / len(v) if v else 0.0) for k, v in per_file.items()}

    def asserts_of(ctx: str) -> tuple[float, bool]:
        key = ctx.split("[")[0]
        if key in lookup:
            return float(lookup[key]), True
        fname = key.split("::")[0]
        return (file_avg[fname], False) if fname in file_avg else (0.0, False)

    # 每个测试在被测包内总共执行了多少行（加权分配的分母）
    total_lines: dict[str, int] = {}
    for _module, tests in per_test_index.items():
        for ctx, n in tests.items():
            total_lines[ctx] = total_lines.get(ctx, 0) + n

    out: dict[str, dict] = {}
    n_exact = n_fallback = n_unmatched = 0
    for module, tests in per_test_index.items():
        weighted = 0.0
        raw = 0.0
        for ctx, lines in tests.items():
            a, exact = asserts_of(ctx)
            if exact:
                n_exact += 1
            elif a > 0:
                n_fallback += 1
            else:
                n_unmatched += 1
            raw += a
            denom = total_lines.get(ctx, 0)
            if denom:
                weighted += a * lines / denom
        out[module] = {
            "n_tests": len(tests),
            "n_asserts": int(round(weighted)),
            "n_asserts_raw": int(round(raw)),
            "estimated": n_fallback + n_unmatched > 0,
        }
    return {
        "modules": out,
        "context_match": {
            "exact": n_exact,
            "file_fallback": n_fallback,
            "unmatched": n_unmatched,
            "exact_rate": round(100.0 * n_exact / max(n_exact + n_fallback + n_unmatched, 1), 2),
        },
    }


def enrich_with_asserts(
    subj: Subject,
    per_test_index: dict[str, dict[str, int]],
    test_funcs: list[dict],
) -> dict:
    """见 allocate_asserts()，这里只是绑定 Subject 的便捷入口。"""
    return allocate_asserts(per_test_index, test_funcs, subj.root)


def collect(subj: Subject, *, per_test: bool = False, skip_timing: bool = False) -> dict:
    """完整采集一个被测项目的基线。"""
    static = collect_static(subj)
    ids = collect_test_ids(subj)

    # 参数化感知的断言密度：分母用运行时用例数，与报告里的「用例数」列同口径
    runtime_asserts = metrics.per_runtime_test_asserts(
        static["tests_static"]["funcs"], ids.get("node_ids", []), subj.root
    )
    static["tests_static"]["totals"].update(runtime_asserts)

    timing = {} if skip_timing else time_full_suite(subj)
    cov = run_coverage(subj, per_test=per_test)

    result = {
        "subject": {
            "name": subj.name,
            "repo": subj.repo,
            "tag": subj.tag,
            "package": subj.package,
            "package_dir": str(subj.package_dir),
            "test_paths": list(subj.test_paths),
            "deselected": list(subj.deselect),
            "root": str(subj.root),
            "python": sys.version.split()[0],
            "platform": sys.platform,
        },
        "static": static,
        "collection": ids,
        "timing": timing,
        "coverage": cov,
    }

    if per_test:
        result["assert_allocation"] = enrich_with_asserts(
            subj, cov.get("per_test_index", {}), static["tests_static"]["funcs"]
        )
    return result
