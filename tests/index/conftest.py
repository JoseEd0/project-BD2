import pytest

from index.keys import ScalarKeyCodec
from storage.record import RecordSerializer
from storage.schema import Field, Schema
from storage.types import FieldType

NAME_LENGTH = 8


@pytest.fixture
def key_codec() -> ScalarKeyCodec:
    return ScalarKeyCodec(Field("id", FieldType.INT))


@pytest.fixture
def schema() -> Schema:
    return Schema(
        [
            Field("id", FieldType.INT, nullable=False),
            Field("nombre", FieldType.STRING, NAME_LENGTH),
            Field("ciudad", FieldType.STRING, NAME_LENGTH),
        ]
    )


@pytest.fixture
def serializer(schema: Schema) -> RecordSerializer:
    return RecordSerializer(schema)
