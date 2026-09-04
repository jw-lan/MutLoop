import pytest
from marshmallow.validate import URL

def test_url_validation_rejects_both_relative_and_absolute_false():
    with pytest.raises(ValueError) as excinfo:
        URL(relative=False, absolute=False)
    assert str(excinfo.value) == "URL validation cannot set both relative and absolute to False."
