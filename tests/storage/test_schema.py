import pytest

from storage.schema import DuplicateFieldError, Field, Schema, UnknownFieldError
from storage.types import FieldType, InvalidValueError


def test_empty_schema_is_rejected():
    with pytest.raises(InvalidValueError):
        Schema([])


def test_duplicate_field_is_rejected():
    with pytest.raises(DuplicateFieldError):
        Schema([Field("id", FieldType.INT), Field("ID", FieldType.INT)])


def test_lookup_is_case_insensitive(schema: Schema):
    assert schema.position_of("NOMBRE") == 1
    assert schema.field_of("Id").type is FieldType.INT


def test_unknown_field_is_rejected(schema: Schema):
    with pytest.raises(UnknownFieldError):
        schema.position_of("apellido")


def test_has_field(schema: Schema):
    assert schema.has_field("nota")
    assert not schema.has_field("apellido")


def test_schemas_with_the_same_fields_are_equal(schema: Schema):
    assert Schema(list(schema.fields)) == schema


def test_equal_schemas_hash_alike_and_differ_from_anything_else(schema: Schema):
    twin = Schema(list(schema.fields))
    assert hash(twin) == hash(schema)
    assert len({twin, schema}) == 1
    assert schema != Schema([Field("id", FieldType.INT)])
    assert schema != "id, nombre, nota"


def test_a_schema_shows_its_fields(schema: Schema):
    assert all(name in repr(schema) for name in schema.names)
