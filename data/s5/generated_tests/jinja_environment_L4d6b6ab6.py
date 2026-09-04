import pytest
from jinja2.environment import create_cache

def test_create_cache_distinguishes_zero():
    # The key insight: the first condition checks `size == 0`.
    # For a normal integer 0, this is True and returns None before reaching the mutated line.
    # But what if we pass a value where `size == 0` is False, yet `size <= 0` is True?
    # This is impossible for standard numbers, but we can craft a custom object.
    
    class ZeroLike:
        def __eq__(self, other):
            # Make size == 0 return False, so we skip the first if
            return False
        def __lt__(self, other):
            # For size < 0: return False (so original would not return {})
            return False
        def __le__(self, other):
            # For size <= 0: return True (so mutant would return {})
            return True
    
    # Original code: size == 0 is False, size < 0 is False -> goes to LRUCache
    # Mutant code: size == 0 is False, size <= 0 is True -> returns {}
    # This should kill the mutant!
    
    result = create_cache(ZeroLike())
    # Original returns LRUCache, mutant returns {}
    # We assert it's an LRUCache (original behavior)
    from jinja2.utils import LRUCache
    assert isinstance(result, LRUCache)
