from collections import Counter
from itertools import islice

import pytest

from config import EngineConfig
from external.hash import ExternalHashGrouper, ExternalHashJoin
from external.joining import Unmatched
from hashing import canonical_key_bytes
from storage.record import RecordSerializer

CITIES = ["lima", "cusco", "piura", "tacna"]
MANY_KEYS = 600
HEAVY_KEY_ROWS = 150


@pytest.fixture
def few_partitions(config: EngineConfig) -> EngineConfig:
    return EngineConfig(
        page_size=config.page_size, hash_partitions=4, data_directory=config.data_directory
    )


@pytest.fixture
def small_buffer(config: EngineConfig) -> EngineConfig:
    """Cuatro particiones y un buffer de una página: cualquier entrada mediana no cabe."""
    return EngineConfig(
        page_size=config.page_size,
        hash_partitions=4,
        sort_buffer_pages=1,
        data_directory=config.data_directory,
    )


def collect(rows: list[bytes], record: bytes) -> list[bytes]:
    rows.append(record)
    return rows


def count(seen: int, _record: bytes) -> int:
    return seen + 1


def first(kept: bytes, record: bytes) -> bytes:
    return kept or record


def test_equal_values_share_canonical_bytes():
    assert canonical_key_bytes(1) == canonical_key_bytes(1.0) == canonical_key_bytes(True)
    assert canonical_key_bytes("1") != canonical_key_bytes(1)


def test_grouping_empty_input(few_partitions: EngineConfig, serializer: RecordSerializer, city_of):
    with ExternalHashGrouper(
        few_partitions.data_directory / "g", serializer.size, city_of, few_partitions
    ) as grouper:
        assert list(grouper.reduce([], list, collect)) == []


def test_every_row_lands_in_exactly_one_group(
    few_partitions: EngineConfig, serializer: RecordSerializer, city_of
):
    records = [serializer.pack((index, CITIES[index % 4])) for index in range(200)]
    with ExternalHashGrouper(
        few_partitions.data_directory / "g", serializer.size, city_of, few_partitions
    ) as grouper:
        groups = {key: len(rows) for key, rows in grouper.reduce(records, list, collect)}
    assert groups == dict.fromkeys(CITIES, 50)


def test_a_single_group(few_partitions: EngineConfig, serializer: RecordSerializer, city_of):
    records = [serializer.pack((index, "lima")) for index in range(100)]
    with ExternalHashGrouper(
        few_partitions.data_directory / "g", serializer.size, city_of, few_partitions
    ) as grouper:
        groups = list(grouper.reduce(records, list, collect))
    assert len(groups) == 1
    assert len(groups[0][1]) == 100


def test_partitions_are_used(few_partitions: EngineConfig, serializer: RecordSerializer, id_of):
    records = [serializer.pack((index, "lima")) for index in range(200)]
    with ExternalHashGrouper(
        few_partitions.data_directory / "g", serializer.size, id_of, few_partitions
    ) as grouper:
        list(grouper.reduce(records, int, count))
        assert sum(grouper.partition_sizes) == 200
        assert sum(1 for size in grouper.partition_sizes if size > 0) > 1


def test_a_group_is_reduced_in_arrival_order(
    few_partitions: EngineConfig, serializer: RecordSerializer, city_of
):
    records = [serializer.pack((index, CITIES[index % 4])) for index in range(200)]
    with ExternalHashGrouper(
        few_partitions.data_directory / "g", serializer.size, city_of, few_partitions
    ) as grouper:
        firsts = {key: serializer.unpack(kept)[0] for key, kept in grouper.reduce(records, bytes, first)}
        ids = {
            key: [serializer.unpack(row)[0] for row in rows]
            for key, rows in grouper.reduce(records, list, collect)
        }
    assert firsts == {"lima": 0, "cusco": 1, "piura": 2, "tacna": 3}
    assert all(found == sorted(found) for found in ids.values())


def test_more_groups_than_fit_in_memory_are_partitioned_again(
    small_buffer: EngineConfig, serializer: RecordSerializer, id_of
):
    """Con 600 claves y sitio para una página de grupos, una pasada de hash no basta:
    las particiones se vuelven a repartir con otra función hasta que cada una cabe."""
    records = [serializer.pack((index % MANY_KEYS, "lima")) for index in range(MANY_KEYS * 3)]
    directory = small_buffer.data_directory / "g"
    with ExternalHashGrouper(directory, serializer.size, id_of, small_buffer) as grouper:
        counted = list(grouper.reduce(records, int, count))
        assert grouper.levels > 1
    assert len(counted) == MANY_KEYS
    assert dict(counted) == dict.fromkeys(range(MANY_KEYS), 3)
    assert not directory.exists()


def test_one_huge_group_needs_no_extra_passes(
    small_buffer: EngineConfig, serializer: RecordSerializer, city_of
):
    """Un grupo ocupa un estado, tenga las filas que tenga."""
    records = [serializer.pack((index, "lima")) for index in range(MANY_KEYS * 3)]
    with ExternalHashGrouper(
        small_buffer.data_directory / "g", serializer.size, city_of, small_buffer
    ) as grouper:
        assert list(grouper.reduce(records, int, count)) == [("lima", MANY_KEYS * 3)]
        assert grouper.levels == 1


