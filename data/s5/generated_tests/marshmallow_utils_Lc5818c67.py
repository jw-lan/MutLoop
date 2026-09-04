import pytest
from marshmallow.utils import _Missing

def test_missing_repr():
    missing = _Missing()
    assert repr(missing) == "<marshmallow.missing>"
