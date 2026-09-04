"""识别「只在 import 时执行一次」的代码行。

为什么需要这个模块（实测发现，见 scripts/s3_import_time.py）
------------------------------------------------------------
覆盖率导向调度依赖一条前提：**杀死变异体的测试，必然执行过变异所在的那一行**。
对函数体内的代码这条前提成立，但对下面这些位置不成立：

    class UnprocessedParamType(ParamType):
        name = "text"        # ← 类体：整个进程只执行一次，在 import 时

这行代码只在 import 时跑一次，覆盖率只能把它归给"碰巧第一个触发导入"的测试。
而真正发现改动的测试（click 的 `test_info_dict.py::test_parameter[UNPROCESSED]`）
是通过 `to_info_dict()` **内省**这个值的——它压根不执行这一行。于是调度必然选错。

实测漏杀率（**以 12 模块全量 5349 个变异体的口径为准**，脚本 `scripts/s3_import_time.py`）：

    位置                      变异体   被调度   S2杀死   漏杀    漏杀率
    函数体内                    4540    4433    3065     13     0.42%
    类体/模块级/签名默认值       809     441     484     154    31.82%

相差 76 倍，92% 的漏杀集中在只占 15% 的变异体上。

> 早期曾按「11 个模块 3887 个变异体」统计过一版（函数体 0.59% / import-time 10.08%，
> 相差 17 倍，74%）。**那版口径已作废**，两版不能混用。

所以调度层对这类变异体一律退回全套件。宁可没省时间，也不能系统性低估分数。

**注意：这个修复代码已生效，但从未重跑验证。** 12 个模块的调度结果里
`full(import-time)` 选择数为 0，说明那轮实测跑在修复之前。
修复代价 +121.1 分钟、回收 154/167 漏杀，均为推算（见 `scripts/s3_fix_projection.py`）。

分类规则
--------
import-time（只在定义/导入时执行一次）：
    - 模块级语句
    - **类体**里的语句（类体在 import 时执行一次）
    - 函数的**签名部分**：默认值 `def f(x=False)` 的 `False`、注解、装饰器
      都在外层定义时刻求值，同样只在 import 时执行
func（每次调用都执行）：
    - 函数体 / 方法体内部
"""
from __future__ import annotations

import ast
from functools import lru_cache

IMPORT_TIME = "import-time"
FUNC_BODY = "func"
UNKNOWN = "unknown"


def _classify(path: str) -> dict[int, str]:
    """解析一个源文件，返回 {行号: 'import-time' | 'func'}。"""
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    ctx: dict[int, str] = {}

    def mark(node: ast.AST, tag: str) -> None:
        """把 node 覆盖的所有行标为 tag。先标外层，已标过的不覆盖。"""
        for n in ast.walk(node):
            ln = getattr(n, "lineno", None)
            if ln is None:
                continue
            en = getattr(n, "end_lineno", None) or ln
            for i in range(ln, en + 1):
                ctx.setdefault(i, tag)

    def walk_body(body, tag: str) -> None:
        for stmt in body:
            # 函数：装饰器 + 签名属于外层（定义时求值），只有 body 是运行时
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for d in stmt.decorator_list:
                    mark(d, tag)
                mark(stmt.args, tag)
                if getattr(stmt, "returns", None) is not None:
                    mark(stmt.returns, tag)
                walk_body(stmt.body, FUNC_BODY)
            # 类：类体整体在 import 时执行一次
            elif isinstance(stmt, ast.ClassDef):
                for d in stmt.decorator_list:
                    mark(d, tag)
                for b in stmt.bases:
                    mark(b, tag)
                for kw in stmt.keywords:
                    mark(kw, tag)
                walk_body(stmt.body, tag)
            else:
                mark(stmt, tag)
                # 嵌套的函数/类定义（if 里套 def 等）继续下钻
                for ch in ast.iter_child_nodes(stmt):
                    if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        walk_body([ch], tag)

    walk_body(tree.body, IMPORT_TIME)
    # 空行、纯注释等没有 AST 节点的位置：保守当作未知（后续按 import-time 处理）
    for i in range(1, len(src.splitlines()) + 1):
        ctx.setdefault(i, UNKNOWN)
    return ctx


@lru_cache(maxsize=None)
def _classify_cached(path: str) -> dict[int, str]:
    return _classify(path)


def line_tag(path: str, line: int) -> str:
    """返回某行的上下文标签。解析失败时返回 'unknown'（按最保守方式处理）。"""
    try:
        return _classify_cached(str(path)).get(line, UNKNOWN)
    except Exception:
        # 读不到或解析不了源文件，绝不猜：交给调用方按"不可调度"处理
        return UNKNOWN


def is_import_time(path: str, line: int) -> bool:
    """该行是否只在 import 时执行一次（含解析失败的未知行）。

    未知一律按 import-time 处理——这是保守方向，最多不省时间，
    不会把 killed 错判成 survived。
    """
    return line_tag(path, line) != FUNC_BODY
