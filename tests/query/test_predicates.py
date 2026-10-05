"""Lo que el SELECT y el WHERE saben evaluar: LIKE, IN, NULL, funciones y sus errores."""

import math

import pytest

from query.engine import Engine
from query.expressions import AmbiguousColumnError, ExpressionError, UnknownColumnError
from query.operators import UnsupportedQueryError
from spatial.geometry import Point

ROWS = (
    "(1, 1, 2.5, 'ana', POINT(-12.0, -77.0), TRUE, '2026-01-01'), "
    "(2, 2, 3.5, 'luis', POINT(-12.5, -77.5), FALSE, '2026-02-01'), "
    "(3, NULL, NULL, NULL, NULL, NULL, NULL)"
)
SQUARE = "POLYGON((-13, -78), (-13, -76), (-11, -76), (-11, -78))"


@pytest.fixture
def datos(engine: Engine) -> Engine:
    engine.execute(
        "CREATE TABLE t (id INT PRIMARY KEY, n INT, x FLOAT, s VARCHAR(8), p POINT, "
        "ok BOOL, f DATE)"
    )
    engine.execute(f"INSERT INTO t VALUES {ROWS}")
    engine.execute("CREATE TABLE u (id INT PRIMARY KEY, t_id INT, v VARCHAR(4))")
    engine.execute("INSERT INTO u VALUES (10, 1, 'a'), (11, 1, 'b'), (12, 9, 'c')")
    return engine


def ids(engine: Engine, condition: str) -> list[int]:
    return [row[0] for row in engine.execute(f"SELECT id FROM t WHERE {condition} ORDER BY id").rows]


@pytest.mark.parametrize(
    ("pattern", "found"),
    [
        ("'_na'", [1]),
        ("'l_i%'", [2]),
        ("'%'", [1, 2]),
        ("'____'", [2]),
        ("'a.a'", []),
        ("'%A%'", []),
        ("NULL", []),
    ],
)
def test_like_matches_one_character_or_any_run(datos: Engine, pattern: str, found: list[int]):
    """`_` es exactamente un carácter y `%` cualquier secuencia; el punto no es comodín."""
    assert ids(datos, f"s LIKE {pattern}") == found


def test_not_like_leaves_nulls_out(datos: Engine):
    assert ids(datos, "s NOT LIKE '%a%'") == [2]
    assert datos.execute("SELECT s LIKE 'a%' FROM t ORDER BY id").rows == (
        (True,),
        (False,),
        (None,),
    )


def test_like_only_applies_to_text(datos: Engine):
    with pytest.raises(ExpressionError, match="LIKE solo se aplica a texto"):
        datos.execute("SELECT id FROM t WHERE n LIKE '1%'")


def test_in_with_a_null_never_says_not_in(datos: Engine):
    """`n NOT IN (1, NULL)` es desconocido para la fila cuyo n es NULL, no verdadero."""
    assert ids(datos, "n IN (1, NULL)") == [1]
    assert ids(datos, "n NOT IN (1, NULL)") == [2]
    assert ids(datos, "x BETWEEN NULL AND 3") == []
    assert ids(datos, "NOT n = 1") == [2]


def test_operators_propagate_null(datos: Engine):
    result = datos.execute("SELECT -n, NOT ok, -x, n + x, s IN ('ana', 'x') FROM t ORDER BY id")
    assert result.columns == ("-n", "NOT ok", "-x", "n + x", "s IN ('ana', 'x')")
    assert result.rows == (
        (-1, False, -2.5, 3.5, True),
        (-2, True, -3.5, 5.5, False),
        (None, None, None, None, None),
    )


def test_literals_are_named_as_sql_writes_them(datos: Engine):
    result = datos.execute("SELECT 1.5, TRUE, 'x', NULL, -3 FROM t WHERE id = 1")
    assert result.columns == ("1.5", "TRUE", "'x'", "NULL", "-3")
    assert result.rows == ((1.5, True, "x", None, -3),)


def test_arithmetic_needs_numbers(datos: Engine):
    with pytest.raises(ExpressionError, match="se esperaba un número y llegó 'ana'"):
        datos.execute("SELECT s + 1 FROM t")


