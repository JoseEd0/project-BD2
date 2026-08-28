import pytest

from config import EngineConfig
from external.hash import ExternalHashGrouper, ExternalHashJoin
from hashing import canonical_key_bytes
from storage.record import RecordSerializer

CITIES = ["lima", "cusco", "piura", "tacna"]


@pytest.fixture
def few_partitions(config: EngineConfig) -> EngineConfig:
    return EngineConfig(
        page_size=config.page_size, hash_partitions=4, data_directory=config.data_directory
    )


def test_equal_values_share_canonical_bytes():
    assert canonical_key_bytes(1) == canonical_key_bytes(1.0) == canonical_key_bytes(True)
    assert canonical_key_bytes("1") != canonical_key_bytes(1)


def test_grouping_empty_input(few_partitions: EngineConfig, serializer: RecordSerializer, city_of):
    with ExternalHashGrouper(
        few_partitions.data_directory / "g", serializer.size, city_of, few_partitions
    ) as grouper:
        assert list(grouper.group([])) == []


def test_every_row_lands_in_exactly_one_group(
    few_partitions: EngineConfig, serializer: RecordSerializer, city_of
):
    records = [serializer.pack((index, CITIES[index % 4])) for index in range(200)]
    with ExternalHashGrouper(
        few_partitions.data_directory / "g", serializer.size, city_of, few_partitions
    ) as grouper:
        groups = {key: len(rows) for key, rows in grouper.group(records)}
    assert groups == dict.fromkeys(CITIES, 50)


def test_a_single_group(few_partitions: EngineConfig, serializer: RecordSerializer, city_of):
    records = [serializer.pack((index, "lima")) for index in range(100)]
    with ExternalHashGrouper(
        few_partitions.data_directory / "g", serializer.size, city_of, few_partitions
    ) as grouper:
        groups = list(grouper.group(records))
    assert len(groups) == 1
    assert len(groups[0][1]) == 100


def test_partitions_are_used(few_partitions: EngineConfig, serializer: RecordSerializer, id_of):
    records = [serializer.pack((index, "lima")) for index in range(200)]
    with ExternalHashGrouper(
        few_partitions.data_directory / "g", serializer.size, id_of, few_partitions
    ) as grouper:
        list(grouper.group(records))
        assert sum(grouper.partition_sizes) == 200
        assert sum(1 for size in grouper.partition_sizes if size > 0) > 1


def test_join_on_equal_keys(few_partitions: EngineConfig, serializer: RecordSerializer, id_of):
    left = [serializer.pack((key, "izq")) for key in range(100)]
    right = [serializer.pack((key * 2, "der")) for key in range(100)]
    with ExternalHashJoin(
        few_partitions.data_directory / "j",
        serializer.size,
        serializer.size,
        id_of,
        id_of,
        few_partitions,
    ) as joiner:
        pairs = [
            (serializer.unpack(a)[0], serializer.unpack(b)[0]) for a, b in joiner.join(left, right)
        ]
    assert len(pairs) == 50
    assert all(a == b for a, b in pairs)


def test_join_without_matches(few_partitions: EngineConfig, serializer: RecordSerializer, id_of):
    left = [serializer.pack((key, "izq")) for key in range(50)]
    right = [serializer.pack((key + 1000, "der")) for key in range(50)]
    with ExternalHashJoin(
        few_partitions.data_directory / "j",
        serializer.size,
        serializer.size,
        id_of,
        id_of,
        few_partitions,
    ) as joiner:
        assert list(joiner.join(left, right)) == []


def test_join_multiplies_repeated_keys(
    few_partitions: EngineConfig, serializer: RecordSerializer, id_of
):
    left = [serializer.pack((7, f"i{index}")) for index in range(3)]
    right = [serializer.pack((7, f"d{index}")) for index in range(4)]
    with ExternalHashJoin(
        few_partitions.data_directory / "j",
        serializer.size,
        serializer.size,
        id_of,
        id_of,
        few_partitions,
    ) as joiner:
        assert len(list(joiner.join(left, right))) == 12


def test_join_with_an_empty_side(few_partitions: EngineConfig, serializer: RecordSerializer, id_of):
    left = [serializer.pack((key, "izq")) for key in range(10)]
    with ExternalHashJoin(
        few_partitions.data_directory / "j",
        serializer.size,
        serializer.size,
        id_of,
        id_of,
        few_partitions,
    ) as joiner:
        assert list(joiner.join(left, [])) == []
