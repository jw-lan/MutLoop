import pytest
from datetime import datetime
from dateutil.rrule import rrule, DAILY

def test_rrule_getitem_slice_with_step_zero():
    start = datetime(2020, 1, 1)
    rule = rrule(DAILY, dtstart=start, count=10)
    
    # Original code: step=0 -> uses 1 (no skipping), returns 5 elements
    # Mutated code: step=0 -> uses 0, raises ValueError
    # We want original to pass, mutated to fail.
    # So we assert that no exception is raised and result is correct.
    # Mutated will raise, causing test failure.
    result = rule[0:5:0]
    assert len(result) == 5
    assert result[0] == start
    assert result[-1] == datetime(2020, 1, 5)
