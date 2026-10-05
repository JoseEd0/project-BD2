"""Reunión por bucles anidados en bloques: cualquier condición, con memoria acotada."""

from collections.abc import Callable

import pytest

from config import EngineConfig
from external.joining import Unmatched
from external.loop import BlockNestedLoopJoin
from storage.record import RecordSerializer

Ids = list[tuple[int | None, int | None]]


@pytest.fixture
def small_blocks(config: EngineConfig) -> EngineConfig:
    """Un bloque de una sola página: pocas filas bastan para necesitar varios."""
    return EngineConfig(
        page_size=config.page_size, sort_buffer_pages=1, data_directory=config.data_directory
    )


def by_side(pair: tuple[int | None, int | None]) -> tuple[bool, int, bool, int]:
    return (pair[0] is None, pair[0] or 0, pair[1] is None, pair[1] or 0)


def always(_left: int, _right: int) -> bool:
    return True


def never(_left: int, _right: int) -> bool:
    return False


def join_ids(
    config: EngineConfig,
    serializer: RecordSerializer,
    left: list[int],
    right: list[int],
    accepts: Callable[[int, int], bool],
    unmatched: Unmatched = Unmatched.NONE,
) -> Ids:
    def test(first: bytes, second: bytes) -> bool:
        return accepts(serializer.unpack(first)[0], serializer.unpack(second)[0])

    with BlockNestedLoopJoin(
        config.data_directory / "nl", serializer.size, serializer.size, test, config, unmatched
    ) as joiner:
        pairs = joiner.join(
            (serializer.pack((key, "izq")) for key in left),
            (serializer.pack((key, "der")) for key in right),
        )
        return sorted(
            (
                (
                    None if first is None else serializer.unpack(first)[0],
                    None if second is None else serializer.unpack(second)[0],
                )
                for first, second in pairs
            ),
            key=by_side,
        )


def test_empty_inputs(small_blocks: EngineConfig, serializer: RecordSerializer):
    assert join_ids(small_blocks, serializer, [], [], always) == []
    assert join_ids(small_blocks, serializer, [1, 2], [], always) == []
    assert join_ids(small_blocks, serializer, [], [1, 2], always) == []


def test_a_single_pair(small_blocks: EngineConfig, serializer: RecordSerializer):
    assert join_ids(small_blocks, serializer, [1], [2], lambda a, b: a < b) == [(1, 2)]
    assert join_ids(small_blocks, serializer, [2], [1], lambda a, b: a < b) == []


def test_every_pair_is_compared_across_several_blocks(
    small_blocks: EngineConfig, serializer: RecordSerializer
):
    left, right = list(range(150)), list(range(0, 150, 7))
    found = join_ids(small_blocks, serializer, left, right, lambda a, b: a < b)
    assert found == sorted(((a, b) for a in left for b in right if a < b), key=by_side)


@pytest.mark.parametrize(
    ("unmatched", "extra"),
    [
        (Unmatched.NONE, []),
        (Unmatched.LEFT, [(key, None) for key in range(100, 120)]),
        (Unmatched.RIGHT, [(None, 0)]),
        (Unmatched.BOTH, [*((key, None) for key in range(100, 120)), (None, 0)]),
    ],
)
def test_outer_joins_keep_the_rows_without_a_partner(
    small_blocks: EngineConfig, serializer: RecordSerializer, unmatched: Unmatched, extra: Ids
):
    """Las filas izquierdas sin pareja se conocen al acabar su bloque; las derechas, solo
    tras el último, porque cualquier bloque puede emparejarlas."""
    left, right = list(range(120)), [0, 50, 100]
    matched = [(a, b) for a in left for b in right if a < b]
    found = join_ids(small_blocks, serializer, left, right, lambda a, b: a < b, unmatched)
    assert found == sorted([*matched, *extra], key=by_side)


def test_outer_joins_with_an_empty_side_or_no_match(
    small_blocks: EngineConfig, serializer: RecordSerializer
):
    assert join_ids(small_blocks, serializer, [1, 2], [], always, Unmatched.LEFT) == [
        (1, None),
        (2, None),
    ]
    assert join_ids(small_blocks, serializer, [], [1, 2], always, Unmatched.RIGHT) == [
        (None, 1),
        (None, 2),
    ]
    assert join_ids(small_blocks, serializer, [], [1, 2], always, Unmatched.LEFT) == []
    assert join_ids(small_blocks, serializer, [1], [2], never, Unmatched.BOTH) == [
        (1, None),
        (None, 2),
    ]


def test_the_temporary_file_is_removed(small_blocks: EngineConfig, serializer: RecordSerializer):
    join_ids(small_blocks, serializer, [1, 2, 3], [1, 2, 3], lambda a, b: a != b, Unmatched.BOTH)
    assert not (small_blocks.data_directory / "nl").exists()
