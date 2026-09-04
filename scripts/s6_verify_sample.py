"""S6 pilot：验证一个 bug 样本的前提是否成立（用 git worktree，不污染主工作区）。

前提（两条都满足，样本才合格）：
  ① T_old（修复前的测试文件）在 V_bug（修复前代码）上**全过** → 证明测试确实弱、没抓到 bug
  ② T_fix（修复后的测试文件）在 V_bug 上**有失败**            → 证明 bug 真实存在且新测试能抓它

**为什么用 worktree**：早期版本直接在 subjects/ 上 checkout，一旦中途被中断，
主工作区就会停在历史 commit（实测把 marshmallow 的 16 个 tests/ 文件删掉了）。
改用 worktree 后，所有版本切换都在 data/_s6wt/ 的独立副本里进行，
即使中断，subjects/ 主工作区也毫发无损（worktree remove 在下次运行时清理即可）。

用法
----
    python scripts/s6_verify_sample.py --auto 8     # 取 candidates.json 前 N 个试
    python scripts/s6_verify_sample.py --proj attrs --fix 1c962d15a7 \
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

from mutloop.subjects import DATA_DIR, get  # noqa: E402

WT_ROOT = ROOT / "data" / "_s6wt"
PY = sys.executable


def _git(proj: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT / "subjects" / proj), *args],
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def run_test_in(proj: str, wt: Path, test_file: str, timeout: int = 600):
    """在 worktree 里跑单个测试文件。PYTHONPATH 指向 worktree 的源码根，
    确保 import 的是该版本的代码而非 editable install 的当前版本。"""
    subj = get(proj)
    env = dict(os.environ)
    # package 形如 "src/attr" → 源码根 = wt/src；若形如 "click" → 源码根 = wt
    src_root = wt / Path(subj.package).parent
    env["PYTHONPATH"] = str(src_root)
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    proc = subprocess.run(
        [PY, "-m", "pytest", test_file, "-p", "no:cacheprovider",
         "--no-header", "-q", "--tb=line"],
        cwd=str(wt), capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env, timeout=timeout)
    tail = (proc.stdout or "").strip().splitlines()
    return proc.returncode, (tail[-1] if tail else "")


def _cleanup_worktree(proj: str, wt: Path) -> None:
    """彻底清理 worktree：git 注销 + 强制删目录。

    Windows 上 pytest 可能短暂占用文件导致删除失败，所以重试几次。
    残留目录会让下一次 worktree add 报 "already exists"。
    """
    _git(proj, "worktree", "remove", "--force", str(wt))
    _git(proj, "worktree", "prune")
    for _ in range(4):
        if not wt.exists():
            return
        shutil.rmtree(wt, ignore_errors=True)
        time.sleep(0.4)


def verify(proj: str, fix_hash: str, test_file: str, src_file: str) -> dict:
    bug_hash = _git(proj, "rev-parse", f"{fix_hash}^").stdout.strip()
    res = {"proj": proj, "fix_hash": fix_hash, "bug_hash": bug_hash,
           "src_file": src_file, "test_file": test_file}
    wt = WT_ROOT / f"{proj}_{bug_hash[:10]}"
    # 清理可能残留的 worktree（上次中断/失败留下的）——否则 add 会报 already exists
    _cleanup_worktree(proj, wt)
    WT_ROOT.mkdir(parents=True, exist_ok=True)
    cp = _git(proj, "worktree", "add", "--force", "--detach", str(wt), bug_hash)
    if cp.returncode != 0:
        res["premise_ok"] = False
        res["tail_old"] = f"worktree 创建失败: {cp.stderr.strip()[:120]}"
        return res
    try:
        # ① worktree 此刻 = C_bug（源码+测试都是修复前版本）→ 期望全过
        rc_old, tail_old = run_test_in(proj, wt, test_file)
        res["rc_old_on_bug"], res["tail_old"] = rc_old, tail_old
        # ② 只把修复后的测试文件取过来（源码仍是 C_bug）→ 期望失败
        _git(proj, "checkout", "--quiet", fix_hash, "--", test_file) if False else None
        subprocess.run(["git", "-C", str(wt), "checkout", fix_hash, "--", test_file],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
        rc_new, tail_new = run_test_in(proj, wt, test_file)
        res["rc_new_on_bug"], res["tail_new"] = rc_new, tail_new
        res["premise_ok"] = (rc_old == 0) and (rc_new != 0)
        return res
    finally:
        _cleanup_worktree(proj, wt)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--proj")
    ap.add_argument("--fix")
    ap.add_argument("--test-file")
    ap.add_argument("--src-file")
    ap.add_argument("--auto", type=int, help="自动取 candidates.json 前 N 个试")
    args = ap.parse_args()

    if args.auto:
        cands = json.loads((DATA_DIR / "s6" / "candidates.json").read_text(encoding="utf-8"))
        picked = cands[:args.auto]
    else:
        picked = [{"proj": args.proj, "hash": args.fix,
                   "src": [[args.src_file, 0]], "test": [[args.test_file, 0]]}]

    ok, out = 0, []
    for c in picked:
        proj, fix = c["proj"], c["hash"]
        src_file, test_file = c["src"][0][0], c["test"][0][0]
        r = verify(proj, fix, test_file, src_file)
        out.append(r)
        if r.get("premise_ok"):
            ok += 1
        print(f"{'✓ 合格' if r.get('premise_ok') else '✗ 不合格'}  {proj}/{src_file}  {fix[:10]}")
        print(f"      修复前测试@bug版: rc={r.get('rc_old_on_bug')}  {r.get('tail_old','')[:70]}")
        print(f"      修复后测试@bug版: rc={r.get('rc_new_on_bug')}  {r.get('tail_new','')[:70]}")

    print(f"\n合格 {ok}/{len(picked)}")
    (DATA_DIR / "s6").mkdir(parents=True, exist_ok=True)
    json.dump(out, open(DATA_DIR / "s6" / "verified_samples.json", "w", encoding="utf-8"),
              indent=1, ensure_ascii=False)
    print("结果已存 data/s6/verified_samples.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
