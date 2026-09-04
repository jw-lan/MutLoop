import pytest
from dateutil.rrule import rrule
from dateutil.rrule import DAILY

def test_mutation_kill_count_until_warning():
    with pytest.warns(DeprecationWarning) as record:
        rrule(DAILY, count=1, until=__import__('datetime').datetime(2020, 1, 1))
    assert len(record) == 1
    assert "inconsistent with RFC 5545" in str(record[0].message)
