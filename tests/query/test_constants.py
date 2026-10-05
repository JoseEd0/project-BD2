"""Una expresión constante vale lo mismo que un literal: `-5`, `2 + 8`, `POINT(…)`.

El planificador la busca en un índice y la validación comprueba su tipo antes de leer
ninguna fila, exactamente igual que con un literal escrito a mano.
"""

import pytest

from query.engine import Engine
from query.expressions import NOT_CONSTANT, ExpressionError, constant_value
from spatial.geometry import Point
from sql import parse_script
from sql.nodes import SelectStatement

ROWS = (
    "(-5, 1.5, POINT(-12.0, -77.0), '2026-01-01', 'a'), "
    "(10, 2.5, POINT(-12.5, -77.5), '2026-02-01', 'b'), "
    "(11, -2.5, POINT(-12.0, -77.0), NULL, NULL), "
    "(12, 0.0, NULL, '2026-03-01', 'a')"
)


def expression_of(text: str):
    statement = parse_script(f"SELECT {text} FROM t")[0]
    assert isinstance(statement, SelectStatement)
    return statement.projections[0].expression


def access_path(engine: Engine, condition: str) -> tuple[str, str]:
    plan = engine.execute(f"EXPLAIN SELECT id FROM t WHERE {condition}").plan
    while plan.children:
        plan = plan.children[0]
    return plan.operation, plan.detail


def ids(engine: Engine, condition: str) -> list[int]:
    return sorted(row[0] for row in engine.execute(f"SELECT id FROM t WHERE {condition}").rows)


@pytest.fixture
def tabla(engine: Engine) -> Engine:
    engine.execute(
        "CREATE TABLE t (id INT PRIMARY KEY, x FLOAT INDEX BTREE, p POINT INDEX RTREE, "
        "f DATE INDEX BTREE, s VARCHAR(4) INDEX HASH)"
    )
    engine.execute(f"INSERT INTO t VALUES {ROWS}")
    return engine


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("-5", -5),
        ("2 + 8", 10),
        ("-(1 + 1.5)", -2.5),
        ("'a'", "a"),
        ("NULL", None),
        ("1 < 2", True),
        ("POINT(-12.0, -77.0)", Point(-12.0, -77.0)),
    ],
)
def test_the_value_of_a_constant_expression(text: str, value: object):
    assert constant_value(expression_of(text)) == value


@pytest.mark.parametrize("text", ["id", "id + 1", "-id", "POINT(id, 2)", "1 / 0", "POINT(100, 2)", "*"])
def test_what_depends_on_a_row_or_cannot_be_computed_is_not_a_constant(text: str):
    assert constant_value(expression_of(text)) is NOT_CONSTANT


@pytest.mark.parametrize(
    ("condition", "operation", "detail", "found"),
    [
        ("id = -5", "IndexLookup", "t.id = -5", [-5]),
        ("-5 = id", "IndexLookup", "t.id = -5", [-5]),
        ("id = 2 + 8", "IndexLookup", "t.id = 10", [10]),
        ("id = -(-10)", "IndexLookup", "t.id = 10", [10]),
        ("x = -2.5", "IndexLookup", "t.x = -2.5", [11]),
        ("s = 'a'", "IndexLookup", "t.s = 'a'", [-5, 12]),
        ("f = '2026-01-01'", "IndexLookup", "t.f = '2026-01-01'", [-5]),
        ("id < -1", "IndexRange", "t.id <= -1", [-5]),
        ("-1 > id", "IndexRange", "t.id <= -1", [-5]),
        ("11 <= id", "IndexRange", "t.id >= 11", [11, 12]),
        ("10 < id", "IndexRange", "t.id >= 10", [11, 12]),
        ("id BETWEEN -6 AND 5 + 5", "IndexRange", "t.id entre -6 y 10", [-5, 10]),
        ("x >= -(1 + 1.5)", "IndexRange", "t.x >= -2.5", [-5, 10, 11, 12]),
        ("f < '2026-01-15'", "IndexRange", "t.f <= '2026-01-15'", [-5]),
    ],
)
def test_a_constant_is_looked_up_in_the_index(
    tabla: Engine, condition: str, operation: str, detail: str, found: list[int]
):
    planned, shown = access_path(tabla, condition)
    assert planned == operation
    assert shown.startswith(detail)
    assert ids(tabla, condition) == found


def test_an_exact_point_is_looked_up_in_the_rtree(tabla: Engine):
    for condition in ("p = POINT(-12.0, -77.0)", "POINT(-12.0, -77.0) = p"):
        operation, detail = access_path(tabla, condition)
        assert operation == "IndexLookup"
        assert detail == "t.p = POINT(-12.0, -77.0) (índice idx_t_p, RTREE)"
        assert ids(tabla, condition) == [-5, 11]
    assert ids(tabla, "p = POINT(-12.0, -77.1)") == []


def test_the_index_answers_like_the_scan(tabla: Engine, engine: Engine):
    """Las mismas condiciones sobre una tabla sin índices devuelven las mismas filas."""
    engine.execute("CREATE TABLE u (id INT, x FLOAT, p POINT, f DATE, s VARCHAR(4))")
    engine.execute(f"INSERT INTO u VALUES {ROWS}")
    for condition in (
        "id = -5",
        "id < -1",
        "10 < id",
        "x >= -(1 + 1.5)",
        "p = POINT(-12.0, -77.0)",
        "f < '2026-01-15'",
        "id BETWEEN -6 AND 5 + 5",
        "id = NULL",
    ):
        indexed = ids(tabla, condition)
        scanned = sorted(
            row[0] for row in engine.execute(f"SELECT id FROM u WHERE {condition}").rows
        )
        assert indexed == scanned, condition


@pytest.mark.parametrize(
    ("condition", "message"),
    [
        ("s = -5", "la columna 's' es STRING y no se puede comparar con un número"),
        ("id = POINT(1, 2)", r"no se puede comparar con un punto \(POINT\(1.0, 2.0\)\)"),
        ("p = -1", "la columna 'p' es POINT y no se puede comparar con un número"),
        ("id BETWEEN 'a' AND 5", "no se puede comparar con un texto"),
        ("id = 1 / 0", "división por cero"),
        ("id > 0 AND 5 % 0 = 1", "división por cero"),
        ("p = POINT(100, 2)", "fuera de"),
    ],
)
def test_a_wrong_constant_is_rejected_before_reading_any_row(
    engine: Engine, condition: str, message: str
):
    """La tabla está vacía: si el error dependiera de evaluar filas, no saltaría."""
    engine.execute("CREATE TABLE t (id INT PRIMARY KEY, p POINT, s VARCHAR(4))")
    with pytest.raises(ExpressionError, match=message):
        engine.execute(f"SELECT id FROM t WHERE {condition}")


def test_a_constant_that_cannot_be_computed_is_rejected_anywhere_in_the_query(engine: Engine):
    engine.execute("CREATE TABLE t (id INT PRIMARY KEY, x FLOAT)")
    for query in (
        "SELECT 1 / 0 FROM t",
        "SELECT id FROM t ORDER BY 1 / 0",
        "SELECT COUNT(*) FROM t GROUP BY 1 / 0",
        "SELECT id FROM t GROUP BY id HAVING COUNT(*) > 1 / 0",
        "UPDATE t SET x = 1 / 0",
        "DELETE FROM t WHERE x = 1 / 0",
    ):
        with pytest.raises(ExpressionError, match="división por cero"):
            engine.execute(query)
