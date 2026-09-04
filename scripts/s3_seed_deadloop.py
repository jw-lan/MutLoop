"""用 S2 + S3 两次已有运行结果，离线预置死循环缓存（修复二）。

不跑任何测试，只读 JSON，几秒钟出结果。用途是在真正的重跑之前，
先看清「能跳过多少、省多少机时」，而不是跑完才知道。

为什么要两次运行才算数
----------------------
单次超时可能是偶发（资源竞争、机器卡顿）。S2 是朴素全套件，S3 是调度子集，
两者是**两次独立运行**，且跑的测试集不同——两个都判 timeout 的变异体，
是真的死循环而不是某一次运气不好。

为什么要把「修复前的备份」也算一个来源
--------------------------------------
`record()` 会跳过 `from_cache` 的结果（否则缓存会自我确认、永远不失效）。
于是**当前 S3 结果里被跳过的那批死循环，在它自己那份结果里是没有记录的**——
只拿「S2 + 当前 S3」做种子，这批就会从 2 次掉回 1 次、从已确认变成未确认。

实测踩过：rrule 的 100 个死循环在重跑时全部命中缓存被跳过，
只用 S2 + 新 S3 重新预置后，已确认条目从 100 变成 31（那 31 个是重跑时新跑出来的），
原来的 100 个反而丢了。所以种子必须包含**修复前的备份**那次运行。

用法
----
    python scripts/s3_seed_deadloop.py              # 预置全部 12 个模块
    python scripts/s3_seed_deadloop.py --dry-run    # 只报告，不写缓存文件

省下的机时怎么估：把被跳过的变异体在**上一次 S3 运行里的 duration_s** 加起来。
这是上界——前提是它们这次也确实会超时（两次独立运行都超时，可信度较高）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mutloop import deadloop_cache as dlc  # noqa: E402
from mutloop.subjects import DATA_DIR, get  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只报告，不写缓存文件")
    args = ap.parse_args()

    sel = json.loads(
        (DATA_DIR / "module_selection.json").read_text(encoding="utf-8")
    )["selected"]

    print(f"{'模块':<30}{'S2超时':>7}{'S3超时':>7}{'两次都超时':>11}"
          f"{'省机时(分)':>11}")
    print("-" * 68)
    tot_conf = 0
    tot_saved = 0.0
    for proj, mods in sel.items():
        s = get(proj)
        for m in mods:
            module = m["module"]
            stem = Path(module).stem
            s2 = DATA_DIR / "s2" / f"{proj}-{stem}.json"
            s3 = DATA_DIR / "s3" / f"{proj}-{stem}-sched.json"
            if not (s2.exists() and s3.exists()):
                print(f"{proj + '/' + module:<30}  缺少 S2/S3 结果，跳过")
                continue
            r2 = json.loads(s2.read_text(encoding="utf-8"))["results"]
            r3 = json.loads(s3.read_text(encoding="utf-8"))["results"]
            # 修复前的备份也算一次独立运行：当前 S3 里被缓存跳过的那批，
            # 只在备份（和 S2）里有实跑记录，少了它就是 100 → 31 的倒退。
            bk = (DATA_DIR / "s3" / "_pre_fix_backup"
                  / f"{proj}-{stem}-sched.json")
            rbk = (json.loads(bk.read_text(encoding="utf-8"))["results"]
                   if bk.exists() else None)
            src = (s.package_dir / Path(module).name
                   if (s.package_dir / Path(module).name).exists()
                   else s.package_dir / module)
            if not src.exists():
                print(f"{proj + '/' + module:<30}  源文件不存在，跳过")
                continue
            sha = dlc.source_sha256(src.read_text(encoding="utf-8"))
            # 顺序有讲究：**越靠后越"新"**，selection 以最后一次为准。
            # 所以备份放当前 S3 之前——当前 S3 里实跑出来的超时才是最新状态。
            runs = [("s2-naive", r2)]
            if rbk is not None:
                runs.append(("s3-sched-prefix", rbk))
            runs.append(("s3-sched", r3))
            cache, n_conf = dlc.seed_from_runs(proj, module, sha, runs)
            # 省下的机时 = 已确认条目**最近一次实跑**的耗时之和
            saved = sum(e.get("last_duration_s", 0.0)
                        for e in cache["entries"].values() if e.get("confirmed"))
            n2 = sum(1 for r in r2 if r.get("status") == "timeout")
            n3 = sum(1 for r in r3 if r.get("status") == "timeout")
            print(f"{proj + '/' + module:<30}{n2:>7}{n3:>7}{n_conf:>11}"
                  f"{saved / 60:>11.1f}")
            tot_conf += n_conf
            tot_saved += saved
            if not args.dry_run and n_conf:
                dlc.save(cache)

    print("-" * 68)
    print(f"{'合计':<30}{'':>7}{'':>7}{tot_conf:>11}{tot_saved / 60:>11.1f}")
    if args.dry_run:
        print("\n（--dry-run：未写缓存文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
