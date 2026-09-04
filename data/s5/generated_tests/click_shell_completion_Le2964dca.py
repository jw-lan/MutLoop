import os
import click
from click.shell_completion import ZshComplete
from click.testing import CliRunner

def test_get_completion_args_kill_mutation():
    os.environ["COMP_WORDS"] = "prog --flag value"
    os.environ["COMP_CWORD"] = "2"

    @click.command()
    @click.option("--flag")
    def cli(flag):
        pass

    runner = CliRunner()
    with runner.isolated_filesystem():
        ctx = cli.make_context("prog", ["--flag", "value"])
        zsh_complete = ZshComplete(cli, ctx, "prog", "COMP_WORDS")
        args, incomplete = zsh_complete.get_completion_args()
        assert args == ["--flag"]
        assert incomplete == "value"
