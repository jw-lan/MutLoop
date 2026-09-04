import pytest
from dateutil.relativedelta import relativedelta

def test_sub_leapdays_or_vs_and():
    rd1 = relativedelta(leapdays=1)
    rd2 = relativedelta(leapdays=0)
    result = rd1 - rd2
    assert result.leapdays == 1
