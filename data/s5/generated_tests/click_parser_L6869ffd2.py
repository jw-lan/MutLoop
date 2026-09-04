import pytest
from click.parser import Argument, ParsingState
from click.exceptions import BadArgumentUsage


class MockCoreArgument:
    def __init__(self):
        self.envvar = None


def test_argument_process_raises_bad_usage_with_correct_message():
    # Setup
    arg = Argument(obj=MockCoreArgument(), dest="myarg", nargs=3)
    state = ParsingState(rargs=[])
    value = ["val1", None, "val3"]  # holes=1, not all None

    # Execute & Assert
    with pytest.raises(BadArgumentUsage) as exc_info:
        arg.process(value, state)

    # The mutation changes the message prefix/suffix, so check exact message
    assert str(exc_info.value) == "Argument 'myarg' takes 3 values."
