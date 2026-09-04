"""S6 完整流程：对单个 bug 样本跑「调度变异测试 → AI 补测 → 在 bug 版验证」。

流程（对应一个 bug 样本，proj + fix_hash）：
  1. 用 git worktree 拿到 V_fix（修复后正确代码）与 V_bug（修复前带 bug 代码）
  2. 在 V_fix 上采集行级覆盖率索引（复用 s3_coverage_probe.collect_line_index）
  3. 在 V_fix 上对 src_file 跑**调度**变异测试（复用 run_one + LineIndex.select）
     → 存活变异体 = 测试盲区
  4. 对每个存活变异体，AI 补测（复用 s5_probe_one.probe_one，root 指向 V_fix）
     → 生成测试 T_ai
  5. 把 T_ai 拿到 V_bug 上跑：
     - FAIL = 抓到真实 bug ✓（补测提升变异分数 → 换来真实缺陷检出能力）

为什么用 worktree 而非直接 checkout subjects：
  见 s6_verify_sample.py —— 直接动主工作区会被中断破坏。

用法
----
    python scripts/s6_run_flow.py --auto 3          # 取 verified_samples 前 N 个合格样本
    python scripts/s6_run_flow.py --proj attrs --fix 1c962d15a7 \
        --src-file src/attr/_make.py --test-file tests/test_make.py
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mutloop.mutator import enumerate_mutants  # noqa: E402
from mutloop.runner import (  # noqa: E402
    default_timeout, in_package_path, prepare_workspace, run_one,
)
from mutloop.schedule import LineIndex  # noqa: E402
from mutloop.subjects import DATA_DIR, get, with_root  # noqa: E402

WT_ROOT = ROOT / "data" / "_s6wt"
PY = sys.executable


def _git(proj: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT / "subjects" / proj), *args],
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def _cleanup_wt(proj: str, wt: Path) -> None:
    _git(proj, "worktree", "remove", "--force", str(wt))
    _git(proj, "worktree", "prune")
    for _ in range(4):
        if not wt.exists():
            return
        shutil.rmtree(wt, ignore_errors=True)
        time.sleep(0.4)


def _mk_wt(proj: str, commit: str) -> Path:
    wt = WT_ROOT / f"{proj}_{commit[:10]}"
    _cleanup_wt(proj, wt)
    WT_ROOT.mkdir(parents=True, exist_ok=True)
    cp = _git(proj, "worktree", "add", "--force", "--detach", str(wt), commit)
    if cp.returncode != 0:
        raise RuntimeError(f"worktree 创建失败: {cp.stderr.strip()[:200]}")
    return wt


def dict_to_lineindex(index: dict) -> LineIndex:
    """把 collect_line_index 的 {rel: {line: [nodeid]}} 转成 LineIndex。"""
    pool: dict[str, int] = {}
    tests: list[str] = []
    lines: dict[str, dict[int, list[int]]] = {}
    for rel, per_line in index.items():
        bucket: dict[int, list[int]] = {}
        for lineno, nodeids in per_line.items():
            ids = []
            for t in nodeids:
                i = pool.get(t)
                if i is None:
                    i = pool[t] = len(tests)
                    tests.append(t)
                ids.append(i)
            bucket[int(lineno)] = ids
        lines[rel] = bucket
    return LineIndex(tests=tests, lines=lines)


def run_one_sample(proj: str, fix_hash: str, bug_hash: str,
                   src_file: str, test_file: str, *,
                   api_key: str, model: str = "deepseek-v4-flash") -> dict:
    from scripts.s3_coverage_probe import collect_line_index
    from scripts.s5_probe_one import probe_one

    res = {"proj": proj, "fix_hash": fix_hash, "src_file": src_file}

    wt_fix = _mk_wt(proj, fix_hash)
    wt_bug = _mk_wt(proj, bug_hash)
    try:
        subj_fix = with_root(get(proj), wt_fix)

        # 2) 在 V_fix 上采集行级索引
        t0 = time.time()
        raw_idx, _wall = collect_line_index(proj, root=wt_fix)
        index = dict_to_lineindex(raw_idx)
        res["index_wall"] = round(time.time() - t0, 1)
        res["index_files"] = len(raw_idx)

        # 3) 在 V_fix 上对 src_file 跑调度变异测试
        # src_file 是相对项目根的完整路径（如 src/attr/_make.py），
        # 所以要用 subj.root / src_file，而不是 package_dir / src_file
        p = subj_fix.root / src_file
        mutants = list(enumerate_mutants(p))
        if not mutants:
            res["error"] = f"{src_file} 无变异体"
            return res
        res["n_mutants"] = len(mutants)
        ws = prepare_workspace(subj_fix, ROOT / "data" / "_s6ws" / proj)
        original = p.read_text(encoding="utf-8")
        wall = json.loads((DATA_DIR / "baseline" / f"{proj}.json")
                          .read_text(encoding="utf-8"))["timing"]["wall_seconds"]
        timeout = default_timeout(wall)
        survived = []
        t0 = time.time()
        for m in mutants:
            rel = str(in_package_path(subj_fix, m.file)).replace("\\", "/")
            node_ids = index.select(rel, m.line)
            r = run_one(subj_fix, m, ws, original_source=original,
                        timeout_s=timeout, node_ids=node_ids)
            if r.status == "survived":
                survived.append(m)
        res["mut_wall"] = round(time.time() - t0, 1)
        res["n_survived"] = len(survived)
        if not survived:
            res["n_killed_by_ai"] = 0
            res["n_caught_bug"] = 0
            res["conclusion"] = "无存活变异体（弱测试套件在正确代码上变异分数 100%？）"
            return res

        # 4) 对每个存活变异体 AI 补测（root 指向 V_fix）
        # probe_one 的 module 是「相对 package_dir 的文件名」，src_file 是相对项目根的完整路径
        rel_module = (src_file[len(subj_fix.package) + 1:]
                      if src_file.startswith(subj_fix.package + "/") else src_file)
        n_killed = 0
        n_caught = 0
        caught_ids = []
        for m in survived:
            r = probe_one(proj, rel_module, m.line, m.operator,
                          api_key=api_key, arm="directed", root=wt_fix, model=model)
            if r.get("status") != "killed":
                continue
            n_killed += 1
            code = r.get("code", "")
            # 5) 把 T_ai 拿到 V_bug 上跑
            tf = wt_bug / "tests" / f"test_s6_probe_{m.mutant_id[:8]}.py"
            tf.write_text(code, encoding="utf-8")
            env = dict(os.environ, PYTHONPATH=str(wt_bug / "src"),
                       PYTHONDONTWRITEBYTECODE="1")
            proc = subprocess.run(
                [PY, "-m", "pytest", str(tf), "-p", "no:cacheprovider",
                 "--no-header", "-q", "--tb=line"],
                cwd=str(wt_bug), capture_output=True, text=True,
                encoding="utf-8", errors="replace", env=env, timeout=300)
            if proc.returncode != 0:
                n_caught += 1
                caught_ids.append(m.mutant_id[:8])
        res["n_killed_by_ai"] = n_killed
        res["n_caught_bug"] = n_caught
        res["caught_ids"] = caught_ids
        res["conclusion"] = (f"{n_caught}/{n_killed} 个补测测试在 bug 版 FAIL"
                             if n_killed else "AI 未能杀死任何存活变异体")
        return res
    finally:
        _cleanup_wt(proj, wt_fix)
        _cleanup_wt(proj, wt_bug)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", type=int, help="取 verified_samples 前 N 个合格样本")
    ap.add_argument("--proj")
    ap.add_argument("--fix")
    ap.add_argument("--src-file")
    ap.add_argument("--test-file")
    ap.add_argument("--model", default="deepseek-v4-flash")
    args = ap.parse_args()

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("[错误] 缺少 DEEPSEEK_API_KEY")
        return 1

    if args.auto:
        vf = DATA_DIR / "s6" / "verified_samples.json"
        samples = [r for r in json.loads(vf.read_text(encoding="utf-8"))
                   if r.get("premise_ok")][:args.auto]
    else:
        samples = [{"proj": args.proj, "fix_hash": args.fix,
                    "src_file": args.src_file, "test_file": args.test_file,
                    "bug_hash": None}]

    out = []
    for s in samples:
        bug_hash = s.get("bug_hash") or _git(s["proj"], "rev-parse",
                                             f"{s['fix_hash']}^").stdout.strip()
        print(f"\n=== {s['proj']}/{s['src_file']}  fix={s['fix_hash'][:10]} ===")
        r = run_one_sample(s["proj"], s["fix_hash"], bug_hash,
                           s["src_file"], s.get("test_file", ""), api_key=api_key,
                           model=args.model)
        out.append(r)
        print(f"  变异体 {r.get('n_mutants')}，存活 {r.get('n_survived')}，"
              f"AI 杀死 {r.get('n_killed_by_ai')}，在 bug 版 FAIL {r.get('n_caught_bug')}")
        print(f"  结论: {r.get('conclusion')}")

    (DATA_DIR / "s6").mkdir(parents=True, exist_ok=True)
    json.dump(out, open(DATA_DIR / "s6" / "flow_results.json", "w", encoding="utf-8"),
              indent=1, ensure_ascii=False)
    print(f"\n结果已存 data/s6/flow_results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
