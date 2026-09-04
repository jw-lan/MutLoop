import pytest
from click.types import DateTime
from click.exceptions import BadParameter

def test_datetime_convert_error_message_uses_original_value():
    dt = DateTime()
    with pytest.raises(BadParameter) as excinfo:
        dt.convert("not-a-date", None, None)
    assert "not-a-date" in str(excinfo.value)
