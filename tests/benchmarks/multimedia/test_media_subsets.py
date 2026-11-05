"""La partición de los datasets multimedia: anidada, en proporción, determinista y con las
consultas fuera de la base."""

import random
from collections import Counter
from pathlib import Path

import pytest
from benchmarks.multimedia.subsets import (
    QUERIES_CSV,
    QUERY_POOL_FACTOR,
    SUBSETS_DIRECTORY,
    Columns,
    MediaItem,
    SubsetError,
    missing_files,
    partition,
    stratified_order,
    summary_table,
    write_partition,
)

from config import EngineConfig
from query.engine import Engine

SEED = 7
CLASS_SIZES = {"perro": 120, "gato": 60, "ave": 15, "pez": 5}
TAKES_PER_GROUP = 5


def dataset() -> list[MediaItem]:
    """Elementos de cuatro clases desiguales; cada grupo (hablante) tiene cinco tomas."""
    items = []
    for category, count in CLASS_SIZES.items():
        for number in range(count):
            group = f"{category}-{number // TAKES_PER_GROUP}"
            items.append(MediaItem(f"clips/{category}/{number}.wav", f"{category} {number}", category, group))
    return items


def test_every_prefix_has_the_classes_in_proportion():
    ordered = stratified_order(dataset(), random.Random(SEED))
    total = len(ordered)
    for size in range(1, total + 1):
        counts = Counter(item.category for item in ordered[:size])
        for category, members in CLASS_SIZES.items():
            assert abs(counts[category] - size * members / total) < len(CLASS_SIZES) / 2


def test_the_order_does_not_depend_on_the_input_order():
    items = dataset()
    shuffled = list(items)
    random.Random(99).shuffle(shuffled)
    assert stratified_order(items, random.Random(SEED)) == stratified_order(
        shuffled, random.Random(SEED)
    )


def test_subsets_are_nested_and_the_same_for_the_same_seed():
    first = partition(dataset(), 150, 8, SEED)
    again = partition(reversed(dataset()), 150, 8, SEED)
    assert first == again
    assert first.subset(10) == first.subset(100)[:10]
    assert len(set(first.base)) == 150
    assert partition(dataset(), 150, 8, SEED + 1) != first


def test_queries_share_no_group_with_the_base():
    split = partition(dataset(), 150, 8, SEED)
    assert len(split.queries) == 8
    query_groups = {item.group for item in split.queries}
    assert query_groups.isdisjoint(item.group for item in split.base)
    assert not set(split.queries) & set(split.base)


def test_whole_groups_are_held_out_for_several_times_the_queries():
    held_out = 10 * QUERY_POOL_FACTOR
    split = partition(dataset(), len(dataset()) - held_out, 10, SEED)
    assert len({item.group for item in split.queries}) > 10 // TAKES_PER_GROUP
    with pytest.raises(SubsetError):
        partition(dataset(), len(dataset()) - held_out + 1, 10, SEED)


def test_without_queries_the_base_is_a_proportional_sample():
    split = partition(dataset(), 40, 0, SEED)
    assert split.queries == ()
    counts = Counter(item.category for item in split.base)
    assert counts == {"perro": 24, "gato": 12, "ave": 3, "pez": 1}


def test_a_base_that_does_not_fit_after_the_queries_is_rejected():
    with pytest.raises(SubsetError, match="quedan"):
        partition(dataset(), 195, 5, SEED)


def test_more_queries_than_the_dataset_has_are_rejected():
    with pytest.raises(SubsetError, match="se apartan"):
        partition(dataset(), 1, 60, SEED)


@pytest.mark.parametrize(("largest", "queries"), [(0, 5), (-1, 0), (10, -1)])
def test_invalid_sizes_are_rejected(largest: int, queries: int):
    with pytest.raises(SubsetError):
        partition(dataset(), largest, queries, SEED)


def test_a_subset_larger_than_the_base_is_rejected():
    with pytest.raises(SubsetError):
        partition(dataset(), 20, 0, SEED).subset(21)


def test_the_summary_has_a_row_per_set():
    split = partition(dataset(), 100, 8, SEED)
    table = summary_table(split.named_sets([10, 100]))
    rows = table.splitlines()[2:]
    assert [row.split(" | ")[0] for row in rows] == ["| 10", "| 100", "| consultas"]
    assert rows[1].startswith("| 100 | 100 |")
    assert " | 4 | perro " in rows[1]


def test_missing_files_are_the_ones_not_on_disk(tmp_path: Path):
    items = dataset()[:3]
    (tmp_path / "clips" / "perro").mkdir(parents=True)
    (tmp_path / items[1].path).write_bytes(b"RIFF")
    assert missing_files(items, tmp_path, "clips") == ["perro/0.wav", "perro/2.wav"]


def test_the_written_csvs_load_in_the_engine(config: EngineConfig):
    split = partition(dataset(), 100, 8, SEED)
    output = config.data_directory / "audio"
    columns = Columns(category="palabra", group="hablante")
    write_partition(split, [10, 100], output, "data/datasets/audio", "clips.csv", columns)
    small = output / SUBSETS_DIRECTORY / "10" / "clips.csv"
    with Engine(config) as engine:
        engine.execute(f"CREATE TABLE clips FROM FILE '{small}'")
        engine.execute(f"CREATE TABLE consultas FROM FILE '{output / QUERIES_CSV}'")
        rows = engine.execute("SELECT id, ruta, titulo, palabra, hablante FROM clips ORDER BY id").rows
        queries = engine.execute("SELECT COUNT(*) FROM consultas").rows
    first = split.base[0]
    assert rows[0] == (1, f"data/datasets/audio/{first.path}", first.title, first.category, first.group)
    assert [row[0] for row in rows] == list(range(1, 11))
    assert queries == ((8,),)
    large = (output / SUBSETS_DIRECTORY / "100" / "clips.csv").read_text(encoding="utf-8")
    assert large.startswith(small.read_text(encoding="utf-8"))
