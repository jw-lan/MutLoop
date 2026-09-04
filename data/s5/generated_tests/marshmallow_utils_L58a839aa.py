import pytest
from marshmallow.utils import set_value

def test_set_value_raises_value_error_with_message_when_target_not_dict():
    dct = {"foo": "existing_value"}
    with pytest.raises(ValueError, match="Cannot set foo.bar in foo due to existing value: existing_value"):
        set_value(dct, "foo.bar", 42)
