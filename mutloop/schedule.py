"""S3 调度层：覆盖率导向的测试选择。

朴素做法（S2）是「每个变异体都跑完整测试套件」。本模块回答的问题是：
**这个变异体改的是第 N 行，那么哪些测试真的执行到了第 N 行？**
只把这些测试挑出来跑，就是覆盖率导向调度的全部。

为什么可以这样剪
----------------
一个测试如果压根没执行到被改动的语句，它就不可能发现这次改动——
它的输入没走到那段代码，输出也就不会变。这是覆盖率导向调度的理论依据。

已知的可靠性代价（实测，见 data/s3/coverage_probe.json）
-------------------------------------------------------
S2 的 5349 个结果里有 2981 个 killed 可以反查「杀死它的测试是否覆盖了变异行」，
其中 **5.6% 是被没覆盖该行的测试杀死的**。这些在调度后会从 killed 退化成 survived，
即**漏杀**：分数被系统性低估，是下界，不是随机误差。

漏杀率分布极不均匀（推测与子进程测试不被 coverage 追踪有关，未证实）：
    jinja 0.0% / attrs 0.0% / marshmallow 0.8–1.4% /
    dateutil 2.4–10.3% / click 6.1–13.0%

补救手段：把调度后幸存的变异体拿回全套件复核一次，漏杀全部找回，结果精确。
代价见 HANDOVER.md §6。

索引为什么要紧凑化
------------------
`data/s3/line_index_<项目>.json` 直接存 nodeid 字符串，dateutil 有 21MB、
jinja 有 41MB。8 个 worker 各 pickle 一份既慢又占内存。紧凑版把 nodeid 换成
在该项目测试列表里的**下标**，体积降一个数量级。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .subjects import DATA_DIR

S3_DIR = DATA_DIR / "s3"


@dataclass
class LineIndex:
    """行级覆盖率索引：行号 -> 覆盖该行的测试集合。

    lines 的键是**包内相对路径**（如 `tz/win.py`），与 `runner.in_package_path`
    的返回值一致；值是 `{行号: [测试下标...]}`，下标指向 `self.tests`。
    """

    tests: list[str] = field(default_factory=list)
    lines: dict[str, dict[int, list[int]]] = field(default_factory=dict)

    def select(self, rel_path: str, line: int, *, window: int = 5) -> list[str] | None:
        """返回覆盖 `rel_path:line` 的测试 nodeid 列表。

        找不到数据时退回 `None`，意思是「这个位置没有覆盖率信息，请跑全套件」——
        **绝不能返回空列表**，pytest 收到零个用例会报 rc=5，那既不是 killed
        也不是 survived，会污染判定。

        window：变异行不一定恰好是 coverage 记录的语句行。两类常见情况：
          - 多行表达式、链式调用——覆盖记在**首行**，变异却落在续行上
          - 文档字符串、装饰器、默认值——本身不是语句，最近的语句可能在其**下方**
        所以必须**双向**向外搜最近的已覆盖行。只向一个方向搜会漏掉一半：
        实测 jinja/nodes.py 有 56% 的变异体在单向回退下查不到数据。
        这属于**多加测试**而非少加，方向是安全的（最坏是没省时间）。
        """
        rel = rel_path.replace("\\", "/")
        lines = self.lines.get(rel)
        if not lines:
            return None
        if line in lines:
            return [self.tests[i] for i in lines[line]]
        for d in range(1, window + 1):
            for cand in (line - d, line + d):
                if cand in lines:
                    return [self.tests[i] for i in lines[cand]]
        return None

    def size_at(self, rel_path: str, line: int) -> int | None:
        """该行覆盖的测试条数（用于估算子集耗时）。"""
        ids = self.select(rel_path, line)
        return len(ids) if ids is not None else None


def compact_path(name: str) -> Path:
    return S3_DIR / f"compact_index_{name}.json"


def build_compact_index(name: str) -> Path:
    """把明文 line_index 转成紧凑的下标版。"""
    src = S3_DIR / f"line_index_{name}.json"
    if not src.exists():
        raise FileNotFoundError(
            f"缺少 {src}。先跑 python scripts/s3_coverage_probe.py 采集（约 110 秒）"
        )
    raw = json.loads(src.read_text(encoding="utf-8"))

    pool: dict[str, int] = {}
    tests: list[str] = []
    lines: dict[str, dict[int, list[int]]] = {}
    for rel, per_line in raw.items():
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

    out = compact_path(name)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"tests": tests, "lines": lines}), encoding="utf-8")
    return out


def load_index(name: str) -> LineIndex:
    """加载（必要时先紧凑化）某项目的行级索引。"""
    p = compact_path(name)
    if not p.exists():
        p = build_compact_index(name)
    d = json.loads(p.read_text(encoding="utf-8"))
    return LineIndex(
        tests=d["tests"],
        lines={rel: {int(k): v for k, v in per.items()} for rel, per in d["lines"].items()},
    )


def subset_wall_seconds(name: str, n_tests: int) -> float:
    """估算跑 n 条测试的耗时，用 S1 拟合的 a + b·n。

    拟合系数来自 data/per_mutant_overhead.json（对 1/5/20/100/300 条测试实测
    后最小二乘得到），a 是 pytest 进程启动 + 收集的固定开销（2.9–4.6s），
    它决定了覆盖率导向调度的加速比上限。
    """
    ov = json.loads((DATA_DIR / "per_mutant_overhead.json").read_text(encoding="utf-8"))
    s = ov[name]
    return s["fitted_fixed_overhead_s"] + s["fitted_per_test_s"] * n_tests
