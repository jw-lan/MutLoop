"""估算两处修复的代价与收益（只读 JSON，不跑测试）。

修复一：把「只在 import 时执行」的变异体改为跑全套件
修复二：缓存已确认为真死循环的变异体，不再重复等待超时

估算方法（为什么不能简单用模块均值）
------------------------------------
模块均值会被 timeout 污染：rrule 的 100 个超时每个烧 120 秒，把「调度后平均耗时」
抬到比「全套件平均耗时」还高，于是会算出"改跑全套件反而更省"的荒谬结论。
所以这里统一用**非超时变异体的中位数**：
    全套件成本 = 该模块 S2 非超时变异体的耗时中位数
    调度成本   = 该模块 S3 非超时、且确实被调度的变异体耗时中位数
"""
from __future__ import annotations

import glob
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mutloop.import_time import is_import_time  # noqa: E402
from mutloop.subjects import get  # noqa: E402


def med(v: list[float]) -> float:
    v = sorted(v)
    return v[len(v) // 2] if v else 0.0


def main() -> None:
    print("=" * 92)
    print(f"{'模块':<26}{'全套件中位':>10}{'调度中位':>9}{'待改数':>7}"
          f"{'修复一增量':>11}{'超时等待':>9}{'现机时':>9}{'两修后':>9}{'加速':>8}")
    print("-" * 92)

    tot: dict[str, float] = defaultdict(float)
    rows = []
    for s3p in sorted(glob.glob("data/s3/*-sched.json")):
        s3d = json.load(open(s3p, encoding="utf-8"))
        subj, tgt = s3d["subject"], s3d["target"]
        s2d = json.load(open(f"data/s2/{subj}-{os.path.splitext(tgt)[0]}.json",
                             encoding="utf-8"))
        so = get(subj)

        full_s2 = med([r["duration_s"] for r in s2d["results"]
                       if r["status"] != "timeout"])
        sched = [r["duration_s"] for r in s3d["results"]
                 if r["status"] != "timeout"
                 and (r.get("selection") or "").startswith("line")]
        sched_med = med(sched)

        n_imp = sum(
            1 for r in s3d["results"]
            if (r.get("selection") or "").startswith("line")
            and is_import_time(str(so.root / r["file"]), r["line"])
        )
        # 修复一：这些变异体改跑全套件。成本差为负（全套件反而更快）时不改。
        extra1 = n_imp * max(0.0, full_s2 - sched_med) / 60
        # 修复二：已知死循环不再等。这里按"全部跳过"给上界，实际需要一次短确认
        timeout_min = sum(r["duration_s"] for r in s3d["results"]
                          if r["status"] == "timeout") / 60
        cpu_s2 = sum(r["duration_s"] for r in s2d["results"]) / 60
        cpu_s3 = sum(r["duration_s"] for r in s3d["results"]) / 60
        new = cpu_s3 + extra1 - timeout_min
        rows.append((f"{subj}/{tgt}", full_s2, sched_med, n_imp, extra1,
                     timeout_min, cpu_s3, new, cpu_s2 / new if new else 0))
        tot["extra1"] += extra1
        tot["tmo"] += timeout_min
        tot["s3"] += cpu_s3
        tot["s2"] += cpu_s2

    for n, f_, s_, ni, e1, tm, c3, new, sp in sorted(rows, key=lambda x: -x[4]):
        print(f"{n:<26}{f_:>10.2f}{s_:>9.2f}{ni:>7}{e1:>11.1f}{tm:>9.1f}"
              f"{c3:>9.1f}{new:>9.1f}{sp:>7.2f}x")
    print("-" * 92)
    new_all = tot["s3"] + tot["extra1"] - tot["tmo"]
    print(f"{'合计':<26}{'':>10}{'':>9}{'':>7}{tot['extra1']:>11.1f}"
          f"{tot['tmo']:>9.1f}{tot['s3']:>9.1f}{new_all:>9.1f}"
          f"{tot['s2'] / new_all:>7.2f}x")
    print()
    print(f"现状加速比 {tot['s2'] / tot['s3']:.2f}x"
          f"　→　两处修复后 {tot['s2'] / new_all:.2f}x")
    print(f"修复一（import-time 改全套件）代价 +{tot['extra1']:.1f} 分钟，"
          f"回收 154/167 个漏杀（92%）")
    print(f"修复二（死循环缓存）省下 {tot['tmo']:.1f} 分钟"
          f"（占现状 {tot['tmo'] / tot['s3'] * 100:.1f}%）")
    print("注：修复二按'完全跳过'估算，是上界；稳妥做法是保留一次短超时确认。")


if __name__ == "__main__":
    main()
