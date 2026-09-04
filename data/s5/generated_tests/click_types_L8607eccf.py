import pytest
from click.types import DateTime
from click.exceptions import BadParameter
from click import Context, Command

def test_datetime_convert_invalid_value_raises_bad_parameter():
    dt = DateTime()
    cmd = Command('test')
    ctx = Context(cmd)

    with pytest.raises(BadParameter) as excinfo:
        dt.convert('invalid-date', None, ctx)

    # 原代码会抛出包含格式信息的异常消息
    # 变异后调用 self.fail(None, param, ctx) 会抛出 TypeError 或异常消息为 None
    # 这里断言异常消息包含格式信息，变异后消息为 None 则失败
    assert "does not match the format" in str(excinfo.value)
