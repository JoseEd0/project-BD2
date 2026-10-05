"""Agregaciones calculadas sobre la marcha: sin guardar las filas de cada grupo."""

import math
from datetime import date
from pathlib import Path

import pytest

from config import EngineConfig
from query.aggregation import Aggregate, accumulator_for
from query.engine import Engine
from query.expressions import ExpressionError
from query.functions import AggregateKind
from sql.nodes import ColumnRef

GROUPS = 500
ROWS_PER_GROUP = 3


def aggregated(kind: AggregateKind, values: list) -> object:
    accumulator = accumulator_for(Aggregate(kind, ColumnRef("x"), kind.value))
    for value in values:
        accumulator.add(value)
    return accumulator.result()


@pytest.mark.parametrize("kind", [AggregateKind.SUM, AggregateKind.AVG, AggregateKind.MIN, AggregateKind.MAX])
def test_an_aggregate_over_nothing_is_null(kind: AggregateKind):
    assert aggregated(kind, []) is None


def test_count_over_nothing_is_zero():
    assert aggregated(AggregateKind.COUNT, []) == 0


def test_integers_are_summed_exactly():
    huge = 2**62
    total = aggregated(AggregateKind.SUM, [huge, huge, 1])
    assert total == 2**63 + 1
    assert isinstance(total, int)


def test_reals_are_summed_without_accumulating_rounding_error():
    """Sumando de uno en uno, `1e16 + 1.0` pierde el 1 y el total sale 0."""
    assert aggregated(AggregateKind.SUM, [1e16, 1.0, -1e16]) == 1.0
    tenths = [0.1] * 10
    assert aggregated(AggregateKind.SUM, tenths) == math.fsum(tenths) == 1.0
    assert aggregated(AggregateKind.AVG, tenths) == 0.1


def test_a_sum_that_overflows_is_infinite_not_undefined():
    assert aggregated(AggregateKind.SUM, [1e308, 1e308]) == math.inf


def test_mixing_integers_and_reals():
    assert aggregated(AggregateKind.SUM, [1, 2, 0.5]) == 3.5
    assert aggregated(AggregateKind.AVG, [1, 2]) == 1.5


def test_extremes_keep_the_first_among_equals():
    assert aggregated(AggregateKind.MIN, ["b", "a", "c"]) == "a"
    assert aggregated(AggregateKind.MAX, [2, 7, 7.0, 3]) == 7
    assert isinstance(aggregated(AggregateKind.MAX, [2, 7, 7.0, 3]), int)


@pytest.mark.parametrize("kind", [AggregateKind.SUM, AggregateKind.AVG])
def test_adding_up_something_that_is_not_a_number_is_rejected(kind: AggregateKind):
    for value in ("lima", True):
        with pytest.raises(ExpressionError, match="necesita valores numéricos"):
            aggregated(kind, [value])


@pytest.fixture
def small_memory(tmp_path: Path, config: EngineConfig):
    """Un buffer de una página y cuatro particiones: 500 grupos no caben de una pasada."""
    tight = EngineConfig(
        page_size=config.page_size,
        buffer_pool_pages=config.buffer_pool_pages,
        sort_buffer_pages=1,
        hash_partitions=4,
        data_directory=tmp_path,
    )
    with Engine(tight) as engine:
        engine.execute("CREATE TABLE t (id INT PRIMARY KEY, g INT, x FLOAT)")
        rows = ", ".join(
            f"({number}, {number % GROUPS}, {number / 4})"
            for number in range(GROUPS * ROWS_PER_GROUP)
        )
        engine.execute(f"INSERT INTO t VALUES {rows}")
        yield engine


def test_more_groups_than_fit_in_memory(small_memory: Engine, tmp_path: Path):
    result = small_memory.execute(
        "SELECT g, COUNT(*), SUM(x), MIN(id), MAX(id), AVG(x) FROM t GROUP BY g ORDER BY g"
    )
    expected = []
    for group in range(GROUPS):
        ids = [group + GROUPS * turn for turn in range(ROWS_PER_GROUP)]
        values = [number / 4 for number in ids]
        expected.append(
            (group, ROWS_PER_GROUP, math.fsum(values), ids[0], ids[-1], math.fsum(values) / 3)
        )
    assert list(result.rows) == expected
    assert not list(tmp_path.glob("group-*"))


def test_aggregating_a_whole_table_without_grouping(small_memory: Engine):
    total = GROUPS * ROWS_PER_GROUP
    result = small_memory.execute("SELECT COUNT(*), COUNT(x), SUM(id), MIN(x), MAX(x) FROM t")
    assert result.rows == ((total, total, total * (total - 1) // 2, 0.0, (total - 1) / 4),)


def test_aggregates_over_an_empty_table(engine: Engine):
    engine.execute("CREATE TABLE t (id INT PRIMARY KEY, x FLOAT)")
    assert engine.execute("SELECT COUNT(*), COUNT(x), SUM(x), AVG(x), MIN(x), MAX(x) FROM t").rows == (
        (0, 0, None, None, None, None),
    )
    assert engine.execute("SELECT x, COUNT(*) FROM t GROUP BY x").rows == ()


def test_nulls_are_counted_as_rows_but_not_as_values(engine: Engine):
    engine.execute("CREATE TABLE t (id INT PRIMARY KEY, x FLOAT)")
    engine.execute("INSERT INTO t VALUES (1, NULL), (2, 4.0), (3, NULL), (4, 2.0)")
    assert engine.execute("SELECT COUNT(*), COUNT(x), SUM(x), AVG(x), MIN(x), MAX(x) FROM t").rows == (
        (4, 2, 6.0, 3.0, 2.0, 4.0),
    )


@pytest.mark.parametrize(
    ("aggregate", "message"),
    [
        ("SUM(s)", "SUM\\(s\\) necesita valores numéricos y su argumento es STRING"),
        ("AVG(ok)", "necesita valores numéricos y su argumento es BOOL"),
        ("SUM(f)", "su argumento es DATE"),
        ("SUM(x > 1)", "su argumento es BOOL"),
        ("MIN(p)", "necesita valores con orden y un POINT no lo tiene"),
        ("MAX(p)", "un POINT no lo tiene"),
    ],
)
def test_an_aggregate_over_the_wrong_type_is_rejected_on_an_empty_table(
    engine: Engine, aggregate: str, message: str
):
    """Sin filas, sumar textos devolvería NULL sin avisar: el tipo se comprueba antes."""
    engine.execute("CREATE TABLE t (id INT PRIMARY KEY, x FLOAT, s VARCHAR(4), ok BOOL, f DATE, p POINT)")
    for query in (f"SELECT {aggregate} FROM t", f"SELECT id FROM t GROUP BY id HAVING {aggregate} IS NULL"):
        with pytest.raises(ExpressionError, match=message):
            engine.execute(query)


def test_extremes_work_on_every_ordered_type(engine: Engine):
    engine.execute("CREATE TABLE t (id INT PRIMARY KEY, x FLOAT, s VARCHAR(4), ok BOOL, f DATE)")
    engine.execute(
        "INSERT INTO t VALUES (1, 2.5, 'b', TRUE, '2026-02-01'), (2, -1.0, 'a', FALSE, '2025-12-31'), "
        "(3, NULL, NULL, NULL, NULL)"
    )
    result = engine.execute("SELECT MIN(x), MAX(s), MAX(ok), MIN(f), COUNT(f), SUM(id + x) FROM t")
    assert result.rows == ((-1.0, "b", True, date(2025, 12, 31), 2, 4.5),)