def test_a_tuple_of_coordinates_with_a_null_is_null(datos: Engine):
    result = datos.execute(
        "SELECT (n, x), distancia((n, x), POINT(1, 2.5), metrica='euclidiana') FROM t ORDER BY id"
    )
    assert result.rows[0] == ((1.0, 2.5), 0.0)
    assert result.rows[2] == (None, None)
    assert ids(datos, "(n, x) = (1, 2.5)") == [1]


def test_a_point_built_from_a_null_is_null(datos: Engine):
    result = datos.execute("SELECT POINT(n, x), POINT(x, NULL) FROM t ORDER BY id")
    assert result.rows == ((Point(1.0, 2.5), None), (Point(2.0, 3.5), None), (None, None))
    datos.execute("INSERT INTO t VALUES (20, 1, 1.0, 'x', POINT(NULL, 2), NULL, NULL)")
    assert datos.execute("SELECT p FROM t WHERE id = 20").rows == ((None,),)


def test_spatial_functions_answer_null_for_a_row_without_location(datos: Engine):
    result = datos.execute(
        f"SELECT intersecta(p, {SQUARE}), distancia(p, POINT(-12.0, -77.0)) FROM t ORDER BY id"
    )
    assert [row[0] for row in result.rows] == [True, True, None]
    assert result.rows[0][1] == 0.0
    assert result.rows[2][1] is None


@pytest.mark.parametrize(
    ("expression", "message"),
    [
        ("distancia(p, POINT(-12.0, -77.0), metrica=5)", "la métrica se indica por su nombre"),
        ("distancia(p, POINT(-12.0, -77.0), metrica='manhattan')", "métrica desconocida"),
        ("distancia(p, POINT('a', -77.0))", "se esperaba un número y llegó 'a'"),
        ("distancia(p, POINT(1e999, 0))", "fuera de"),
        (f"distancia(p, POINT(1{'0' * 400}, 0))", "no cabe"),
        ("distancia(p)", "necesita al menos 2 argumento"),
        ("distancia(p, p, p)", "admite como mucho 2 argumento"),
        ("distancia(p, p, radio=3)", "no tiene el argumento 'radio'"),
        ("intersecta(p, POINT(1, 2))", "debe ser un POLYGON"),
        ("intersecta(p, 5)", "debe ser un POLYGON"),
        ("distancia(p, 5)", "se esperaba un punto y llegó 5"),
        ("distancia(p, 'lima')", "se esperaba un punto"),
        ("POLYGON(p, (0, 0), 'x')", "se esperaba un punto"),
        ("POINT(id, 'x')", "se esperaba un número"),
        ("intersecta(p, p)", "el argumento 2 no puede ser la columna 'p', que es POINT"),
        ("intersecta(s, POLYGON((0, 0), (0, 1), (1, 1)))", "la columna 's', que es STRING"),
        ("distancia(s, POINT(1, 2))", "el argumento 1 no puede ser la columna 's'"),
        ("distancia(POINT(1, 2), id)", "el argumento 2 no puede ser la columna 'id', que es INT"),
        ("POINT(s, 1)", "el argumento 1 no puede ser la columna 's', que es STRING"),
        ("POLYGON(p, p, id)", "el argumento 3 no puede ser la columna 'id'"),
        ("nope(id)", "la función 'nope' no existe"),
    ],
)
def test_a_wrong_function_call_is_rejected_before_reading_any_row(
    engine: Engine, expression: str, message: str
):
    """La tabla está vacía: el error no puede depender de evaluar ninguna fila."""
    engine.execute("CREATE TABLE t (id INT PRIMARY KEY, p POINT, s VARCHAR(4))")
    with pytest.raises(ExpressionError, match=message):
        engine.execute(f"SELECT {expression} FROM t")


def test_functions_answer_null_when_an_argument_is_null(datos: Engine):
    result = datos.execute(
        "SELECT distancia(p, NULL), intersecta(p, NULL), intersecta(NULL, POLYGON(p, p, NULL)), "
        "POINT(NULL, NULL) FROM t WHERE id = 1"
    )
    assert result.rows == ((None, None, None, None),)


