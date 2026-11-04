"""Archivos temporales de registros y su mezcla."""

from pathlib import Path

from config import EngineConfig
from external.runs import RunReader, RunWriter, merge_sorted_runs, merge_sorted_streams
from storage.page import slot_capacity

RECORD_SIZE = 8


def record(key: int, tag: int = 0) -> bytes:
    return key.to_bytes(4, "big") + tag.to_bytes(4, "big")


def key_of(raw: bytes) -> int:
    return int.from_bytes(raw[:4], "big")


def write_run(path: Path, records: list[bytes], config: EngineConfig) -> Path:
    with RunWriter(path, RECORD_SIZE, config) as writer:
        writer.extend(records)
    return path


def read_run(path: Path, config: EngineConfig) -> list[bytes]:
    with RunReader(path, RECORD_SIZE, config) as reader:
        return list(reader)


def test_an_empty_run_reads_back_empty(config: EngineConfig):
    path = write_run(config.data_directory / "vacio.run", [], config)
    assert read_run(path, config) == []


def test_a_run_of_exactly_one_page(config: EngineConfig):
    capacity = slot_capacity(config.page_size, RECORD_SIZE)
    records = [record(number) for number in range(capacity)]
    path = write_run(config.data_directory / "justo.run", records, config)
    assert read_run(path, config) == records
    assert path.stat().st_size == config.page_size


def test_merging_keeps_the_order_and_is_stable(config: EngineConfig):
    first = write_run(config.data_directory / "a.run", [record(1, 1), record(3, 1)], config)
    second = write_run(config.data_directory / "b.run", [record(1, 2), record(2, 2)], config)
    merged = list(merge_sorted_runs([first, second], RECORD_SIZE, key_of, config))
    assert merged == [record(1, 1), record(1, 2), record(2, 2), record(3, 1)]


def test_merging_nothing_gives_nothing(config: EngineConfig):
    assert list(merge_sorted_runs([], RECORD_SIZE, key_of, config)) == []


def test_merging_streams_tags_each_item_with_its_stream_and_is_stable():
    streams = [iter([(1, "a"), (3, "a")]), iter([]), iter([(1, "c"), (2, "c")])]
    merged = list(merge_sorted_streams(streams, key_of=lambda item: item[0]))
    assert merged == [(0, (1, "a")), (2, (1, "c")), (2, (2, "c")), (0, (3, "a"))]


def test_merging_streams_pulls_the_next_item_only_when_asked():
    pulled: list[int] = []

    def numbers(values: list[int]):
        for value in values:
            pulled.append(value)
            yield value

    merged = merge_sorted_streams([numbers([1, 4]), numbers([2, 3])], key_of=lambda value: value)
    assert next(merged) == (0, 1)
    assert pulled == [1, 2]
    assert next(merged) == (1, 2)
    assert pulled == [1, 2, 4]
