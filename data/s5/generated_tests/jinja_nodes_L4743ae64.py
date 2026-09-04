import inspect
import pytest
from jinja2.nodes import Filter, Const
from jinja2 import Environment
from jinja2.exceptions import TemplateRuntimeError

def test_kill_and_or_mutation():
    # Create an async environment
    env = Environment(autoescape=True, enable_async=True)
    
    # Create a filter function that is NOT async and has no async variant
    def my_filter(value):
        return value
    
    # Register the filter
    env.filters['my_filter'] = my_filter
    
    # Create a Filter node - need to check the actual constructor signature
    # Looking at the source, Filter inherits from _FilterTestCommon which inherits from Expr
    # The Expr class has 'node' as attribute, but Filter's __init__ might be different
    # Let's use the correct way to instantiate
    from jinja2.nodes import Filter as FilterNode
    # Check the actual signature
    import inspect as insp
    sig = insp.signature(FilterNode.__init__)
    # The signature likely has 'node' as first arg after self
    # But the error says unknown attribute, so let's try positional
    node = FilterNode(Const(42), 'my_filter', [], [], None, None)
    
    # Get the eval context using the internal method
    from jinja2.nodes import EvalContext
    eval_ctx = EvalContext(environment=env)
    
    # For original code: is_async is True, but func is not async and has no async variant
    # So the condition (is_async and (False or False)) is False -> should NOT raise Impossible
    # For mutated code: (is_async or (False or False)) is True -> raises Impossible
    
    # Test that as_const returns the expected value (original behavior)
    result = node.as_const(eval_ctx)
    assert result == 42
