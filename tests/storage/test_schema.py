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


def test_projection_keeps_the_requested_order(schema: Schema):
    assert schema.project(["nota", "id"]).names == ("nota", "id")


def test_schemas_with_the_same_fields_are_equal(schema: Schema):
    assert Schema(list(schema.fields)) == schema
