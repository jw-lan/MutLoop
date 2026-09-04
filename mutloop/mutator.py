"""变异算子层：用 mutmut 生成变异体，MutLoop 负责分类、编号与逐个渲染。

分工（对应总原则 1：不自造算子引擎）：
- **变异体生成**交给 mutmut（libcst 实现），我们一行算子逻辑都不重写。
- **分类 / 编号 / 渲染 / 落盘**由 MutLoop 负责，因为这一步决定了后续
  调度层（S3）能不能按行挑测试、缓存层能不能用内容哈希去重。

为什么不用 mutmut 自带的一体化文件（trampoline）：那个方案把所有变异体塞进
一个文件、用环境变量在运行时切换，跑测试时仍需加载整个文件；而我们要的是
「一个变异体一个独立源码」，这样才能配合按行挑选的测试子集和进程级隔离。

关于 mutmut 3.7 不支持原生 Windows：
它只拒绝跑**自带的 CLI/运行器**（要求 WSL），`create_mutations` 等纯生成接口
在 Windows 上完全可用。我们本来就要自己实现执行与调度，所以不受影响。
"""
from __future__ import annotations

import ast
import hashlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import libcst as cst
from libcst.metadata import MetadataWrapper, PositionProvider
from mutmut.mutation.file_mutation import MutationVisitor, deep_replace
from mutmut.mutation.mutators import mutation_operators
from mutmut.mutation.pragma_handling import get_ignored_lines

# --------------------------------------------------------------------------
# 算子分类
# --------------------------------------------------------------------------
AOR = "AOR"  # 算术／位运算符替换
ROR = "ROR"  # 关系运算符替换
COR = "COR"  # 条件（布尔）运算符替换
CR = "CR"    # 常量替换（数字 / 字符串 / True-False）
ARG = "ARG"  # 实参删除或置空
OTHER = "OTHER"

OPERATOR_LABELS = {
    AOR: "算术/位运算符替换",
    ROR: "关系运算符替换",
    COR: "条件运算符替换",
    CR: "常量替换",
    ARG: "实参删除/置空",
    OTHER: "其他（mutmut 未纳入本实验的算子）",
}

# 计划中的 5 个算子：AOR / ROR / COR / CR 是原生支持的四个；
# 「语句删除」mutmut 3.7 没有对应实现，按「取最接近」的原则用 ARG（实参删除）顶上，
# 二者同属"删掉一个程序组成元素"的语义，但粒度不同（实参 vs 整条语句）。
PLANNED_OPERATORS = (AOR, ROR, COR, CR, ARG)
DEFAULT_OPERATORS = PLANNED_OPERATORS

_ARITH_OPS = (
    cst.Add, cst.Subtract, cst.Multiply, cst.Divide, cst.FloorDivide,
    cst.Modulo, cst.Power, cst.BitAnd, cst.BitOr, cst.BitXor,
    cst.LeftShift, cst.RightShift,
)
_CMP_OPS = (
    cst.LessThan, cst.LessThanEqual, cst.GreaterThan, cst.GreaterThanEqual,
    cst.Equal, cst.NotEqual,
)
_BOOL_OPS = (cst.And, cst.Or)


def _snippet(node: cst.CSTNode) -> str:
    try:
        return cst.Module([]).code_for_node(node).strip()
    except Exception:  # pragma: no cover - 个别节点无法单独渲染
        return "<node>"


def _op_name(node: cst.CSTNode) -> str:
    op = getattr(node, "operator", None)
    return type(op).__name__ if op is not None else ""


