import pytest
from dateutil.rrule import weekday

def test_weekday_n_zero_raises_value_error():
    with pytest.raises(ValueError) as excinfo:
        weekday(0, n=0)
    assert str(excinfo.value) == "Can't create weekday with n==0"
