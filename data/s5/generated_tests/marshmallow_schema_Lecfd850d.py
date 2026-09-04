import pytest
from marshmallow.schema import SchemaOpts

def test_schema_opts_additional_invalid_type():
    class InvalidMeta:
        additional = "not_a_list_or_tuple"
        fields = ()
        exclude = ()
    
    with pytest.raises(ValueError) as excinfo:
        SchemaOpts(InvalidMeta)
    
    assert str(excinfo.value) == "`additional` option must be a list or tuple."
