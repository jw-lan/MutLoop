"""存活变异体分级（S4b：triage）。

把 S2 的 survived 变异体按「可检测性」分成三层，为 S5 定向补测提供优先级：

- **Tier A「覆盖到了但没断言住」**：变异行被测试执行过（行级覆盖索引里有记录），
  但变异仍存活。这是最高优先级——它直接证明「覆盖率≠检测能力」，
  且一个定向测试就能杀死它。
- **Tier B「根本没覆盖」**：变异行从未被任何测试执行。覆盖率已经知道这里弱，
  补测的收益是「先补覆盖」，故事性不如 A。
- **Tier C「import 时执行」**：类体 / 模块级 / 函数签名默认值，只在 import 时执行一次，
  覆盖率归属不可靠，需要专门的内省式测试。

分层依据：
- 覆盖与否 → 查 `data/s3/line_index_<项目>.json`（行 → 覆盖该行的测试列表）。
  该行有记录且列表非空 = 覆盖；无记录 = 未覆盖。
- import-time → `mutloop.import_time.is_import_time()`（纯 AST，与 S3 修复一同一判据）。

用法
----
    python scripts/triage_survivors.py             # 打印分级汇总 + 写 JSON
    python scripts/triage_survivors.py --top 20    # 额外打印 Tier A 前 20 个（按覆盖测试数升序）

注意
----
- 数据源是 **S2 的 survived**（真实缺口 1558 个），不是 S3 的调度结果
  （后者含 13 个漏杀，会被全套件杀死，不该进 S5 的补测池）。
- mutant_id 只在「同一枚举上下文」内稳定；增量模式用 covered_lines 过滤后 ID 会变，
  所以本脚本只读 S2 数据，不做跨模式匹配。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mutloop.import_time import is_import_time  # noqa: E402
from mutloop.runner import in_package_path  # noqa: E402
from mutloop.subjects import DATA_DIR, get  # noqa: E402

TIER_A = "A-覆盖到了但没断言住"
TIER_B = "B-根本没覆盖"
TIER_C = "C-import时执行"


def load_line_index(subject: str) -> dict:
    p = DATA_DIR / "s3" / f"line_index_{subject}.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def line_covered(index: dict, module_key: str, line: int) -> int:
    """返回覆盖该行的测试数；0 = 未覆盖。"""
    tests = index.get(module_key, {}).get(str(line), [])
    return len(tests)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=0,
                    help="额外打印 Tier A 前 N 个（按覆盖测试数升序，越少越容易补）")
    ap.add_argument("--out", default=str(DATA_DIR / "s4" / "survivor_triage.json"))
    args = ap.parse_args()

    sel = json.loads(
        (DATA_DIR / "module_selection.json").read_text(encoding="utf-8")
    )["selected"]

    # 收集全部 survived 变异体 + 判层
    records: list[dict] = []
    index_cache: dict[str, dict] = {}
    for proj, mods in sel.items():
        s = get(proj)
        if proj not in index_cache:
            index_cache[proj] = load_line_index(proj)
        index = index_cache[proj]
        for m in mods:
            module = m["module"]
            stem = Path(module).stem
            s2 = DATA_DIR / "s2" / f"{proj}-{stem}.json"
            if not s2.exists():
                continue
            data = json.loads(s2.read_text(encoding="utf-8"))
            for r in data["results"]:
                if r["status"] != "survived":
                    continue
                module_key = str(in_package_path(s, r["file"])).replace("\\", "/")
                n_cov = line_covered(index, module_key, r["line"])
                src = s.package_dir / in_package_path(s, r["file"])
                is_imp = is_import_time(src, r["line"]) if src.exists() else False
                tier = TIER_C if is_imp else (TIER_A if n_cov > 0 else TIER_B)
                records.append({
                    "mutant_id": r["mutant_id"],
                    "project": proj,
                    "module": module,
                    "file": r["file"],
                    "line": r["line"],
                    "operator": r["operator"],
                    "description": r["description"],
                    "tier": tier,
                    "covered_tests": n_cov,
                })

    n = len(records)
    by_tier = Counter(r["tier"] for r in records)
    print(f"=== 存活变异体分级（S2 真值，共 {n} 个 survived） ===\n")
    for tier in (TIER_A, TIER_B, TIER_C):
        cnt = by_tier.get(tier, 0)
        print(f"{tier}: {cnt} ({100 * cnt / n:.1f}%)")

    # 每个 tier 按模块 × 算子展开
    for tier in (TIER_A, TIER_B, TIER_C):
        sub = [r for r in records if r["tier"] == tier]
        if not sub:
            continue
        print(f"\n--- {tier} ---")
        by_mod = Counter(r["project"] + "/" + r["module"] for r in sub)
        print("  按模块:", dict(by_mod.most_common()))
        by_op = Counter(r["operator"] for r in sub)
        print("  按算子:", dict(by_op.most_common()))

    # Tier A 里按覆盖测试数升序（越少越容易用定向测试补）
    if args.top:
        tier_a = sorted(
            (r for r in records if r["tier"] == TIER_A),
            key=lambda r: (r["covered_tests"], r["line"]),
        )
        print(f"\n=== Tier A 前 {args.top}（按覆盖测试数升序，越少越好补） ===")
        for r in tier_a[: args.top]:
            print(f"  {r['project']}/{r['module']} L{r['line']:<5} {r['operator']:<4} "
                  f"覆盖 {r['covered_tests']} 测试  {r['description'][:40]}")

    # 写 JSON 供 S5 消费
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "total_survived": n,
        "by_tier": dict(by_tier),
        "records": records,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n分级结果已写入: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
