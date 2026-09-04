import datetime
from dateutil.tz.win import tzwinlocal

def test_picknthweekday_mutation_kill():
    # picknthweekday is a module-level function in dateutil.tz.win
    # but it's defined inside the class in the mutated code context.
    # Since it's a static method (no self), we need to call it via the class
    # but the class doesn't expose it directly. Let's access it via the module.
    import dateutil.tz.win as win_module
    picknthweekday = win_module.picknthweekday
    
    # Choose a year/month where the 5th (whichweek=5) occurrence of a weekday
    # falls outside the month, so the original code subtracts one week.
    # Example: March 2023, whichweek=5, dayofweek=0 (Sunday)
    # March 2023 has 31 days. Sundays in March 2023: 5,12,19,26 -> only 4 Sundays,
    # so the 5th Sunday would be April 2, 2023. Original code subtracts one week
    # to get March 26, 2023. Mutated code (==) would NOT subtract, returning April 2.
    result = picknthweekday(2023, 3, 0, 12, 0, 5)
    expected = datetime.datetime(2023, 3, 26, 12, 0)
    assert result == expected
