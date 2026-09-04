"""S5 批量抽样：从 Tier A 存活变异体分层抽样，跑 LLM 生成测试，统计杀死率。

分层抽样策略：每个模块取 2 个变异体——优先 covered_tests 最少的（最容易补测），
且尽量一个 CR + 一个非 CR（ARG/AOR/ROR/COR），保证算子多样性。

统计口径：
- killed / survived 是「有效判定」（LLM 生成了测试且原代码 PASS）
- equivalent 是 LLM 明确判定的「语义等价」
- invalid 是重言式断言被拦下 / 代码无法解析
- error 是测试对原代码就 FAIL / LLM 调用失败
- 杀死率 = killed / (killed + survived)

用法
----
    DEEPSEEK_API_KEY=sk-... python scripts/s5_sample_run.py --n 24
    DEEPSEEK_API_KEY=sk-... python scripts/s5_sample_run.py --n 24 --model deepseek-v4-pro
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mutloop.subjects import DATA_DIR  # noqa: E402
from s5_probe_one import probe_one  # noqa: E402


def sample_tier_a(n: int) -> list[dict]:
    """从 triage 的 Tier A 分层抽样 n 个。

    按 (模块, 算子) 分组，组内按 covered_tests 升序（优先最容易补测的），
    然后轮询各组逐个取——保证 12 个模块、5 个算子都有代表，不偏向某一类。
    """
    triage = json.loads((DATA_DIR / "s4" / "survivor_triage.json").read_text(encoding="utf-8"))
    tier_a = [r for r in triage["records"] if r["tier"].startswith("A")]

    groups = defaultdict(list)
    for r in tier_a:
        groups[(r["project"], r["module"], r["operator"])].append(r)
    for k in groups:
        groups[k].sort(key=lambda r: (r["covered_tests"], r["line"]))

    keys = list(groups.keys())
    picked = []
    while len(picked) < n and any(groups[k] for k in keys):
        for k in keys:
            if len(picked) >= n:
                break
            if groups[k]:
                picked.append(groups[k].pop(0))
    return picked[:n]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=24)
    ap.add_argument("--arm", default="directed",
                    choices=["directed", "coverage", "blind"],
                    help="directed=臂1 完整变异diff；coverage=臂2 覆盖测试列表；"
                         "blind=臂3 仅模块名+行号")
    ap.add_argument("--from", dest="from_json",
                    help="从既有的 run JSON 读取变异体坐标——三臂对照时用这个保证"
                         "**同一批变异体**，不用重新抽样")
    ap.add_argument("--limit", type=int,
                    help="配合 --from：只跑其中 N 个（等间隔取样，"
                         "保证模块/算子分布均匀，从而三臂样本严格对等）")
    ap.add_argument("--model", default="deepseek-v4-flash")
    ap.add_argument("--max-tokens", type=int, default=4000)
    ap.add_argument("--thinking", default="disabled", choices=["disabled", "enabled"])
    ap.add_argument("--max-rounds", type=int, default=5)
    ap.add_argument("--out", default=str(DATA_DIR / "s5" / "sample_run.json"))
    args = ap.parse_args()

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("[错误] 缺少环境变量 DEEPSEEK_API_KEY")
        return 1

    if args.from_json:
        prev = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        samples = [{"project": r["project"], "module": r["module"],
                    "line": r["line"], "operator": r["operator"]}
                   for r in prev["results"]]
        if args.limit and 0 < args.limit < len(samples):
            # 等间隔取样：sample_tier_a 是轮询产出的，等间隔能保证各模块/算子层都有代表
            k = len(samples)
            idx = sorted({int(i * k / args.limit) for i in range(args.limit)})
            samples = [samples[i] for i in idx]
        src = f"{args.from_json}（{len(samples)} 个）"
        print(f"复用 {len(samples)} 个变异体（来自 {src}），臂 {args.arm}，模型 {args.model}\n")
    else:
        samples = sample_tier_a(args.n)
        print(f"抽样 {len(samples)} 个 Tier A 变异体，臂 {args.arm}，模型 {args.model}\n")

    results = []
    status_count = Counter()
    t0 = time.time()
    for i, s in enumerate(samples, 1):
        r = probe_one(s["project"], s["module"], s["line"], s["operator"],
                      api_key=api_key, arm=args.arm,
                      model=args.model, max_tokens=args.max_tokens,
                      thinking=args.thinking, max_rounds=args.max_rounds)
        results.append(r)
        status_count[r["status"]] += 1
        desc = r.get("description", "")[:40]
        tok = (r.get("usage") or {}).get("total_tokens", 0)
        line = (f"[{i}/{len(samples)}] {r['status']:<20} "
                f"{r['project']}/{r['module'].replace('.py','')} L{r['line']:<5} "
                f"{r['operator']:<4} ({tok}t, {r.get('rounds','?')}轮) {desc}")
        if r["status"] in ("error", "invalid"):
            line += f"  << {r.get('reason', '')}"
        print(line)
        sys.stdout.flush()

    dt = time.time() - t0
    n = len(samples)
    killed = status_count["killed"]
    susp = status_count["suspected_equivalent"]
    err = status_count["error"]

    # 四个质量指标（反映 LLM 生成测试对抗变异测试的质量）
    compile_pass = 100 * (n - err) / n if n else 0          # 编译通过率：终版测试能跑通原代码
    kill_rate = 100 * killed / n if n else 0                # 杀死率
    killed_rounds = [r["rounds"] for r in results if r["status"] == "killed"]
    avg_rounds = sum(killed_rounds) / len(killed_rounds) if killed_rounds else 0  # 平均尝试轮次
    total_rounds = sum(r.get("rounds", 0) for r in results)
    total_taut = sum(r.get("tautological_rounds", 0) for r in results)
    cheat_rate = 100 * total_taut / total_rounds if total_rounds else 0  # 作弊率

    print(f"\n=== 结果汇总（{n} 个，{dt:.0f}s） ===")
    print(f"  状态分布: {dict(status_count)}")
    print(f"  编译通过率 = {compile_pass:.1f}%（{n - err}/{n} 终版测试能跑通原代码）")
    print(f"  杀死率     = {kill_rate:.1f}%（{killed}/{n}）")
    print(f"  平均尝试轮次 = {avg_rounds:.1f}（仅统计 killed，取值 1-{args.max_rounds}）")
    print(f"  作弊率     = {cheat_rate:.1f}%（重言式断言轮次 {total_taut}/{total_rounds}）")
    if susp:
        print(f"  疑似等价（LLM 无法判定，如实计入未杀死）: {susp} 个")
    if err:
        print(f"  编译失败（终版测试对原代码报错）: {err} 个")

    # 写结果
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "model": args.model,
        "arm": args.arm,
        "n": n,
        "status_count": dict(status_count),
        "compile_pass_rate": compile_pass,
        "kill_rate": kill_rate,
        "avg_rounds_killed": avg_rounds,
        "cheat_rate": cheat_rate,
        "results": results,
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n结果已写入: {out}")

    # 统一清理临时测试文件（每个项目一次，避免累积删除触发沙箱保护）
    from mutloop.subjects import SUBJECTS
    for name in SUBJECTS:
        f = ROOT / "subjects" / name / "tests" / "test_s5_probe_tmp.py"
        if f.exists():
            f.unlink(missing_ok=True)
    print("已清理临时测试文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
