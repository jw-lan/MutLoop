import pytest
from jinja2.idtracking import Symbols

def test_declare_parameter_adds_name_to_stores():
    symbols = Symbols()
    symbols.declare_parameter("param_name")
    assert "param_name" in symbols.stores
