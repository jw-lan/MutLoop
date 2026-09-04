import pytest
from marshmallow.schema import SchemaOpts

def test_schema_opts_additional_invalid_type_raises_value_error():
    class Meta:
        additional = "not_a_list_or_tuple"
    
    with pytest.raises(ValueError) as excinfo:
        SchemaOpts(Meta)
    
    assert str(excinfo.value) == "`additional` option must be a list or tuple."
