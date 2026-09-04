"""S5 交叉验证：用更强的模型（pro）对疑似等价变异体重跑，区分「真等价」vs「flash 能力不足」。

从上一轮的 sample_run.json 里取 suspected_equivalent 的变异体，用 pro 跑同样的
5 轮循环。能杀死的 → 之前是 flash 能力不足；仍杀不死 → 真等价候选。

用法
----
    DEEPSEEK_API_KEY=sk-... python scripts/s5_cross_validate.py
    DEEPSEEK_API_KEY=sk-... python scripts/s5_cross_validate.py --model deepseek-v4-pro
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mutloop.subjects import DATA_DIR  # noqa: E402
from s5_probe_one import probe_one  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="deepseek-v4-pro")
    ap.add_argument("--input", default=str(DATA_DIR / "s5" / "sample_run.json"))
    ap.add_argument("--out", default=str(DATA_DIR / "s5" / "cross_validate_pro.json"))
    args = ap.parse_args()

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("[错误] 缺少环境变量 DEEPSEEK_API_KEY")
        return 1

    prev = json.loads(Path(args.input).read_text(encoding="utf-8"))
    sus = [r for r in prev["results"] if r["status"] == "suspected_equivalent"]
    if not sus:
        print("没有疑似等价变异体可交叉验证")
        return 0

    print(f"交叉验证 {len(sus)} 个疑似等价（模型 {args.model}）\n")

    results = []
    cnt = Counter()
    t0 = time.time()
    for i, s in enumerate(sus, 1):
        r = probe_one(s["project"], s["module"], s["line"], s["operator"],
                      api_key=api_key, model=args.model)
        results.append(r)
        cnt[r["status"]] += 1
        tok = (r.get("usage") or {}).get("total_tokens", 0)
        print(f"[{i}/{len(sus)}] {r['status']:<20} "
              f"{s['project']}/{s['module'].replace('.py','')} L{s['line']:<5} "
              f"{s['operator']:<4} ({tok}t, {r.get('rounds','?')}轮)")
        sys.stdout.flush()

    dt = time.time() - t0
    killed = cnt["killed"]
    still = cnt["suspected_equivalent"]
    print(f"\n=== 交叉验证结果（{len(sus)} 个，{dt:.0f}s） ===")
    print(f"  {args.model} 杀死: {killed} 个（← 这些是 flash 能力不足，不是真等价）")
    print(f"  {args.model} 仍杀不死: {still} 个（← 真等价候选，待人工决定）")
    print(f"  状态分布: {dict(cnt)}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "model": args.model,
        "n": len(sus),
        "status_count": dict(cnt),
        "results": results,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n结果已写入: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
