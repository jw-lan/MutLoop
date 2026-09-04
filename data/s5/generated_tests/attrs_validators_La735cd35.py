import pytest
from attr.validators import lt

def test_lt_mutation_kill():
    validator = lt(10)
    # The mutation changes the compare_op from "<" to "XX<XX"
    # The __repr__ method exposes the compare_op
    assert repr(validator) == "<Validator for x < 10>"
