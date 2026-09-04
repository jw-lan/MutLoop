"""实测模块变动率（churn）：用前几个 release tag 的文件哈希对比，而不是靠命名猜。

为什么不用"下划线开头 = 不稳定"这种启发式：它会把 attrs 的 `_make.py` 也排掉，
而那恰恰是 attrs 最核心、最值得做变异分析的模块。

做法：对每个项目取最近 2 个历史 release tag，用 `git ls-tree -r` 列出包内每个文件的
blob 哈希，与当前锁定 tag 对比。哈希一致 = 该模块在这几次发布中没动过 = 稳定。

注意：**可复现性由锁定 release tag 保证，与模块是否稳定无关**。churn 影响的是结论的
"时效性"——一个下个版本就要重写的模块，在它上面得出的结论没有推广价值。

结果写入 data/module_churn.json。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mutloop.subjects import DATA_DIR, SUBJECTS  # noqa: E402

# 每个项目回看的历史 release tag（由新到旧）
HISTORY = {
    "attrs": ("25.2.0", "25.1.0"),
    "click": ("8.1.7", "8.1.6"),
    "dateutil": ("2.9.0", "2.8.2"),
    "jinja": ("3.1.5", "3.1.4"),
    "marshmallow": ("3.26.1", "3.26.0"),
}


def git(repo: Path, *args: str) -> tuple[int, str]:
    p = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return p.returncode, p.stdout


def ls_tree(repo: Path, rev: str, prefix: str) -> dict[str, str]:
    rc, out = git(repo, "ls-tree", "-r", rev, "--", prefix)
    if rc != 0:
        return {}
    files = {}
    for line in out.splitlines():
        if "\t" not in line:
            continue
        meta, path = line.split("\t", 1)
        parts = meta.split()
        if len(parts) >= 3:
            files[path.replace("\\", "/")] = parts[2]
    return files


def main() -> int:
    result: dict[str, dict] = {}
    for name, tags in HISTORY.items():
        subj = SUBJECTS[name]
        repo = subj.root
        prefix = subj.package

        for t in tags:
            git(repo, "fetch", "--depth", "1", "origin", f"refs/tags/{t}:refs/tags/{t}")

        current = ls_tree(repo, "HEAD", prefix)
        if not current:
            print(f"[跳过] {name}: 无法列出当前 tag 的文件树")
            continue

        pkg = prefix.split("/")[-1]
        per_module: dict[str, dict] = {}
        for path, blob in current.items():
            rel = path[len(prefix) + 1:] if path.startswith(prefix + "/") else Path(path).name
            if not rel.endswith(".py"):
                continue
            changed_in = []
            for t in tags:
                old = ls_tree(repo, t, prefix)
                if old and old.get(path) != blob:
                    changed_in.append(t)
            per_module[rel] = {
                "changed_in": changed_in,
                "stable": not changed_in,
            }

        n_stable = sum(1 for v in per_module.values() if v["stable"])
        result[name] = per_module
        print(f"{name:<12} 模块 {len(per_module):>3} 个，其中 {n_stable} 个在 "
              f"{', '.join(tags)} 中未改动")

    out = DATA_DIR / "module_churn.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n结果已写入: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
