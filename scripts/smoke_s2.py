"""S2 冒烟测试：先跑对照，再小批量跑变异体，确认判定链路正确后才放大。

用法：
    python scripts/smoke_s2.py marshmallow utils 10
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mutloop.mutator import enumerate_mutants, count_by_operator  # noqa: E402
from mutloop.runner import (  # noqa: E402
    STATUS_KILLED,
    default_timeout,
    prepare_workspace,
    run_all,
    summarize,
)
from mutloop.subjects import DATA_DIR, get  # noqa: E402


def main(argv: list[str]) -> int:
    name = argv[0] if argv else "marshmallow"
    module = argv[1] if len(argv) > 1 else "utils.py"
    limit = int(argv[2]) if len(argv) > 2 else 10

    subj = get(name)
    pkg = subj.package_dir
    target = pkg / module
    if not target.exists():
        print(f"找不到 {target}")
        return 1

    baseline = json_load(name)
    wall = baseline["timing"]["wall_seconds"]
    timeout = default_timeout(wall)
    print(f"=== {name} / {module}　基线全量 {wall}s，超时阈值 {timeout:.0f}s ===")

    ws = prepare_workspace(subj, DATA_DIR / "_ws")
    print(f"工作副本: {ws}")

    # --- 对照：不改任何代码，用工作副本跑一遍，必须全绿 ---
    import subprocess
    env = None
    import os
    env = dict(os.environ, PYTHONPATH=str(ws.resolve()))
    args = [sys.executable, "-m", "pytest", *subj.test_paths, "-p", "no:cacheprovider",
            "--no-header", "-q", "--tb=no"]
    for d in subj.deselect:
        args += ["--deselect", d]
    r = subprocess.run(args, cwd=str(subj.root), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    print(f"\n[对照] 未变异的工作副本 → returncode={r.returncode}")
    print("   ", (r.stdout or "").strip().splitlines()[-1][:100])
    if r.returncode != 0:
        print("对照未通过：工作副本与真源码不等价，先修这个再继续")
        return 1

    # --- 枚举变异体 ---
    mutants = enumerate_mutants(target)
    counts = count_by_operator(mutants)
    print(f"\n[枚举] {module} 共 {len(mutants)} 个变异体：{counts}")

    sample = mutants[:limit]
    print(f"\n[试跑] 前 {len(sample)} 个变异体（全量套件，naive 跑法）")
    results = run_all(subj, sample, ws, timeout_s=timeout, progress_every=5)
    summary = summarize(results)
    print()
    for res in results:
        extra = ""
        if res.failed_tests:
            extra = f"  首个失败 {res.failed_tests[0].split('::')[-1][:32]}"
        print(f"  {res.mutant_id} L{res.line:<4} {res.operator:<5} "
              f"{res.status:<14} {res.duration_s:>6.2f}s{extra}")
    print(f"\n[汇总] {summary}")
    return 0


def json_load(name: str) -> dict:
    import json
    return json.loads((DATA_DIR / "baseline" / f"{name}.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
