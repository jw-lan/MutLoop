import click
from click.core import Option

def test_mutation_cr_1_to_2():
    # Create an Option that is a boolean flag with distinct True/False opts
    # and a default value of True, so that it enters the branch at line 2799-2804
    opt = Option(
        param_decls=["--flag/--no-flag"],
        is_flag=True,
        default=True,
        show_default=True,
        help="test flag",
    )
    # Build a context with show_default enabled
    ctx = click.Context(click.Command("test"))
    ctx.show_default = True
    # Call get_help_record to trigger _write_opts
    help_record = opt.get_help_record(ctx)
    # The help_record is a tuple (param_decls, help_text, ...)
    # The help text is the second element (index 1)
    help_text = help_record[1]
    # The default string should be "flag" (the part after the prefix)
    # Original code splits on the first prefix and takes [1], giving "flag"
    # Mutated code takes [2], which would be out of range or wrong
    assert "default: flag" in help_text
