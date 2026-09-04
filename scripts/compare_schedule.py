"""对照 S3 调度结果 与 S2 朴素全套件结果，逐个变异体对账。

为什么必须逐个对账
------------------
只看总分会被「误差互相抵消」骗过去，所以必须逐变异体列出分歧，
才能确认方向符合预期、并给出可信的加速比。

误差方向（2026-09-02 实测修正，见 HANDOVER §6）
----------------------------------------------
- **漏杀**（killed → survived）：调度只跑覆盖该行的测试，测少了自然少杀。
  这是主方向，修复前有 167 个。
- **假杀**（stillborn → killed）：**反向**。跑全套件时收集阶段崩溃 = stillborn
  （排除出分母），但只跑 1 条测试时收集成功、该测试失败，就被记成 killed。
  修复前有 90 个，其中 82 个在 `dateutil/rrule.py`。

所以**误差是双向的**，「调度分数是下界」这句话不成立。
本脚本会把两个方向都列出来，不要只盯着漏杀看。

用法
----
    python scripts/compare_schedule.py dateutil relativedelta.py
    python scripts/compare_schedule.py --all        # 比对所有已跑的调度结果
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mutloop.subjects import DATA_DIR  # noqa: E402

S2 = DATA_DIR / "s2"
S3 = DATA_DIR / "s3"

JUDGED = ("killed", "survived", "timeout")
KILLS = ("killed", "timeout")  # 计分时算「被杀死」的状态


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def scored(rows: list[dict]) -> tuple[int, int]:
    """（进入分母的变异体数, 分子=被杀死数）；timeout 计入 killed（S2 定稿口径）。"""
    judged = [r for r in rows if r["status"] in JUDGED]
    kills = sum(1 for r in judged if r["status"] in ("killed", "timeout"))
    return len(judged), kills


def compare(subj: str, target: str) -> dict:
    base = S2 / f"{subj}-{Path(target).stem}.json"
    sched = S3 / f"{subj}-{Path(target).stem}-sched.json"
    if not base.exists():
        return {"error": f"缺少 S2 基线 {base}"}
    if not sched.exists():
        return {"error": f"缺少 S3 调度结果 {sched}"}

    b, s = load(base), load(sched)
    bmap = {r["mutant_id"]: r for r in b["results"]}
    smap = {r["mutant_id"]: r for r in s["results"]}

    diffs = Counter()
    examples: list[dict] = []
    for mid, br in bmap.items():
        sr = smap.get(mid)
        if sr is None:
            diffs["只在S2出现"] += 1
            continue
        bs, ss = br["status"], sr["status"]
        if bs == ss:
            diffs["一致"] += 1
            continue
        key = f"{bs}→{ss}"
        diffs[key] += 1
        if len(examples) < 12:
            examples.append({
                "mutant_id": mid, "line": br["line"], "operator": br["operator"],
                "from": bs, "to": ss,
                "selection": sr.get("selection"),
                "description": br.get("description", "")[:60],
            })

    bn, bk = scored(b["results"])
    sn, sk = scored(s["results"])
    bcpu = sum(r["duration_s"] for r in b["results"])
    scpu = sum(r["duration_s"] for r in s["results"])

    # 「共同可判定集」上的对照。
    #
    # 为什么需要它：调度会改变**分母**。实测 dateutil/rrule.py 有 82 个变异体
    # 在 S2 是 stillborn（导入/收集期崩溃，被排除出判定），调度只收集少数测试
    # 文件后它们能跑起来并被杀死（stillborn→killed）。于是 S3 的可判定数从
    # 1377 涨到 1459，分数被"多出来的这批难题"拉低——这部分不是调度误差。
    # 只在两边都可判定的变异体上比，才能干净地量出调度本身的偏差。
    common = [
        (br, sr) for mid, br in bmap.items()
        if (sr := smap.get(mid)) is not None
        and br["status"] in JUDGED and sr["status"] in JUDGED
    ]
    cn = len(common)
    ck = sum(1 for br, _ in common if br["status"] in KILLS)
    cks = sum(1 for _, sr in common if sr["status"] in KILLS)
    common_bias = round(100 * cks / cn - 100 * ck / cn, 2) if cn else 0.0

    # 漏杀必须把 timeout→survived 也算进来：timeout 在计分时是 killed，
    # 它退化成 survived 与 killed→survived 是同一类误差（分数被低估）。
    missed = sum(v for k, v in diffs.items()
                 if k in ("killed→survived", "timeout→survived"))
    # 反向误差（本来没杀死，调度后反而杀死了）：理论上是 stillborn→killed 这类
    # 分母变化，不是调度把 survived 变成 killed。若出现 survived→killed 要警惕。
    gained = sum(v for k, v in diffs.items()
                 if k in ("survived→killed", "survived→timeout"))

    sel = Counter()
    for r in s["results"]:
        v = r.get("selection") or "?"
        sel["line" if str(v).startswith("line:") else v] += 1

    return {
        "module": f"{subj}/{target}",
        "mutants": len(bmap),
        "s2": {"score": round(100 * bk / bn, 2), "killed": bk, "judged": bn,
               "cpu_seconds": round(bcpu, 1)},
        "s3": {"score": round(100 * sk / sn, 2), "killed": sk, "judged": sn,
               "cpu_seconds": round(scpu, 1)},
        "speedup": round(bcpu / scpu, 2) if scpu else 0,
        "bias_pp": round(100 * sk / sn - 100 * bk / bn, 2) if sn and bn else 0,
        # 共同可判定集上的偏差：剔除分母变化后，调度本身造成的误差
        "common": {"judged": cn, "bias_pp": common_bias, "s2_kills": ck, "s3_kills": cks},
        "missed": missed,
        "gained": gained,
        "diffs": dict(diffs),
        "selection": dict(sel),
        "examples": examples,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("subject", nargs="?", help="项目名，如 dateutil")
    ap.add_argument("target", nargs="?", help="目标模块，如 relativedelta.py")
    ap.add_argument("--all", action="store_true", help="比对所有已跑的调度结果")
    ap.add_argument("--table", action="store_true", help="只打印汇总表，不逐模块展开")
    args = ap.parse_args()

    if args.all:
        pairs = []
        for f in sorted(S3.glob("*-sched.json")):
            stem = f.name[: -len("-sched.json")]
            subj, _, tgt = stem.partition("-")
            pairs.append((subj, tgt + ".py"))
    elif args.subject and args.target:
        pairs = [(args.subject, args.target)]
    else:
        ap.error("给 项目 模块 两个参数，或用 --all")

    rows = [compare(s, t) for s, t in pairs]
    for r in rows:
        if "error" in r:
            print(f"[跳过] {r['error']}")
            continue
        if args.table:
            continue
        print("=" * 74)
        print(f"{r['module']}　{r['mutants']} 个变异体")
        print("-" * 74)
        print(f"  S2 朴素全套件   分数 {r['s2']['score']:6.2f}%  "
              f"killed {r['s2']['killed']:4d}/{r['s2']['judged']:4d}  "
              f"CPU {r['s2']['cpu_seconds'] / 60:6.1f} 分钟")
        print(f"  S3 覆盖率导向   分数 {r['s3']['score']:6.2f}%  "
              f"killed {r['s3']['killed']:4d}/{r['s3']['judged']:4d}  "
              f"CPU {r['s3']['cpu_seconds'] / 60:6.1f} 分钟")
        print(f"  加速比 {r['speedup']:.2f}x　　分数偏差 {r['bias_pp']:+.2f} pp"
              f"（共同可判定集 {r['common']['judged']} 个上 {r['common']['bias_pp']:+.2f} pp）")
        print(f"  实际调度到的变异体: {r['selection']}")
        print(f"  逐个对账: {r['diffs']}")
        print(f"  漏杀 {r['missed']} 个（含 timeout→survived）　反向 gained {r['gained']} 个")
        for e in r["examples"][:6]:
            print(f"    - L{e['line']:<5} {e['operator']:<4} {e['from']}→{e['to']}"
                  f"  [{e['selection']}]  {e['description']}")

    ok = [r for r in rows if "error" not in r]
    if not ok:
        return 0

    # ---- 汇总表 ----------------------------------------------------------
    print()
    print("=" * 88)
    print(f"{'模块':<26}{'变异体':>7}{'S2分数':>9}{'S3分数':>9}{'偏差pp':>8}"
          f"{'S2机时':>9}{'S3机时':>9}{'加速':>7}")
    print("-" * 88)
    tb = tk = sb = sk = 0
    cb = cs = 0.0
    for r in sorted(ok, key=lambda x: -x["speedup"]):
        print(f"{r['module']:<26}{r['mutants']:>7}{r['s2']['score']:>8.2f}%"
              f"{r['s3']['score']:>8.2f}%{r['bias_pp']:>+8.2f}"
              f"{r['s2']['cpu_seconds'] / 60:>8.1f}m{r['s3']['cpu_seconds'] / 60:>8.1f}m"
              f"{r['speedup']:>6.2f}x")
        tb += r["s2"]["judged"]; tk += r["s2"]["killed"]
        sb += r["s3"]["judged"]; sk += r["s3"]["killed"]
        cb += r["s2"]["cpu_seconds"]; cs += r["s3"]["cpu_seconds"]
    print("-" * 88)
    print(f"{'合计':<26}{sum(r['mutants'] for r in ok):>7}"
          f"{100 * tk / tb:>8.2f}%{100 * sk / sb:>8.2f}%{100 * sk / sb - 100 * tk / tb:>+8.2f}"
          f"{cb / 60:>8.1f}m{cs / 60:>8.1f}m{cb / cs if cs else 0:>6.2f}x")

    # 漏杀 = S2 判被杀死（killed 或 timeout）、S3 没杀死。
    # 注意必须把 timeout→survived 算进来：timeout 计分时算 killed，它退化成
    # survived 与 killed→survived 是同一类低估（实测 rrule 有 31 个）。
    miss = sum(r["missed"] for r in ok)
    gained = sum(r["gained"] for r in ok)
    cn = sum(r["common"]["judged"] for r in ok)
    ck2 = sum(r["common"]["s2_kills"] for r in ok)
    ck3 = sum(r["common"]["s3_kills"] for r in ok)
    c_bias = 100 * ck3 / cn - 100 * ck2 / cn if cn else 0.0
    print()
    print(f"漏杀合计 {miss} 个（killed→survived + timeout→survived），"
          f"占 S2 killed 的 {100 * miss / tk:.2f}%")
    print(f"反向误差（survived→被杀死）{gained} 个"
          f"{'　← 需排查' if gained else '　← 为 0，误差方向单一'}")
    print(f"共同可判定集 {cn} 个（两边都可判定的变异体）："
          f"S2 {ck2} → S3 {ck3}，偏差 {c_bias:+.2f} pp")
    print("  这个数比上面的整体偏差更干净：它剔除了 stillborn→killed 造成的分母变化")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
