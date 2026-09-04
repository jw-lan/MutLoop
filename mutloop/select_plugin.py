"""覆盖率导向调度的「测试选择插件」。

为什么需要它
------------
最直接的做法是把选中的测试 nodeid 当作命令行参数传给 pytest：

    pytest tests/test_a.py::test_x tests/test_a.py::test_y ...

但在 Windows 上会撞墙：**命令行总长上限 32767 字符**。实测
`marshmallow/utils.py` 有变异行被 500+ 条测试覆盖，nodeid 拼起来轻松超限，
报 `FileNotFoundError: [WinError 206] 文件名或扩展名太长`，
整个模块以一个和变异体毫无关系的错误失败。

解决办法：**把选择集写进文件，用 pytest 插件在收集阶段过滤**。
命令行上只多一个短参数 `-p mutloop_select`，选择集通过环境变量指向的文件传递
（环境变量本身也有长度上限，所以传的是**路径**而不是内容）。

用法
----
由 `runner.run_one` 自动装配，不需要手工调用：

    write_plugin(workspace)          # 把插件写进工作副本根目录（该目录已在 PYTHONPATH 里）
    env["MUTLOOP_SELECT_FILE"] = ... # 指向写好的选择集文件
    pytest -p mutloop_select ...
"""
from __future__ import annotations

from pathlib import Path

PLUGIN_NAME = "mutloop_select"
ENV_VAR = "MUTLOOP_SELECT_FILE"
ENV_VAR_FAILED = "MUTLOOP_FAILED_FILE"

# 插件源码以字符串形式内联：它必须能被**子进程里的 pytest** 导入，
# 所以得是一个真实存在于 PYTHONPATH 上的 .py 文件，不能靠闭包或 pickle 传过去。
PLUGIN_SOURCE = '''"""pytest 插件：只保留 MUTLOOP_SELECT_FILE 里列出的用例。

由 mutloop 自动生成，勿手工编辑。选择集为空或文件缺失时不做任何过滤，
以便退化为"跑全套件"而不是静默地一条都不跑。
"""
import os
from pathlib import Path


def pytest_collection_modifyitems(config, items):
    path = os.environ.get("MUTLOOP_SELECT_FILE")
    if not path:
        return
    try:
        keep = set(Path(path).read_text(encoding="utf-8").splitlines())
    except OSError:
        return
    keep.discard("")
    if not keep:
        return
    remaining, deselected = [], []
    for it in items:
        # nodeid 与 coverage 上下文、命令行参数的格式一致（相对 rootdir）
        (remaining if it.nodeid in keep else deselected).append(it)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = remaining


# ---------------------------------------------------------------------------
# 失败用例落盘
#
# 为什么不能解析终端输出：pytest 的简短摘要行形如
#     FAILED <nodeid> - <message>
# 而**参数化用例的 nodeid 本身含空格**，例如
#     tests/test_commands.py::test_group_with_args[args0-2-Error: ...]
# 按空格切分会切在括号内部。实测 data/s2 里 175151 条记录中有 12512 条（7.1%）
# 因此被截断，S4/S5 想定位"哪个测试杀死了哪个变异体"时这些 ID 全部不可用。
# nodeid 只能从 report 对象上取，所以这里用钩子直接落盘。
# ---------------------------------------------------------------------------
_FAILED = []


def pytest_runtest_logreport(report):
    # setup 期失败也算"这个测试没通过"，同样能证明变异被检测到；
    # teardown 失败通常与被测代码无关，不计入。
    if report.failed and report.when in ("setup", "call"):
        _FAILED.append(report.nodeid)


def pytest_sessionfinish(session, exitstatus):
    path = os.environ.get("MUTLOOP_FAILED_FILE")
    if not path:
        return
    try:
        Path(path).write_text("\\n".join(_FAILED), encoding="utf-8")
    except OSError:
        pass
'''


def write_plugin(workspace: Path) -> Path:
    """把插件写进工作副本根目录并返回路径。幂等。"""
    p = Path(workspace) / f"{PLUGIN_NAME}.py"
    p.write_text(PLUGIN_SOURCE, encoding="utf-8")
    return p


def write_selection(workspace: Path, node_ids: list[str], tag: str = "sel") -> Path:
    """把选择集写成一行一个 nodeid 的文件，返回路径。"""
    f = Path(workspace) / f".{tag}.txt"
    f.write_text("\n".join(node_ids), encoding="utf-8")
    return f


def init_failed_report(workspace: Path) -> Path:
    """清空失败用例文件并返回路径。

    必须在每次 pytest 之前调用：重试会再跑一遍进程，不清空就会把两次的
    结果拼接起来（同一条用例出现两次）。
    """
    f = Path(workspace) / ".failed.txt"
    f.write_text("", encoding="utf-8")
    return f


def read_failed_report(path: Path) -> list[str]:
    """读取落盘的失败用例 nodeid。文件不存在或为空时返回空列表。"""
    try:
        return [
            x for x in Path(path).read_text(encoding="utf-8").splitlines() if x
        ]
    except OSError:
        return []
