"""统计真实变异体数量（S2 第 1 步，也是修订成本预算的唯一依据）。

之前所有成本估算都建立在「每语句 2 个变异体」这个拍脑袋的假设上。
这一步用 mutmut 真实枚举，得到：
  1. 每个模块 / 每个项目的真实变异体数
  2. 真实的「变异体 / 语句」比值
  3. 各算子的分布（判断有没有某个算子在虚增数量）

只做 AST 解析，不跑测试，所以很快。

用法：
    python scripts/count_mutants.py              # 只统计实验集选中的模块
    python scripts/count_mutants.py --all        # 统计全部模块（用于整项目成本估算）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mutloop.mutator import (  # noqa: E402
    DEFAULT_OPERATORS,
    enumerate_mutants,
)
from mutloop.subjects import DATA_DIR, get  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="统计全部模块而非仅实验集选中模块")
    ap.add_argument("--project", action="append")
    args = ap.parse_args()

    sel_path = DATA_DIR / "module_selection.json"
    selected: dict[str, list[str]] = {}
    if sel_path.exists() and not args.all:
        d = json.loads(sel_path.read_text(encoding="utf-8"))
        selected = {k: [m["module"] for m in v] for k, v in d["selected"].items()}

    projects = args.project or sorted(selected) if selected else (
        args.project or ["attrs", "click", "dateutil", "jinja", "marshmallow"]
    )

    grand_mutants = 0
    grand_statements = 0
    grand_cpu_hours = 0.0
    per_project: dict[str, dict] = {}

    print(f"{'项目':<13}{'模块':<24}{'语句':>7}{'变异体':>8}{'比':>7}  算子分布")
    print("-" * 88)

    for name in projects:
        subj = get(name)
        # Windows 下 coverage 的键是反斜杠，统一成正斜杠再查
        cov = {
            k.replace("\\", "/"): v
            for k, v in json.loads(
                (DATA_DIR / "baseline" / f"{name}.json").read_text(encoding="utf-8")
            )["coverage"]["files"].items()
        }
        ovh = json.loads((DATA_DIR / "per_mutant_overhead.json").read_text(encoding="utf-8"))
        sec_per_mutant = ovh.get(name, {}).get("measured_seconds_per_mutant")

        modules = selected.get(name) or sorted(cov)
        p_mut = p_stmt = 0
        p_ops: dict[str, int] = {}
        for mod in modules:
            stmts = cov.get(mod, {}).get("num_statements", 0)
            # 用完整相对路径，不能只取文件名——dateutil 的 tz/win.py 这类
            # 子包模块会被丢掉。
            path = subj.package_dir / mod
            if not path.exists():
                print(f"  [跳过] {name}/{mod} 文件不存在")
                continue
            ms = enumerate_mutants(path, root=subj.root, operators=DEFAULT_OPERATORS)
            ops: dict[str, int] = {}
            for m in ms:
                ops[m.operator] = ops.get(m.operator, 0) + 1
                p_ops[m.operator] = p_ops.get(m.operator, 0) + 1
            ratio = len(ms) / stmts if stmts else 0
            p_mut += len(ms)
            p_stmt += stmts
            dist = " ".join(f"{k}:{v}" for k, v in sorted(ops.items()))
            print(f"{name:<13}{mod:<24}{stmts:>7}{len(ms):>8}{ratio:>7.2f}  {dist}")

        hours = p_mut * sec_per_mutant / 3600 if sec_per_mutant else 0.0
        print(f"{'':<13}{'小计':<24}{p_stmt:>7}{p_mut:>8}"
              f"{(p_mut / p_stmt if p_stmt else 0):>7.2f}  "
              f"→ {hours:.2f} CPU 小时（{sec_per_mutant}s/变异体）")
        print("-" * 88)
        per_project[name] = {
            "modules": modules,
            "statements": p_stmt,
            "mutants": p_mut,
            "mutants_per_statement": round(p_mut / p_stmt, 3) if p_stmt else 0,
            "operators": p_ops,
            "estimated_cpu_hours": round(hours, 2),
        }
        grand_mutants += p_mut
        grand_statements += p_stmt
        grand_cpu_hours += hours

    print(f"{'合计':<13}{'':<24}{grand_statements:>7}{grand_mutants:>8}"
          f"{(grand_mutants / grand_statements if grand_statements else 0):>7.2f}"
          f"  → {grand_cpu_hours:.2f} CPU 小时")

    out = DATA_DIR / ("mutant_counts_full.json" if args.all else "mutant_counts.json")
    out.write_text(
        json.dumps(
            {
                "operators": list(DEFAULT_OPERATORS),
                "per_project": per_project,
                "total_mutants": grand_mutants,
                "total_statements": grand_statements,
                "mutants_per_statement": round(grand_mutants / grand_statements, 3)
                if grand_statements else 0,
                "estimated_cpu_hours": round(grand_cpu_hours, 2),
            },
            indent=2, ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"\n已写入: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
