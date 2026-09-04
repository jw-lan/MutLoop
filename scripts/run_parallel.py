"""并行跑单个模块的全部变异体（每个 worker 一份独立工作副本）。

为什么需要它
------------
S2 的 naive 全量跑法串行太慢：5190 个变异体 × 约 5s ≈ **7.2 小时**。
16 核机器上并行能把墙钟压到 1 小时以内。

为什么不直接用 S3 的调度层
--------------------------
S3 要做的是**覆盖率导向的测试选择**（每个变异体只跑覆盖它的测试），
那是 RQ1 的研究对象；这里的并行只是**把原本就要做的工作分摊到多核**，
不改变每个变异体跑什么测试，因此不影响任何实验结论的正确性。

正确性前提
----------
**每个 worker 必须有独立的工作副本**。多个进程共用一个目录会互相覆盖源码，
判定结果全乱。这是本实现最容易出错的地方，务必留意 `worker_dir()`。

用法
----
    python scripts/run_parallel.py --subject marshmallow --target utils.py
    python scripts/run_parallel.py --subject dateutil --target rrule.py --workers 8
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mutloop import deadloop_cache as dlc  # noqa: E402
from mutloop.mutator import enumerate_mutants  # noqa: E402
from mutloop import runner as runner_mod  # noqa: E402
from mutloop.runner import (  # noqa: E402
    control_run,
    default_timeout,
    in_package_path,
    prepare_workspace,
    run_one,
    summarize,
    write_results,
)
from mutloop.schedule import load_index, subset_wall_seconds  # noqa: E402
from mutloop.import_time import is_import_time  # noqa: E402
from mutloop.subjects import DATA_DIR, get  # noqa: E402

# 传给子进程的状态：必须可 pickle，且不能携带 libcst 节点（Mutant 不可序列化）
_WORKER: dict = {}


def worker_dir(base: Path, wid: int) -> Path:
    """第 wid 个 worker 的独立工作副本目录。

    **绝不能让两个 worker 共用目录**——它们会同时写同一个源文件，
    导致变异体张冠李戴。
    """
    return base / f"w{wid}"


def _init(subject: str, ws_root: str, timeout_s: float, originals: dict[str, str],
          schedule: str = "none", timeout_mode: str = "full",
          contention: float = 4.0, deadloop: dict | None = None,
          covered_lines: set[int] | None = None) -> None:
    from multiprocessing import current_process

    subj = get(subject)
    # 用进程名里的序号区分 worker；ProcessPoolExecutor 里是 ForkProcess-N / SpawnProcess-N
    name = current_process().name
    wid = 0
    if "-" in name:
        try:
            wid = int(name.rsplit("-", 1)[1])
        except ValueError:
            wid = 0
    _WORKER["subj"] = subj
    _WORKER["ws"] = prepare_workspace(subj, Path(worker_dir(Path(ws_root), wid)))
    _WORKER["timeout"] = timeout_s
    _WORKER["originals"] = originals
    _WORKER["schedule"] = schedule
    _WORKER["timeout_mode"] = timeout_mode
    _WORKER["contention"] = contention
    # 索引只在调度模式下加载；紧凑版约 1.8MB，加载 0.16s，每个 worker 各读一份
    _WORKER["index"] = load_index(subj.name) if schedule == "line" else None
    # 死循环缓存只读快照。worker 不回写——8 个进程并发写同一文件会损坏它，
    # 记录统一由父进程在池结束后做一次。
    _WORKER["deadloop"] = deadloop or {}
    # 增量模式：只枚举这些行的变异体（worker 里 _rebuild 要用同一份）
    _WORKER["covered_lines"] = covered_lines


def _plan(subj, m) -> tuple[list[str] | None, float, str]:
    """为单个变异体决定「跑哪些测试」和「等多久」。

    返回 (node_ids, timeout_s, selection 描述)。node_ids 为 None 表示跑全套件。

    为什么查不到覆盖数据时要退回全套件而不是跳过：coverage 只记录**语句行**，
    而变异体可能落在文档字符串、装饰器、默认值等非语句位置上。这类位置没有
    覆盖率信息，此时唯一安全的选择就是全套件——宁可没省时间，也不能把
    killed 错判成 survived。
    """
    if _WORKER["schedule"] != "line":
        return None, _WORKER["timeout"], "full"

    # 类体 / 模块级 / 函数签名默认值：只在 import 时执行一次，覆盖率归属不可靠，
    # 实测漏杀率 10.08%（函数体内只有 0.59%），一律退回全套件。详见 mutloop/import_time.py
    if is_import_time(subj.root / m.file, m.line):
        return None, _WORKER["timeout"], "full(import-time)"

    rel = str(in_package_path(subj, m.file)).replace("\\", "/")
    ids = _WORKER["index"].select(rel, m.line)
    if not ids:
        return None, _WORKER["timeout"], "full(no-coverage-data)"

    if _WORKER["timeout_mode"] == "subset":
        est = subset_wall_seconds(subj.name, len(ids))
        t = default_timeout(est) * _WORKER["contention"]
    else:
        t = _WORKER["timeout"]
    return ids, t, f"line:{len(ids)}"


def _run(rec: dict) -> dict:
    """子进程里跑单个变异体。rec 是 Mutant 的可序列化投影。"""
    subj = _WORKER["subj"]
    m = _rebuild(subj, rec)
    node_ids, timeout_s, selection = _plan(subj, m)

    # 死循环缓存命中：直接判 timeout，不真跑。
    # 结果带 from_cache=True，读者能一眼看出这是复用上一轮的知识而非实测。
    hit = dlc.lookup(_WORKER.get("deadloop") or {}, m.mutant_id, selection)
    if hit is not None:
        r = runner_mod.MutantResult(
            mutant_id=m.mutant_id, file=m.file, line=m.line,
            operator=m.operator, description=m.description,
            status=runner_mod.STATUS_TIMEOUT, duration_s=0.0,
            tests_run=len(node_ids) if node_ids else None,
            failed_tests=None, returncode=None,
            first_error_type=None, error_origin=None, first_error_line=None,
            kill_class=None, retries=0, selection=selection, from_cache=True,
            # 这条本身没跑、耗时 0，所以把"省下的时间"单独记下来，
            # 否则汇总时会显示省了 0 分钟，把真实收益抹掉
            cache_saved_s=round(float(hit.get("last_duration_s") or 0.0), 3),
        )
        r.kill_class = runner_mod.classify_kill(r)
        return r.as_record()

    out = run_one(
        subj,
        m,
        _WORKER["ws"],
        original_source=_WORKER["originals"][m.file],
        timeout_s=timeout_s,
        node_ids=node_ids,
    ).as_record()
    out["selection"] = selection
    return out


def _rebuild(subj, rec: dict):
    """在工作进程里重新枚举一次，按 **mutant_id** 找回 Mutant 对象。

    Mutant 持有 libcst 节点，不能跨进程 pickle，所以只传标量、进来再重建。
    枚举很快（纯 AST），这点开销可以接受。

    **必须按 mutant_id 匹配，不能用 (line, description)**：
    同一行同一算子的不同变体可能有完全相同的描述（实测 attrs L182 的两个变体
    都是 `SimpleString → SimpleString`），按描述匹配会把它们当成同一个，
    导致部分变异体的判定被覆盖丢失（60 个变异体里有 4 个被吃掉）。
    """
    cache = _WORKER.setdefault("cache", {})
    key = rec["file"]
    if key not in cache:
        # in_package_path 处理子目录模块：tz/win.py 不能退化成 win.py
        path = subj.package_dir / in_package_path(subj, key)
        cache[key] = {m.mutant_id: m for m in enumerate_mutants(
            path, covered_lines=_WORKER.get("covered_lines"))}
    mid = rec["mutant_id"]
    if mid not in cache[key]:
        raise KeyError(f"找不到变异体 {key}:{mid}")
    return cache[key][mid]


def parse_lines(spec: str) -> set[int]:
    """把 --lines 的行号说明解析成集合。支持逗号分隔与范围，如 `59,100-105`。"""
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return out


def diff_lines(repo_root: Path, target: Path) -> set[int]:
    """用 `git diff`（工作树 vs HEAD）取改动行，返回新文件里的行号集合。

    增量模式（PR 级）的"改动行"来源。只统计新增/修改后的新行号，
    删除的行不产生变异体（它们已经不存在了）。
    """
    import re
    import subprocess

    rel = target.relative_to(repo_root).as_posix()
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "diff", "--unified=0", "--", rel],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    lines: set[int] = set()
    for ln in proc.stdout.splitlines():
        if ln.startswith("@@"):
            m = re.search(r"\+(\d+)(?:,(\d+))?", ln)
            if m:
                start = int(m.group(1))
                count = int(m.group(2) or 1)
                lines.update(range(start, start + count))
    return lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", required=True)
    ap.add_argument("--target", required=True, help="目标源码文件名")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--schedule", choices=["none", "line"], default="none",
                    help="none=朴素全套件（S2 行为，默认）；"
                         "line=覆盖率导向，每个变异体只跑覆盖该行的测试")
    ap.add_argument("--timeout-mode", choices=["full", "subset"], default="full",
                    help="full=阈值按全套件耗时推导（与 S2 一致，做对照实验时用这个）；"
                         "subset=按所选测试子集耗时推导（省机时，见 HANDOVER §6）")
    ap.add_argument("--verify", action="store_true",
                    help="二阶段：把调度后幸存的变异体拿回全套件复核，消除漏杀、结果精确")
    ap.add_argument("--deadloop-cache", choices=["off", "on", "seed"], default="off",
                    help="死循环结果缓存（S3 修复二）。"
                         "off=不启用（默认，保持 S3 基线语义）；"
                         "on=读已有缓存、跳过已确认死循环、跑完回写新发现；"
                         "seed=先用 S2 与 S3 两次已有结果离线预置缓存再跑。"
                         "注意：首次运行全新项目时收益为 0，只对重复运行有效")
    ap.add_argument("--lines", help="增量模式：只变异指定行，逗号分隔，支持范围，"
                                   "如 `59,100-105`")
    ap.add_argument("--diff", action="store_true",
                    help="增量模式：用 git diff（工作树 vs HEAD）自动取改动行（与 --lines 互斥）")
    ap.add_argument("--out", help="输出 JSON 路径")
    args = ap.parse_args()

    subj = get(args.subject)
    target = subj.package_dir / args.target
    if not target.exists():
        print(f"[错误] 目标不存在: {target}")
        return 1

    # ---- 增量模式：只变异改动行 ------------------------------------------
    if args.lines and args.diff:
        print("[错误] --lines 与 --diff 互斥")
        return 1
    covered_lines: set[int] | None = None
    if args.lines:
        covered_lines = parse_lines(args.lines)
        print(f"增量模式：只变异行 {sorted(covered_lines)}")
    elif args.diff:
        covered_lines = diff_lines(subj.root, target)
        if not covered_lines:
            print("[错误] git diff 工作树无改动行（--diff 需要先有未提交的改动）")
            return 1
        print(f"增量模式：git diff 改动行 {sorted(covered_lines)}")

    mutants = enumerate_mutants(target, covered_lines=covered_lines)
    print(f"=== {subj.name} / {target.name}　{len(mutants)} 个变异体 ===")

    wall = json.loads((DATA_DIR / "baseline" / f"{subj.name}.json").read_text(
        encoding="utf-8"))["timing"]["wall_seconds"]
    # **并行时超时阈值必须按 worker 数放宽。**
    # default_timeout（baseline×4）是按单进程独占 CPU 设计的；并行 N 个 worker 抢 CPU，
    # 单个变异体会慢数倍——实测 click/shell_completion 串行 38s 的变异体，
    # 8 workers 并行下超过 110s，结果 205 个里 198 个被误判成 timeout。
    # 超时的目的是抓「真死循环」而不是「慢」，阈值宽松只会让死循环多等一会儿，
    # 阈值过紧则会把正常的慢变异体全部错杀。
    #
    # **已知代价（S3 要解决）**：阈值按全套件耗时推导，而 S3 调度后每个变异体只跑
    # 一个测试子集，同样宽松的倍数就明显过头了。实测 S2 的 149 个超时全部集中在
    # dateutil/rrule.py（全套件仅 7.5s，阈值 30×4=120s），光等死循环就烧掉约
    # 5 CPU 小时。S3 应改为按「所选测试子集的耗时」推导阈值，rrule 子集约 3.5s，
    # 阈值回落到 30s 下限即可，这一项就能省一半以上。
    contention = max(1, args.workers // 2)
    timeout = default_timeout(wall) * contention
    ws_root = DATA_DIR / "_ws" / subj.name
    print(f"调度 {args.schedule}　超时模式 {args.timeout_mode}"
          f"　基准阈值 {timeout:.0f}s（并行 {args.workers} workers 已放宽）")
    if args.schedule == "line":
        # 预热一次，确保索引可用——不要等 8 个 worker 各自报错才发现
        load_index(subj.name)
        print(f"  行级索引已就绪（{subj.name}）")

    # 先做对照，确认工作副本与真源码等价——不过关就不该往下跑
    ctrl_ws = prepare_workspace(subj, ws_root)
    rc, tail = control_run(subj, ctrl_ws, timeout)
    if rc != 0:
        print(f"[中止] 对照未通过 rc={rc}：{tail}")
        return 1
    print(f"对照通过：rc={rc}")

    originals: dict[str, str] = {}
    for m in mutants:
        if m.file not in originals:
            originals[m.file] = (
                subj.package_dir / in_package_path(subj, m.file)
            ).read_text(encoding="utf-8")

    # ---- 死循环缓存（修复二）--------------------------------------------
    # 哈希取**被测源文件的原始内容**：源码一改，行号和变异体都会变，
    # 整份缓存作废，绝不复用旧判定。
    source_sha = dlc.source_sha256(originals[mutants[0].file]) if mutants else ""
    deadloop: dict = {}
    if args.deadloop_cache != "off" and mutants:
        if args.deadloop_cache == "seed":
            # 用 S2（朴素全套件）与 S3（调度）两次**独立**运行结果预置。
            # 两次都判 timeout 的变异体直接 confirmed，本次即可跳过。
            runs = []
            s2 = DATA_DIR / "s2" / f"{subj.name}-{target.stem}.json"
            s3 = DATA_DIR / "s3" / f"{subj.name}-{target.stem}-sched.json"
            if s2.exists():
                runs.append(("s2-naive", json.loads(
                    s2.read_text(encoding="utf-8"))["results"]))
            if s3.exists():
                runs.append(("s3-sched", json.loads(
                    s3.read_text(encoding="utf-8"))["results"]))
            deadloop, n_conf = dlc.seed_from_runs(
                subj.name, args.target, source_sha, runs)
            print(f"死循环缓存：由 {len(runs)} 次历史运行预置，"
                  f"已确认 {n_conf} 个可跳过")
        else:
            deadloop = dlc.load(subj.name, args.target, source_sha)
            n_conf = sum(1 for e in deadloop.get("entries", {}).values()
                         if e.get("confirmed"))
            print(f"死循环缓存：已确认 {n_conf} 个可跳过")

    recs = [{"file": m.file, "mutant_id": m.mutant_id} for m in mutants]

    print(f"并行 {args.workers} 个 worker 开始…")
    t0 = time.time()
    results = []
    done = 0
    with ProcessPoolExecutor(
        max_workers=args.workers,
        initializer=_init,
        initargs=(args.subject, str(ws_root), timeout, originals,
                  args.schedule, args.timeout_mode, contention, deadloop,
                  covered_lines),
    ) as ex:
        futures = {ex.submit(_run, r): r for r in recs}
        for fu in as_completed(futures):
            results.append(fu.result())
            done += 1
            if done % 100 == 0:
                el = time.time() - t0
                eta = el / done * (len(recs) - done)
                print(f"  {done}/{len(recs)}　已用 {el / 60:.1f}m　预计剩 {eta / 60:.1f}m",
                      flush=True)

    # ---- 二阶段：幸存者回全套件复核 --------------------------------------
    # 在 killed ↔ survived 之间，覆盖率导向的误差是单向的：只会把 killed 误判成
    # survived（漏杀）。所以把幸存者拿回全套件重跑，漏杀就能找回。
    #
    # **但误差整体不是单向的（2026-09-02 实测，见 HANDOVER §6）**：还有一类
    # **假杀**——变异让 pytest 收集阶段崩溃时，跑全套件是 stillborn（rc=2，排除出分母），
    # 而只跑 1 条测试时收集成功、该测试失败，就被记成 killed。
    # 实测修复前有 90 个这样的假杀（82 个在 dateutil/rrule.py），修复一后降到 2 个。
    #
    # 所以**只复核幸存者并不足以得到精确结果**，被判 killed 的那批里也可能有假杀。
    # 本脚本的 --verify 目前只复核 survivor，用它得到的"精确"要按上面的口径打折。
    n_verify = 0
    recovered = 0
    if args.verify and args.schedule == "line":
        to_verify = [
            r for r in results
            if r["status"] == "survived" and str(r.get("selection", "")).startswith("line:")
        ]
        if to_verify:
            n_verify = len(to_verify)
            print(f"\n二阶段复核：{n_verify} 个幸存者回全套件重跑…")
            recs2 = [{"file": next(m.file for m in mutants if m.mutant_id == r["mutant_id"]),
                      "mutant_id": r["mutant_id"]} for r in to_verify]
            with ProcessPoolExecutor(
                max_workers=args.workers,
                initializer=_init,
                # schedule="none"：这一阶段必须跑全套件，不能再剪
                initargs=(args.subject, str(ws_root), timeout, originals,
                          "none", args.timeout_mode, contention, {},
                          covered_lines),
            ) as ex:
                for fu in as_completed([ex.submit(_run, r) for r in recs2]):
                    r2 = fu.result()
                    for i, r in enumerate(results):
                        if r["mutant_id"] == r2["mutant_id"]:
                            if r2["status"] != "survived":
                                recovered += 1
                            r2["selection"] = "full(verified)"
                            results[i] = r2
                            break
            print(f"  复核找回 {recovered} 个被漏杀的变异体")

    # 回写缓存：只记**真跑出来**的 timeout（from_cache 的不记，否则会自我确认、
    # 永远不失效）。并发写有风险，所以放在池结束后由父进程做一次。
    if args.deadloop_cache != "off" and mutants:
        run_id = f"{args.schedule}-{args.timeout_mode}-w{args.workers}"
        n = dlc.record(deadloop, results, run_id, source_sha)
        p = dlc.save(deadloop)
        n_conf = sum(1 for e in deadloop.get("entries", {}).values()
                     if e.get("confirmed"))
        n_hit = sum(1 for r in results if r.get("from_cache"))
        print(f"死循环缓存：本次记录 {n} 个 timeout，累计已确认 {n_conf} 个 "
              f"（本次跳过 {n_hit} 个）-> {p}")

    dt = time.time() - t0
    from mutloop.runner import MutantResult

    objs = [MutantResult(**r) for r in results]
    summary = summarize(objs)
    print()
    print(f"  状态: {summary['by_status']}")
    print(f"  变异分数  传统 {summary['mutation_score']}%　"
          f"严格 {summary.get('strict_mutation_score')}%")
    print(f"  killed 含金量: {summary.get('kill_classes')}")
    print(f"  墙钟 {dt / 60:.1f} 分钟（串行预计 {len(mutants) * (dt / len(mutants)) / 60:.1f} 分钟）")

    if args.out:
        out = Path(args.out)
    elif args.schedule == "line":
        out = DATA_DIR / "s3" / f"{subj.name}-{target.stem}-sched.json"
    else:
        out = DATA_DIR / "s2" / f"{subj.name}-{target.stem}.json"
    if args.schedule == "line":
        n_sched = sum(1 for r in results if str(r.get("selection", "")).startswith("line:"))
        execution = (f"coverage-guided-line(workers={args.workers},"
                     f"timeout={args.timeout_mode},"
                     f"selected={n_sched}/{len(results)}"
                     + (f",verified={n_verify},recovered={recovered}"
                        if args.verify else "") + ")")
    else:
        execution = f"naive-full-suite-parallel(workers={args.workers})"
    write_results(out, {
        "subject": subj.name,
        "tag": subj.tag,
        "target": target.name,
        "operators": sorted(set(m.operator for m in mutants)),
        "baseline_wall_seconds": wall,
        "timeout_seconds": timeout,
        "execution": execution,
        "summary": summary,
        "results": results,
    })
    print(f"结果已写入: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
