import pytest

from storage.record import RecordSerializer
from storage.schema import Field, Schema
from storage.types import FieldType

NAME_LENGTH = 12


@pytest.fixture
def schema() -> Schema:
    return Schema(
        [
            Field("id", FieldType.INT, nullable=False),
            Field("nombre", FieldType.STRING, NAME_LENGTH),
            Field("nota", FieldType.FLOAT),
        ]
    )


@pytest.fixture
def serializer(schema: Schema) -> RecordSerializer:
    return RecordSerializer(schema)
