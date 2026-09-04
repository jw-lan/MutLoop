import pytest
from attr.validators import lt
from types import SimpleNamespace

def test_lt_mutation():
    validator = lt(10)
    attr = SimpleNamespace(name="test_attr")
    with pytest.raises(ValueError):
        validator(None, attr, 10)
