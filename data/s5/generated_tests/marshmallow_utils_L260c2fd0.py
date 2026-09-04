import pytest
from marshmallow.utils import from_iso_datetime

def test_from_iso_datetime_mutation_kill():
    # Trigger the mutated line with a timezone offset string of length 3
    # e.g., "+01" (sign + 2 digits) - len(tzinfo) == 3
    # Original: len(tzinfo) > 3 is False -> offset_mins = 0
    # Mutated: len(tzinfo) >= 3 is True -> offset_mins = int("01") = 1
    # This changes the resulting offset from 60 to 61 minutes
    result = from_iso_datetime("2023-01-01T00:00:00+01")
    # Original would produce +01:00, mutated produces +01:01
    assert result.utcoffset().total_seconds() == 3600  # 1 hour
