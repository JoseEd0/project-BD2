import pytest

from config import EngineConfig
from storage.heap import HeapFile
from storage.heap.heap_file import (
    HEAP_MAGIC,
    HEAP_VERSION,
    HeapFormatError,
    RecordNotFoundError,
)
from storage.record import RecordSerializer
from storage.record_id import RecordId

RECORD_SIZE = 16


def blob(marker: int) -> bytes:
    return bytes([marker % 256]) * RECORD_SIZE


@pytest.fixture
def heap(config: EngineConfig):
    with HeapFile(config.data_directory / "t.heap", RECORD_SIZE, config) as heap_file:
        yield heap_file


def test_new_file_is_empty(heap: HeapFile):
    assert heap.record_count == 0
    assert list(heap.scan()) == []


def test_single_record(heap: HeapFile):
    record_id = heap.insert(blob(1))
    assert heap.read(record_id) == blob(1)
    assert heap.record_count == 1


def test_records_span_several_pages(heap: HeapFile):
    count = heap.slots_per_page * 3 + 1
    ids = [heap.insert(blob(value)) for value in range(count)]
    assert heap.record_count == count
    assert len({record_id.page_id for record_id in ids}) > 1
    assert all(heap.read(ids[value]) == blob(value) for value in range(count))


def test_scan_returns_every_live_record(heap: HeapFile):
    ids = [heap.insert(blob(value)) for value in range(10)]
    scanned = dict(heap.scan())
    assert scanned == {record_id: heap.read(record_id) for record_id in ids}


def test_delete_removes_the_record(heap: HeapFile):
    record_id = heap.insert(blob(1))
    heap.delete(record_id)
    assert heap.record_count == 0
    with pytest.raises(RecordNotFoundError):
        heap.read(record_id)


def test_deleting_twice_is_rejected(heap: HeapFile):
    record_id = heap.insert(blob(1))
    heap.delete(record_id)
    with pytest.raises(RecordNotFoundError):
        heap.delete(record_id)


def test_free_space_is_reused_before_growing(heap: HeapFile):
    ids = [heap.insert(blob(value)) for value in range(heap.slots_per_page * 2)]
    pages_before = heap.page_count
    heap.delete(ids[0])
    heap.delete(ids[-1])
    assert heap.insert(blob(200)) == ids[-1]
    assert heap.insert(blob(201)) == ids[0]
    assert heap.page_count == pages_before


def test_update_keeps_the_address(heap: HeapFile):
    record_id = heap.insert(blob(1))
    heap.update(record_id, blob(2))
    assert heap.read(record_id) == blob(2)
    assert heap.record_count == 1


def test_update_of_a_deleted_record_is_rejected(heap: HeapFile):
    record_id = heap.insert(blob(1))
    heap.delete(record_id)
    with pytest.raises(RecordNotFoundError):
        heap.update(record_id, blob(2))


def test_reading_the_header_page_is_rejected(heap: HeapFile):
    with pytest.raises(RecordNotFoundError):
        heap.read(RecordId(page_id=0, slot=0))


def test_state_survives_reopening(config: EngineConfig):
    path = config.data_directory / "t.heap"
    with HeapFile(path, RECORD_SIZE, config) as heap:
        ids = [heap.insert(blob(value)) for value in range(20)]
        heap.delete(ids[5])
    with HeapFile(path, RECORD_SIZE, config) as heap:
        assert heap.record_count == 19
        assert heap.read(ids[0]) == blob(0)
        assert heap.insert(blob(99)) == ids[5]


def test_opening_with_another_record_size_is_rejected(config: EngineConfig):
    path = config.data_directory / "t.heap"
    with HeapFile(path, RECORD_SIZE, config):
        pass
    with pytest.raises(HeapFormatError):
        HeapFile(path, RECORD_SIZE + 1, config)


def test_serialized_rows_round_trip(config: EngineConfig, serializer: RecordSerializer):
    with HeapFile(config.data_directory / "rows.heap", serializer.size, config) as rows:
        record_id = rows.insert(serializer.pack((1, "ana", 5.0)))
        assert serializer.unpack(rows.read(record_id)) == (1, "ana", 5.0)


def test_read_many_returns_the_records_in_the_order_asked(heap: HeapFile):
    addresses = [heap.insert(blob(marker)) for marker in range(60)]
    wanted = [addresses[41], addresses[3], addresses[40], addresses[59], addresses[3]]
    assert list(heap.read_many(wanted)) == [blob(41), blob(3), blob(40), blob(59), blob(3)]
    assert list(heap.read_many([])) == []


def test_read_many_loads_each_page_once_when_addresses_are_sorted(heap: HeapFile):
    """Es lo que hace barato ir al heap con muchas direcciones: una lectura por página."""
    addresses = [heap.insert(blob(marker)) for marker in range(200)]
    pages = {address.page_id for address in addresses}
    assert len(pages) > 8
    heap.flush()
    heap._pager._cache.clear()
    heap._pager.reads = 0
    assert len(list(heap.read_many(sorted(addresses)))) == 200
    assert heap._pager.reads == len(pages)


def test_read_many_rejects_a_freed_slot(heap: HeapFile):
    addresses = [heap.insert(blob(marker)) for marker in range(5)]
    heap.delete(addresses[2])
    with pytest.raises(RecordNotFoundError):
        list(heap.read_many(addresses))


def test_a_file_that_is_not_a_heap_is_rejected(config: EngineConfig):
    path = config.data_directory / "ajeno.heap"
    path.write_bytes(b"x" * config.page_size)
    with pytest.raises(HeapFormatError, match="no es un heap file"):
        HeapFile(path, RECORD_SIZE, config)


def test_a_heap_of_another_version_is_rejected(config: EngineConfig):
    path = config.data_directory / "t.heap"
    with HeapFile(path, RECORD_SIZE, config):
        pass
    raw = bytearray(path.read_bytes())
    raw[len(HEAP_MAGIC)] = HEAP_VERSION + 1
    path.write_bytes(bytes(raw))
    with pytest.raises(HeapFormatError, match="versión"):
        HeapFile(path, RECORD_SIZE, config)


def test_pages_that_hold_a_single_record(config: EngineConfig):
    """Con un registro por página, cada inserción la llena y cada borrado la deja entera
    libre: la lista de páginas con espacio se recorre en los dos sentidos."""
    wide = config.page_size - 2 * RECORD_SIZE
    with HeapFile(config.data_directory / "ancho.heap", wide, config) as heap:
        assert heap.slots_per_page == 1
        ids = [heap.insert(bytes([marker]) * wide) for marker in range(5)]
        assert len({record_id.page_id for record_id in ids}) == 5
        heap.delete(ids[1])
        heap.delete(ids[3])
        pages = heap.page_count
        reused = [heap.insert(bytes([marker]) * wide) for marker in (11, 13)]
        assert {record_id.page_id for record_id in reused} == {ids[1].page_id, ids[3].page_id}
        assert heap.page_count == pages
        assert heap.record_count == 5
        assert heap.insert(bytes([20]) * wide).page_id not in {item.page_id for item in ids}
