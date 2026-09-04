import pytest
from dateutil.relativedelta import relativedelta

def test_mutation_yearday_59():
    rd = relativedelta(yearday=59)
    assert rd.leapdays == 0
