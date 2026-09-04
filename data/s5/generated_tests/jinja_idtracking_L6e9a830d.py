import pytest
from jinja2.idtracking import Symbols

def test_load_kills_mutation():
    symbols = Symbols()
    symbols.load("my_var")
    ref = symbols.find_ref("my_var")
    assert ref is not None
    assert ref[0] == "l"  # VAR_LOAD_RESOLVE constant
    assert ref[1] == "_"  # mutated code uses None as name, so ref[1] becomes "_"
