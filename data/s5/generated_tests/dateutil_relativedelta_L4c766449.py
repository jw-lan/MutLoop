import pytest
from datetime import date
from dateutil.relativedelta import relativedelta

def test_mutant_kill():
    with pytest.raises(TypeError) as excinfo:
        relativedelta(date(2020, 1, 1), "not a date")
    assert str(excinfo.value) == "relativedelta only diffs datetime/date"
