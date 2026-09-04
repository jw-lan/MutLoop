"""S3 决策用探针：量化「覆盖率导向调度」的收益与风险。

为什么需要这个脚本
------------------
S3 要在两种调度策略里选一个。光靠推理不够，两个数字必须实测：

  1. 收益：每个变异体从「跑全套件」缩到「只跑覆盖该行的测试」，能省多少时间？
  2. 风险：杀死变异体的那些测试，是不是真的覆盖了变异所在行？
     如果某个变异体是被「没覆盖它的测试」杀死的，那么覆盖率导向调度
     根本不会选到那个测试 → 本来 killed 会误判成 survived（**漏杀**）。

第 2 点就是覆盖率导向的**可靠性代价**，文献里叫 false survivor。
本脚本用 S2 已有的 5349 个变异体结果（带 failed_tests）反查覆盖率索引，
直接数出漏杀率——不需要重跑任何变异体，只需要重采一次覆盖率（约 110 秒）。

产出：data/s3/coverage_probe.json
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mutloop.baseline import PYTEST_BASE_ARGS, _OutcomeCollector  # noqa: E402
from mutloop.runner import in_package_path  # noqa: E402
from mutloop.subjects import DATA_DIR, get  # noqa: E402


def collect_line_index(name: str, root: Path | None = None) -> tuple[dict[str, dict[int, list[str]]], float]:
    """重采一次带 per-test 上下文的覆盖率，返回 {包内相对路径: {行号: [nodeid]}}。

    root 非空时在被测项目的历史版本（S6 的 git worktree）上采集。
    """
    import os

    import pytest
    from coverage import Coverage

    subj = get(name)
    if root is not None:
        from mutloop.subjects import with_root
        subj = with_root(subj, root)

    class _Switcher:
        """把 coverage 上下文切到当前用例的 nodeid。"""

        def __init__(self, cov: Coverage) -> None:
            self.cov = cov

        def pytest_runtest_logstart(self, nodeid, location):  # noqa: D102
            self.cov.switch_context(nodeid)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    data_file = DATA_DIR / f".coverage-probe-{name}-{int(time.time() * 1000)}"
    cwd = Path.cwd()
    os.chdir(subj.root)
    # src 布局下 `import <top_module>` 需要 <root>/src 在 sys.path；
    # 对 worktree（S6）尤其关键——否则 import 走的是 editable install 的 subjects 版本，
    # coverage 采集不到 worktree 的代码（表现为 "No data was collected"）。
    sys.path.insert(0, str(subj.package_dir.parent))
    sys.path.insert(0, str(subj.root))
    try:
        cov = Coverage(
            source=[str(subj.package_dir)],
            data_file=str(data_file),
            config_file=False,
        )
        switcher = _Switcher(cov)
        args = [*subj.test_paths, *PYTEST_BASE_ARGS]
        for d in subj.deselect:
            args += ["--deselect", d]

        cov.start()
        t0 = time.perf_counter()
        try:
            pytest.main(args, plugins=[_OutcomeCollector(), switcher])
        finally:
            wall = time.perf_counter() - t0
            cov.stop()
            cov.save()

        data = cov.get_data()
        pkg_root = subj.package_dir.resolve()
        index: dict[str, dict[int, list[str]]] = {}
        for f in data.measured_files():
            fp = Path(f).resolve()
            try:
                rel = str(fp.relative_to(pkg_root)).replace("\\", "/")
            except ValueError:
                continue
            try:
                ctx_by_line = data.contexts_by_lineno(str(fp)) or {}
            except Exception:  # pragma: no cover - coverage 版本差异
                ctx_by_line = {}
            lines: dict[int, list[str]] = {}
            for lineno, contexts in ctx_by_line.items():
                ts = [c for c in contexts if c]
                if ts:
                    lines[int(lineno)] = ts
            if lines:
                index[rel] = lines
        return index, wall
    finally:
        os.chdir(cwd)
        if sys.path and sys.path[0] == str(subj.root):
            sys.path.pop(0)


PROJECTS = ["marshmallow", "dateutil", "jinja", "attrs", "click"]


def cmd_collect(name: str) -> int:
    """只采集一个项目的行级索引，写入独立文件。

    为什么必须一个项目一个进程：pytest 在本进程内跑（coverage 需要直接拿到
    CoverageData），而连续跑多个项目会互相污染 sys.modules / sys.path，
    实测 marshmallow 之后跑 dateutil 会出现 24 个收集错误、索引直接为空。
    """
    idx, wall = collect_line_index(name)
    out = DATA_DIR / "s3" / f"line_index_{name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(idx), encoding="utf-8")
    n_lines = sum(len(v) for v in idx.values())
    print(f"  {name:<12} 覆盖采集 {wall:6.1f}s  文件 {len(idx):3d}  行 {n_lines:6d}  -> {out.name}")
    return 0 if n_lines else 1


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("project", nargs="?", help="只采集该项目（省略则由父进程逐个 subprocess 调用）")
    args = ap.parse_args()

    out_dir = DATA_DIR / "s3"
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.project:
        return cmd_collect(args.project)

    # ---- 1. 采集行级覆盖率索引（每项目一个子进程，避免状态污染） --------
    import subprocess

    for name in PROJECTS:
        if (out_dir / f"line_index_{name}.json").exists():
            print(f"  {name:<12} 已有索引，跳过")
            continue
        rc = subprocess.call([sys.executable, str(Path(__file__).resolve()), name],
                             cwd=str(ROOT))
        if rc != 0:
            print(f"  {name:<12} [采集失败 rc={rc}]")

    line_index: dict[str, dict[int, list[str]]] = {}
    for f in out_dir.glob("line_index_*.json"):
        name = f.stem[len("line_index_"):]
        for rel, lines in json.loads(f.read_text(encoding="utf-8")).items():
            line_index[f"{name}:{rel}"] = {int(l): v for l, v in lines.items()}
    print(f"索引合计 {len(line_index)} 个文件\n")

    # ---- 2. 与 S2 变异体结果对账 ----------------------------------------
    stats = Counter()
    per_module: dict[str, dict] = {}
    missed_examples: list[dict] = []

    for p in sorted((DATA_DIR / "s2").glob("*.json")):
        if p.name.startswith("_") or p.name.startswith("marshmallow-utils-rerun"):
            continue
        j = json.loads(p.read_text(encoding="utf-8"))
        subj_name, target = j["subject"], j["target"]
        key = f"{subj_name}/{target}"
        st = Counter()
        for r in j["results"]:
            status = "killed" if r["status"] == "timeout" else r["status"]
            if status != "killed":
                continue
            st["killed"] += 1
            ft = r.get("failed_tests")
            if not ft:
                st["no_failed_tests_record"] += 1
                continue
            # 变异体所在行 -> 包内相对路径
            subj = get(subj_name)
            rel = str(in_package_path(subj, r["file"])).replace("\\", "/")
            lines = line_index.get(f"{subj_name}:{rel}")
            if lines is None:
                st["file_not_in_index"] += 1
                continue
            covering = set(lines.get(r["line"], []))
            if not covering:
                st["line_not_covered"] += 1
                continue
            st["line_covered"] += 1
            # 归一化：nodeid 在 coverage 里被 _norm 过（去掉前导 ./），failed_tests
            # 来自 pytest -q 输出，形如 tests/test_x.py::test_y
            norm_ft = {t.replace("\\", "/").lstrip("./") for t in ft}
            if covering & norm_ft:
                st["killed_by_covering_test"] += 1
            else:
                st["killed_by_noncovering_test"] += 1
                if len(missed_examples) < 15:
                    missed_examples.append({
                        "module": key, "mutant_id": r["mutant_id"],
                        "line": r["line"], "operator": r["operator"],
                        "description": r["description"],
                        "covering_tests": len(covering),
                        "failed_tests_sample": sorted(norm_ft)[:3],
                    })
        per_module[key] = dict(st)
        stats.update(st)

    print("\n" + "=" * 72)
    print("覆盖率导向调度的可靠性代价（用 S2 的 killed 变异体反查）")
    print("=" * 72)
    tot = stats["killed_by_covering_test"] + stats["killed_by_noncovering_test"]
    if tot:
        print(f"可判定的 killed 变异体:            {tot}")
        print(f"  杀死它的测试覆盖了变异行:        {stats['killed_by_covering_test']} "
              f"({100 * stats['killed_by_covering_test'] / tot:.1f}%)  → 调度选得到")
        print(f"  杀死它的测试没覆盖变异行:        {stats['killed_by_noncovering_test']} "
              f"({100 * stats['killed_by_noncovering_test'] / tot:.1f}%)  → **漏杀**")
    print(f"  辅助：无 failed_tests 记录 {stats['no_failed_tests_record']}，"
          f"文件不在索引 {stats['file_not_in_index']}，"
          f"该行无覆盖数据 {stats['line_not_covered']}")

    print("\n逐模块:")
    for k, v in per_module.items():
        t = v.get("killed_by_covering_test", 0) + v.get("killed_by_noncovering_test", 0)
        if t:
            print(f"  {k:<28} 漏杀 {v.get('killed_by_noncovering_test', 0):3d}/{t:3d} "
                  f"= {100 * v.get('killed_by_noncovering_test', 0) / t:5.1f}%")

    out = out_dir / "coverage_probe.json"
    out.write_text(json.dumps(
        {"totals": dict(stats), "per_module": per_module,
         "missed_examples": missed_examples},
        indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n结果已写入: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
