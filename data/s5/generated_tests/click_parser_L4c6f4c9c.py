import pytest
from click.parser import OptionParser, ParsingState
from click.exceptions import BadOptionUsage
from click.parser import Option


class FakeOptionObj:
    _flag_needs_value = False


class FakeOption:
    def __init__(self):
        self.nargs = 2
        self.obj = FakeOptionObj()


def test_mutation_arg_option_name_none():
    parser = OptionParser()
    option = FakeOption()
    state = ParsingState(rargs=[])  # fewer args than nargs

    with pytest.raises(BadOptionUsage) as excinfo:
        parser._get_value_from_state("--test-option", option, state)

    # The original code formats with name=option_name, so the message contains
    # the actual option name. The mutated code uses name=None, which would
    # produce "Option None requires..." instead of "Option '--test-option' requires..."
    assert "--test-option" in str(excinfo.value)
