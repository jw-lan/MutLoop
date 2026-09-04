"""变异体执行与判定（S2）。

设计要点：

1. **不改动真实源码。** 每个被测项目先把包整体复制到一个工作目录，变异只作用在
   副本上，用 `PYTHONPATH` 让副本盖过 editable 安装。这样进程被中断也不会留下
   被污染的源码——变异测试最大的工程风险就是这个。

2. **S2 只求判定正确，不求快。** 每个变异体跑**全量测试套件**（naive 跑法），
   作为 ground truth。覆盖率导向调度是 S3 的事，届时要拿这份结果做对照，
   确认"少跑测试"没有漏掉任何 kill。

3. **状态判定**：
   - `compile_error`：变异后的源码无法编译／导入（不是被测试杀死，必须区分开）
   - `killed`：有测试失败
   - `survived`：全部通过
   - `timeout`：超过阈值（变异常造成死循环）
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from .mutator import Mutant
from .select_plugin import (ENV_VAR, ENV_VAR_FAILED, PLUGIN_NAME, init_failed_report,
                            read_failed_report, write_plugin, write_selection)
from .subjects import Subject

STATUS_KILLED = "killed"
STATUS_SURVIVED = "survived"
STATUS_TIMEOUT = "timeout"
# 语法不合法：变异后的源码连 compile() 都过不了，是算子生成了坏代码
STATUS_COMPILE_ERROR = "compile_error"
# 死胎变异体（stillborn）：语法合法，但变异让被测代码在**测试收集阶段**就崩了，
# 一个测试都没跑到，因此无法判定 killed/survived。
# 实测成因举例：marshmallow 的 utils.py L59 变异后，tests/base.py 在导入期抛
# StringNotCollectionError，pytest 收集失败并返回 rc=4。
# 这类**不是**编译错误——叫 compile_error 会误导读者以为是语法问题。
STATUS_STILLBORN = "stillborn"

ALL_STATUSES = (
    STATUS_KILLED,
    STATUS_SURVIVED,
    STATUS_TIMEOUT,
    STATUS_COMPILE_ERROR,
    STATUS_STILLBORN,
)

# 进入变异分数分母的状态。
# **timeout 也算可判定**：变异让程序死循环、测试跑不完，这本身就是"变异产生了
# 可观察影响"，等价于被杀死（文献里 timeout 通常算 killed）。
# 排除的只有 stillborn / compile_error——它们连测试都没跑起来，无法判定。
JUDGED_STATUSES = (STATUS_KILLED, STATUS_SURVIVED, STATUS_TIMEOUT)

# --------------------------------------------------------------------------
# killed 的三种「含金量」，用于严格口径的变异分数
# --------------------------------------------------------------------------
# 程序崩溃：变异让代码跑出一个本不该有的异常，测试因此失败。
# 这种 killed 不能证明测试有检测能力——任何执行到那一行的测试都会挂。
# 例：删实参导致 TypeError、字典键被改导致 KeyError。
CRASH_EXCEPTIONS = frozenset({
    "TypeError", "AttributeError", "IndexError", "KeyError", "NameError",
    "ZeroDivisionError", "UnboundLocalError", "OSError", "LookupError",
    "RecursionError", "ImportError", "NotImplementedError", "StopIteration",
})
# 业务异常是**设计内**抛出的（如 marshmallow 的 ValidationError），测试常显式断言它，
# 算真实检测。判定方式很简单：不在上面的集合里就算业务异常或断言命中。

KILL_CRASH = "crash"          # 程序崩溃，严格口径下不计入 killed
KILL_BUSINESS = "business"    # 业务异常被触发，算真实检测
KILL_ASSERTION = "assertion"  # 断言 / 契约检查（DID NOT RAISE）命中


def classify_kill(result: "MutantResult") -> str | None:
    """给一个被杀死的变异体定性（含 timeout）。survived/死胎返回 None。

    判据：首个失败的**错误类型** + **抛出位置**。
    - 超时（死循环）→ crash，属廉价 kill
    - 崩溃类异常 且 从被测代码内部冒出 → crash
    - AssertionError / Failed（pytest 的 DID NOT RAISE 等）→ assertion
    - 其余（业务异常等）→ business
    """
    if result.status not in (STATUS_KILLED, STATUS_TIMEOUT):
        return None
    # timeout：死循环让测试跑不完，与崩溃同属"程序异常"而非"测试主动检测到差异"
    if result.status == STATUS_TIMEOUT:
        return KILL_CRASH
    t = result.first_error_type or ""
    if t in CRASH_EXCEPTIONS and result.error_origin == "package":
        return KILL_CRASH
    if t in ("AssertionError", "Failed"):
        return KILL_ASSERTION
    return KILL_BUSINESS


@dataclass
class MutantResult:
    mutant_id: str
    file: str
    line: int
    operator: str
    description: str
    status: str
    duration_s: float
    tests_run: int | None = None
    failed_tests: list[str] | None = None
    returncode: int | None = None
    # 首个失败的错误类型（AssertionError / TypeError / ValidationError / ...）
    first_error_type: str | None = None
    # 错误抛出位置：tests（测试代码主动检查命中）/ package（被测代码自己崩了）
    error_origin: str | None = None
    # 原始 traceback 行。存它是为了事后重新归类时不必重跑（一次全量 28 分钟）
    first_error_line: str | None = None
    # killed 的含金量（crash / business / assertion），见 classify_kill
    kill_class: str | None = None
    # 异常 rc 的重试次数（0 或 1），用于区分偶发失败与真实死胎
    retries: int = 0
    # S3 调度层记录：本变异体实际跑了哪些测试。
    #   "line:N"                  —— 覆盖率导向，跑了覆盖该行的 N 条测试
    #   "full"                    —— 跑全套件（调度关闭）
    #   "full(no-coverage-data)"  —— 想调度但这一行没有覆盖率数据，安全退回全套件
    selection: str | None = None
    # S3 修复二：本条结果是否**来自死循环缓存**（没真跑，是查表判的 timeout）。
    # 必须显式标记——读者有权知道哪些判定是实测、哪些是复用上一轮的知识。
    # 缓存只在**重复运行**时才有收益，首次运行恒为 False（详见 deadloop_cache.py）。
    from_cache: bool = False
    # 命中缓存时，这个变异体**上一次实跑**花了多少秒。
    # 缓存条目本身 duration_s=0（没跑），所以省下的时间必须单独记，
    # 否则报告里会显示"省了 0 分钟"，把 200.3 分钟的收益抹掉。
    cache_saved_s: float = 0.0

    def as_record(self) -> dict:
        return asdict(self)


def prepare_workspace(subj: Subject, workdir: Path) -> Path:
    """把被测包复制一份到工作目录，返回可以直接塞进 PYTHONPATH 的路径。

    幂等：目录已存在就只做增量覆盖，不删除任何东西（沙箱禁止删除）。
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    dest = workdir / subj.package_dir.name
    src = subj.package_dir
    for p in src.rglob("*"):
        if p.is_dir():
            continue
        # 绝不复制 __pycache__：里面的 .pyc 会按「源文件 mtime(秒) + 大小」判定
        # 是否失效，同一次运行内多次覆盖源文件时可能命中旧字节码，
        # 导致变异根本没生效却被判成 survived。
        if "__pycache__" in p.parts or p.suffix in (".pyc", ".pyo"):
            continue
        rel = p.relative_to(src)
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if not out.exists() or out.read_bytes() != p.read_bytes():
            out.write_bytes(p.read_bytes())
    return workdir


