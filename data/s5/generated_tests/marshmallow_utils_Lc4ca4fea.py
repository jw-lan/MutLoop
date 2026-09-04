import pytest
from marshmallow.utils import from_iso_datetime

def test_from_iso_datetime_mutation_aor():
    # This test kills the mutation: Add → Subtract in offset calculation
    # For a timezone like "+0530", original: offset = 60*5 + 30 = 330 minutes
    # Mutated: offset = 60*5 - 30 = 270 minutes
    # The resulting datetime should have tzinfo with correct offset
    result = from_iso_datetime("2023-01-01T12:00:00+0530")
    # Original offset: +5:30 = 330 minutes = 5.5 hours
    # Mutated offset: +4:30 = 270 minutes = 4.5 hours
    # Check the UTC offset of the result
    assert result.utcoffset().total_seconds() == 330 * 60
