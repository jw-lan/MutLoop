import pytest
from marshmallow.utils import from_timestamp

def test_from_timestamp_boolean_true():
    with pytest.raises(ValueError):
        from_timestamp(True)
