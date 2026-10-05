"""`SELECT DISTINCT`: la primera aparición de cada resultado, en su orden, quepan o no las
filas distintas en memoria."""

from pathlib import Path

import pytest

from config import EngineConfig
from query.engine import Engine

DISTINCT_VALUES = 700
COPIES = 3


def column(result) -> list:
    return [row[0] for row in result.rows]


def operations(plan) -> list[str]:
    found = [plan.operation]
    for child in plan.children:
        found.extend(operations(child))
    return found


def node(plan, operation: str):
    if plan.operation == operation:
        return plan
    for child in plan.children:
        found = node(child, operation)
        if found is not None:
            return found
    return None


def test_distinct_keeps_the_first_appearance_in_order(alumnos: Engine):
    assert column(alumnos.execute("SELECT DISTINCT ciudad FROM alumnos ORDER BY id")) == [
        "lima",
        "cusco",
        "piura",
    ]
    assert column(alumnos.execute("SELECT DISTINCT ciudad FROM alumnos ORDER BY id DESC")) == [
        "piura",
        "cusco",
        "lima",
    ]


def test_distinct_compares_what_the_select_computes(alumnos: Engine):
    assert column(alumnos.execute("SELECT DISTINCT id % 4 FROM alumnos ORDER BY id")) == [0, 1, 2, 3]
    pairs = alumnos.execute("SELECT DISTINCT ciudad, id % 2 FROM alumnos ORDER BY ciudad, id % 2").rows
    assert len(pairs) == 6
    assert len(alumnos.execute("SELECT DISTINCT * FROM alumnos").rows) == 30


def test_distinct_treats_nulls_and_equal_numbers_as_repeated(engine: Engine):
    engine.execute("CREATE TABLE t (id INT PRIMARY KEY, n INT, x FLOAT)")
    engine.execute("INSERT INTO t VALUES (1, NULL, 1.0), (2, NULL, 2.0), (3, 1, 1.0), (4, 2, NULL)")
    assert column(engine.execute("SELECT DISTINCT n FROM t ORDER BY id")) == [None, 1, 2]
    assert column(engine.execute("SELECT DISTINCT x FROM t ORDER BY id")) == [1.0, 2.0, None]
    assert column(engine.execute("SELECT DISTINCT n = x FROM t ORDER BY id")) == [None, True]


def test_distinct_with_limit_and_offset(alumnos: Engine):
    assert column(alumnos.execute("SELECT DISTINCT ciudad FROM alumnos ORDER BY id LIMIT 2")) == [
        "lima",
        "cusco",
    ]
    assert column(
        alumnos.execute("SELECT DISTINCT ciudad FROM alumnos ORDER BY id LIMIT 5 OFFSET 2")
    ) == ["piura"]


def test_distinct_over_a_join_and_over_groups(alumnos: Engine):
    alumnos.execute("CREATE TABLE sedes (ciudad VARCHAR(12) PRIMARY KEY, region VARCHAR(8))")
    alumnos.execute("INSERT INTO sedes VALUES ('lima', 'costa'), ('piura', 'costa'), ('cusco', 'sierra')")
    joined = alumnos.execute(
        "SELECT DISTINCT s.region FROM alumnos AS a JOIN sedes AS s ON a.ciudad = s.ciudad "
        "ORDER BY s.region"
    )
    assert column(joined) == ["costa", "sierra"]
    grouped = alumnos.execute("SELECT DISTINCT COUNT(*) FROM alumnos GROUP BY ciudad")
    assert grouped.rows == ((10,),)


def test_distinct_stops_reading_once_the_limit_is_served(alumnos: Engine):
    result = alumnos.execute("SELECT DISTINCT ciudad FROM alumnos LIMIT 2")
    assert len(result.rows) == 2
    scan = node(result.plan, "SequentialScan")
    assert scan.actual_rows < 30
    assert node(result.plan, "Distinct").detail == "en memoria"


def test_the_plan_puts_distinct_under_the_projection(alumnos: Engine):
    plan = alumnos.execute("SELECT DISTINCT ciudad FROM alumnos ORDER BY id LIMIT 2").plan
    assert operations(plan) == ["Limit", "Projection", "Distinct", "ExternalSort", "SequentialScan"]


@pytest.fixture
def repeated(tmp_path: Path, config: EngineConfig):
    """700 valores distintos, cada uno tres veces: no caben en un buffer de una página."""
    tight = EngineConfig(
        page_size=config.page_size,
        buffer_pool_pages=config.buffer_pool_pages,
        sort_buffer_pages=1,
        hash_partitions=4,
        data_directory=tmp_path,
    )
    with Engine(tight) as engine:
        engine.execute("CREATE TABLE t (id INT PRIMARY KEY INDEX BTREE, v INT, s VARCHAR(8))")
        rows = ", ".join(
            f"({number}, {number % DISTINCT_VALUES}, 's{number % 7}')"
            for number in range(DISTINCT_VALUES * COPIES)
        )
        engine.execute(f"INSERT INTO t VALUES {rows}")
        yield engine


def test_more_distinct_rows_than_fit_in_memory(repeated: Engine, tmp_path: Path):
    """Los primeros valores se entregan desde memoria. Sus copias llegan después, cuando el
    operador ya trabaja en disco, y no pueden volver a salir."""
    result = repeated.execute("SELECT DISTINCT v FROM t ORDER BY id")
    assert column(result) == list(range(DISTINCT_VALUES))
    assert node(result.plan, "Distinct").detail == "hashing y ordenamiento externos"
    assert not list(tmp_path.glob("distinct-*"))


def test_distinct_on_disk_keeps_the_order_it_was_asked_for(repeated: Engine):
    descending = repeated.execute("SELECT DISTINCT v FROM t ORDER BY id DESC")
    assert column(descending) == list(range(DISTINCT_VALUES - 1, -1, -1))
    by_value = repeated.execute("SELECT DISTINCT v % 350, s FROM t ORDER BY v % 350, s").rows
    assert list(by_value) == sorted(set(by_value))
    assert len(by_value) == len({(number % 350, f"s{number % 7}") for number in range(2100)})


def test_distinct_on_disk_with_limit_and_offset(repeated: Engine, tmp_path: Path):
    result = repeated.execute("SELECT DISTINCT v FROM t ORDER BY id LIMIT 5 OFFSET 690")
    assert column(result) == [690, 691, 692, 693, 694]
    assert not list(tmp_path.glob("distinct-*"))


def test_few_distinct_rows_among_many_stay_in_memory(repeated: Engine):
    result = repeated.execute("SELECT DISTINCT s FROM t ORDER BY id")
    assert column(result) == [f"s{number}" for number in range(7)]
    assert node(result.plan, "Distinct").detail == "en memoria"
