"""Semántica de SQL que la auditoría encontró rota: cada test fija la respuesta correcta.

La referencia es la de PostgreSQL y SQLite salvo las desviaciones que el motor documenta
(`/` siempre da un real).
"""

import pytest

from query.catalog import CatalogError
from query.engine import Engine
from query.expressions import ExpressionError
from query.operators import UnsupportedQueryError
from sql.errors import SqlSyntaxError

LONG_CHAIN = 150
PATHOLOGICAL_CHAIN = 1000


@pytest.fixture
def pares(engine: Engine) -> Engine:
    engine.execute("CREATE TABLE a (id INT PRIMARY KEY, v INT, s VARCHAR(8), dia DATE)")
    engine.execute("CREATE TABLE b (id INT PRIMARY KEY, v INT, texto VARCHAR(10))")
    engine.execute(
        "INSERT INTO a VALUES (1, 10, '1', '2024-01-05'), (2, 20, '2', '2024-02-01')"
    )
    engine.execute("INSERT INTO b VALUES (1, 1, '2024-01-05'), (2, 2, '2023-12-31')")
    return engine


def test_aggregates_of_different_tables_are_different_aggregates(pares: Engine):
    result = pares.execute("SELECT SUM(a.v), SUM(b.v), MAX(a.v), MAX(b.v) FROM a JOIN b ON a.id = b.id")
    assert result.rows == ((30, 3, 20, 2),)
    assert len(set(result.columns)) == 4


def test_aggregates_that_differ_in_a_named_argument_are_different(engine: Engine):
    engine.execute("CREATE TABLE g (id INT, lugar POINT)")
    engine.execute("INSERT INTO g VALUES (1, POINT(-12.0, -77.0)), (2, POINT(-12.1, -77.1))")
    result = engine.execute(
        "SELECT SUM(distancia(lugar, POINT(-12.0, -77.0))), "
        "SUM(distancia(lugar, POINT(-12.0, -77.0), metrica='euclidiana')) FROM g"
    )
    meters, degrees = result.rows[0]
    assert meters > 1000 > degrees


@pytest.mark.parametrize(
    ("expression", "expected"),
    [("-7 % 3", -1), ("7 % -3", 1), ("-7 % -3", -1), ("7 % 3", 1), ("-7.5 % 2", -1.5)],
)
def test_the_remainder_has_the_sign_of_the_dividend(engine: Engine, expression: str, expected):
    engine.execute("CREATE TABLE uno (id INT)")
    engine.execute("INSERT INTO uno VALUES (1)")
    assert engine.execute(f"SELECT {expression} FROM uno").rows == ((expected,),)


def test_grouping_by_a_remainder_keeps_negative_numbers_apart(engine: Engine):
    engine.execute("CREATE TABLE n (x INT)")
    engine.execute("INSERT INTO n VALUES (-4), (-1), (2), (5)")
    groups = engine.execute("SELECT x % 3, COUNT(*) FROM n GROUP BY x % 3").rows
    assert sorted(groups) == [(-1, 2), (2, 2)]


@pytest.mark.parametrize("expression", ["1e308 * 10", "-1e308 * 10", "1e308 * 10 - 1e308 * 10"])
def test_a_real_that_overflows_is_an_error(engine: Engine, expression: str):
    engine.execute("CREATE TABLE r (x FLOAT)")
    with pytest.raises(ExpressionError, match="no cabe"):
        engine.execute(f"INSERT INTO r VALUES ({expression})")
    assert engine.execute("SELECT * FROM r").rows == ()


def test_a_huge_integer_mixed_with_a_real_is_an_error(engine: Engine):
    engine.execute("CREATE TABLE r (x FLOAT)")
    with pytest.raises(ExpressionError, match="no cabe"):
        engine.execute(f"INSERT INTO r VALUES ({'9' * 400} + 1.5)")


@pytest.mark.parametrize(
    "definition",
    [
        "CREATE TABLE c (p INT, q INT, PRIMARY KEY (p, q))",
        "CREATE TABLE c (p INT PRIMARY KEY, q INT PRIMARY KEY)",
    ],
)
def test_a_composite_primary_key_is_rejected(engine: Engine, definition: str):
    with pytest.raises(CatalogError, match="varias columnas"):
        engine.execute(definition)
    assert engine.table_names() == []


def test_a_join_between_columns_that_cannot_be_compared_is_rejected(pares: Engine):
    with pytest.raises(ExpressionError, match="no se puede comparar"):
        pares.execute("SELECT * FROM a JOIN b ON a.s = b.id")


def test_a_join_between_a_date_and_iso_text_finds_its_pairs(pares: Engine):
    rows = pares.execute("SELECT a.id, b.id FROM a JOIN b ON a.dia = b.texto").rows
    assert rows == ((1, 1),)


@pytest.mark.parametrize(
    "condition", ["0 AND id = 1", "id", "NOT 5", "v + 1", "s", "id = 1 OR 3"]
)
def test_a_value_that_is_not_a_condition_is_rejected_before_reading(pares: Engine, condition: str):
    pares.execute("CREATE TABLE vacia (id INT, v INT, s VARCHAR(4))")
    for table in ("a", "vacia"):
        with pytest.raises(ExpressionError, match="condición"):
            pares.execute(f"SELECT * FROM {table} WHERE {condition}")
    with pytest.raises(ExpressionError, match="condición"):
        pares.execute(f"DELETE FROM a WHERE {condition}")
    assert pares.execute("SELECT COUNT(*) FROM a").rows == ((2,),)


