import pytest
from datetime import date
from dateutil.relativedelta import relativedelta

def test_mutation_or_to_and():
    rd1 = relativedelta(leapdays=0)
    rd2 = relativedelta(leapdays=1)
    result = rd1 + rd2
    assert result.leapdays == 1
