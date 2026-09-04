"""静态度量：代码规模与测试断言强度。

这一层不执行任何测试，纯 AST/tokenize 分析，秒级完成。
之所以把"断言密度"做成一等公民指标：S1 的选型目标不是找覆盖率低的项目
（那种项目变异分数必然低，没有故事），而是找"覆盖率高但断言弱"的模块——
这类模块覆盖率虚高、变异分数低，正是本项目要解决的问题。
"""
from __future__ import annotations

import ast
import io
import tokenize
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "FileMetrics",
    "file_metrics",
    "package_metrics",
    "test_file_metrics",
    "TestFunc",
]

_SKIP_DIRS = {
    "__pycache__",
    ".git",
    ".tox",
    ".venv",
    "venv",
    "build",
    "dist",
    ".mypy_cache",
    ".pytest_cache",
    ".hypothesis",
    "node_modules",
    "docs",
}


def iter_python_files(root: Path) -> list[Path]:
    """遍历 root 下的 .py 文件，跳过缓存/构建/文档目录。"""
    if root.is_file():
        return [root] if root.suffix == ".py" else []
    out: list[Path] = []
    for p in sorted(root.rglob("*.py")):
        if any(part in _SKIP_DIRS for part in p.relative_to(root).parts[:-1]):
            continue
        out.append(p)
    return out


@dataclass
class FileMetrics:
    path: str
    loc: int = 0            # 物理行数
    sloc: int = 0           # 非空、非纯注释行（含 docstring 行）
    docstring_lines: int = 0  # docstring 覆盖的行数
    ncloc: int = 0          # sloc - docstring_lines，最接近"真代码量"
    ast_stmts: int = 0      # AST 语句节点数（变异算子的潜在落点规模）

    @property
    def rel(self) -> str:
        return self.path


