import pytest
from click.types import Choice
from click.core import Parameter

class FakeParam(Parameter):
    def __init__(self, required, param_type_name):
        self.required = required
        self.param_type_name = param_type_name

def test_choice_get_metavar_required_false_argument():
    choice = Choice(["a", "b"])
    param = FakeParam(required=False, param_type_name="argument")
    # Original: required=False and argument=True -> False -> returns "[a|b]"
    # Mutated: required=False or argument=True -> True -> returns "{a|b}"
    assert choice.get_metavar(param) == "[a|b]"
