"""测量「单个变异体的判定成本」，这是变异分析总成本的地板，也是 RQ1 的分子。

背景：S3 的调度层每判定一个变异体都要启动一次 pytest（传一个测试子集），于是

    单变异体成本 = 固定开销 a + 单条测试耗时 b × 覆盖该行的测试数 n

S1 报告最初只算了 b × n 这一项，漏了 a，导致加速比被高估 3 倍。
本脚本同时给出两种口径：
  - 拟合法：对 1/5/20/100/300 条测试分别计时，最小二乘拟合 t(n) = a + b·n
  - 直接法：按该项目的真实「行均测试数」跑一批，3 次取最快（更可信，作为预算依据）

结果写入 data/per_mutant_overhead.json。
"""
from __future__ import annotations

import json
import random
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mutloop.subjects import DATA_DIR, get  # noqa: E402

SIZES = (1, 5, 20, 100, 300)
# 「每语句生成几个变异体」的经验估计；S2 接入 mutmut 后用真实计数替换
MUTANTS_PER_STATEMENT = 2


def collect_node_ids(root: Path) -> list[str]:
    # verbosity 说明：不加 -q 是树状视图，单个 -q 才是平铺 nodeid，两个 -q 变成「文件: 条数」
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "--collect-only", "-q", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout
    return [
        line.strip().split(" ")[0]
        for line in out.splitlines()
        if "::" in line and not line.strip().startswith(("-", "=", "<"))
    ]


def time_run(root: Path, node_ids: list[str], repeat: int = 2) -> float:
    best = float("inf")
    for _ in range(repeat):
        t0 = time.perf_counter()
        subprocess.run(
            [sys.executable, "-m", "pytest", *node_ids, "-q", "-p", "no:cacheprovider",
             "--no-header", "--tb=no"],
            cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        best = min(best, time.perf_counter() - t0)
    return best


def fit(xs: list[int], ys: list[float]) -> tuple[float, float]:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den if den else 0.0
    return my - b * mx, b


def load_avg_tests_per_line(name: str) -> float:
    """从 S1 基线里读「一条被覆盖的语句平均被多少条测试覆盖」。"""
    p = DATA_DIR / "baseline" / f"{name}.json"
    if not p.exists():
        return 0.0
    d = json.loads(p.read_text(encoding="utf-8"))
    idx = d["coverage"].get("per_test_index", {})
    incidences = sum(sum(t.values()) for t in idx.values())
    return incidences / max(d["coverage"]["totals"]["covered"], 1)


def main(names: list[str] | None = None) -> int:
    names = names or ["attrs", "click", "dateutil", "jinja", "marshmallow"]
    results: dict[str, dict] = {}

    print(f"{'项目':<12}{'行均测试':>9}{'拟合a(s)':>10}{'拟合b(s)':>10}"
          f"{'实测(s)':>10}{'CPU小时':>10}")
    print("-" * 62)

    for name in names:
        subj = get(name)
        ids = collect_node_ids(subj.root)
        if not ids:
            print(f"{name:<12} 收集失败，跳过")
            continue

        rnd = random.Random(20260830)
        xs: list[int] = []
        ys: list[float] = []
        for n in SIZES:
            if n > len(ids):
                continue
            # 必须随机采样：按收集顺序取前 n 条会集中在靠前的文件，
            # 实测 click 前 300 条的边际成本只有全套件平均的 1/4，会严重低估。
            batch = rnd.sample(ids, n)
            xs.append(n)
            ys.append(time_run(subj.root, batch))
        a, b = fit(xs, ys)

        avg_tests = load_avg_tests_per_line(name)
        n_direct = int(min(round(avg_tests), len(ids)))
        direct = time_run(subj.root, random.Random(7).sample(ids, n_direct), repeat=3)

        stmts = json.loads(
            (DATA_DIR / "baseline" / f"{name}.json").read_text(encoding="utf-8")
        )["coverage"]["totals"]["num_statements"]
        mutants = stmts * MUTANTS_PER_STATEMENT
        cpu_hours = mutants * direct / 3600

        results[name] = {
            "avg_tests_per_line": round(avg_tests, 1),
            "n_direct": n_direct,
            "fitted_fixed_overhead_s": round(a, 3),
            "fitted_per_test_s": round(b, 5),
            "measured_seconds_per_mutant": round(direct, 3),
            "statements": stmts,
            "estimated_mutants": mutants,
            "cpu_hours_full_run": round(cpu_hours, 2),
            "points": [[x, round(y, 3)] for x, y in zip(xs, ys)],
        }
        print(f"{name:<12}{avg_tests:>9.1f}{a:>10.3f}{b:>10.5f}"
              f"{direct:>10.3f}{cpu_hours:>10.2f}")

    out = DATA_DIR / "per_mutant_overhead.json"
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n结果已写入: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or None))