def classify(orig: cst.CSTNode, mutated: cst.CSTNode) -> str:
    """按原始/变异节点的类型判定算子类别。

    mutmut 的 Mutation 不携带算子名，只能从节点形态反推；判定规则集中在
    这里，便于与计划里的 5 个算子对齐。
    """
    if isinstance(orig, cst.ComparisonTarget):
        return ROR if isinstance(orig.operator, _CMP_OPS) else OTHER
    if isinstance(orig, cst.BooleanOperation):
        return COR if isinstance(orig.operator, _BOOL_OPS) else OTHER
    if isinstance(orig, cst.BinaryOperation):
        return AOR if isinstance(orig.operator, _ARITH_OPS) else OTHER
    if isinstance(orig, cst.AugAssign):
        # `a += b` → `a = b` 是赋值形态变化，不算运算符替换
        if not isinstance(mutated, cst.AugAssign):
            return OTHER
        return AOR if isinstance(orig.operator, _ARITH_OPS) else OTHER
    if isinstance(orig, cst.UnaryOperation):
        # 删掉一元运算（如 -x → x）不属于我们选定的算子
        if isinstance(mutated, cst.UnaryOperation):
            return AOR if isinstance(orig.operator, _ARITH_OPS) else OTHER
        return OTHER
    if isinstance(orig, cst.BaseNumber):
        return CR
    if isinstance(orig, cst.BaseString):
        return CR
    if isinstance(orig, cst.Name):
        return CR
    if isinstance(orig, cst.Call):
        return _classify_call(orig, mutated)
    return OTHER


def _classify_call(orig: cst.Call, mutated: cst.CSTNode) -> str:
    """Call 节点上只把「实参置 None」算 ARG；「删实参」已排除，理由见下。

    mutmut 在 cst.Call 上挂了四个算子：`arg_removal`（删实参、实参置 None）、
    `dict_arguments`（dict 的关键字名加 XX）、两个字符串方法互换。
    后两者语义上不属于"删除"，归为 OTHER。

    **「删实参」于 2026-09-01 排除。** marshmallow/utils.py 实测（166 个 ARG）：

    | 形态        | 变异体 | kill 率 | TypeError          | 廉价 kill 率 |
    |-------------|--------|---------|--------------------|--------------|
    | 删实参      | 91     | 69.5%   | 33（占其 killed 58%） | **78.9%** |
    | 实参置 None | 75     | 86.8%   | 15                 | 64.4%        |
    | CR（对照）  | 118    | 57.8%   | 0                  | 63.5%        |

    删实参会让调用的参数个数对不上，大量产生 TypeError —— 测试只是"碰巧"因为
    代码崩了而失败，并不是真的检测到了行为变化。而「置 None」的廉价率与 CR
    持平、kill 率还更高，是有效算子。

    去掉删实参后：变异体 312 → 221（−29%），变异分数 69.23% → 69.12%（几乎不变），
    廉价 kill 率 66.7% → 61.7%。**分数不受影响、更可信、还省 29% 计算**，故排除。
    """
    if not isinstance(mutated, cst.Call):
        return OTHER
    o_args, m_args = orig.args, mutated.args
    # 只认「实参数量不变、某个实参被换成 None」这一种形态
    if len(m_args) == len(o_args):
        for oa, ma in zip(o_args, m_args):
            o_none = isinstance(oa.value, cst.Name) and oa.value.value == "None"
            m_none = isinstance(ma.value, cst.Name) and ma.value.value == "None"
            if m_none and not o_none:
                return ARG
    return OTHER


def describe(orig: cst.CSTNode, mutated: cst.CSTNode) -> str:
    """给出人能读懂的变异描述，用于报告和 triage。"""
    o, m = _op_name(orig), _op_name(mutated)
    if o and m:
        return f"{o} → {m}"
    so, sm = _snippet(orig), _snippet(mutated)
    if len(so) > 40 or len(sm) > 40:
        return f"{type(orig).__name__} → {type(mutated).__name__}"
    return f"{so} → {sm}"


# --------------------------------------------------------------------------
# 变异体
# --------------------------------------------------------------------------
@dataclass(eq=False)
class Mutant:
    """一个变异体。这是 S2 定义的数据契约，后续缓存/triage/报告都吃这个结构。"""

    mutant_id: str
    file: str          # 相对被测项目根的路径
    line: int
    operator: str
    description: str
    index: int         # 在该文件变异列表中的序号

    _module: cst.Module = field(repr=False, default=None)
    _orig: cst.CSTNode = field(repr=False, default=None)
    _mutated: cst.CSTNode = field(repr=False, default=None)

    def source(self) -> str:
        """渲染出这个变异体的完整源码（只应用这一个变异）。"""
        new_module = deep_replace(self._module, self._orig, self._mutated)
        return new_module.code

    def as_record(self) -> dict:
        return {
            "mutant_id": self.mutant_id,
            "file": self.file,
            "line": self.line,
            "operator": self.operator,
            "description": self.description,
            "index": self.index,
        }


