"""逐个串行验证指定的 timeout 变异体：单独跑能否在限时内完成。

用来区分「真死循环」与「批量跑工具/并行环境出问题」：
- 单独跑能在限时内完成   -> 批量跑的工具或并行环境有问题
- 单独跑也跑满超时       -> 是真死循环，timeout 是合法结果
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
    ap.add_argument("--subject", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--limit", type=int, default=6, help="验证前 N 个 timeout")
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

    print(f"共 {len(tmo)} 个 timeout，单独串行验证前 {args.limit} 个"
          f"（超时阈值 {args.timeout:.0f}s）\n")

    n_timeout = 0
    for r in tmo[: args.limit]:
        m = ms.get(r["mutant_id"])
        if m is None:
            print(f"  {r['mutant_id']} 找不到对应 Mutant")
            continue
        t0 = time.time()
        res = runner.run_one(subj, m, ws, original_source=orig,
                             timeout_s=args.timeout)
        el = time.time() - t0
        if res.status == "timeout":
            n_timeout += 1
        print(f"  L{m.line:<5} {m.operator:<4} {m.description[:44]:<46} "
              f"{res.status:<10} {el:.1f}s")

    print(f"\n其中 {n_timeout}/{min(args.limit, len(tmo))} 个单独跑仍超时")
    if n_timeout == 0:
        print("-> 单独跑都能完成：不是死循环，应排查批量/并行环境")
    else:
        print("-> 单独跑也超时：是真实死循环，timeout 属于合法结果")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
