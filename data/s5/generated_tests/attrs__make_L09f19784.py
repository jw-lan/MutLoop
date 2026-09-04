import attr
from attr._make import Attribute, _CountingAttr

def test_from_counting_attr_name_not_none():
    ca = _CountingAttr(
        default=42,
        validator=None,
        repr=True,
        hash=None,
        init=True,
        metadata={},
        converter=None,
        kw_only=False,
        eq=True,
        order=False,
        on_setattr=None,
        cmp=False,
        type=None,
        eq_key=None,
        order_key=None,
        alias=None,
    )
    result = Attribute.from_counting_attr("my_attr", ca, type=None)
    assert result.name == "my_attr"