def test_columns_of_the_right_type_are_accepted_as_arguments(datos: Engine):
    result = datos.execute(
        "SELECT distancia(p, p), POINT(n, x), distancia(POINT(n, id), POINT(x, 2), "
        "metrica=euclidiana) FROM t WHERE id = 1"
    )
    assert result.rows == ((0.0, Point(1.0, 2.5), pytest.approx(math.hypot(1.5, 1.0))),)
    triangle = datos.execute("SELECT intersecta(p, POLYGON(p, POINT(n, x), (0, 0))) FROM t WHERE id = 1")
    assert triangle.rows == ((True,),)


def test_a_qualified_star_takes_the_columns_of_one_table(datos: Engine):
    result = datos.execute("SELECT u.*, t.s FROM t JOIN u ON t.id = u.t_id ORDER BY u.id")
    assert result.columns == ("id", "t_id", "v", "s")
    assert result.rows == ((10, 1, "a", "ana"), (11, 1, "b", "ana"))
    both = datos.execute("SELECT t.*, u.* FROM t JOIN u ON t.id = u.t_id")
    assert len(both.columns) == 10
    with pytest.raises(UnknownColumnError, match=r"'z\.\*' no encaja con ninguna tabla"):
        datos.execute("SELECT z.* FROM t")


def test_a_column_of_two_joined_tables_must_be_qualified(datos: Engine):
    with pytest.raises(AmbiguousColumnError, match="la columna 'id' es ambigua"):
        datos.execute("SELECT id FROM t JOIN u ON t.id = u.t_id")


@pytest.mark.parametrize(
    ("query", "error", "message"),
    [
        ("SELECT *, COUNT(*) FROM t", UnsupportedQueryError, "no se puede combinar"),
        ("SELECT s FROM t HAVING n > 1", UnsupportedQueryError, "HAVING necesita GROUP BY"),
        ("SELECT SUM() FROM t", UnsupportedQueryError, "SUM necesita un argumento"),
        ("SELECT id FROM t ORDER BY nada", UnknownColumnError, "la columna 'nada' no existe"),
        ("SELECT nada FROM t", UnknownColumnError, "la columna 'nada' no existe"),
        ("SELECT t.nada FROM t", UnknownColumnError, "nada"),
        ("SELECT z.id FROM t", UnknownColumnError, "id"),
        ("SELECT id FROM t WHERE t.nada > 5", UnknownColumnError, "nada"),
        ("SELECT id FROM t WHERE t.nada = 5", UnknownColumnError, "nada"),
        ("SELECT id FROM t WHERE nada BETWEEN 1 AND 5", UnknownColumnError, "nada"),
    ],
)
def test_a_query_that_makes_no_sense_is_rejected(
    datos: Engine, query: str, error: type[Exception], message: str
):
    with pytest.raises(error, match=message):
        datos.execute(query)


def test_descending_order_puts_nulls_last(datos: Engine):
    assert [row[0] for row in datos.execute("SELECT id FROM t ORDER BY n DESC").rows] == [2, 1, 3]
    assert [row[0] for row in datos.execute("SELECT id FROM t ORDER BY n").rows] == [3, 1, 2]
    mixed = datos.execute("SELECT id FROM t ORDER BY ok DESC, id DESC").rows
    assert [row[0] for row in mixed] == [1, 2, 3]


def test_a_limit_of_zero_and_an_offset_beyond_the_end(datos: Engine):
    assert datos.execute("SELECT id FROM t LIMIT 0").rows == ()
    assert datos.execute("SELECT id FROM t ORDER BY id LIMIT 2 OFFSET 5").rows == ()
    assert datos.execute("SELECT id FROM t ORDER BY id OFFSET 2").rows == ((3,),)


def test_explain_shows_the_operators_before_they_run(datos: Engine):
    plan = datos.execute("EXPLAIN SELECT DISTINCT s FROM t ORDER BY s LIMIT 2").plan
    rendered = plan.render()
    assert [line.strip().lstrip("└─ ").split(":")[0] for line in rendered.splitlines()] == [
        "Limit",
        "Projection",
        "Distinct",
        "ExternalSort",
        "SequentialScan",
    ]
    assert "run(s)" not in rendered
    assert "filas=" not in rendered
    analyzed = datos.execute("EXPLAIN ANALYZE SELECT DISTINCT s FROM t ORDER BY s").plan.render()
    assert "run(s)" in analyzed
    assert "en memoria" in analyzed