def test_abandoning_a_grouping_leaves_no_files_behind(
    small_buffer: EngineConfig, serializer: RecordSerializer, id_of
):
    records = [serializer.pack((index, "lima")) for index in range(MANY_KEYS)]
    directory = small_buffer.data_directory / "g"
    with ExternalHashGrouper(directory, serializer.size, id_of, small_buffer) as grouper:
        assert len(list(islice(grouper.reduce(records, int, count), 5))) == 5
        assert any(directory.iterdir())
    assert not directory.exists()


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


def ids_of(serializer: RecordSerializer, pairs) -> list[tuple[int | None, int | None]]:
    return sorted(
        (
            (
                None if left is None else serializer.unpack(left)[0],
                None if right is None else serializer.unpack(right)[0],
            )
            for left, right in pairs
        ),
        key=by_side,
    )


def by_side(pair: tuple[int | None, int | None]) -> tuple[bool, int, bool, int]:
    """Orden total para pares con `None`: primero los que tienen fila izquierda."""
    return (pair[0] is None, pair[0] or 0, pair[1] is None, pair[1] or 0)


@pytest.mark.parametrize(
    ("unmatched", "extra"),
    [
        (Unmatched.NONE, []),
        (Unmatched.LEFT, [(0, None), (1, None)]),
        (Unmatched.RIGHT, [(None, 5), (None, 6)]),
        (Unmatched.BOTH, [(0, None), (1, None), (None, 5), (None, 6)]),
    ],
)
def test_outer_joins_keep_the_rows_without_a_partner(
    few_partitions: EngineConfig, serializer: RecordSerializer, id_of, unmatched, extra
):
    left = [serializer.pack((key, "izq")) for key in range(5)]
    right = [serializer.pack((key, "der")) for key in range(2, 7)]
    with ExternalHashJoin(
        few_partitions.data_directory / "j",
        serializer.size,
        serializer.size,
        id_of,
        id_of,
        few_partitions,
        unmatched=unmatched,
    ) as joiner:
        found = ids_of(serializer, joiner.join(left, right))
    matched = [(2, 2), (3, 3), (4, 4)]
    assert found == sorted([*matched, *extra], key=by_side)


def test_rows_with_a_null_key_never_match(
    few_partitions: EngineConfig, serializer: RecordSerializer, city_of
):
    left = [serializer.pack((1, None)), serializer.pack((2, "lima"))]
    right = [serializer.pack((3, None)), serializer.pack((4, "lima"))]
    with ExternalHashJoin(
        few_partitions.data_directory / "j",
        serializer.size,
        serializer.size,
        city_of,
        city_of,
        few_partitions,
        unmatched=Unmatched.BOTH,
    ) as joiner:
        found = ids_of(serializer, joiner.join(left, right))
    assert found == sorted([(2, 4), (1, None), (None, 3)], key=by_side)


def test_an_extra_condition_decides_which_equal_keys_pair_up(
    few_partitions: EngineConfig, serializer: RecordSerializer, city_of
):
    """Las dos filas comparten ciudad, pero solo emparejan si además cumplen la condición;
    la que no la cumple cuenta como fila sin pareja."""
    left = [serializer.pack((key, "lima")) for key in (1, 2, 3)]
    right = [serializer.pack((key, "lima")) for key in (2, 3, 4)]

    def lower_id(first: bytes, second: bytes) -> bool:
        return bool(serializer.unpack(first)[0] < serializer.unpack(second)[0] - 1)

    with ExternalHashJoin(
        few_partitions.data_directory / "j",
        serializer.size,
        serializer.size,
        city_of,
        city_of,
        few_partitions,
        accepts=lower_id,
        unmatched=Unmatched.BOTH,
    ) as joiner:
        found = ids_of(serializer, joiner.join(left, right))
    expected = [(1, 3), (1, 4), (2, 4), (3, None), (None, 2)]
    assert found == sorted(expected, key=by_side)


def brute_force_pairs(left, right, unmatched: Unmatched) -> Counter:
    """La reunión calculada comparando todo con todo, sobre pares `(id, clave)`."""
    pairs: Counter = Counter()
    paired_left: set[int] = set()
    paired_right: set[int] = set()
    for left_id, left_key in left:
        for right_id, right_key in right:
            if left_key is not None and left_key == right_key:
                pairs[(left_id, right_id)] += 1
                paired_left.add(left_id)
                paired_right.add(right_id)
    if Unmatched.LEFT in unmatched:
        pairs.update((left_id, None) for left_id, _ in left if left_id not in paired_left)
    if Unmatched.RIGHT in unmatched:
        pairs.update((None, right_id) for right_id, _ in right if right_id not in paired_right)
    return pairs


