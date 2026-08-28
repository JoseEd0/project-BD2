import pytest

from config import EngineConfig
from index.bplustree import UnclusteredBPlusIndex
from storage.record import RecordSerializer
from storage.record_id import RecordId


def row(serializer: RecordSerializer, key: int, city: str) -> bytes:
    return serializer.pack((key, f"n{key}", city))


@pytest.fixture
def index(config: EngineConfig, serializer: RecordSerializer):
    with UnclusteredBPlusIndex(
        config.data_directory / "u.bpt", serializer, "ciudad", config
    ) as unclustered:
        yield unclustered


def test_empty_index(index: UnclusteredBPlusIndex):
    assert index.entry_count == 0
    assert index.search("lima") == []


def test_points_at_the_row_address(index: UnclusteredBPlusIndex, serializer: RecordSerializer):
    address = RecordId(page_id=3, slot=2)
    index.insert(row(serializer, 1, "lima"), address)
    assert index.search("lima") == [address]


def test_repeated_values_keep_every_address(
    index: UnclusteredBPlusIndex, serializer: RecordSerializer
):
    addresses = [RecordId(page_id=1, slot=slot) for slot in range(5)]
    for slot, address in enumerate(addresses):
        index.insert(row(serializer, slot, "lima"), address)
    assert sorted(index.search("lima")) == sorted(addresses)
    assert index.entry_count == 5


def test_values_are_separated(index: UnclusteredBPlusIndex, serializer: RecordSerializer):
    index.insert(row(serializer, 1, "lima"), RecordId(1, 0))
    index.insert(row(serializer, 2, "cusco"), RecordId(1, 1))
    assert index.search("lima") == [RecordId(1, 0)]
    assert index.search("cusco") == [RecordId(1, 1)]
    assert index.search("piura") == []


def test_range_search_over_values(index: UnclusteredBPlusIndex, serializer: RecordSerializer):
    for slot, city in enumerate(["arequipa", "cusco", "lima", "piura", "tacna"]):
        index.insert(row(serializer, slot, city), RecordId(1, slot))
    found = [city for city, _ in index.range_search("cusco", "piura")]
    assert found == ["cusco", "lima", "piura"]


def test_scan_is_ordered_by_value(index: UnclusteredBPlusIndex, serializer: RecordSerializer):
    for slot, city in enumerate(["lima", "arequipa", "tacna", "cusco"]):
        index.insert(row(serializer, slot, city), RecordId(1, slot))
    assert [city for city, _ in index.scan()] == ["arequipa", "cusco", "lima", "tacna"]


def test_delete_removes_only_that_address(
    index: UnclusteredBPlusIndex, serializer: RecordSerializer
):
    first, second = RecordId(1, 0), RecordId(1, 1)
    index.insert(row(serializer, 1, "lima"), first)
    index.insert(row(serializer, 2, "lima"), second)
    assert index.delete(row(serializer, 1, "lima"), first)
    assert index.search("lima") == [second]
    assert not index.delete(row(serializer, 1, "lima"), first)


def test_many_rows_share_one_value(index: UnclusteredBPlusIndex, serializer: RecordSerializer):
    addresses = [RecordId(page_id=page, slot=slot) for page in range(1, 20) for slot in range(5)]
    for address in addresses:
        index.insert(row(serializer, address.slot, "lima"), address)
    assert sorted(index.search("lima")) == sorted(addresses)
