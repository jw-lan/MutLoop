import pytest
from datetime import date
from dateutil.relativedelta import relativedelta

def test_mutation_kill():
    with pytest.raises(TypeError) as excinfo:
        relativedelta(dt1=date(2020, 1, 1), dt2="not a date")
    assert str(excinfo.value) == "relativedelta only diffs datetime/date"
