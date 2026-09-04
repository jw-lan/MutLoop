import pytest
from click.parser import split_arg_string

def test_split_arg_string_commenter_mutation():
    # Original: commenters = "" (no comment stripping)
    # Mutated: commenters = "XXXX" (treats X as comment start)
    result = split_arg_string("hello Xworld")
    assert result == ["hello", "Xworld"]
