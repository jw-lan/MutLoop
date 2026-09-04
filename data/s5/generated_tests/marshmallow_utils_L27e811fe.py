import datetime as dt
from marshmallow.utils import get_fixed_timezone

def test_get_fixed_timezone_zero_offset():
    result = get_fixed_timezone(0)
    assert result.tzname(None) == "+0000"
