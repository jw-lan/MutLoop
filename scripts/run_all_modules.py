"""按变异体数从小到大批量跑模块，每个跑完立即做健康检查，异常就停。

为什么要它而不是一个个手跑
--------------------------
串行跑完 12 个模块约 12 小时；并行（8 workers）约 3.5 小时，仍然不短。
逐个手动等不现实，但一次性跑完又违背「早发现问题」的诉求。
所以：**自动跑 + 每个模块结束后立即体检，异常立刻中止**。

体检项（都是"工具链可能出错"的信号，不是结果好坏）
------------------------------------------------
- rc 分布出现 0/1/4 之外的值 —— 环境或命令有问题
- rc=5（一条用例都没收集到）—— 环境问题，不是变异体问题
- stillborn 占比过高 —— 更像是 conftest/依赖没装好，而非变异体真死胎
- 变异分数为 0 或 100 —— 可疑，通常工作副本没生效或全崩了
- **mutant_id 有重复** —— 并行匹配用了错误键，会静默丢失变异体（踩过）

用法
----
    python scripts/run_all_modules.py                       # 跑全部（并行 8 workers）
    python scripts/run_all_modules.py --max-modules 4       # 只跑最小的 4 个
    python scripts/run_all_modules.py --workers 4           # 降低并行度
    python scripts/run_all_modules.py --force               # 已有结果也重跑
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mutloop.subjects import DATA_DIR, get  # noqa: E402

PY = sys.executable
# 已用最终配置跑完并通过体检的模块（__all__ 过滤 + seed + 每变异体库 + 重试 + 放宽超时）
DONE: set[tuple[str, str]] = {
    ("attrs", "validators.py"),
    ("click", "shell_completion.py"),
    ("click", "parser.py"),
    ("click", "types.py"),
    ("dateutil", "tz/win.py"),
    ("dateutil", "relativedelta.py"),
    ("dateutil", "rrule.py"),
    ("jinja", "environment.py"),
    ("jinja", "nodes.py"),
    ("marshmallow", "utils.py"),
    ("marshmallow", "validate.py"),
    ("marshmallow", "schema.py"),
}  # 12/12 全部跑完（2026-09-02），结果在 data/s2/*.json
WARN_STILLBORN = 25.0  # stillborn 占比超过这个值就报警

_FORCE = False


def list_modules(schedule: str = "none") -> list[tuple[int, str, str]]:
    """按变异体数从小到大列出待跑模块。

    `DONE` 是 S2 朴素基线的进度标记，**只对 schedule=none 生效**。
    跑调度模式时必须改看 data/s3/ 下的输出文件是否存在——否则 S2 全部跑完后
    DONE 里就是 12 个模块，调度模式会直接判定"没有待跑模块"。
    """
    from mutloop import mutator

    sel = json.loads(
        (DATA_DIR / "module_selection.json").read_text(encoding="utf-8")
    )["selected"]
    rows = []
    for proj, mods in sel.items():
        s = get(proj)
        for m in mods:
            if schedule == "none":
                done = (proj, m["module"]) in DONE
            else:
                done = (DATA_DIR / "s3"
                        / f"{proj}-{Path(m['module']).stem}-sched.json").exists()
            if done and not _FORCE:
                continue
            path = s.package_dir / m["module"]
            if not path.exists():
                print(f"[跳过] {proj}/{m['module']} 文件不存在")
                continue
            rows.append((len(mutator.enumerate_mutants(path)), proj, m["module"]))
    rows.sort()
    return rows


def health_check(data: dict) -> list[str]:
    """结果是否有"工具链出错"的信号。空列表 = 健康。"""
    problems = []
    s = data["summary"]
    res = data["results"]
    total = s["total_mutants"]

    rcs: dict = {}
    for r in res:
        rcs[r["returncode"]] = rcs.get(r["returncode"], 0) + 1
    # rc 语义：0=全过 1=有失败 2=收集中断(import 失败) 3=pytest 内部错误
    #           4=配置/用法错误 5=没收集到用例 None=超时
    # 2/3/4 都是 stillborn 的合法来源（变异让导入期/收集期崩溃），只有 5 是环境问题。
    #
    # **rc=None（超时）不在这里报警**，它由下面的「超时占比」检查单独负责。
    # 原因：超时是变异测试的**合法结果**（死循环），dateutil/rrule 有 100 个真死循环，
    # 在这里报警只会让每轮批量跑都在 rrule 上误中止——2026-09-02 那轮重跑就是这样
    # 被判"未通过体检"的，但逐个核对 S2 真值后确认 131 个超时全部合法。
    odd = {k: v for k, v in rcs.items()
           if k is not None and k not in (0, 1, 2, 3, 4)}
    if odd:
        problems.append(f"出现非预期的 returncode: {odd}")
    if 3 in rcs and rcs[3] > 2:
        problems.append(f"rc=3（pytest 内部错误）有 {rcs[3]} 个，超过 2 个就值得排查")
    if 5 in rcs:
        problems.append(
            f"rc=5 共 {rcs[5]} 个（没收集到任何用例，是环境问题不是变异体问题）"
        )

    # 超时占比。注意**不能**一看到大面积超时就判定为环境问题：
    # dateutil/rrule 的 149 个 timeout（10.2%）经单独串行验证是真实死循环，
    # 是合法结果；而 click 那次 96.6% 才是并行拖慢的误判。
    # 所以这里只提示进一步验证，不直接断言原因。
    # **已知死循环（来自缓存）不计入这个报警**：它们已经过两次独立运行验证，
    # 再报一次只会让批量跑在 rrule 上误中止。只有**新出现**的超时才需要排查。
    tmo = sum(1 for r in res
              if r.get("status") == "timeout" and not r.get("from_cache"))
    n_cached = sum(1 for r in res
                   if r.get("status") == "timeout" and r.get("from_cache"))
    if total and 100.0 * tmo / total > 10.0:
        problems.append(
            f"新增超时占比 {100.0 * tmo / total:.1f}% > 10%"
            f"（另有 {n_cached} 个来自死循环缓存的已知超时，不计入）"
            "（用 probe_timeout_one.py 抽样：单独跑也超时则是真死循环、属合法结果）"
        )

    sb = s["by_status"].get("stillborn", 0)
    if total and 100.0 * sb / total > WARN_STILLBORN:
        problems.append(
            f"stillborn 占比 {100.0 * sb / total:.1f}% > {WARN_STILLBORN}%"
            "（更像是 conftest/依赖问题，而非变异体真的死胎）"
        )

    ms = s["mutation_score"]
    if ms == 0.0:
        problems.append("变异分数为 0（工作副本可能没生效，或测试整体没跑起来）")
    if ms == 100.0:
        problems.append("变异分数为 100（可疑：所有变异体都被杀死）")

    if s["judged_mutants"] == 0:
        problems.append("没有任何可判定变异体")

    # ID 唯一性：并行实现若按 (line, description) 而非 mutant_id 匹配，
    # 描述相同的变体会被当成同一个，结果条数够但唯一变异体变少。
    ids = [r["mutant_id"] for r in res]
    if len(set(ids)) != len(ids):
        problems.append(
            f"mutant_id 有 {len(ids) - len(set(ids))} 个重复"
            f"（结果 {len(ids)} 条但只有 {len(set(ids))} 个唯一变异体）"
        )
    if len(ids) != total:
        problems.append(f"结果条数 {len(ids)} 与变异体总数 {total} 不符")

    return problems


def run_one(proj: str, module: str, idx: int, total: int, workers: int,
            schedule: str = "none", verify: bool = False,
            deadloop: str = "off") -> tuple[bool, str]:
    # 调度模式的结果单独落在 data/s3/，绝不能覆盖 S2 的朴素基线——
    # 那是整个项目唯一的真值来源，一旦被覆盖，加速比和漏杀率就再也算不出来了。
    if schedule == "none":
        out = DATA_DIR / "s2" / f"{proj}-{Path(module).stem}.json"
    else:
        out = DATA_DIR / "s3" / f"{proj}-{Path(module).stem}-sched.json"
    if out.exists() and not _FORCE:
        print(f"[{idx}/{total}] {proj}/{module} 已有结果，跳过（加 --force 重跑）")
        data = json.loads(out.read_text(encoding="utf-8"))
        probs = health_check(data)
        return (not probs), ("; ".join(probs))

    print(f"\n{'=' * 70}")
    print(f"[{idx}/{total}] {proj}/{module}")
    print("=" * 70)
    t0 = time.time()
    cmd = [PY, str(ROOT / "scripts" / "run_parallel.py"),
           "--subject", proj, "--target", module,
           "--workers", str(workers), "--out", str(out)]
    if schedule != "none":
        cmd += ["--schedule", schedule]
        if verify:
            cmd += ["--verify"]
    if deadloop != "off":
        cmd += ["--deadloop-cache", deadloop]
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    dt = time.time() - t0

    if proc.returncode != 0:
        out_tail = (proc.stdout or "").strip().splitlines()[-6:]
        err_tail = (proc.stderr or "").strip().splitlines()[-8:]
        tail = "\n".join(out_tail + (["--- stderr ---"] + err_tail if err_tail else []))
        return False, f"命令失败 rc={proc.returncode}\n{tail}"
    if not out.exists():
        return False, "命令成功但没有产出结果文件"

    data = json.loads(out.read_text(encoding="utf-8"))
    s = data["summary"]
    print(
        f"  变异体 {s['total_mutants']}　"
        f"killed {s['by_status'].get('killed', 0)}　"
        f"survived {s['by_status'].get('survived', 0)}　"
        f"stillborn {s['by_status'].get('stillborn', 0)}"
    )
    print(f"  变异分数  传统 {s['mutation_score']}%　严格 {s.get('strict_mutation_score')}%")
    kc = s.get("kill_classes", {})
    if kc:
        print(f"  killed 含金量  {kc}")
    n_cache = sum(1 for r in data["results"] if r.get("from_cache"))
    if n_cache:
        # 用 cache_saved_s（上次实跑的耗时），不能用 duration_s——
        # 缓存命中的结果 duration_s 恒为 0，那样会显示"省了 0 分钟"
        saved = sum(r.get("cache_saved_s", 0.0) for r in data["results"]
                    if r.get("from_cache"))
        print(f"  死循环缓存命中 {n_cache} 个（跳过，未实跑；"
              f"上次这些共耗时 {saved / 60:.1f} 分钟）")
    rt = sum(1 for r in data["results"] if r.get("retries"))
    if rt:
        print(f"  触发重试 {rt} 个（异常 rc 自动重试一次，已排除偶发）")
    print(f"  墙钟 {dt / 60:.1f} 分钟")

    probs = health_check(data)
    if probs:
        print("  [体检未通过]")
        for p in probs:
            print(f"    - {p}")
        return False, "; ".join(probs)
    print("  [体检通过]")
    return True, ""


def main() -> int:
    global _FORCE
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-modules", type=int, help="只跑最小的 N 个模块")
    ap.add_argument("--only", help="只跑指定模块，格式 项目:模块，可逗号分隔")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--schedule", choices=["none", "line"], default="none",
                    help="line=覆盖率导向调度（结果写入 data/s3/，不会覆盖 S2 基线）")
    ap.add_argument("--verify", action="store_true",
                    help="配合 --schedule line：幸存者回全套件复核，结果精确")
    ap.add_argument("--deadloop-cache", choices=["off", "on", "seed"], default="off",
                    help="死循环结果缓存（S3 修复二）。seed=用 S2+S3 历史结果预置，"
                         "本次即可跳过已确认死循环。首次运行新项目收益为 0，"
                         "所以 RQ1 主数字仍取无缓存的首次运行")
    ap.add_argument("--force", action="store_true", help="已有结果也重跑")
    args = ap.parse_args()
    _FORCE = args.force

    if args.only:
        from mutloop import mutator
        rows = []
        for item in args.only.split(","):
            proj, mod = item.split(":", 1)
            n = len(mutator.enumerate_mutants(get(proj).package_dir / mod))
            rows.append((n, proj, mod))
        rows.sort()
    else:
        rows = list_modules(args.schedule)
        if args.max_modules:
            rows = rows[: args.max_modules]

    if not rows:
        print("没有待跑模块")
        return 0

    print(f"待跑 {len(rows)} 个模块，共 {sum(r[0] for r in rows)} 变异体")
    print(f"并行 {args.workers} workers，预计 {sum(r[0] for r in rows) * 8 / args.workers / 60:.0f} 分钟\n")

    failed = []
    for i, (n, proj, mod) in enumerate(rows, 1):
        ok, msg = run_one(proj, mod, i, len(rows), args.workers,
                          args.schedule, args.verify, args.deadloop_cache)
        if not ok:
            failed.append((proj, mod, msg))
            print(f"\n[中止] {proj}/{mod} 未通过检查")
            break

    print(f"\n{'=' * 70}")
    if failed:
        for proj, mod, msg in failed:
            print(f"[失败] {proj}/{mod}: {msg}")
        return 1
    print(f"[完成] {len(rows)} 个模块全部通过检查")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
