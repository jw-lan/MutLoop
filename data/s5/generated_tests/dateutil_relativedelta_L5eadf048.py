from datetime import datetime, timezone, timedelta
from dateutil.relativedelta import relativedelta

def test_kill_ror_less_than_equal():
    # Two datetimes representing the same instant but with different
    # year/month due to timezone offset crossing a month boundary.
    # dt1: 2020-01-01 00:00 UTC
    # dt2: 2019-12-31 19:00-05:00 (which is 2020-01-01 00:00 UTC)
    dt1 = datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    dt2 = datetime(2019, 12, 31, 19, 0, 0, 
                   tzinfo=timezone(timedelta(hours=-5)))
    
    # These are equal instants: dt1 == dt2 is True
    # months = (2020-2019)*12 + (1-12) = 12 - 11 = 1
    rd = relativedelta(dt1, dt2)
    
    # For original code: dt1 < dt2 is False (they're equal), 
    # so compare=operator.lt, increment=-1
    # dtm = dt2 + 1 month = 2019-12-31 19:00-05:00 + 1 month 
    #      = 2020-01-31 19:00-05:00 = 2020-02-01 00:00 UTC
    # while dt1 < dtm? 2020-01-01 00:00 UTC < 2020-02-01 00:00 UTC? True
    # months += -1 -> 0, dtm = dt2 + 0 months = dt2
    # while dt1 < dt2? False (equal), exit. months=0
    
    # For mutated code: dt1 <= dt2 is True (equal),
    # so compare=operator.gt, increment=1
    # dtm = dt2 + 1 month = same as above
    # while dt1 > dtm? 2020-01-01 00:00 UTC > 2020-02-01 00:00 UTC? False
    # loop doesn't run. months remains 1
    
    # So original gives months=0, mutated gives months=1
    assert rd.months == 0
    assert rd.years == 0
    assert rd.days == 0
    assert rd.hours == 0
    assert rd.minutes == 0
    assert rd.seconds == 0
    assert rd.microseconds == 0
