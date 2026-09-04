import click
from click.core import Option


def test_mutation_cr_0_to_1():
    # Create an Option that is a bool flag with distinct True/False opts
    # and default value True, so that self.default is True
    opt = Option(
        param_decls=["--flag/--no-flag"],
        is_flag=True,
        default=True,
        show_default=True,
        help="test flag",
    )
    # Force the help formatting path that triggers _write_opts
    help_record = opt.get_help_record(click.Context(click.Command("test")))
    # The default string should be derived from self.opts[0] (the True opt)
    # which is "--flag" (without prefix), not from secondary_opts[1]
    assert help_record is not None
    # The default string should contain "flag" (the True opt without prefix)
    assert "default: flag" in help_record[1]
    # The default string should NOT contain "no-flag" (the False opt)
    assert "no-flag" not in help_record[1]