def test_a_boolean_column_is_a_condition(engine: Engine):
    engine.execute("CREATE TABLE f (id INT, ok BOOL)")
    engine.execute("INSERT INTO f VALUES (1, TRUE), (2, FALSE), (3, NULL)")
    assert engine.execute("SELECT id FROM f WHERE ok").rows == ((1,),)
    assert engine.execute("SELECT id FROM f WHERE NOT ok").rows == ((2,),)


def test_a_having_that_is_not_a_condition_fails_on_the_first_group(pares: Engine):
    with pytest.raises(ExpressionError, match="se esperaba una condición"):
        pares.execute("SELECT v FROM a GROUP BY v HAVING COUNT(*)")


def test_like_matches_text_that_spans_several_lines(engine: Engine):
    engine.execute("CREATE TABLE notas (id INT, texto VARCHAR(16))")
    engine.execute("INSERT INTO notas VALUES (1, 'a\nb')")
    assert engine.execute("SELECT id FROM notas WHERE texto LIKE 'a%'").rows == ((1,),)
    assert engine.execute("SELECT id FROM notas WHERE texto LIKE 'a_b'").rows == ((1,),)


def test_a_qualified_column_reads_an_unqualified_group_key(pares: Engine):
    rows = pares.execute("SELECT a.v, COUNT(*) FROM a GROUP BY v ORDER BY v").rows
    assert rows == ((10, 1), (20, 1))


def test_a_long_chain_of_conditions_runs(pares: Engine):
    condition = " OR ".join(f"v = {value}" for value in range(LONG_CHAIN))
    assert pares.execute(f"SELECT id FROM a WHERE {condition} ORDER BY id").rows == ((1,), (2,))
    total = " + ".join("v" for _ in range(LONG_CHAIN))
    assert pares.execute(f"SELECT {total} FROM a WHERE id = 1").rows == ((10 * LONG_CHAIN,),)


@pytest.mark.parametrize("operator", [" OR ", " AND ", " + ", " * "])
def test_a_pathological_chain_is_a_syntax_error_not_a_crash(pares: Engine, operator: str):
    chain = operator.join(f"v = {value}" if operator.strip() in ("OR", "AND") else "v"
                          for value in range(PATHOLOGICAL_CHAIN))
    with pytest.raises(SqlSyntaxError, match="más de"):
        pares.execute(f"SELECT {chain} FROM a")


def test_chains_nested_in_one_another_add_up_their_depth(pares: Engine):
    """Cada cadena es corta, pero el operando izquierdo de una es la cadena anterior."""
    products = " * ".join("v" for _ in range(LONG_CHAIN))
    sums = " + ".join([products] + ["v"] * LONG_CHAIN)
    condition = " AND ".join([f"{sums} > 0"] + ["id > 0"] * LONG_CHAIN)
    with pytest.raises(SqlSyntaxError, match="más de"):
        pares.execute(f"SELECT id FROM a WHERE {condition}")


@pytest.mark.parametrize(
    "suffix", ["USING BM25", "USING HNSW WITH METRIC=cosine", "WITH METRIC=cosine", "USING RTREE"]
)
def test_a_search_method_without_a_search_is_rejected(pares: Engine, suffix: str):
    with pytest.raises(UnsupportedQueryError, match="MATCH o SIMILAR_TO"):
        pares.execute(f"SELECT * FROM a WHERE id = 1 {suffix}")


def test_index_options_that_no_method_uses_are_rejected(pares: Engine):
    with pytest.raises(CatalogError, match="no admite opciones"):
        pares.execute("CREATE INDEX i ON a USING HASH (v) WITH (m=16)")
    assert pares.table_of_index("i") is None


def test_an_output_alias_wins_in_order_by(pares: Engine):
    """Como en SQL estándar: `ORDER BY v` ordena por el alias `v` del SELECT, no por la
    columna `v` de la tabla."""
    rows = pares.execute("SELECT id, -v AS v FROM a ORDER BY v").rows
    assert rows == ((2, -20), (1, -10))


def test_a_table_column_wins_in_group_by(pares: Engine):
    """Agrupar ocurre antes de calcular el SELECT: `GROUP BY v` es la columna de la tabla."""
    rows = pares.execute("SELECT id AS v, COUNT(*) FROM a GROUP BY v, id ORDER BY 1").rows
    assert rows == ((1, 1), (2, 1))


def test_a_computed_text_column_is_named_as_sql_writes_it(engine: Engine):
    engine.execute("CREATE TABLE uno (id INT)")
    engine.execute("INSERT INTO uno VALUES (1)")
    assert engine.execute("SELECT 'it''s' FROM uno").columns == ("'it''s'",)


@pytest.mark.parametrize("column", ["lugar", "rasgos"])
def test_values_without_order_cannot_be_sorted_or_btree_indexed(engine: Engine, column: str):
    engine.execute("CREATE TABLE m (id INT, lugar POINT, rasgos VECTOR(2))")
    engine.execute("INSERT INTO m VALUES (1, POINT(-12.0, -77.0), (1.0, 2.0))")
    with pytest.raises(ExpressionError, match="no tiene orden"):
        engine.execute(f"SELECT id FROM m ORDER BY {column}")
    with pytest.raises(CatalogError):
        engine.execute(f"CREATE INDEX i ON m USING BTREE ({column})")


def test_a_point_and_a_vector_cannot_be_compared(engine: Engine):
    engine.execute("CREATE TABLE m (id INT, lugar POINT, rasgos VECTOR(2))")
    engine.execute("INSERT INTO m VALUES (1, POINT(1.0, 2.0), (1.0, 2.0))")
    with pytest.raises(ExpressionError, match="con un vector"):
        engine.execute("SELECT id FROM m WHERE lugar = rasgos")
