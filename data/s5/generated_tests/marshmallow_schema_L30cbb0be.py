import pytest
from marshmallow.schema import SchemaOpts

def test_schema_opts_raises_when_both_fields_and_additional_set():
    class Meta:
        fields = ("name",)
        additional = ("age",)

    with pytest.raises(ValueError, match="Cannot set both `fields` and `additional` options for the same Schema."):
        SchemaOpts(Meta)
