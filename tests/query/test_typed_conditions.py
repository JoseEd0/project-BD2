"""Tipos en las condiciones: una comparación responde lo mismo, o se rechaza igual, sea cual
sea la organización de la tabla y haya o no un índice sobre la columna."""

from datetime import date

import pytest

from query.engine import Engine, EngineError, QueryResult
from query.expressions import ExpressionError
from storage.types import StorageError

COLUMNS = "n INT{n}, x FLOAT{x}, s VARCHAR(8){s}, w VARCHAR(8){w}, f DATE{f}, b BOOL"
WITHOUT_INDEXES = COLUMNS.format(n="", x="", s="", w="", f="")
WITH_INDEXES = COLUMNS.format(
    n=" INDEX BTREE", x=" INDEX BTREE", s=" INDEX HASH", w=" INDEX BTREE", f=" INDEX BTREE"
)
TABLES = {
    "plana": f"CREATE TABLE plana (id INT, {WITHOUT_INDEXES})",
    "heap": f"CREATE TABLE heap (id INT PRIMARY KEY, {WITH_INDEXES})",
    "secuencial": f"CREATE TABLE secuencial (id INT PRIMARY KEY INDEX SEQ, {WITHOUT_INDEXES})",
    "agrupada": f"CREATE TABLE agrupada (id INT PRIMARY KEY INDEX BTREE, {WITHOUT_INDEXES})",
}
ROWS = (
    "(1, 5, 5.0, 'abc', 'abc', '2024-01-05', TRUE), "
    "(5, 7, 5.5, 'zzz', 'abd', '2023-12-31', FALSE), "
    "(7, -3, -3.0, '', 'b', '2024-06-30', TRUE), "
    "(9, NULL, NULL, NULL, NULL, NULL, NULL)"
)
LITERALS = ("5", "5.0", "5.5", "'abc'", "'2024-01-05'", "'no-es-fecha'", "TRUE", "NULL", "-3")
SHAPES = (
    "{c} = {v}",
    "{v} = {c}",
    "{c} < {v}",
    "{c} >= {v}",
    "{c} BETWEEN {v} AND {v}",
    "{c} IN ({v}, {v})",
    "{c} <> {v}",
)
HUGE = "1" + "0" * 320


@pytest.fixture
def typed(engine: Engine) -> Engine:
    for name, ddl in TABLES.items():
        engine.execute(ddl)
        engine.execute(f"INSERT INTO {name} VALUES {ROWS}")
    return engine


def outcome(engine: Engine, sql: str) -> tuple[str, object]:
    """Filas de la consulta o el error de dominio con que se rechaza; nada más es aceptable."""
    try:
        return "filas", tuple(sorted(engine.execute(sql).rows))
    except (ExpressionError, StorageError, EngineError) as error:
        return "error", str(error)


def ids(result: QueryResult) -> list[int]:
    return sorted(int(row[0]) for row in result.rows)


@pytest.mark.parametrize("column", ["id", "n", "x", "s", "w", "f", "b"])
def test_every_access_path_agrees_on_every_typed_condition(typed: Engine, column: str):
    for literal in LITERALS:
        for shape in SHAPES:
            condition = shape.format(c=column, v=literal)
            expected = outcome(typed, f"SELECT id FROM plana WHERE {condition}")
            for table in ("heap", "secuencial", "agrupada"):
                assert outcome(typed, f"SELECT id FROM {table} WHERE {condition}") == expected, (
                    table,
                    condition,
                )


@pytest.mark.parametrize("table", list(TABLES))
@pytest.mark.parametrize(
    "condition", ["id = 'abc'", "'abc' = id", "id < 'abc'", "s = 5", "n BETWEEN 'a' AND 'z'", "b = 1"]
)
def test_a_literal_of_another_type_is_a_clear_error(typed: Engine, table: str, condition: str):
    with pytest.raises(ExpressionError, match="no se puede comparar"):
        typed.execute(f"SELECT id FROM {table} WHERE {condition}")


def test_the_type_clash_is_caught_before_reading_any_row(engine: Engine):
    engine.execute("CREATE TABLE vacia (id INT, nombre VARCHAR(8))")
    with pytest.raises(ExpressionError, match="la columna 'id' es INT"):
        engine.execute("SELECT * FROM vacia WHERE id = 'abc'")
    with pytest.raises(ExpressionError, match="la columna 'nombre' es STRING"):
        engine.execute("SELECT * FROM vacia WHERE nombre IN ('a', 3)")


@pytest.mark.parametrize("table", list(TABLES))
def test_dates_are_written_and_compared_as_iso_text(typed: Engine, table: str):
    assert ids(typed.execute(f"SELECT id FROM {table} WHERE f >= '2024-01-05'")) == [1, 7]
    assert ids(typed.execute(f"SELECT id FROM {table} WHERE f = '2023-12-31'")) == [5]
    between = f"SELECT id FROM {table} WHERE f BETWEEN '2024-01-01' AND '2024-03-01'"
    assert ids(typed.execute(between)) == [1]
    assert typed.execute(f"SELECT f FROM {table} WHERE id = 1").rows == ((date(2024, 1, 5),),)


def test_a_date_index_is_used_with_a_text_literal(typed: Engine):
    result = typed.execute("SELECT id FROM heap WHERE f = '2024-06-30'")
    assert ids(result) == [7]
    assert result.plan is not None and "IndexLookup" in result.plan.render()


def test_text_that_is_not_a_date_is_rejected_when_stored(typed: Engine):
    with pytest.raises(StorageError, match="no es una fecha"):
        typed.execute("INSERT INTO plana VALUES (20, 1, 1.0, 'a', 'a', 'mañana', TRUE)")


@pytest.mark.parametrize("table", ["heap", "secuencial", "agrupada"])
def test_a_whole_real_finds_an_integer_key_and_a_fraction_finds_nothing(typed: Engine, table: str):
    assert ids(typed.execute(f"SELECT id FROM {table} WHERE id = 5.0")) == [5]
    assert ids(typed.execute(f"SELECT id FROM {table} WHERE id = 5.5")) == []
    assert ids(typed.execute(f"SELECT id FROM {table} WHERE id < 5.5")) == [1, 5]


def test_a_value_the_column_cannot_hold_matches_nothing(typed: Engine):
    assert ids(typed.execute("SELECT id FROM heap WHERE s = 'mucho más de ocho bytes'")) == []
    assert ids(typed.execute(f"SELECT id FROM heap WHERE n = {HUGE}")) == []
    assert ids(typed.execute(f"SELECT id FROM heap WHERE n < {HUGE}")) == [1, 5, 7]


def test_equality_with_null_matches_nothing_on_an_indexed_column(typed: Engine):
    assert ids(typed.execute("SELECT id FROM heap WHERE s = NULL")) == []
    assert ids(typed.execute("SELECT id FROM heap WHERE s IS NULL")) == [9]


def test_numbers_too_large_for_a_real_are_rejected_not_crashed(typed: Engine):
    with pytest.raises(ExpressionError, match="no cabe en un real"):
        typed.execute(f"SELECT ({HUGE}, 0) FROM plana")
    with pytest.raises(StorageError, match="no cabe en 64 bits"):
        typed.execute(f"INSERT INTO plana VALUES ({HUGE}, 1, 1.0, 'a', 'a', NULL, TRUE)")
    with pytest.raises(StorageError, match="no cabe en un real"):
        typed.execute(f"INSERT INTO plana VALUES (30, 1, {HUGE}, 'a', 'a', NULL, TRUE)")
