import pytest
from click.types import Choice
from click.core import Argument

def test_choice_get_missing_message_mutation():
    choice = Choice(["a", "b"])
    param = Argument(param_decls=["test"], required=True)
    result = choice.get_missing_message(param)
    assert result == "Choose from:\n\ta,\n\tb"
