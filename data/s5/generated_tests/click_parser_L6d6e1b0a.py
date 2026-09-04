import pytest
from click.parser import OptionParser, ParsingState
from click.exceptions import NoSuchOption
from click import Context
import click


@click.command()
def dummy_command():
    pass


def test_mutation_short_opt_unknown_option_message():
    ctx = Context(dummy_command)
    parser = OptionParser(ctx=ctx)
    # 构造一个不存在的短选项，例如 -z
    arg = "-z"
    state = ParsingState(rargs=[])
    
    with pytest.raises(NoSuchOption) as excinfo:
        parser._match_short_opt(arg, state)
    
    # 检查异常消息中包含选项名 "-z"
    assert "-z" in str(excinfo.value)
