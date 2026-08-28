import random
from collections.abc import Callable

import pytest

from config import EngineConfig
from external.sort import ExternalSorter
from index.keys import Key
from storage.record import RecordSerializer

SHUFFLE_SEED = 13


@pytest.fixture
def small_buffer(config: EngineConfig) -> EngineConfig:
    """Buffer de una sola página: obliga a generar muchos runs y varias pasadas."""
    return EngineConfig(
        page_size=config.page_size,
        sort_buffer_pages=1,
        merge_fan_in=3,
        data_directory=config.data_directory,
    )


def rows(serializer: RecordSerializer, keys: list[int]) -> list[bytes]:
    return [serializer.pack((key, "lima")) for key in keys]


def sorted_keys(
    engine: EngineConfig,
    serializer: RecordSerializer,
    id_of: Callable[[bytes], Key],
    records: list[bytes],
) -> tuple[list[int], ExternalSorter]:
    sorter = ExternalSorter(engine.data_directory / "sort", serializer.size, id_of, engine)
    with sorter:
        return [serializer.unpack(record)[0] for record in sorter.sort(records)], sorter


def test_empty_input(config: EngineConfig, serializer: RecordSerializer, id_of):
    keys, sorter = sorted_keys(config, serializer, id_of, [])
    assert keys == []
    assert sorter.run_count == 0


def test_single_record(config: EngineConfig, serializer: RecordSerializer, id_of):
    keys, _ = sorted_keys(config, serializer, id_of, rows(serializer, [7]))
    assert keys == [7]


def test_input_that_fits_in_the_buffer_makes_one_run(
    config: EngineConfig, serializer: RecordSerializer, id_of
):
    keys, sorter = sorted_keys(config, serializer, id_of, rows(serializer, [3, 1, 2]))
    assert keys == [1, 2, 3]
    assert sorter.run_count == 1
    assert sorter.merge_passes == 1


def test_already_sorted_input(config: EngineConfig, serializer: RecordSerializer, id_of):
    keys, _ = sorted_keys(config, serializer, id_of, rows(serializer, list(range(200))))
    assert keys == list(range(200))


def test_reversed_input(config: EngineConfig, serializer: RecordSerializer, id_of):
    keys, _ = sorted_keys(
        config, serializer, id_of, rows(serializer, list(reversed(range(200))))
    )
    assert keys == list(range(200))


def test_random_input_needs_several_merge_passes(
    small_buffer: EngineConfig, serializer: RecordSerializer, id_of
):
    shuffled = list(range(500))
    random.Random(SHUFFLE_SEED).shuffle(shuffled)
    keys, sorter = sorted_keys(small_buffer, serializer, id_of, rows(serializer, shuffled))
    assert keys == sorted(shuffled)
    assert sorter.run_count > sorter.fan_in
    assert sorter.merge_passes > 1


def test_repeated_keys_are_all_kept(config: EngineConfig, serializer: RecordSerializer, id_of):
    keys, _ = sorted_keys(config, serializer, id_of, rows(serializer, [5] * 50))
    assert keys == [5] * 50


def test_sorting_by_a_text_column(config: EngineConfig, serializer: RecordSerializer, city_of):
    cities = ["piura", "lima", "tacna", "cusco"]
    records = [serializer.pack((index, city)) for index, city in enumerate(cities)]
    with ExternalSorter(
        config.data_directory / "sort", serializer.size, city_of, config
    ) as sorter:
        assert [serializer.unpack(record)[1] for record in sorter.sort(records)] == sorted(cities)


def test_temporary_files_are_removed(
    small_buffer: EngineConfig, serializer: RecordSerializer, id_of
):
    directory = small_buffer.data_directory / "sort"
    with ExternalSorter(directory, serializer.size, id_of, small_buffer) as sorter:
        list(sorter.sort(rows(serializer, list(range(300)))))
    assert list(directory.glob("*.tmp")) == []
