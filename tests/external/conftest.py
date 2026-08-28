from collections.abc import Callable

import pytest

from index.keys import Key
from storage.record import RecordSerializer
from storage.schema import Field, Schema
from storage.types import FieldType

CITY_LENGTH = 8


@pytest.fixture
def serializer() -> RecordSerializer:
    return RecordSerializer(
        Schema(
            [
                Field("id", FieldType.INT, nullable=False),
                Field("ciudad", FieldType.STRING, CITY_LENGTH),
            ]
        )
    )


@pytest.fixture
def id_of(serializer: RecordSerializer) -> Callable[[bytes], Key]:
    return lambda record: serializer.unpack_field(record, 0)


@pytest.fixture
def city_of(serializer: RecordSerializer) -> Callable[[bytes], Key]:
    return lambda record: serializer.unpack_field(record, 1)
