import pytest
from marshmallow import Schema, fields, post_load
from marshmallow.exceptions import ValidationError

class MySchema(Schema):
    x = fields.Int(required=True)

    @post_load
    def process(self, data, **kwargs):
        data['processed'] = True
        return data

def test_mutant_kill_post_load_not_called_when_errors_and_no_hooks():
    schema = MySchema()
    # This input will cause a validation error (missing required field 'x')
    # Original: if not errors and postprocess and self._hooks[POST_LOAD]:
    #   - errors is non-empty -> condition False -> post_load NOT called
    # Mutant: if not errors and postprocess or self._hooks[POST_LOAD]:
    #   - errors is non-empty, but self._hooks[POST_LOAD] is truthy (has post_load)
    #   -> condition True -> post_load IS called (but it shouldn't be)
    with pytest.raises(ValidationError) as exc_info:
        schema.load({})
    # The post_load should NOT have been called, so 'processed' should not be in valid_data
    assert 'processed' not in exc_info.value.valid_data
