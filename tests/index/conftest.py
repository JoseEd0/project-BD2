from collections.abc import Callable

import numpy as np
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


VECTOR_CLUSTERS = 12
VECTOR_CENTER_SEED = 7
VECTOR_NOISE = 0.5

ClusteredVectors = Callable[[int, int, int], np.ndarray]


@pytest.fixture
def clustered_vectors() -> ClusteredVectors:
    """Vectores agrupados alrededor de los mismos centros para cualquier semilla, como los
    histogramas de objetos parecidos: la base y las consultas salen de la misma población."""

    def generate(count: int, dimension: int, seed: int) -> np.ndarray:
        centers = np.random.default_rng(VECTOR_CENTER_SEED).standard_normal(
            (VECTOR_CLUSTERS, dimension)
        )
        rng = np.random.default_rng(seed)
        noise = rng.standard_normal((count, dimension)) * VECTOR_NOISE
        return (centers[rng.integers(0, VECTOR_CLUSTERS, count)] + noise).astype(np.float32)

    return generate
