import pytest
from datetime import datetime
from dateutil.rrule import rrule, WEEKLY, MO, TU, WE, TH, FR, SA, SU

def test_kill_aor_weekly_interval():
    # This test triggers the WEEKLY branch in _iter() where wkst > weekday
    # Original: day += -(weekday+1+(6-wkst)) + self._interval*7
    # Mutant:   day += -(weekday+1+(6-wkst)) - self._interval*7
    # With wkst=SU(6), weekday=MO(0), interval=1:
    # Original: day += -(0+1+(6-6)) + 7 = -1 + 7 = 6
    # Mutant:   day += -(0+1+(6-6)) - 7 = -1 - 7 = -8
    # This difference will produce different dates.
    
    # Start on Monday, weekly with weekstart=Sunday
    start = datetime(2020, 1, 6)  # Monday
    rule = rrule(WEEKLY, dtstart=start, count=3, wkst=SU)
    
    # Get the list of dates
    dates = list(rule)
    
    # Expected dates for original code:
    # 2020-01-06 (Monday), 2020-01-13 (Monday), 2020-01-20 (Monday)
    expected = [
        datetime(2020, 1, 6),
        datetime(2020, 1, 13),
        datetime(2020, 1, 20)
    ]
    
    assert dates == expected
