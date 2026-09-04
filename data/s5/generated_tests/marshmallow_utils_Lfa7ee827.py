import pytest
from marshmallow.utils import from_iso_time

def test_from_iso_time_invalid_raises_valueerror_with_message():
    with pytest.raises(ValueError) as excinfo:
        from_iso_time("not-a-valid-time")
    assert str(excinfo.value) == "Not a valid ISO8601-formatted time string"
