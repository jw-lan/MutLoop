import pytest
from jinja2.idtracking import Symbols, VAR_LOAD_PARAMETER

def test_declare_parameter_returns_name():
    symbols = Symbols()
    result = symbols.declare_parameter("my_param")
    assert result == "l_0_my_param"
    # find_ref returns a string (the internal name), not a ref object
    assert symbols.find_ref("my_param") == "l_0_my_param"
