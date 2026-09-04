import pytest
from dateutil.rrule import rrule, YEARLY

def test_bysetpos_zero_raises():
    with pytest.raises(ValueError):
        rrule(YEARLY, bysetpos=0)
