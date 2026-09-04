import pytest
from jinja2.environment import Environment

def test_compile_expression_mutation():
    env = Environment()
    expr = env.compile_expression("42")
    # 原代码：expr() 返回 42；变异代码：expr() 抛出 UndefinedError
    assert expr() == 42
