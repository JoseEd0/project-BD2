import random

import pytest

from config import EngineConfig
from index.bplustree import ClusteredBPlusIndex, DuplicateKeyError
from storage.record import RecordSerializer

SHUFFLE_SEED = 3


def row(serializer: RecordSerializer, key: int) -> bytes:
    return serializer.pack((key, f"n{key}", "lima"))


def keys_of(records: list[bytes], serializer: RecordSerializer) -> list[int]:
    return [serializer.unpack(record)[0] for record in records]


@pytest.fixture
def index(config: EngineConfig, serializer: RecordSerializer):
    with ClusteredBPlusIndex(
        config.data_directory / "c.bpt", serializer, "id", config
    ) as clustered:
        yield clustered


def test_empty_index(index: ClusteredBPlusIndex):
    assert index.record_count == 0
    assert index.search(1) is None
    assert list(index.scan()) == []


def test_the_row_lives_in_the_leaf(index: ClusteredBPlusIndex, serializer: RecordSerializer):
    index.insert(row(serializer, 7))
    stored = index.search(7)
    assert stored is not None
    assert serializer.unpack(stored) == (7, "n7", "lima")


def test_scan_is_ordered(index: ClusteredBPlusIndex, serializer: RecordSerializer):
    keys = list(range(200))
    random.Random(SHUFFLE_SEED).shuffle(keys)
    for key in keys:
        index.insert(row(serializer, key))
    assert keys_of(list(index.scan()), serializer) == sorted(keys)


def test_range_search_returns_rows_in_order(
    index: ClusteredBPlusIndex, serializer: RecordSerializer
):
    for key in range(100):
        index.insert(row(serializer, key))
    assert keys_of(list(index.range_search(20, 24)), serializer) == [20, 21, 22, 23, 24]


def test_duplicate_primary_key_is_rejected(
    index: ClusteredBPlusIndex, serializer: RecordSerializer
):
    index.insert(row(serializer, 1))
    with pytest.raises(DuplicateKeyError):
        index.insert(row(serializer, 1))


def test_delete(index: ClusteredBPlusIndex, serializer: RecordSerializer):
    for key in range(60):
        index.insert(row(serializer, key))
    assert index.delete(30)
    assert index.search(30) is None
    assert not index.delete(30)
    assert index.record_count == 59


def test_state_survives_reopening(config: EngineConfig, serializer: RecordSerializer):
    path = config.data_directory / "p.bpt"
    with ClusteredBPlusIndex(path, serializer, "id", config) as index:
        for key in range(50):
            index.insert(row(serializer, key))
    with ClusteredBPlusIndex(path, serializer, "id", config) as index:
        assert index.record_count == 50
        assert keys_of(list(index.range_search(5, 7)), serializer) == [5, 6, 7]
