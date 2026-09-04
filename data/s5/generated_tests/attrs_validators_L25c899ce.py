import pytest
from attr.validators import le

def test_kill_mutation_lt_le_ge_gt_le_boundary():
    # Test that the validator correctly accepts a value equal to the bound
    # when the bound is inclusive (le means <=)
    validator = le(5)
    # Should not raise for value equal to bound
    validator(None, None, 5)
