import pytest
from jinja2.nodes import _FilterTestCommon, Impossible, Const
from jinja2 import Environment
from jinja2.nodes import EvalContext

def test_mutant_kill():
    env = Environment()
    eval_ctx = EvalContext(env)
    eval_ctx.volatile = False
    
    # Create a filter that exists and requires context
    def context_filter_ignores(value, **kwargs):
        return value + 1
    
    context_filter_ignores.contextfunction = True
    env.filters['ctx_ignore'] = context_filter_ignores
    
    # Create a node with a simple constant
    node = Const(42)
    
    # Use the actual _FilterTestCommon class via a subclass that already exists
    # Since we can't create custom node types, we need to use an existing subclass
    # Looking at jinja2 source, _FilterTestCommon is abstract, but we can use
    # the actual Filter or Test classes that inherit from it
    from jinja2.nodes import Filter
    
    # Create a Filter node directly
    test_obj = Filter('ctx_ignore', node, [], [], None, None)
    
    # Original code: func is not None and pass_arg is context -> raises Impossible
    # Mutant code: func is not None AND pass_arg is context -> False (because pass_arg is context, AND is False)
    # So mutant proceeds to call func(42) which returns 43, no exception
    # This test will pass on original (raises Impossible) and fail on mutant (no exception)
    
    with pytest.raises(Impossible):
        test_obj.as_const(eval_ctx)
