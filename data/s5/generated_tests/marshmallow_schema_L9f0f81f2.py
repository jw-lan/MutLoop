import pytest
from marshmallow import Schema
from marshmallow import base

class TestSchema:
    def test_bind_field_with_field_class_and_not_type(self):
        # Create a schema instance
        schema = Schema()
        
        # Create a field class that is a subclass of FieldABC
        class MyField(base.FieldABC):
            def _deserialize(self, value, attr, data, **kwargs):
                return value
            def _serialize(self, value, attr, obj, **kwargs):
                return value
            def deserialize(self, value, attr=None, data=None, **kwargs):
                return value
            def serialize(self, attr, obj, accessor=None, **kwargs):
                return value
        
        # Create an instance of the field class
        field_instance = MyField()
        
        # Mock _bind_to_schema to raise TypeError
        def raise_type_error(*args, **kwargs):
            raise TypeError("mock error")
        field_instance._bind_to_schema = raise_type_error
        
        # Now call _bind_field directly
        with pytest.raises(TypeError) as exc_info:
            schema._bind_field("test_field", field_instance)
        
        # For original code: isinstance(field_instance, type) is False, 
        # so condition is False -> re-raise original TypeError ("mock error")
        # For mutated code: issubclass(field_instance, base.FieldABC) is True,
        # so condition is True -> raise custom TypeError with message
        
        # Check that the error message is the original one (original behavior)
        # This will fail on mutated code because mutated raises custom message
        assert str(exc_info.value) == "mock error"
