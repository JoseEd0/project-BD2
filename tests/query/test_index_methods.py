"""El registro de métodos de índice secundario."""

import pytest

from query.index_methods import Capability, method_named
from sql.nodes import IndexType
from storage.schema import Field
from storage.types import FieldType

NUMBER = Field("n", FieldType.INT)
PLACE = Field("lugar", FieldType.POINT)
FEATURES = Field("rasgos", FieldType.VECTOR, 4)
NAME = Field("nombre", FieldType.STRING, 20)
DESCRIPTION = Field("descripcion", FieldType.TEXT)


@pytest.mark.parametrize(
    ("method", "capabilities"),
    [
        (IndexType.BTREE, {Capability.EQUALITY, Capability.RANGE}),
        (IndexType.HASH, {Capability.EQUALITY}),
        (IndexType.RTREE, {Capability.EQUALITY, Capability.SPATIAL}),
        (IndexType.INVERTED, {Capability.TEXT_SEARCH}),
        (IndexType.HNSW, {Capability.SIMILARITY}),
        (IndexType.IVF, {Capability.SIMILARITY}),
    ],
)
def test_each_method_declares_what_it_answers(method: IndexType, capabilities: set):
    known = method_named(method)
    assert known is not None
    assert set(known.capabilities) == capabilities


@pytest.mark.parametrize(
    ("method", "field", "refused"),
    [
        (IndexType.BTREE, NUMBER, False),
        (IndexType.BTREE, PLACE, True),
        (IndexType.BTREE, FEATURES, True),
        (IndexType.HASH, NUMBER, False),
        (IndexType.HASH, FEATURES, False),
        (IndexType.HASH, PLACE, True),
        (IndexType.RTREE, PLACE, False),
        (IndexType.RTREE, NUMBER, True),
        (IndexType.INVERTED, NAME, False),
        (IndexType.INVERTED, DESCRIPTION, False),
        (IndexType.INVERTED, NUMBER, True),
        (IndexType.INVERTED, PLACE, True),
        (IndexType.HNSW, NAME, False),
        (IndexType.HNSW, DESCRIPTION, False),
        (IndexType.HNSW, NUMBER, True),
        (IndexType.IVF, NAME, False),
        (IndexType.IVF, PLACE, True),
    ],
)
def test_each_method_says_which_columns_it_serves(method: IndexType, field: Field, refused: bool):
    known = method_named(method)
    assert known is not None
    assert (known.refusal_for(field) is not None) is refused


def test_methods_that_are_not_secondary_indexes_are_not_registered():
    assert method_named(IndexType.SEQUENTIAL) is None


def test_a_method_without_options_refuses_every_one():
    known = method_named(IndexType.HASH)
    assert known is not None
    assert known.unknown_options({"m": 16, "Lists": 4}) == ["Lists", "m"]


def test_the_inverted_index_takes_its_language():
    known = method_named(IndexType.INVERTED)
    assert known is not None
    assert known.unknown_options({"IDIOMA": "en", "k1": 1.2}) == ["k1"]
