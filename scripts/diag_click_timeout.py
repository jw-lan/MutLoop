"""诊断 click/shell_completion.py 96.6% 超时的根因。

上次体检：205 个变异体里 198 个超时（timeout=110s），覆盖全部算子，
不是某个算子特有。stdin=DEVNULL 没有解决。

诊断方法：用与 runner **完全一致**的命令跑一个超时变异体，
但用 Popen 非阻塞轮询，每 10 秒看一次 stdout 是否还在增长——
区分"在慢慢跑"和"完全卡死"。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mutloop import mutator, runner  # noqa: E402
from mutloop.subjects import get  # noqa: E402

PY = sys.executable


def build_cmd(subj) -> list[str]:
    """与 runner.run_one 完全一致的 pytest 命令。"""
    args = [PY, "-m", "pytest", *subj.test_paths, "-p", "no:cacheprovider",
            "--no-header", "-q", "--tb=line", "-rf"]
    for d in subj.deselect:
        args += ["--deselect", d]
    return args


def observe(subj, ws: Path, label: str, timeout_s: float = 120) -> None:
    """跑一次并轮询 stdout 增长，判断是慢还是死锁。"""
    env = dict(os.environ, PYTHONPATH=str(ws),
               PYTHONDONTWRITEBYTECODE="1")
    cmd = build_cmd(subj)
    p = subprocess.Popen(cmd, cwd=str(subj.root), stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace", env=env,
                         stdin=subprocess.DEVNULL)
    t0 = time.time()
    chunks: list[str] = []
    last_len = 0
    while True:
        rc = p.poll()
        el = time.time() - t0
        if rc is not None:
            out = "".join(chunks)
            tail = out.strip().splitlines()[-3:] if out.strip() else ["<无输出>"]
            print(f"[{label}] 完成 rc={rc} 耗时 {el:.1f}s")
            for l in tail:
                print(f"    {l[:100]}")
            return
        if el > timeout_s:
            p.kill()
            out = "".join(chunks)
            n = len(out)
            tail = out.strip().splitlines()[-3:] if out.strip() else ["<无输出>"]
            print(f"[{label}] **超时被杀**（{el:.0f}s）stdout 共 {n} 字符")
            for l in tail:
                print(f"    {l[:100]}")
            return
        time.sleep(2)
        # 非阻塞读：Windows 上 text 模式没有方便的 select，改用定期读一小段
        try:
            import msvcrt
            while msvcrt.kbhit():  # 仅清键盘，无实际作用
                break
        except ImportError:
            pass
        # 简单轮询：靠 poll 为主，这里每 10 秒打印一次存活状态
        if int(el) % 10 == 0 and int(el) > 0 and int(el) != last_len:
            last_len = int(el)
            print(f"[{label}] {el:.0f}s 仍在运行…", flush=True)


def main() -> int:
    subj = get("click")
    target = subj.package_dir / "shell_completion.py"
    ws = runner.prepare_workspace(subj, ROOT / "data" / "_ws" / "click_diag")
    dest = ws / subj.package_dir.name / "shell_completion.py"
    original = target.read_text(encoding="utf-8")

    print("=== 第 1 步：未变异基线（工作副本）===")
    observe(subj, ws, "基线", timeout_s=90)

    print("\n=== 第 2 步：一个超时变异体（L36 CR）===")
    mutants = mutator.enumerate_mutants(target)
    m = next((x for x in mutants if x.line == 36 and x.operator == "CR"), mutants[0])
    print(f"变异体: L{m.line} {m.operator} {m.description[:60]}")
    dest.write_text(m.source(), encoding="utf-8")
    try:
        observe(subj, ws, "变异", timeout_s=120)
    finally:
        dest.write_text(original, encoding="utf-8")
        print("[已还原]")

    print("\n=== 第 3 步：只跑收集（--co）看是不是收集阶段就卡 ===")
    env = dict(os.environ, PYTHONPATH=str(ws), PYTHONDONTWRITEBYTECODE="1")
    t0 = time.time()
    p = subprocess.run([PY, "-m", "pytest", *subj.test_paths, "-p", "no:cacheprovider",
                        "--no-header", "-q", "--collect-only"],
                       cwd=str(subj.root), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env,
                       stdin=subprocess.DEVNULL, timeout=90)
    el = time.time() - t0
    out = (p.stdout or "").strip().splitlines()
    print(f"收集耗时 {el:.1f}s rc={p.returncode} 输出 {len(out)} 行")
    if out:
        print(f"    尾行: {out[-1][:90]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
