"""Agrupación, agregaciones y nombres de columna: lo que el SELECT puede calcular."""

import pytest

from query.engine import Engine
from query.expressions import ExpressionError, UnknownColumnError

ROWS = "(1, 1, 2.5, 'a'), (2, 2, 3.5, 'a'), (3, 3, 1.0, 'b'), (4, 4, NULL, NULL)"


@pytest.fixture
def medidas(engine: Engine) -> Engine:
    engine.execute("CREATE TABLE t (id INT PRIMARY KEY, n INT, x FLOAT, s VARCHAR(4))")
    engine.execute(f"INSERT INTO t VALUES {ROWS}")
    return engine


def test_a_computed_column_is_named_as_it_is_written(medidas: Engine):
    result = medidas.execute(
        "SELECT x * 2, (n + 1) * 2, n - (id - 1), -n, n BETWEEN 1 AND 2, s IS NULL, x > 2 OR n = 4 "
        "FROM t WHERE id = 1"
    )
    assert result.columns == (
        "x * 2",
        "(n + 1) * 2",
        "n - (id - 1)",
        "-n",
        "n BETWEEN 1 AND 2",
        "s IS NULL",
        "x > 2 OR n = 4",
    )
    assert result.rows == ((5.0, 4, 1, -1, True, False, True),)


def test_grouping_by_an_expression(medidas: Engine):
    result = medidas.execute("SELECT n % 2, COUNT(*), SUM(x) FROM t GROUP BY n % 2 ORDER BY n % 2")
    assert result.columns == ("n % 2", "COUNT(*)", "SUM(x)")
    assert result.rows == ((0, 2, 3.5), (1, 2, 3.5))


def test_grouping_by_an_alias_of_the_select(medidas: Engine):
    result = medidas.execute("SELECT x > 2 AS alto, COUNT(*) AS filas FROM t GROUP BY alto ORDER BY alto")
    assert result.columns == ("alto", "filas")
    assert result.rows == ((None, 1), (False, 1), (True, 2))


def test_the_select_can_compute_on_top_of_a_grouped_expression(medidas: Engine):
    result = medidas.execute(
        "SELECT (n % 2) * 10 + 1 AS codigo, MAX(x) FROM t GROUP BY n % 2 ORDER BY codigo"
    )
    assert result.rows == ((1, 3.5), (11, 2.5))


def test_a_grouped_expression_can_be_sorted_on_disk(medidas: Engine):
    """La clave calculada pasa por el ordenamiento externo, así que necesita un tipo de
    columna: entero para `%`, real para `/`, booleano para una comparación."""
    for key, expected in (
        ("n % 3", [0, 1, 2]),
        ("n / 2", [0.5, 1.0, 1.5, 2.0]),
        ("n > 2", [False, True]),
        ("-n", [-4, -3, -2, -1]),
    ):
        rows = medidas.execute(f"SELECT {key} FROM t GROUP BY {key} ORDER BY {key}").rows
        assert [row[0] for row in rows] == expected, key


def test_aggregates_can_be_combined_in_one_expression(medidas: Engine):
    result = medidas.execute(
        "SELECT s, SUM(x) / COUNT(*) AS media, MAX(n) - MIN(n) FROM t GROUP BY s ORDER BY s"
    )
    assert result.columns == ("s", "media", "MAX(n) - MIN(n)")
    assert result.rows == ((None, None, 0), ("a", 3.0, 1), ("b", 1.0, 0))
    assert medidas.execute("SELECT COUNT(*) + 1 FROM t").rows == ((5,),)


def test_two_aggregates_over_different_expressions_do_not_mix(medidas: Engine):
    result = medidas.execute("SELECT SUM(n * 2), SUM(n * 3), MIN(n + 1), MAX(s) FROM t")
    assert result.columns == ("SUM(n * 2)", "SUM(n * 3)", "MIN(n + 1)", "MAX(s)")
    assert result.rows == ((20, 30, 2, "b"),)


@pytest.mark.parametrize(
    ("having", "groups"),
    [
        ("COUNT(*) IN (2, 5)", ["a"]),
        ("SUM(x) IS NOT NULL", ["a", "b"]),
        ("SUM(x) IS NULL", [None]),
        ("COUNT(*) BETWEEN 1 AND 1", [None, "b"]),
        ("NOT (COUNT(*) = 1)", ["a"]),
        ("MAX(n) - MIN(n) > 0 AND s LIKE 'a%'", ["a"]),
    ],
)
def test_having_takes_aggregates_inside_any_predicate(medidas: Engine, having: str, groups: list):
    rows = medidas.execute(f"SELECT s FROM t GROUP BY s HAVING {having} ORDER BY s").rows
    assert [row[0] for row in rows] == groups


