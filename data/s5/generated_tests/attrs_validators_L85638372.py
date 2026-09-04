import pytest
from attr.validators import gt
from attr import attrs, attrib

@attrs
class Sample:
    x = attrib()

def test_gt_error_message_contains_operator():
    validator = gt(0)
    instance = Sample(x=-1)
    # 获取真实的属性对象（通过类属性）
    attr_obj = Sample.__attrs_attrs__[0]
    with pytest.raises(ValueError) as excinfo:
        validator(instance, attr_obj, -1)
    assert "XX>XX" not in str(excinfo.value)
