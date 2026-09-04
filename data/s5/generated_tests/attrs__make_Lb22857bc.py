import pytest
from attr._make import Attribute, _CountingAttr

def test_from_counting_attr_type_conflict_raises_valueerror_with_message():
    ca = _CountingAttr(
        default=None,
        validator=None,
        repr=True,
        cmp=None,
        hash=None,
        init=True,
        metadata=None,
        type=None,
        converter=None,
        kw_only=False,
        eq=True,
        eq_key=None,
        order=True,
        order_key=None,
        on_setattr=None,
        alias=None,
    )
    ca.type = "annotated_type"

    with pytest.raises(ValueError) as exc_info:
        Attribute.from_counting_attr("test_attr", ca, type="explicit_type")

    assert str(exc_info.value) == (
        "Type annotation and type argument cannot both be present for 'test_attr'."
    )
