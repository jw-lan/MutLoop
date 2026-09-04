import pytest
from jinja2.environment import Environment
from jinja2.lexer import TokenStream

def test_tokenize_mutation_kill():
    env = Environment()
    
    # Create a custom extension that returns a non-TokenStream object
    # from filter_stream, which will trigger the mutated code path
    class CustomExtension:
        identifier = "custom_extension"
        priority = 10
        
        def __init__(self, environment):
            self.environment = environment
            
        def preprocess(self, source, name, filename=None):
            return source
            
        def filter_stream(self, stream):
            # Return a list instead of TokenStream to trigger the conversion
            return list(stream)
    
    # Register the extension
    env.add_extension(CustomExtension)
    
    # This should trigger _tokenize with a non-TokenStream result
    # from the extension's filter_stream
    result = env._tokenize("Hello {{ name }}", "test_template", "test.html")
    
    # The result should be a TokenStream with the correct name and filename
    assert isinstance(result, TokenStream)
    assert result.name == "test_template"
    assert result.filename == "test.html"
