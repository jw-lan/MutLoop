"""S5 三臂对比：并排输出三个臂的四个质量指标，并按模块/算子做分解对比。

用法
----
    python scripts/s5_arm_compare.py
    python scripts/s5_arm_compare.py --dir data/s5 --prefix arm
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ARMS = [("directed", "臂1 定向"), ("coverage", "臂2 覆盖率"), ("blind", "臂3 盲测")]


def metrics(results: list[dict]) -> dict:
    n = len(results)
    c = Counter(r["status"] for r in results)
    err = c.get("error", 0)
    killed = c.get("killed", 0)
    kr = [r["rounds"] for r in results if r["status"] == "killed"]
    tr = sum(r.get("rounds", 0) for r in results)
    tt = sum(r.get("tautological_rounds", 0) for r in results)
    return {
        "n": n,
        "status": dict(c),
        "compile_pass_rate": 100 * (n - err) / n if n else 0,
        "kill_rate": 100 * killed / n if n else 0,
        "avg_rounds_killed": sum(kr) / len(kr) if kr else 0,
        "cheat_rate": 100 * tt / tr if tr else 0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(ROOT / "data" / "s5"))
    ap.add_argument("--prefix", default="arm")
    args = ap.parse_args()

    loaded = []
    for arm, label in ARMS:
        f = Path(args.dir) / f"{args.prefix}_{arm}.json"
        if not f.exists():
            print(f"[跳过] 缺 {f}")
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        loaded.append((arm, label, d))

    if not loaded:
        print("没有可对比的臂结果")
        return 1

    print("=== 三臂对照：四个质量指标 ===")
    hdr = f"{'指标':<16}" + "".join(f"{lab:>14}" for _, lab, _ in loaded)
    print(hdr)
    print("-" * len(hdr))
    keys = [("kill_rate", "杀死率"), ("compile_pass_rate", "编译通过率"),
            ("avg_rounds_killed", "平均尝试轮次"), ("cheat_rate", "作弊率")]
    for key, name in keys:
        cell = f"{name:<16}"
        for _, _, d in loaded:
            v = d.get(key, metrics(d["results"])[key])
            cell += f"{v:>13.1f}%" if key != "avg_rounds_killed" else f"{v:>14.1f}"
        print(cell)

    print()
    for arm, label, d in loaded:
        st = d.get("status_count") or metrics(d["results"])["status"]
        print(f"  {label}: n={d.get('n', len(d['results']))}  状态={st}")

    # 按模块分解的杀死率
    print("\n=== 按模块的杀死率 ===")
    by_mod = defaultdict(dict)
    for arm, label, d in loaded:
        per = defaultdict(lambda: [0, 0])
        for r in d["results"]:
            per[r["module"]][1] += 1
            if r["status"] == "killed":
                per[r["module"]][0] += 1
        for m, (k, n) in per.items():
            by_mod[m][label] = (k, n)
    print(f"{'模块':<24}" + "".join(f"{lab:>14}" for _, lab, _ in loaded))
    for m in sorted(by_mod, key=lambda m: -sum(x[1] for x in by_mod[m].values())):
        row = f"{m:<24}"
        for _, label, _ in loaded:
            if label in by_mod[m]:
                k, n = by_mod[m][label]
                row += f"{100 * k / n:>12.0f}%({k}/{n})"
            else:
                row += f"{'-':>14}"
        print(row)

    # 按算子分解的杀死率
    print("\n=== 按算子的杀死率 ===")
    by_op = defaultdict(dict)
    for arm, label, d in loaded:
        per = defaultdict(lambda: [0, 0])
        for r in d["results"]:
            per[r["operator"]][1] += 1
            if r["status"] == "killed":
                per[r["operator"]][0] += 1
        for m, (k, n) in per.items():
            by_op[m][label] = (k, n)
    print(f"{'算子':<24}" + "".join(f"{lab:>14}" for _, lab, _ in loaded))
    for m in sorted(by_op, key=lambda m: -sum(x[1] for x in by_op[m].values())):
        row = f"{m:<24}"
        for _, label, _ in loaded:
            if label in by_op[m]:
                k, n = by_op[m][label]
                row += f"{100 * k / n:>12.0f}%({k}/{n})"
            else:
                row += f"{'-':>14}"
        print(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
