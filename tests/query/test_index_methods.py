"""El registro de métodos de índice secundario."""

import pytest

from query.index_methods import Capability, method_named
from sql.nodes import IndexType
from storage.schema import Field
from storage.types import FieldType

NUMBER = Field("n", FieldType.INT)
PLACE = Field("lugar", FieldType.POINT)
FEATURES = Field("rasgos", FieldType.VECTOR, 4)


@pytest.mark.parametrize(
    ("method", "capabilities"),
    [
        (IndexType.BTREE, {Capability.EQUALITY, Capability.RANGE}),
        (IndexType.HASH, {Capability.EQUALITY}),
        (IndexType.RTREE, {Capability.EQUALITY, Capability.SPATIAL}),
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
    ],
)
def test_each_method_says_which_columns_it_serves(method: IndexType, field: Field, refused: bool):
    known = method_named(method)
    assert known is not None
    assert (known.refusal_for(field) is not None) is refused


def test_methods_that_are_not_secondary_indexes_are_not_registered():
    assert method_named(IndexType.SEQUENTIAL) is None
    assert method_named(IndexType.HNSW) is None


def test_no_method_accepts_options_yet():
    known = method_named(IndexType.HASH)
    assert known is not None
    assert known.unknown_options({"m": 16, "Lists": 4}) == ["Lists", "m"]
