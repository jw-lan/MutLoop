"""死循环（timeout）变异体结果缓存 —— S3 修复二。

它解决什么
----------
变异常常造出死循环。判定一个变异体是不是死循环，唯一的办法是**等它跑满超时**——
dateutil/rrule.py 有 100 个这样的变异体，每个白等 120 秒，合计 200.3 分钟，
占 12 个模块总机时的 27.7%。而且这个判定是**确定的**：S3 跑出的 100 个超时
全是 S2 那 149 个真死循环的子集，零新增，说明重复跑只会重复烧掉同样的时间。

缓存把"已知死循环"记下来，下次直接判 timeout，不再等待。

为什么必须带失效判据
--------------------
缓存的前提是「这个变异体**仍然**是死循环」。源码一改，行为就可能变，
缓存会过期。所以每条记录都绑定**被测源文件的 sha256**，
文件变了整份缓存作废（同一个模块的所有条目一起失效，不做逐条判断——
源码改动会移动行号，逐条比对反而更危险）。

同理，`selection` 也进缓存键：调度后每个变异体只跑一个测试子集，
而"是否死循环"取决于跑哪些测试。全套件下不死的变异体，在子集下可能死，
反之亦然。所以只有当次规划的 selection 与记录一致时才敢复用。

为什么要求「两次独立运行都超时」才生效
--------------------------------------
单次超时可能是偶发（资源竞争、机器卡顿）。要求两次独立运行都判 timeout
才置 confirmed，可以把偶发误判挡在外面。这个规则是通用的，不是针对某个项目调的。

已知的方法论局限（写 RQ1 时必须避开）
------------------------------------
**它的收益依赖"已知哪些是死循环"。** 首次运行一个新项目时没有这份清单，
死循环只能靠等满超时发现，收益为 **0**。只有重复运行（比如 S5 补测后重评分）
才有那 200.3 分钟。所以 4.28x **不能**当成 RQ1 的加速比——它把"上一轮跑出来的
知识"算进了这一轮的收益，属于跨运行信息复用，对全新项目不成立。

对外可泛化的 RQ1 主数字是**首次运行的 3.27x**（修复一后、无缓存）。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .subjects import DATA_DIR

CACHE_VERSION = 1
# 缓存目录放在 data/s3/ 下，与调度结果同生命周期
CACHE_DIRNAME = "_deadloop"
# 置 confirmed 所需的独立运行次数。2 = 要求两次独立运行都判 timeout。
CONFIRM_RUNS = 2


def source_sha256(text: str) -> str:
    """被测源文件内容的哈希，用作缓存失效判据。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_selection(selection: str | None) -> str:
    """把 selection 归一化成缓存键。

    **凡是跑全套件的都归成 `full`**——`None`（S2 朴素模式不写这个字段）、
    `full`、`full(import-time)`、`full(no-coverage-data)`、`full(verified)`
    跑的都是同一批测试，死循环与否当然一样，不该因为标签不同就白白重跑。

    `line:N` 保留原样：跑的是覆盖该行的 N 条测试，测试集不同、行为可能不同，
    必须严格匹配才能复用。
    """
    if selection is None:
        return "full"
    s = str(selection)
    if s == "full" or s.startswith("full("):
        return "full"
    return s


def cache_path(subject: str, module: str) -> Path:
    """每个 (项目, 模块) 一份缓存文件，避免并发写同一份。"""
    stem = Path(module).stem
    return DATA_DIR / "s3" / CACHE_DIRNAME / f"{subject}-{stem}.json"


def new_cache(subject: str, module: str, source_sha: str) -> dict:
    return {
        "version": CACHE_VERSION,
        "subject": subject,
        "module": module,
        "source_sha256": source_sha,
        "entries": {},
    }


def load(subject: str, module: str, source_sha: str) -> dict:
    """读缓存。文件缺失或损坏都返回空缓存——缓存只是加速，坏了不能让主流程挂。"""
    p = cache_path(subject, module)
    if not p.exists():
        return new_cache(subject, module, source_sha)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return new_cache(subject, module, source_sha)
    if data.get("version") != CACHE_VERSION:
        return new_cache(subject, module, source_sha)
    # 源码变了：整份作废。行号会随改动漂移，逐条比对比全丢更危险。
    if data.get("source_sha256") != source_sha:
        return new_cache(subject, module, source_sha)
    return data


def lookup(cache: dict, mutant_id: str, selection: str) -> dict | None:
    """查一个变异体能否直接判 timeout。命中返回条目，否则 None。

    三个条件同时满足才复用：
    1. 在缓存里
    2. 已 confirmed（≥2 次独立运行都超时）
    3. 当次规划的 selection 与记录一致（跑的测试集一样）
    """
    e = cache.get("entries", {}).get(mutant_id)
    if not e:
        return None
    if not e.get("confirmed"):
        return None
    if e.get("selection") != normalize_selection(selection):
        return None
    return e


def record(cache: dict, results: list[dict], run_id: str, source_sha: str) -> int:
    """把本次运行中新发现的 timeout 记进缓存，返回新增/更新的条目数。

    只记真跑出来的 timeout（from_cache 的不记，否则会自我确认、永远不失效）。
    """
    if cache.get("source_sha256") != source_sha:
        # 调用方传错哈希：宁可整份作废，也不混进不同源码的判定
        cache["entries"] = {}
        cache["source_sha256"] = source_sha
    entries = cache.setdefault("entries", {})
    n = 0
    for r in results:
        if r.get("status") != "timeout":
            continue
        if r.get("from_cache"):
            continue
        mid = r.get("mutant_id")
        if not mid:
            continue
        e = entries.setdefault(mid, {
            "file": r.get("file"),
            "line": r.get("line"),
            "operator": r.get("operator"),
            "selection": normalize_selection(r.get("selection")),
            "runs": [],
            "confirmed": False,
            "last_duration_s": 0.0,
        })
        runs = e.setdefault("runs", [])
        if run_id not in runs:
            runs.append(run_id)
        # selection 以**最近一次**为准：调度策略变了就按新的记
        e["selection"] = normalize_selection(r.get("selection"))
        # 记下这次实跑的耗时，供下次命中时报告"省了多少"
        e["last_duration_s"] = round(float(r.get("duration_s") or 0.0), 3)
        e["confirmed"] = len(runs) >= CONFIRM_RUNS
        n += 1
    return n


def save(cache: dict) -> Path:
    p = cache_path(cache["subject"], cache["module"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
    return p


def seed_from_runs(subject: str, module: str, source_sha: str,
                   runs: list[tuple[str, list[dict]]]) -> tuple[dict, int]:
    """用若干次**已有**运行结果预置缓存（离线，不跑测试）。

    每轮用不同的 run_id 依次 record，于是出现 ≥2 轮的变异体自动 confirmed。
    典型用法：拿 S2 朴素全套件 + S3 调度两次结果做种子——它们是两次独立运行，
    两次都超时的变异体是真死循环的可信度很高。

    返回 (cache, confirmed 条目数)。
    """
    cache = new_cache(subject, module, source_sha)
    for run_id, results in runs:
        record(cache, results, run_id, source_sha)
    n = sum(1 for e in cache["entries"].values() if e.get("confirmed"))
    return cache, n
