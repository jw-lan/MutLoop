"""把 S3 相对 S2 的偏差拆成可归因的几类，定位每一类该由哪个修复手段负责。

只读 JSON，不跑测试。用法：python scripts/s3_miss_breakdown.py
"""
from __future__ import annotations

import glob
import json
import os
from collections import Counter, defaultdict

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mutloop.import_time import is_import_time  # noqa: E402
from mutloop.subjects import get  # noqa: E402

KILLED = {"killed", "timeout"}  # 与 compare_schedule.py 一致：timeout 计入 killed


def main() -> None:
    # 类别 -> 个数
    cat: Counter = Counter()
    # 类别 -> 模块 -> 个数
    per: dict[str, Counter] = defaultdict(Counter)
    # 被调度 / 跑全套件 的机时
    cpu: dict[str, float] = defaultdict(float)
    timeout_cpu = 0.0

    for s3p in sorted(glob.glob("data/s3/*-sched.json")):
        s3d = json.load(open(s3p, encoding="utf-8"))
        subj, tgt = s3d["subject"], s3d["target"]
        name = f"{subj}/{tgt}"
        s2p = f"data/s2/{subj}-{os.path.splitext(tgt)[0]}.json"
        s2 = {r["mutant_id"]: r for r in json.load(open(s2p, encoding="utf-8"))["results"]}
        subj_obj = get(subj)

        for r in s3d["results"]:
            o = s2.get(r["mutant_id"])
            if o is None:
                continue
            d = r.get("duration_s") or 0
            sel = r.get("selection") or "full"
            cpu["line" if sel.startswith("line") else "full"] += d / 60
            if r["status"] == "timeout":
                timeout_cpu += d / 60

            k2, k3 = o["status"], r["status"]
            if not KILLED & {k2}:
                continue  # S2 本来就没杀死，调度无所谓对错（S4/S5 的素材）
            if KILLED & {k3}:
                continue  # 判定一致

            # 到这里：S2 杀死了，S3 没杀死 → 漏杀
            if k2 == "timeout":
                c = "超时→存活（死循环没被子集触发）"
            else:
                src = subj_obj.root / r["file"]
                c = ("类体/模块级（覆盖率归属不可靠）" if is_import_time(str(src), r["line"])
                     else "函数体内（其余漏杀）")
            cat[c] += 1
            per[c][name] += 1

    total_miss = sum(cat.values())
    print("=" * 78)
    print("S3 相对 S2 的漏杀归因（S2 判 killed、S3 判非 killed）")
    print("=" * 78)
    for c, n in cat.most_common():
        print(f"\n{c}　{n} 个（占漏杀 {n / total_miss * 100:.1f}%）")
        for m, k in per[c].most_common(6):
            print(f"    {m:<30}{k:>5}")
    print("\n" + "-" * 78)
    print(f"漏杀合计 {total_miss} 个")

    print("\n机时构成（全部 12 模块）：")
    print(f"  调度执行（line）  {cpu['line']:>8.1f} 分钟")
    print(f"  全套件执行        {cpu['full']:>8.1f} 分钟")
    print(f"  其中超时等待      {timeout_cpu:>8.1f} 分钟"
          f"（占总量 {timeout_cpu / (cpu['line'] + cpu['full']) * 100:.1f}%）")


if __name__ == "__main__":
    main()