class _CapturingVisitor(MutationVisitor):
    """在 mutmut 的 visitor 基础上记录每个变异体的源码位置。

    mutmut 的 Mutation 只存 original/mutated 节点，不带位置；而调度层（S3）
    要靠行号去挑测试，所以必须在这里顺手把位置抓下来。
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.positions: list = []

    def _create_mutations(self, node: cst.CSTNode) -> None:
        position = self.get_metadata(PositionProvider, node, None)
        before = len(self.mutations)
        super()._create_mutations(node)
        for _ in range(len(self.mutations) - before):
            self.positions.append(position)


def export_list_lines(code: str) -> set[int]:
    """找出模块级 `__all__` 之类导出列表覆盖的行号。

    为什么要排除（2026-09-01，attrs/validators.py 实测）：
    `__all__` 里存的是**导出名字符串**，把它变异成 `"XXand_XX"` 或 `"AND_"` 之后，
    `from attr.validators import *` 就找不到对应名字，pytest 在收集阶段直接崩，
    于是每个元素都产生 2 个 stillborn。实测 98 个变异体里 **40 个（40.8%）** 是这么来的。

    这些是"合法"的死胎——改 `__all__` 确实破坏导入——但它们**不测试任何业务逻辑**，
    只测试导入机制。让它们占据样本会严重稀释有效变异体。

    只处理模块级赋值给 `__all__` 的情形，不动其他模块级常量（那些可能是真实逻辑）。
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()
    lines: set[int] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            lo = getattr(node.value, "lineno", node.lineno)
            hi = getattr(node.value, "end_lineno", lo)
            lines.update(range(lo, hi + 1))
    return lines


def enumerate_mutants(
    path: Path | str,
    *,
    root: Path | str | None = None,
    operators: tuple[str, ...] = DEFAULT_OPERATORS,
    covered_lines: set[int] | None = None,
) -> list[Mutant]:
    """枚举一个源文件的全部变异体，按算子类别过滤。

    :param root: 用于把 file 转成相对路径；默认取该文件的被测项目根
    :param operators: 要保留的算子类别
    :param covered_lines: 若给出，只变异这些行（给 S4 的增量模式预留）
    """
    path = Path(path)
    code = path.read_text(encoding="utf-8")
    skip_lines = export_list_lines(code)

    module = cst.parse_module(code)
    wrapper = MetadataWrapper(module)
    ignored = get_ignored_lines(str(path), code, wrapper)
    visitor = _CapturingVisitor(mutation_operators, ignored, covered_lines)
    # 关键：MetadataWrapper 默认会深拷贝整棵树，visitor 访问的是副本。
    # 必须用 visit() 返回的 module 做后续替换，否则 deep_replace 按节点身份
    # 匹配时会全部落空（表现为渲染出的源码和原文完全一样）。
    module = wrapper.visit(visitor)

    root_path = Path(root) if root else _guess_root(path)
    try:
        rel = path.resolve().relative_to(Path(root_path).resolve()).as_posix()
    except ValueError:
        rel = path.name

    wanted = set(operators)
    out: list[Mutant] = []
    for i, (mutation, position) in enumerate(zip(visitor.mutations, visitor.positions)):
        operator = classify(mutation.original_node, mutation.mutated_node)
        if operator not in wanted:
            continue
        line = position.start.line if position else 0
        if line in skip_lines:
            continue
        desc = describe(mutation.original_node, mutation.mutated_node)
        mid = hashlib.sha1(
            f"{rel}|{line}|{operator}|{desc}|{i}".encode("utf-8")
        ).hexdigest()[:12]
        out.append(
            Mutant(
                mutant_id=mid,
                file=rel,
                line=line,
                operator=operator,
                description=desc,
                index=i,
                _module=module,
                _orig=mutation.original_node,
                _mutated=mutation.mutated_node,
            )
        )
    return out


def _guess_root(path: Path) -> Path:
    """从 src/<pkg>/... 形态反推项目根。"""
    parts = path.resolve().parts
    for i, part in enumerate(parts):
        if part == "src" and i + 1 < len(parts):
            return Path(*parts[:i])
    return path.parent


def count_by_operator(mutants: list[Mutant]) -> dict[str, int]:
    c = Counter(m.operator for m in mutants)
    return {k: c[k] for k in OPERATOR_LABELS if c.get(k)}