def control_run(subj: Subject, workspace: Path, timeout_s: float) -> tuple[int, str]:
    """对照：不改变任何代码，用工作副本跑一遍完整套件。

    这一步是 S2 正确性的地基。它同时验证两件事：
    1. 工作副本与真源码等价（复制没漏文件、PYTHONPATH 生效）
    2. 基线是绿的——如果基线本来就红，后续"测试失败"就不能判定为 kill
    """
    import os

    args = [sys.executable, "-m", "pytest", *subj.test_paths, "-p", "no:cacheprovider",
            "--no-header", "-q", "--tb=no"]
    for d in subj.deselect:
        args += ["--deselect", d]
    env = dict(os.environ, PYTHONPATH=str(workspace), PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run(
        args, cwd=str(subj.root), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout_s, env=env,
    )
    tail = (proc.stdout or "").strip().splitlines()
    return proc.returncode, (tail[-1] if tail else "")


def _compile_ok(source: str, filename: str) -> bool:
    try:
        compile(source, filename, "exec")
        return True
    except (SyntaxError, ValueError):
        return False


def _parse_failed_tests(stdout: str) -> list[str]:
    out = []
    for line in stdout.splitlines():
        s = line.strip()
        if s.startswith("FAILED "):
            out.append(s[len("FAILED "):].split(" ")[0])
    return out


# `--tb=line` 每行形如：  C:\...\tests\test_schema.py:560: Failed: DID NOT RAISE xxx
# 需要的是冒号分隔的第 3 段（错误类型）和路径（判断抛出位置）。
# 注意 Windows 盘符本身带冒号（C:\），所以不能用 split(":", 2)，必须先剥掉盘符。
_TB_RE = re.compile(
    r"^(?P<path>[A-Za-z]:[\\/].*?):(?P<lineno>\d+):\s*(?P<rest>.*)$"
)


def _parse_first_error(
    stdout: str, workspace: Path | None = None
) -> tuple[str | None, str | None, str | None]:
    """从 `--tb=line` 的输出里取首个失败的 (错误类型, 抛出位置, 原始行)。

    error_origin 的区分意义：
    - `tests`   —— 异常由测试代码的检查逻辑触发（assert 不成立、DID NOT RAISE 等），
                   说明是**测试主动发现**的行为变化，属于高质量的 killed
    - `package` —— 异常从被测工作副本内部冒出来（TypeError/AttributeError 等），
                   说明代码直接崩了，测试只是"碰巧"失败，属于廉价的 killed

    这条区分是判断某个算子是否"刷分"的关键证据。

    **判据必须看工作副本目录，不能看包名**：被测项目根往往就叫 `marshmallow`，
    于是 `subjects/marshmallow/tests/test_x.py` 里也含 `marshmallow/`，
    用包名匹配会把所有测试侧失败误判成被测代码崩溃（第一版就犯了这错）。

    同时返回原始行，便于事后重新归类而**不必重跑**（一次全量要 28 分钟）。
    """
    def norm(p: str) -> str:
        # pytest 输出的路径分隔符与 Path.resolve() 未必一致（实测一边是 / 一边是 \），
        # 不统一的话 startswith 恒为 False，会把所有 package 侧失败误判成 tests
        return p.replace("/", os.sep).replace("\\", os.sep)

    ws = norm(str(workspace.resolve())) if workspace else None
    for line in stdout.splitlines():
        raw = line.strip()
        m = _TB_RE.match(raw)
        if not m:
            continue
        rest = m.group("rest")
        path = m.group("path")
        # pytest 的断言重写：assert 失败时输出的是展开后的表达式
        # （"assert a == b"），而不是 "AssertionError: ..."，需要归一化
        if rest.startswith("assert "):
            err_type = "AssertionError"
        else:
            err_type = rest.split(":", 1)[0].strip() or None
        origin = "package" if (ws and norm(path).startswith(ws)) else "tests"
        return err_type, origin, raw
    return None, None, None


def in_package_path(subj: Subject, file: str) -> Path:
    """把 Mutant.file（相对项目根，如 `src/dateutil/tz/win.py`）转成**包内**相对路径。

    为什么不能 `Path(file).name`：那会把子目录吃掉——`tz/win.py` 变成 `win.py`，
    于是去 `package_dir/win.py` 找文件，而真实位置是 `package_dir/tz/win.py`。
    顶层模块（utils.py 等）碰巧不受影响，所以这个 bug 直到跑第一个子目录模块
    `dateutil/tz/win.py` 才被体检抓出来。
    """
    pkg = subj.package.replace("\\", "/").rstrip("/")
    f = file.replace("\\", "/")
    if f.startswith(pkg + "/"):
        return Path(f[len(pkg) + 1:])
    return Path(Path(f).name)


def run_one(
    subj: Subject,
    mutant: Mutant,
    workspace: Path,
    *,
    original_source: str,
    timeout_s: float,
    node_ids: list[str] | None = None,
) -> MutantResult:
    """跑单个变异体并判定状态。"""
    dest = workspace / subj.package_dir.name / in_package_path(subj, mutant.file)
    # 先还原成原始内容，再写入本变异体的改动——保证同一工作目录可复用
    dest.write_text(original_source, encoding="utf-8")

    t0 = time.perf_counter()
    mutated_source = mutant.source()
    if not _compile_ok(mutated_source, str(dest)):
        return MutantResult(
            mutant_id=mutant.mutant_id, file=mutant.file, line=mutant.line,
            operator=mutant.operator, description=mutant.description,
            status=STATUS_COMPILE_ERROR, duration_s=round(time.perf_counter() - t0, 3),
        )

    dest.write_text(mutated_source, encoding="utf-8")

    # --tb=line（而非 --tb=no）：-rf 的 FAILED 行只在部分情况下带错误类型，
    # 拿不稳就无法判断 killed 是"断言命中"还是"代码崩了"。--tb=line 每行稳定给出
    # "<文件>:<行号>: <错误类型>: <消息>"，据此可解析错误类型与抛出位置。
    # 选择集**不经过命令行**传递：Windows 命令行上限 32767 字符，
    # 而一个变异行可能被 500+ 条测试覆盖，nodeid 拼起来会直接超限
    # （WinError 206，表现为整个模块以一个和变异体无关的错误失败）。
    # 改为写入文件 + `-p mutloop_select` 插件在收集阶段过滤。
    select_file = None
    targets: list[str] = list(subj.test_paths)
    # 插件**无条件**装配：它除了过滤选择集，还负责把失败用例的 nodeid 落盘。
    # 不调度时也要它，否则 failed_tests 会退回解析终端输出，而参数化用例的
    # nodeid 含空格，解析会截断（详见 select_plugin.py 的注释）。
    write_plugin(workspace)
    if node_ids:
        # 命令行上只放**涉及到的测试文件**（最多二十来个），具体用例由插件筛。
        # 为什么不直接传 subj.test_paths 让插件过滤：pytest 的收集成本是固定
        # 开销的大头（2.9–4.6s 里占一大块），全量收集会把调度省下的时间吃回去。
        targets = sorted({n.split("::")[0] for n in node_ids})
        select_file = write_selection(workspace, node_ids)
    args = [sys.executable, "-m", "pytest", *targets,
            "-p", "no:cacheprovider", "--no-header", "-q", "--tb=line", "-rf",
            "-p", PLUGIN_NAME]
    for d in subj.deselect:
        args += ["--deselect", d]
    # 属性测试每次随机生成用例，同一变异体两次判定可能不同。固定 seed 保证可复现。
    if subj.uses_hypothesis:
        args += ["--hypothesis-seed=0"]

    env = None
    import os

    # PYTHONDONTWRITEBYTECODE=1：根本不生成 .pyc，从源头上消除"旧字节码被复用"
    # 导致假 survived 的风险。代价是每次 import 都要重新编译（几百毫秒），
    # 相比判定错误的代价完全可以接受。
    env = dict(os.environ, PYTHONPATH=str(workspace), PYTHONDONTWRITEBYTECODE="1")
    if select_file is not None:
        env[ENV_VAR] = str(select_file)
    failed_file = init_failed_report(workspace)
    env[ENV_VAR_FAILED] = str(failed_file)

    # hypothesis 会把失败的用例存进数据库（默认 .hypothesis/），**跨运行累积**。
    # 这会造成"前一个变异体的失败例子影响后一个变异体的判定"——实测 attrs L376
    # 单独重跑 3 次都是 survived，放进批量跑却变成 stillborn/killed。
    # 而且串行与并行的累积路径不同（并行每 worker 只累积 1/8），导致两者结果对不上。
    #
    # 所以隔离粒度必须是**每个变异体一个库**，不是每个 worker、更不是全局。
    if subj.uses_hypothesis:
        env["HYPOTHESIS_DATABASE_FILE"] = str(
            workspace / ".hyp" / (mutant.mutant_id or "unknown")
        )

    status = STATUS_SURVIVED
    rc: int | None = None
    stdout = ""
    retries = 0
    # stdin=DEVNULL **必须给**：click 这类 CLI 项目的测试里有交互式输入，
    # 变异一旦破坏输入处理，进程就会挂在等 stdin 上，直到超时。
    # 实测 click/shell_completion.py 205 个变异体里有 **196 个（95.6%）** 因此超时，
    # 白跑 48 分钟。喂 DEVNULL 后进程立刻拿到 EOF，该失败就失败，不会死等。
    stdin = subprocess.DEVNULL

    try:
        proc = subprocess.run(
            args, cwd=str(subj.root), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout_s, env=env,
            stdin=stdin,
        )
        rc = proc.returncode
        stdout = proc.stdout or ""

        # rc ∉ {0,1} 表示测试没能正常跑完。**并行时这可能是偶发的资源竞争**
        # （实测 attrs L175 在串行下 killed、并行下偶发 stillborn），
        # 而 stillborn 判定代价大（一个真实变异体被白白排除），所以重试一次。
        # 成本很低：失败的收集一般只有 2–4 秒。
        if rc not in (0, 1):
            retries = 1
            time.sleep(0.5)
            # 重跑前必须清空失败文件，否则两次的结果会拼在一起
            init_failed_report(workspace)
            proc = subprocess.run(
                args, cwd=str(subj.root), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout_s, env=env,
                stdin=stdin,
            )
            rc = proc.returncode
            stdout = proc.stdout or ""
    except subprocess.TimeoutExpired:
        status = STATUS_TIMEOUT
    else:
        if rc == 0:
            status = STATUS_SURVIVED
        elif rc == 1:
            status = STATUS_KILLED
        else:
            # rc ∉ {0,1} 意味着测试没有正常跑完：
            #   2=收集被中断  3=pytest 内部错误  4=配置/用法错误  5=没收集到任何用例
            # 实测本项目全部是 4，成因是变异让 conftest/测试模块在导入期抛异常，
            # 即 stillborn（死胎）。它既不是 killed 也不是 survived，排除出判定。
            # 注意：rc=5（一条用例都没收集到）是**环境问题而非变异体问题**，
            # 若大量出现必须报警排查，不能默默当成 stillborn 丢弃。
            status = STATUS_STILLBORN
            if rc == 5:
                stdout = (stdout or "") + (
                    "\n[mutloop] rc=5：没有收集到任何用例，疑为环境问题而非变异体问题"
                )

    # 还原，避免影响后续变异体
    dest.write_text(original_source, encoding="utf-8")

    # 只在 killed 时解析错误类型：survived 没有失败，stillborn 连测试都没跑
    err_type: str | None = None
    err_origin: str | None = None
    err_line: str | None = None
    if status == STATUS_KILLED:
        err_type, err_origin, err_line = _parse_first_error(stdout, workspace)

    # 失败用例优先读插件落盘的文件（nodeid 完整）；万一插件没生效（例如子进程
    # 被超时杀掉、sessionfinish 没跑到）才退回解析终端输出，宁可拿到截断的 ID
    # 也不能什么都没有。
    failed = read_failed_report(failed_file) if status == STATUS_KILLED else []
    if status == STATUS_KILLED and not failed:
        failed = _parse_failed_tests(stdout)

    result = MutantResult(
        mutant_id=mutant.mutant_id, file=mutant.file, line=mutant.line,
        operator=mutant.operator, description=mutant.description,
        status=status, duration_s=round(time.perf_counter() - t0, 3),
        tests_run=len(node_ids) if node_ids else None,
        failed_tests=failed or None,
        returncode=rc, first_error_type=err_type, error_origin=err_origin,
        first_error_line=err_line, kill_class=None, retries=retries,
    )
    result.kill_class = classify_kill(result)
    return result


def default_timeout(baseline_wall_s: float) -> float:
    """超时阈值：基线的 4 倍，但不低于 30 秒。

    变异很容易造出死循环；阈值太小会把慢变异误判成 timeout。
    """
    return max(30.0, baseline_wall_s * 4)


def run_all(
    subj: Subject,
    mutants: list[Mutant],
    workspace: Path,
    *,
    timeout_s: float,
    node_ids: list[str] | None = None,
    progress_every: int = 25,
) -> list[MutantResult]:
    """顺序执行全部变异体。S2 只做串行；并发留给 S3。"""
    pkg_dir = subj.package_dir
    originals: dict[str, str] = {}
    results: list[MutantResult] = []
    for i, m in enumerate(mutants, 1):
        if m.file not in originals:
            originals[m.file] = (
                pkg_dir / in_package_path(subj, m.file)
            ).read_text(encoding="utf-8")
        results.append(
            run_one(subj, m, workspace, original_source=originals[m.file],
                    timeout_s=timeout_s, node_ids=node_ids)
        )
        if i % progress_every == 0:
            done = [r.status for r in results]
            print(f"    {i}/{len(mutants)}  "
                  f"killed={done.count(STATUS_KILLED)} survived={done.count(STATUS_SURVIVED)} "
                  f"timeout={done.count(STATUS_TIMEOUT)} "
                  f"stillborn={done.count(STATUS_STILLBORN)} "
                  f"compile_error={done.count(STATUS_COMPILE_ERROR)}", flush=True)
    return results


def summarize(results: list[MutantResult]) -> dict:
    by_status: dict[str, int] = {s: 0 for s in ALL_STATUSES}
    by_op: dict[str, dict[str, int]] = {}
    for r in results:
        by_status[r.status] = by_status.get(r.status, 0) + 1
        op = by_op.setdefault(r.operator, {s: 0 for s in ALL_STATUSES})
        op[r.status] = op.get(r.status, 0) + 1

    judged = [r for r in results if r.status in JUDGED_STATUSES]
    killed = [r for r in judged if r.status == STATUS_KILLED]
    n_timeout = sum(1 for r in judged if r.status == STATUS_TIMEOUT)
    # 传统口径（主用）：timeout 算 killed（死循环是变异产生的可观察影响），
    # crash 也算 killed。分子 = killed + timeout。
    score = (
        round(100.0 * (len(killed) + n_timeout) / len(judged), 2) if judged else 0.0
    )

    # kill 质量：区分"测试主动发现"与"代码崩了被碰巧捕获"。
    # 依据 error_origin —— 异常由测试代码抛出 vs 从被测包内部冒出来。
    # 廉价 kill 占比高 = 这个算子在刷分，不是真的测出了东西。
    kill_quality: dict[str, dict] = {}
    for r in killed:
        q = kill_quality.setdefault(
            r.operator, {"killed": 0, "by_tests": 0, "by_package": 0, "unknown": 0}
        )
        q["killed"] += 1
        if r.error_origin == "tests":
            q["by_tests"] += 1
        elif r.error_origin == "package":
            q["by_package"] += 1
        else:
            q["unknown"] += 1
    for op, q in kill_quality.items():
        q["cheap_kill_rate"] = (
            round(100.0 * q["by_package"] / q["killed"], 2) if q["killed"] else 0.0
        )

    err_types: dict[str, int] = {}
    for r in killed:
        if r.first_error_type:
            err_types[r.first_error_type] = err_types.get(r.first_error_type, 0) + 1

    # kill 含金量统计。遍历 judged 里的"被杀死者"（killed + timeout），
    # timeout 被 classify_kill 归为 KILL_CRASH。
    killed_all = [r for r in judged if r.status in (STATUS_KILLED, STATUS_TIMEOUT)]
    kill_classes: dict[str, int] = {}
    for r in killed_all:
        kc = r.kill_class or "unknown"
        kill_classes[kc] = kill_classes.get(kc, 0) + 1
    n_cheap = kill_classes.get(KILL_CRASH, 0)  # crash + timeout

    # 严格口径（辅助）：廉价 kill（崩溃 + 超时）不计入分子，但仍留在分母。
    # 只保留"测试主动检测到差异"的部分（断言命中 + 业务异常）。
    # 两种口径都报告；主用传统口径，理由见报告第 8 节。
    strict_score = (
        round(100.0 * (len(killed_all) - n_cheap) / len(judged), 2) if judged else 0.0
    )

    return {
        "total_mutants": len(results),
        "by_status": by_status,
        "by_operator": by_op,
        "mutation_score": score,
        "strict_mutation_score": strict_score,
        "judged_mutants": len(judged),
        "total_wall_seconds": round(sum(r.duration_s for r in results), 2),
        "kill_quality": kill_quality,
        "kill_classes": kill_classes,
        "first_error_types": dict(
            sorted(err_types.items(), key=lambda kv: -kv[1])
        ),
    }


def write_results(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
