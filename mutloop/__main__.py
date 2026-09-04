"""MutLoop 命令行入口。

    python -m mutloop list                      # 列出被测项目
    python -m mutloop baseline --subject click  # 采集单个项目基线
    python -m mutloop run --target <file>       # 变异分析（S2 实现）
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from . import baseline as baseline_mod
from .mutator import OPERATOR_LABELS
from .subjects import CANDIDATES, SUBJECTS, get


def _cmd_list(args: argparse.Namespace) -> int:
    print(f"{'项目':<14}{'仓库':<34}{'锁定 tag':<14}{'源码包':<18}就绪")
    print("-" * 92)
    for name, s in SUBJECTS.items():
        flag = "✓" if s.exists() else "✗(未 clone)"
        star = "*" if name in CANDIDATES else " "
        print(f"{star}{name:<13}{s.repo:<34}{s.tag:<14}{s.package:<18}{flag}")
    print("\n* = 正式候选；requests 仅用于论证「为何排除网络依赖型项目」")
    return 0


def _cmd_collect_one(args: argparse.Namespace) -> int:
    """采集单个被测项目并落盘（由 baseline 在子进程中调用）。

    存在意义：pytest.main() 在同一进程内跑第二个项目时会受前一次的
    sys.modules / conftest 残留影响（实测 marshmallow 之后跑 dateutil 直接
    返回 usage error 4）。因此每个项目必须独占一个进程——S3 的并发调度也
    依赖同样的隔离前提。
    """
    from .subjects import DATA_DIR

    subj = get(args.subject)
    out_dir = Path(args.out) if args.out else DATA_DIR / "baseline"
    out_dir.mkdir(parents=True, exist_ok=True)
    result = baseline_mod.collect(subj, per_test=args.per_test, skip_timing=args.skip_timing)
    out = out_dir / f"{subj.name}.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    cov = result["coverage"]["totals"]
    print(
        f"    语句覆盖 {cov['percent_covered']}%  "
        f"({cov['covered']}/{cov['num_statements']})  "
        f"用例 {result['collection']['num_tests_collected']}  "
        f"耗时 {result.get('timing', {}).get('wall_seconds', 'n/a')}s"
    )
    print(f"    -> {out}")
    return 0


def _cmd_baseline(args: argparse.Namespace) -> int:
    from .subjects import DATA_DIR

    names = args.subject or list(CANDIDATES)
    out_dir = Path(args.out) if args.out else DATA_DIR / "baseline"
    out_dir.mkdir(parents=True, exist_ok=True)

    for name in names:
        subj = get(name)
        if not subj.exists():
            print(f"[跳过] {name}: 源码目录不存在 -> {subj.package_dir}")
            continue
        print(f"\n>>> 采集 {name} @ {subj.tag}", flush=True)
        cmd = [
            sys.executable,
            "-m",
            "mutloop",
            "_collect-one",
            "--subject",
            name,
            "--out",
            str(out_dir),
        ]
        if args.per_test:
            cmd.append("--per-test")
        if args.skip_timing:
            cmd.append("--skip-timing")
        proc = subprocess.run(
            cmd,
            cwd=str(Path(__file__).resolve().parents[1]),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        tail = [
            ln
            for ln in (proc.stdout or "").splitlines()
            if ln.startswith("    语句覆盖") or ln.startswith("    ->")
        ]
        if tail:
            print("\n".join(tail))
        else:
            print("    无覆盖率输出，原始日志尾部：")
            print("\n".join((proc.stdout or "").splitlines()[-6:]))
            print("\n".join((proc.stderr or "").splitlines()[-6:]))
    return 0


def _cmd_recollect(args: argparse.Namespace) -> int:
    """只重跑「用例收集」这一步，并补算参数化感知的断言密度。

    用于给已有基线数据补 nodeid 清单，或修正断言密度的口径——
    不必重跑整轮测试（那既慢又会引入计时噪声）。其余字段原样保留。
    """
    from mutloop import metrics
    from mutloop.subjects import DATA_DIR

    names = args.subject or list(CANDIDATES)
    out_dir = Path(args.out) if args.out else DATA_DIR / "baseline"

    for name in names:
        subj = get(name)
        p = out_dir / f"{name}.json"
        if not p.exists():
            print(f"[跳过] {name}: 没有基线数据 {p}")
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        coll = baseline_mod.collect_test_ids(subj)
        data["collection"] = coll
        totals = data["static"]["tests_static"]["totals"]
        # 老数据的字段名是 asserts_per_test，这里统一补成 asserts_per_static_func
        if totals.get("asserts_per_static_func") is None:
            n = totals.get("num_test_funcs_static") or 0
            totals["asserts_per_static_func"] = (
                round(totals.get("num_asserts", 0) / n, 2) if n else 0.0
            )
            totals["asserts_per_test"] = totals["asserts_per_static_func"]
        runtime = metrics.per_runtime_test_asserts(
            data["static"]["tests_static"]["funcs"], coll.get("node_ids", []), subj.root
        )
        totals.update(runtime)
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        t = data["static"]["tests_static"]["totals"]
        print(
            f"{name:<12} 用例 {coll['num_tests_collected']:>5}  "
            f"nodeid {len(coll.get('node_ids', [])):>5}  "
            f"断言/用例(运行时) {t.get('asserts_per_runtime_test')}  "
            f"断言/测试函数(静态) {t.get('asserts_per_static_func')}  "
            f"匹配率 {t.get('match_rate')}%"
        )
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    """S2：对单个文件做变异分析。

    流程：对照（未变异必须全绿）→ 枚举变异体 → 逐个跑全量套件 → 判定状态 → 落盘。
    S2 刻意用 naive 跑法（每个变异体跑全量套件）作为 ground truth，
    覆盖率导向调度留给 S3，届时要拿这份结果验证"少跑测试没漏掉 kill"。
    """
    from mutloop import runner
    from mutloop.mutator import (
        DEFAULT_OPERATORS,
        count_by_operator,
        enumerate_mutants,
    )
    from mutloop.subjects import DATA_DIR

    subj = get(args.subject)
    target = Path(args.target)
    if not target.exists():
        target = subj.package_dir / args.target
    if not target.exists():
        print(f"找不到目标文件: {args.target}")
        return 1

    baseline = json.loads(
        (DATA_DIR / "baseline" / f"{subj.name}.json").read_text(encoding="utf-8")
    )
    wall = baseline["timing"]["wall_seconds"]
    timeout = runner.default_timeout(wall)
    print(f">>> 变异分析 {subj.name} / {target.name}　基线 {wall}s，超时 {timeout:.0f}s")

    # 每个项目用**独立**的工作副本目录。
    # 若共用一个 data/_ws，跑 click 时 PYTHONPATH 下会同时存在 attr / marshmallow 等
    # 其他项目的副本——一旦其他项目有未还原的残留变异，就会污染本次结果。
    ws = runner.prepare_workspace(subj, DATA_DIR / "_ws" / subj.name)
    rc, tail = runner.control_run(subj, ws, timeout)
    if rc != 0:
        print(f"[中止] 对照未通过（returncode={rc}）：工作副本与真源码不等价")
        print(f"       {tail}")
        return 1
    print(f"    对照通过：{tail}")

    operators = tuple(args.operator) if args.operator else DEFAULT_OPERATORS
    mutants = enumerate_mutants(target, root=subj.root, operators=operators)
    if args.limit:
        mutants = mutants[: args.limit]
    counts = count_by_operator(mutants)
    print(f"    变异体 {len(mutants)} 个：{counts}")

    t0 = time.perf_counter()
    results = runner.run_all(subj, mutants, ws, timeout_s=timeout,
                             progress_every=args.progress_every)
    summary = runner.summarize(results)
    summary["wall_seconds"] = round(time.perf_counter() - t0, 2)

    out = Path(args.out) if args.out else (
        DATA_DIR / "s2" / f"{subj.name}-{target.stem}.json"
    )
    runner.write_results(out, {
        "subject": subj.name,
        "tag": subj.tag,
        "target": target.name,
        "operators": list(operators),
        "baseline_wall_seconds": wall,
        "timeout_seconds": timeout,
        "execution": "naive-full-suite",
        "summary": summary,
        "results": [r.as_record() for r in results],
    })
    print(f"\n{json.dumps(summary, ensure_ascii=False, indent=2)}")
    print(f"\n结果已写入: {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mutloop", description="MutLoop 变异测试平台")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="列出被测项目").set_defaults(func=_cmd_list)

    b = sub.add_parser("baseline", help="采集 S1 基线数据")
    b.add_argument("--subject", action="append", help="被测项目名，可重复；默认全部候选")
    b.add_argument("--per-test", action="store_true", help="采集 per-test 覆盖率上下文（较慢）")
    b.add_argument("--skip-timing", action="store_true", help="跳过全量测试计时")
    b.add_argument("--out", help="输出目录，默认 data/baseline")
    b.set_defaults(func=_cmd_baseline)

    one = sub.add_parser("_collect-one", help=argparse.SUPPRESS)
    one.add_argument("--subject", required=True)
    one.add_argument("--per-test", action="store_true")
    one.add_argument("--skip-timing", action="store_true")
    one.add_argument("--out")
    one.set_defaults(func=_cmd_collect_one)

    rc = sub.add_parser("recollect", help="只重跑用例收集，补算参数化感知的断言密度")
    rc.add_argument("--subject", action="append", help="被测项目名，可重复；默认全部候选")
    rc.add_argument("--out", help="基线目录，默认 data/baseline")
    rc.set_defaults(func=_cmd_recollect)

    r = sub.add_parser("run", help="变异分析（S2）")
    r.add_argument("--subject", default="marshmallow")
    r.add_argument("--target", required=True, help="目标源码文件（文件名或路径）")
    r.add_argument("--operator", action="append", choices=list(OPERATOR_LABELS),
                   help="限定算子类别，可重复；默认全部 5 类")
    r.add_argument("--limit", type=int, help="只跑前 N 个变异体（调试用）")
    r.add_argument("--out", help="输出 JSON 路径")
    r.add_argument("--progress-every", type=int, default=25)
    r.set_defaults(func=_cmd_run)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
