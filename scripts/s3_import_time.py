"""按 AST 把每个变异体所在行分成两类，统计覆盖率调度的漏杀率，并估算修复代价。

背景（实测发现）：
    click/types.py 的漏杀几乎全部落在 `name = "text"` 这类**类体/模块级赋值**上。
    这些行只在 import 时执行一次，覆盖率只能把它归给"碰巧第一个触发导入"的测试，
    而真正观察到该值的测试（test_info_dict 通过 to_info_dict() 内省）根本不执行这行，
    于是调度必然选错测试。

分类规则（比"是否在函数里"更细）：
    - import-time：模块级语句、类体语句、以及函数的**签名部分**
      （默认值 `allow_dash: bool = False` 在类定义时就求值了，同样只在 import 时执行）
    - func：函数体内部，每次调用都会执行，覆盖率归属可靠

输出两张表：
    1. 全局分类漏杀率
    2. 逐模块：若把 import-time 变异体全部改为跑全套件，需要多付多少机时、
       能换回多少漏杀 —— 用来判断这笔买卖值不值。
"""
from __future__ import annotations

import ast
import glob
import json
import os
from collections import defaultdict

KILLED = {"killed", "timeout"}  # 与 compare_schedule.py 一致：timeout 计入 killed


def scored(status: str) -> bool:
    return status in KILLED


def line_ctx(path: str) -> dict[int, str]:
    """返回 {行号: 'func' | 'import-time'}。"""
    src = open(path, encoding="utf-8").read()
    n_lines = len(src.splitlines())
    tree = ast.parse(src)
    ctx: dict[int, str] = {}

    def mark(node: ast.AST, tag: str) -> None:
        """把 node 子树里的所有行标为 tag（已标过的不覆盖 → 外层优先）。"""
        for n in ast.walk(node):
            ln = getattr(n, "lineno", None)
            en = getattr(n, "end_lineno", None)
            if ln is None:
                continue
            for i in range(ln, (en or ln) + 1):
                ctx.setdefault(i, tag)

    def walk_body(body, tag: str) -> None:
        for stmt in body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # 装饰器 + 签名（默认值/注解）在外层定义时刻就求值 → import-time
                for d in stmt.decorator_list:
                    mark(d, tag)
                mark(stmt.args, tag)
                if getattr(stmt, "returns", None) is not None:
                    mark(stmt.returns, tag)
                walk_body(stmt.body, "func")  # 只有函数体是每次调用执行
            elif isinstance(stmt, ast.ClassDef):
                for d in stmt.decorator_list:
                    mark(d, tag)
                for b in stmt.bases:
                    mark(b, tag)
                for kw in stmt.keywords:
                    mark(kw, tag)
                walk_body(stmt.body, tag)  # 类体仍是 import-time
            else:
                mark(stmt, tag)
                for ch in ast.iter_child_nodes(stmt):
                    if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        walk_body([ch], tag)

    walk_body(tree.body, "import-time")
    for i in range(1, n_lines + 1):
        ctx.setdefault(i, "unknown")
    return ctx