def test_order_by_takes_an_aggregate_that_is_not_selected(medidas: Engine):
    rows = medidas.execute("SELECT s FROM t GROUP BY s ORDER BY SUM(n) DESC, s").rows
    assert [row[0] for row in rows] == [None, "a", "b"]
    rows = medidas.execute("SELECT s, COUNT(*) FROM t GROUP BY s ORDER BY COUNT(*) DESC, s").rows
    assert rows[0] == ("a", 2)


def test_a_number_in_order_by_or_group_by_is_a_position_of_the_select(medidas: Engine):
    by_count = medidas.execute("SELECT s, COUNT(*) FROM t GROUP BY 1 ORDER BY 2 DESC, 1").rows
    assert by_count == (("a", 2), (None, 1), ("b", 1))
    by_value = medidas.execute("SELECT id, x FROM t ORDER BY 2 DESC").rows
    assert [row[0] for row in by_value] == [2, 1, 3, 4]
    with pytest.raises(ExpressionError, match="la posición 3 no existe"):
        medidas.execute("SELECT id, x FROM t ORDER BY 3")
    with pytest.raises(ExpressionError, match="no puede referirse a"):
        medidas.execute("SELECT * FROM t ORDER BY 1")


def test_a_column_outside_the_group_by_is_rejected(medidas: Engine):
    with pytest.raises(UnknownColumnError):
        medidas.execute("SELECT n % 2, x FROM t GROUP BY n % 2")
    with pytest.raises(UnknownColumnError):
        medidas.execute("SELECT s FROM t GROUP BY s ORDER BY x")


def test_grouping_by_something_that_cannot_be_stored_is_rejected(medidas: Engine):
    with pytest.raises(ExpressionError, match="no se puede guardar en una columna"):
        medidas.execute("SELECT COUNT(*) FROM t GROUP BY POLYGON((0, 0), (0, 1), (1, 1))")


def test_aggregates_can_feed_tuples_and_functions(medidas: Engine):
    """Una agregación dentro de una tupla o de una llamada se calcula igual una sola vez, y
    lo de fuera lee la columna que produjo."""
    result = medidas.execute("SELECT (MIN(n), MAX(x)), POINT(MIN(n), MAX(x)) FROM t")
    assert result.columns == ("(MIN(n), MAX(x))", "point")
    assert result.rows == (((1.0, 3.5), (1.0, 3.5)),)
    by_group = medidas.execute(
        "SELECT s, distancia(POINT(MIN(n), MAX(x)), POINT(0, 0), metrica=euclidiana) AS d "
        "FROM t GROUP BY s ORDER BY s"
    )
    assert [row[0] for row in by_group.rows] == [None, "a", "b"]
    assert by_group.rows[0][1] is None
    assert by_group.rows[2][1] == pytest.approx((3**2 + 1.0**2) ** 0.5)


def test_grouping_by_constants_and_by_function_results(medidas: Engine):
    result = medidas.execute("SELECT 1.5, TRUE, 'x', COUNT(*) FROM t GROUP BY 1.5, TRUE, 'x'")
    assert result.columns == ("1.5", "TRUE", "'x'", "COUNT(*)")
    assert result.rows == ((1.5, True, "x", 4),)
    near = medidas.execute(
        "SELECT distancia(POINT(n, x), POINT(1, 2.5), metrica=euclidiana) = 0, COUNT(*) FROM t "
        "GROUP BY distancia(POINT(n, x), POINT(1, 2.5), metrica=euclidiana) = 0 ORDER BY 2, 1"
    )
    assert near.rows == ((None, 1), (True, 1), (False, 2))
    by_distance = medidas.execute(
        "SELECT COUNT(*) FROM t GROUP BY distancia(POINT(n, x), POINT(1, 2.5), metrica=euclidiana)"
    )
    assert sorted(by_distance.rows) == [(1,), (1,), (1,), (1,)]


def test_a_tuple_cannot_be_a_grouping_key(medidas: Engine):
    with pytest.raises(ExpressionError, match="no se puede guardar en una columna"):
        medidas.execute("SELECT COUNT(*) FROM t GROUP BY (n, x)")
