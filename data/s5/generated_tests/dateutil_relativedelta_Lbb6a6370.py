import pytest
from dateutil.relativedelta import relativedelta

def test_mutant_kill_microseconds_divide():
    # 构造一个 microseconds 绝对值大于 999999 的 relativedelta
    # 原代码: self.microseconds = mod * s
    # 变异后: self.microseconds = mod / s
    # 当 s = -1 时，原代码得到正数，变异代码得到负数
    rd = relativedelta(microseconds=-1000001)
    # 触发 _fix() 内部逻辑
    # 原代码: div, mod = divmod(1000001, 1000000) => div=1, mod=1
    # 原代码: self.microseconds = 1 * (-1) = -1
    # 变异代码: self.microseconds = 1 / (-1) = -1.0 (浮点数)
    # 但更关键的是，当 s = -1 且 mod 不为 0 时，除法会得到浮点数
    # 为了明确杀死，选择 mod 不能被 s 整除的情况
    # 例如 microseconds = -1500000 => abs=1500000
    # divmod(1500000, 1000000) => div=1, mod=500000
    # 原代码: 500000 * (-1) = -500000 (int)
    # 变异代码: 500000 / (-1) = -500000.0 (float)
    rd2 = relativedelta(microseconds=-1500000)
    # 原代码中 microseconds 应为整数 -500000
    # 变异代码中为浮点数 -500000.0
    # 断言类型不同即可杀死
    assert isinstance(rd2.microseconds, int)
    assert rd2.microseconds == -500000
