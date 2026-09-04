import pytest
from dateutil.relativedelta import relativedelta

def test_mutation_kill():
    with pytest.warns(DeprecationWarning) as record:
        relativedelta(year=1.5)
    assert len(record) == 1
    assert "Non-integer value passed" in str(record[0].message)
