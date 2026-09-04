import os
import pytest
from click.shell_completion import FishComplete

def test_fish_complete_mutation_kill():
    os.environ["COMP_WORDS"] = "fish partial"
    os.environ["COMP_CWORD"] = "partial"
    
    fish = FishComplete(cli=None, ctx_args={}, prog_name="fish", complete_var="_FISH_COMPLETE")
    
    args, incomplete = fish.get_completion_args()
    
    # Original: incomplete and args and args[-1] == incomplete -> True, pops last element
    # Mutated: incomplete and args or args[-1] == incomplete -> True, pops last element
    # Both pop, so need a case where original doesn't pop but mutated does
    # Set args[-1] != incomplete, but incomplete truthy and args non-empty
    os.environ["COMP_WORDS"] = "fish other"
    os.environ["COMP_CWORD"] = "partial"
    
    args, incomplete = fish.get_completion_args()
    assert args == ["other"]