def joined_ids(serializer: RecordSerializer, pairs) -> Counter:
    return Counter(
        (
            None if left is None else serializer.unpack(left)[0],
            None if right is None else serializer.unpack(right)[0],
        )
        for left, right in pairs
    )


@pytest.mark.parametrize(
    "unmatched", [Unmatched.NONE, Unmatched.LEFT, Unmatched.RIGHT, Unmatched.BOTH]
)
def test_a_build_side_larger_than_memory_is_partitioned_again(
    small_buffer: EngineConfig, serializer: RecordSerializer, city_of, unmatched
):
    """600 claves distintas a la izquierda no caben en una página por partición; la
    reunión baja de nivel y sigue dando lo mismo que comparar todo con todo."""
    left = [(index, f"k{index}") for index in range(MANY_KEYS)]
    right = [(1000 + index, f"k{index * 2}") for index in range(MANY_KEYS)]
    directory = small_buffer.data_directory / "j"
    with ExternalHashJoin(
        directory, serializer.size, serializer.size, city_of, city_of, small_buffer, unmatched=unmatched
    ) as joiner:
        found = joined_ids(
            serializer, joiner.join(map(serializer.pack, left), map(serializer.pack, right))
        )
        assert joiner.levels > 1
        assert joiner.block_joins == 0
    assert found == brute_force_pairs(left, right, unmatched)
    assert not directory.exists()


@pytest.mark.parametrize(
    "unmatched", [Unmatched.NONE, Unmatched.LEFT, Unmatched.RIGHT, Unmatched.BOTH]
)
def test_a_key_with_more_rows_than_memory_is_joined_by_blocks(
    small_buffer: EngineConfig, serializer: RecordSerializer, city_of, unmatched
):
    """Ningún hash separa filas con la misma clave. Esa partición se reúne por bloques, y
    las demás claves —y las filas con clave NULL— siguen su camino normal."""
    left = [(index, "lima") for index in range(HEAVY_KEY_ROWS)]
    left += [(500, "cusco"), (501, None), (502, "piura")]
    right = [(1000, "lima"), (1001, "lima"), (1002, "cusco"), (1003, None), (1004, "tacna")]
    directory = small_buffer.data_directory / "j"
    with ExternalHashJoin(
        directory, serializer.size, serializer.size, city_of, city_of, small_buffer, unmatched=unmatched
    ) as joiner:
        found = joined_ids(
            serializer, joiner.join(map(serializer.pack, left), map(serializer.pack, right))
        )
        assert joiner.block_joins == 1
    assert found == brute_force_pairs(left, right, unmatched)
    assert not directory.exists()


def test_the_extra_condition_also_applies_when_joining_by_blocks(
    small_buffer: EngineConfig, serializer: RecordSerializer, city_of
):
    left = [serializer.pack((index, "lima")) for index in range(HEAVY_KEY_ROWS)]
    right = [serializer.pack((index, "lima")) for index in (10, 20)]

    def same_id(first_row: bytes, second_row: bytes) -> bool:
        return bool(serializer.unpack(first_row)[0] == serializer.unpack(second_row)[0])

    with ExternalHashJoin(
        small_buffer.data_directory / "j",
        serializer.size,
        serializer.size,
        city_of,
        city_of,
        small_buffer,
        accepts=same_id,
        unmatched=Unmatched.RIGHT,
    ) as joiner:
        found = joined_ids(serializer, joiner.join(left, right))
        assert joiner.block_joins == 1
    assert found == Counter({(10, 10): 1, (20, 20): 1})


def test_abandoning_a_join_leaves_no_files_behind(
    small_buffer: EngineConfig, serializer: RecordSerializer, id_of
):
    rows = [serializer.pack((index, "lima")) for index in range(MANY_KEYS)]
    directory = small_buffer.data_directory / "j"
    with ExternalHashJoin(
        directory, serializer.size, serializer.size, id_of, id_of, small_buffer
    ) as joiner:
        assert len(list(islice(joiner.join(rows, rows), 5))) == 5
        assert any(directory.iterdir())
    assert not directory.exists()


def test_many_rows_with_a_null_key_never_match_even_when_joined_by_blocks(
    small_buffer: EngineConfig, serializer: RecordSerializer, city_of
):
    """Las claves NULL comparten hash como cualquier otro valor repetido, así que su
    partición también se resuelve por bloques; ahí tampoco pueden emparejar."""
    left = [(index, None) for index in range(HEAVY_KEY_ROWS)]
    right = [(1000, None), (1001, None)]
    with ExternalHashJoin(
        small_buffer.data_directory / "j",
        serializer.size,
        serializer.size,
        city_of,
        city_of,
        small_buffer,
        unmatched=Unmatched.BOTH,
    ) as joiner:
        found = joined_ids(
            serializer, joiner.join(map(serializer.pack, left), map(serializer.pack, right))
        )
        assert joiner.block_joins == 1
    assert found == brute_force_pairs(left, right, Unmatched.BOTH)
    assert sum(found.values()) == HEAVY_KEY_ROWS + 2
