"""把被「并行减速」误判成 timeout 的变异体，串行重跑修正。

背景
----
dateutil/rrule.py 并行跑出 149 个 timeout（10.2%），体检报警。
但串行验证 4 个样本都只要 ~9s 就完成（survived）——它们不是真死循环，
是 hypothesis 密集测试在并行 CPU 争抢下被拖过 60s 阈值。

为什么只重跑 timeout、不重跑全部：
- 全部 1462 个并行重跑要 3.5 小时，且还会再超时
- 串行重跑 149 个只要 ~22 分钟，且串行 ~9s 远低于 30s 阈值，结果可信

用法
----
    python scripts/fix_timeout_rerun.py --subject dateutil --target rrule.py
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mutloop import mutator, runner  # noqa: E402
from mutloop.subjects import DATA_DIR, get  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", required=True)
    ap.add_argument("--target", required=True)
    args = ap.parse_args()

    subj = get(args.subject)
    target = subj.package_dir / args.target
    out = DATA_DIR / "s2" / f"{subj.name}-{target.stem}.json"

    data = json.loads(out.read_text(encoding="utf-8"))
    results = data["results"]
    tmo = [r for r in results if r["status"] == "timeout"]
    if not tmo:
        print("没有 timeout 需要修正")
        return 0

    print(f"{subj.name}/{target.name}: 需串行重跑 {len(tmo)} 个 timeout 变异体")

    # 重建 Mutant 映射
    mutants = {m.mutant_id: m for m in mutator.enumerate_mutants(target)}
    ws = runner.prepare_workspace(subj, DATA_DIR / "_ws" / f"{subj.name}_fix")
    originals: dict[str, str] = {}
    for m in mutants.values():
        if m.file not in originals:
            originals[m.file] = (
                subj.package_dir / runner.in_package_path(subj, m.file)
            ).read_text(encoding="utf-8")

    # 串行重跑，用串行阈值（default_timeout），不乘 workers 系数
    from mutloop.runner import default_timeout
    wall = data.get("baseline_wall_seconds", 7.5)
    timeout = default_timeout(wall)

    fixed = 0
    t0 = time.time()
    for i, r in enumerate(tmo, 1):
        m = mutants.get(r["mutant_id"])
        if m is None:
            print(f"  [跳过] 找不到变异体 {r['mutant_id']}")
            continue
        new = runner.run_one(subj, m, ws, original_source=originals[m.file],
                             timeout_s=timeout)
        old_status = r["status"]
        r["status"] = new.status
        r["returncode"] = new.returncode
        r["duration_s"] = new.duration_s
        r["first_error_type"] = new.first_error_type
        r["error_origin"] = new.error_origin
        r["first_error_line"] = new.first_error_line
        r["kill_class"] = new.kill_class
        r["retries"] = new.retries
        fixed += 1
        if i % 20 == 0:
            print(f"  {i}/{len(tmo)}  已用 {time.time()-t0:.0f}s", flush=True)

    # 重算 summary
    from mutloop.runner import MutantResult, summarize
    objs = [MutantResult(**r) for r in results]
    data["summary"] = summarize(objs)
    data["fix_note"] = f"串行重跑修正 {fixed} 个 timeout（并行减速误判），"
    data["execution"] = data.get("execution", "") + " + timeout-fix(serial)"

    runner.write_results(out, data)
    s = data["summary"]
    print(f"\n修正完成 {fixed} 个，耗时 {time.time()-t0:.0f}s")
    print(f"  状态: {s['by_status']}")
    print(f"  变异分数  传统 {s['mutation_score']}%　严格 {s.get('strict_mutation_score')}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
