"""Las dos formas de guardar las filas de una tabla."""

import pytest

from config import EngineConfig
from index.bplustree import ClusteredBPlusIndex
from query.row_store import HeapStore, KeyedStore, UnorderedStoreError
from storage.heap import HeapFile
from storage.record import RecordSerializer
from storage.record_id import RecordId
from storage.schema import Field, Schema
from storage.sequential import SequentialFile
from storage.types import FieldType

SCHEMA = Schema([Field("id", FieldType.INT, nullable=False), Field("v", FieldType.INT)])
SERIALIZER = RecordSerializer(SCHEMA)


def key_of(record: bytes) -> int:
    value = SERIALIZER.unpack_field(record, 0)
    assert isinstance(value, int)
    return value


def row(key: int) -> bytes:
    return SERIALIZER.pack((key, key * 10))


def test_a_heap_without_a_key_index_finds_keys_by_scanning(config: EngineConfig):
    with HeapFile(config.data_directory / "t.heap", SERIALIZER.size, config) as heap:
        store = HeapStore(heap, key_of)
        for key in range(20):
            assert store.insert(row(key)) is not None
        found = [key_of(record) for _, record in store.with_keys([3, 17, 99])]
        assert sorted(found) == [3, 17]
        assert store.keeps_addresses


def test_a_heap_has_no_key_order(config: EngineConfig):
    path = config.data_directory / "t.heap"
    with HeapFile(path, SERIALIZER.size, config) as heap, pytest.raises(UnorderedStoreError):
        list(HeapStore(heap, key_of).in_key_range(1, 5))


@pytest.mark.parametrize("kind", ["sequential", "clustered"])
def test_a_keyed_store_finds_rows_by_key_and_range(config: EngineConfig, kind: str):
    path = config.data_directory / f"t.{kind}"
    storage = (
        SequentialFile(path, SERIALIZER, "id", config)
        if kind == "sequential"
        else ClusteredBPlusIndex(path, SERIALIZER, "id", config)
    )
    store = KeyedStore(storage, key_of)
    try:
        for key in (5, 1, 9, 3):
            assert store.insert(row(key)) is None
        assert [key_of(record) for _, record in store.with_keys([9, 4])] == [9]
        assert [key_of(record) for _, record in store.in_key_range(2, 9)] == [3, 5, 9]
        store.update(None, row(3), SERIALIZER.pack((3, -1)))
        store.delete(None, row(5))
        assert [SERIALIZER.unpack(record) for _, record in store.scan()] == [
            (1, 10),
            (3, -1),
            (9, 90),
        ]
        assert not store.keeps_addresses
        with pytest.raises(UnorderedStoreError):
            list(store.at([RecordId(page_id=1, slot=0)]))
    finally:
        store.close()
