import pytest
from dateutil.relativedelta import relativedelta

def test_microseconds_negative_overflow():
    # 构造一个负的、绝对值大于999999的microseconds值
    # 原代码：divmod(self.microseconds * s, 1000000) 其中 s = -1
    # 变异后：divmod(self.microseconds / s, 1000000) 其中 s = -1
    # 原代码：divmod(-1500000 * -1, 1000000) = divmod(1500000, 1000000) = (1, 500000)
    # 变异后：divmod(-1500000 / -1, 1000000) = divmod(1500000.0, 1000000) = (1.0, 500000.0)
    # 变异后mod为浮点数，导致microseconds变为浮点数，与预期不符
    rd = relativedelta(microseconds=-1500000)
    # 触发_fix逻辑
    rd._fix()
    # 原代码：microseconds = 500000, seconds = 1
    # 变异后：microseconds = 500000.0, seconds = 1.0
    # 注意：原代码中负数的处理，最终microseconds为正，seconds为正
    # 但实际原代码运行结果是microseconds=-500000, seconds=-1，因为_fix只处理绝对值>999999的情况
    # 这里-1500000绝对值>999999，所以会进入分支
    # 原代码：divmod(-1500000 * -1, 1000000) = divmod(1500000, 1000000) = (1, 500000)
    # 然后 self.microseconds = 500000 * -1 = -500000
    # self.seconds += 1 * -1 = -1
    # 所以原代码结果是microseconds=-500000, seconds=-1
    # 变异后：divmod(-1500000 / -1, 1000000) = divmod(1500000.0, 1000000) = (1.0, 500000.0)
    # 然后 self.microseconds = 500000.0 * -1 = -500000.0
    # self.seconds += 1.0 * -1 = -1.0
    # 所以变异后结果是microseconds=-500000.0, seconds=-1.0
    assert rd.microseconds == -500000
    assert rd.seconds == -1
    # 检查类型，变异后为浮点数
    assert isinstance(rd.microseconds, int)
    assert isinstance(rd.seconds, int)
