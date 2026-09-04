import pytest
from jinja2.environment import TemplateStream

def test_enable_buffering_size_one():
    stream = TemplateStream(iter([]))
    with pytest.raises(ValueError):
        stream.enable_buffering(size=1)