def _docstring_line_spans(tree: ast.AST) -> set[int]:
    lines: set[int] = set()
    for node in (tree, *ast.walk(tree)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if not body:
                continue
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                lines.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return lines


def file_metrics(path: Path) -> FileMetrics:
    src = path.read_text(encoding="utf-8", errors="replace")
    lines = src.splitlines()
    loc = len(lines)

    code_lines: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in (
                tokenize.COMMENT,
                tokenize.NL,
                tokenize.NEWLINE,
                tokenize.INDENT,
                tokenize.DEDENT,
                tokenize.ENDMARKER,
            ):
                continue
            if not tok.string.strip():
                continue
            for ln in range(tok.start[0], tok.end[0] + 1):
                code_lines.add(ln)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # 极少数语法异常文件（如故意的错误样例）退化为：非空行即代码行
        code_lines = {i + 1 for i, ln in enumerate(lines) if ln.strip() and not ln.strip().startswith("#")}

    sloc = len(code_lines)
    try:
        tree = ast.parse(src)
        doc_lines = _docstring_line_spans(tree)
        stmts = sum(1 for n in ast.walk(tree) if isinstance(n, ast.stmt)) - len(
            [n for n in ast.walk(tree) if isinstance(n, ast.Expr) and isinstance(getattr(n, "value", None), ast.Constant)]
        )
    except SyntaxError:
        doc_lines, stmts = set(), 0

    return FileMetrics(
        path=str(path),
        loc=loc,
        sloc=sloc,
        docstring_lines=len(doc_lines & code_lines),
        ncloc=sloc - len(doc_lines & code_lines),
        ast_stmts=max(stmts, 0),
    )


def package_metrics(pkg_dir: Path) -> dict:
    """扫描源码包，返回 {files: [FileMetrics], totals: {...}}。"""
    files = [file_metrics(p) for p in iter_python_files(pkg_dir)]
    totals = {
        "num_files": len(files),
        "loc": sum(f.loc for f in files),
        "sloc": sum(f.sloc for f in files),
        "ncloc": sum(f.ncloc for f in files),
        "ast_stmts": sum(f.ast_stmts for f in files),
    }
    return {"files": [vars(f) for f in files], "totals": totals}


# --------------------------------------------------------------------------
# 测试侧：断言强度
# --------------------------------------------------------------------------

_ASSERT_CALL_NAMES = {"raises", "warns", "deprecated_call"}
_NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _count_assertions(node: ast.AST) -> int:
    """统计一个测试函数体内的断言数。

    计入：裸 assert 语句、pytest.raises/warns、unittest 风格的 self.assertXxx。
    不穿透嵌套的函数/类定义（那是 helper，不算这条测试的断言）。
    """
    n = 0
    stack: list[ast.AST] = [node]
    while stack:
        cur = stack.pop()
        for child in ast.iter_child_nodes(cur):
            if isinstance(child, _NESTED):
                continue
            if isinstance(child, ast.Assert):
                n += 1
            elif isinstance(child, ast.Call):
                fn = child.func
                if isinstance(fn, ast.Attribute):
                    if fn.attr in _ASSERT_CALL_NAMES or (
                        fn.attr.startswith("assert") and fn.attr != "assert_"
                    ):
                        n += 1
                elif isinstance(fn, ast.Name) and fn.id in _ASSERT_CALL_NAMES:
                    n += 1
            stack.append(child)
    return n


@dataclass
class TestFunc:
    qualname: str
    file: str
    lineno: int
    n_asserts: int
    parametrized: bool = False


@dataclass
class _TestFileResult:
    file: str
    test_funcs: list[TestFunc] = field(default_factory=list)
    n_asserts: int = 0
    parse_error: str | None = None


def _is_test_func(node: ast.AST, in_test_class: bool) -> bool:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    if node.name.startswith("test"):
        return in_test_class or True
    return False


def test_file_metrics(path: Path) -> _TestFileResult:
    rel = str(path)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError as e:
        return _TestFileResult(file=rel, parse_error=str(e))

    res = _TestFileResult(file=rel)
    base = path.stem

    def walk_body(body: list[ast.stmt], prefix: str, in_class: bool) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                walk_body(node.body, f"{prefix}{node.name}::", node.name.startswith("Test"))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if _is_test_func(node, in_class):
                    deco_names = {
                        d.attr if isinstance(d, ast.Attribute) else getattr(d, "id", "")
                        for d in node.decorator_list
                        if isinstance(d, (ast.Attribute, ast.Name))
                    }
                    para = any("parametrize" in (n or "") for n in deco_names) or "given" in deco_names
                    n = _count_assertions(node)
                    res.test_funcs.append(
                        TestFunc(
                            qualname=f"{prefix}{node.name}",
                            file=rel,
                            lineno=node.lineno,
                            n_asserts=n,
                            parametrized=bool(para),
                        )
                    )
                    res.n_asserts += n

    walk_body(tree.body, "", False)
    res.test_funcs.sort(key=lambda t: (t.lineno,))
    return res


def test_suite_metrics(test_roots: list[Path]) -> dict:
    """扫描测试目录，返回每个测试文件的断言统计与汇总。"""
    files: list[dict] = []
    all_funcs: list[TestFunc] = []
    for root in test_roots:
        for p in iter_python_files(root):
            r = test_file_metrics(p)
            files.append(
                {
                    "file": r.file,
                    "n_tests_static": len(r.test_funcs),
                    "n_asserts": r.n_asserts,
                    "parse_error": r.parse_error,
                }
            )
            all_funcs.extend(r.test_funcs)

    asserts_by_func = sum(f["n_asserts"] for f in files)
    totals = {
        "num_test_files": len(files),
        "num_test_funcs_static": len(all_funcs),
        "num_asserts": asserts_by_func,
        "num_zero_assert_tests": sum(1 for f in all_funcs if f.n_asserts == 0),
        "num_parametrized": sum(1 for f in all_funcs if f.parametrized),
        # 分母是源码里的测试函数个数，参数化未展开
        "asserts_per_static_func": (
            round(asserts_by_func / len(all_funcs), 2) if all_funcs else 0.0
        ),
        # 兼容旧字段名：老脚本读的是 asserts_per_test
        "asserts_per_test": (
            round(asserts_by_func / len(all_funcs), 2) if all_funcs else 0.0
        ),
    }
    return {"files": files, "totals": totals, "funcs": [vars(f) for f in all_funcs]}


def per_runtime_test_asserts(test_funcs: list[dict], node_ids: list[str],
                             base: Path | None = None) -> dict:
    """按**运行时用例数**（参数化已展开）计算断言密度。

    为什么要单独算一份：`asserts_per_static_func` 的分母是源码里的测试函数个数，
    而报告里的「用例数」是 pytest --collect-only 得到的运行时条数。参数化重的项目
    两者能差 2 倍（attrs 619 个函数 → 1346 条用例），直接并列会让读者
    用「断言数 ÷ 用例数」算出的值和表里写的对不上。

    映射规则：运行时 nodeid 形如 tests/test_x.py::test_y[param]，
    去掉 "[...]" 后与静态的 文件::限定名 对应。
    """
    def rel(p: str) -> str:
        if base is None:
            return p.replace("\\", "/")
        try:
            return Path(p).resolve().relative_to(Path(base).resolve()).as_posix()
        except ValueError:
            return Path(p).name

    lookup: dict[str, int] = {}
    for tf in test_funcs:
        lookup[rel(tf["file"]) + "::" + tf["qualname"]] = tf["n_asserts"]

    total, matched = 0, 0
    for nid in node_ids:
        key = nid.split("[")[0].replace("\\", "/")
        if key in lookup:
            total += lookup[key]
            matched += 1
    n = len(node_ids)
    return {
        "num_runtime_tests": n,
        "asserts_attributed": total,
        "matched": matched,
        "unmatched": n - matched,
        "match_rate": round(100.0 * matched / n, 2) if n else 0.0,
        "asserts_per_runtime_test": round(total / n, 2) if n else 0.0,
    }