def main() -> None:
    rows: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    modules: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    # 必须从 data/s3 反查 S2：data/s2 里有 _pc*.json 等调试副本，
    # 直接 glob data/s2/*.json 会把同一模块重复计数（实测多算 853 个变异体）。
    for s3p in sorted(glob.glob("data/s3/*-sched.json")):
        s3d = json.load(open(s3p, encoding="utf-8"))
        subj, tgt = s3d["subject"], s3d["target"]
        name = f"{subj}/{tgt}"
        s2p = f"data/s2/{subj}-{os.path.splitext(tgt)[0]}.json"
        d = json.load(open(s2p, encoding="utf-8"))
        s3 = {r["mutant_id"]: r for r in s3d["results"]}
        path = os.path.join("subjects", subj, d["results"][0]["file"])
        try:
            ctx = line_ctx(path)
        except Exception as e:  # 语法/编码异常时整块跳过，不猜
            print(f"[跳过] {path}: {e}")
            continue

        n = len(d["results"])
        cpu_s2 = sum(r.get("duration_s") or 0 for r in d["results"]) / 60
        cpu_s3 = sum(r.get("duration_s") or 0 for r in s3d["results"]) / 60
        # 全套件/调度的单个变异体平均机时（秒）
        per_full = cpu_s2 * 60 / n
        per_sched = cpu_s3 * 60 / n

        for r in d["results"]:
            o = s3.get(r["mutant_id"])
            if not o:
                continue
            c = ctx.get(r["line"], "unknown")
            rows[c]["total"] += 1
            if (o.get("selection") or "").startswith("line"):
                rows[c]["scheduled"] += 1
            if scored(r["status"]):
                rows[c]["killed_s2"] += 1
                if not scored(o["status"]):
                    rows[c]["miss"] += 1
                    modules[name][f"miss_{c}"] += 1
            if c == "import-time" and (o.get("selection") or "").startswith("line"):
                modules[name]["imp_scheduled"] += 1
            modules[name]["n"] += 1
            modules[name]["per_full"] = per_full
            modules[name]["per_sched"] = per_sched
            modules[name]["cpu_s2"] = cpu_s2
            modules[name]["cpu_s3"] = cpu_s3

    print("=" * 74)
    print(f"{'上下文':<14}{'变异体':>8}{'被调度':>8}{'S2杀死':>8}{'漏杀':>7}{'漏杀率':>9}")
    print("-" * 74)
    for c in ("func", "import-time", "unknown"):
        v = rows[c]
        if not v["total"]:
            continue
        rate = v["miss"] / v["killed_s2"] * 100 if v["killed_s2"] else 0.0
        print(f"{c:<14}{v['total']:>8.0f}{v['scheduled']:>8.0f}"
              f"{v['killed_s2']:>8.0f}{v['miss']:>7.0f}{rate:>8.2f}%")
    print("-" * 74)
    tot_miss = sum(v["miss"] for v in rows.values())
    tot_kill = sum(v["killed_s2"] for v in rows.values())
    print(f"{'合计':<14}{sum(v['total'] for v in rows.values()):>8.0f}"
          f"{sum(v['scheduled'] for v in rows.values()):>8.0f}"
          f"{tot_kill:>8.0f}{tot_miss:>7.0f}{tot_miss / tot_kill * 100:>8.2f}%")
    print("=" * 74)

    # 若把 import-time 变异体全部改为全套件：代价 / 收益
    print("\n把 import-time 变异体改为跑全套件的代价与收益：")
    print(f"{'模块':<28}{'待改':>6}{'回收漏杀':>9}{'现机时':>9}{'新机时':>9}{'加速':>8}")
    print("-" * 74)
    agg = defaultdict(float)
    for name, v in sorted(modules.items(), key=lambda kv: -kv[1].get("miss_import-time", 0)):
        n_imp = v.get("imp_scheduled", 0)
        extra_min = n_imp * (v["per_full"] - v["per_sched"]) / 60
        new_cpu = v["cpu_s3"] + extra_min
        agg["imp"] += n_imp
        agg["recover"] += v.get("miss_import-time", 0)
        agg["cpu_s3"] += v["cpu_s3"]
        agg["new"] += new_cpu
        agg["cpu_s2"] += v["cpu_s2"]
        print(f"{name:<28}{n_imp:>6.0f}{v.get('miss_import-time', 0):>9.0f}"
              f"{v['cpu_s3']:>9.1f}{new_cpu:>9.1f}{v['cpu_s2'] / new_cpu:>7.2f}x")
    print("-" * 74)
    print(f"{'合计':<28}{agg['imp']:>6.0f}{agg['recover']:>9.0f}"
          f"{agg['cpu_s3']:>9.1f}{agg['new']:>9.1f}{agg['cpu_s2'] / agg['new']:>7.2f}x")
    print(f"（现状加速比 {agg['cpu_s2'] / agg['cpu_s3']:.2f}x，"
          f"改后 {agg['cpu_s2'] / agg['new']:.2f}x）")


if __name__ == "__main__":
    main()
