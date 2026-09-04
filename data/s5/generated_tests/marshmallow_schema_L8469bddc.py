import pytest
from marshmallow import Schema, fields

class TestSchema(Schema):
    field1 = fields.Field(data_key="a")
    field2 = fields.Field(data_key="a")

def test_duplicate_data_key_raises_value_error():
    with pytest.raises(ValueError) as excinfo:
        TestSchema()
    # 原代码：count(x) > 1 只包含重复项（2个）
    # 变异代码：count(x) >= 1 包含所有项（2个）
    # 两者都会触发 ValueError，但错误消息中的重复键集合不同
    # 原代码：{"a"} 变异代码：{"a"} 实际上相同，所以需要更精确的断言
    # 检查错误消息中是否包含重复键的详细信息
    error_msg = str(excinfo.value)
    # 原代码会列出重复的 data_key，变异代码也会列出，但我们需要区分
    # 通过检查错误消息中是否包含 "a" 且不包含其他字段名
    assert "a" in error_msg
    # 关键：原代码中 count > 1 只对重复项成立，变异代码 count >= 1 对所有项成立
    # 但这里只有两个字段，结果相同。需要构造三个字段，其中两个重复，一个不重复
    # 这样原代码只包含重复的，变异代码包含所有三个
    # 但当前测试只有两个字段，无法区分，所以需要修改测试类
    # 重新定义测试类
    class TestSchema3(Schema):
        f1 = fields.Field(data_key="dup")
        f2 = fields.Field(data_key="dup")
        f3 = fields.Field(data_key="unique")
    
    with pytest.raises(ValueError) as excinfo2:
        TestSchema3()
    error_msg2 = str(excinfo2.value)
    # 原代码：重复键集合为 {"dup"}，变异代码：{"dup", "unique"}
    # 检查 "unique" 是否出现在错误消息中
    assert "unique" not in error_msg2
