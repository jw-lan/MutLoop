import pytest
from datetime import datetime, timezone
from dateutil.rrule import rrule

def test_mutant_kill():
    # Original code raises ValueError with specific message
    # Mutant code raises ValueError with message None (since it passes None as first arg)
    with pytest.raises(ValueError) as excinfo:
        rrule(
            freq=0,  # YEARLY
            dtstart=datetime(2020, 1, 1, tzinfo=timezone.utc),
            until=datetime(2020, 12, 31)  # naive datetime, not UTC
        )
    # Check that the exception message is not None
    assert excinfo.value.args[0] is not None
    assert "RRULE UNTIL values must be specified in UTC" in str(excinfo.value)
