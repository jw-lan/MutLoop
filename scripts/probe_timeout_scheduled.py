"""验证：死循环变异体在「调度后只跑覆盖测试」时还会不会超时。

这决定了后续能否安全地跳过已知 timeout 变异体：
- 只跑覆盖测试仍超时 -> 死循环是代码属性，可安全跳过/复用结果
- 只跑覆盖测试就不超时 -> 结果与测试集有关，跳过会有风险，必须实测
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
from mutloop.subjects import get  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", default="dateutil")
    ap.add_argument("--target", default="rrule.py")
    ap.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args()

    subj = get(args.subject)
    target = subj.package_dir / args.target
    out = ROOT / "data" / "s2" / f"{subj.name}-{target.stem}.json"
    data = json.loads(out.read_text(encoding="utf-8"))
    tmo = [r for r in data["results"] if r["status"] == "timeout"]
    if not tmo:
        print("没有 timeout 变异体")
        return 0

    ms = {m.mutant_id: m for m in mutator.enumerate_mutants(target)}
    ws = runner.prepare_workspace(subj, ROOT / "data" / "_ws" / f"{subj.name}_probe")
    orig = (subj.package_dir / runner.in_package_path(subj, target.name)
            ).read_text(encoding="utf-8")

    picks = tmo[:3]
    suites = {
        "全量 tests/": None,
        "仅 test_rrule.py": ["tests/test_rrule.py"],
        "仅 test_easter.py": ["tests/test_easter.py"],
    }

    for r in picks:
        m = ms.get(r["mutant_id"])
        if not m:
            continue
        print(f"\n=== {r['mutant_id']}  L{m.line} {m.operator}  {m.description[:44]} ===")
        for label, node_ids in suites.items():
            t0 = time.time()
            res = runner.run_one(subj, m, ws, original_source=orig,
                                 timeout_s=args.timeout, node_ids=node_ids)
            el = time.time() - t0
            flag = "   <- 死循环" if res.status == "timeout" else ""
            print(f"  {label:<22} {res.status:<10} {el:>6.1f}s{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())