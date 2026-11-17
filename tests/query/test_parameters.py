"""Parámetros `$n` de punta a punta: del JSON al valor de la columna, y sus errores."""

import math

import pytest

from errors import DomainError
from query.engine import Engine
from query.expressions import ExpressionError
from query.parameters import ParameterValueError, bound_parameters
from spatial.geometry import Point
from sql.errors import SqlMissingParameterError, SqlParameterError
from sql.nodes import FunctionCall, Literal
from storage.types import InvalidValueError, ValueTooLargeError

INJECTION = "'; DROP TABLE personas; --"
LIMA = {"lat": -12.0464, "lon": -77.0428}
MIRAFLORES = {"lat": -12.1211, "lon": -77.0297}
NEARBY_METERS = 10_000


@pytest.fixture
def personas(engine: Engine) -> Engine:
    engine.execute(
        "CREATE TABLE personas (id INT PRIMARY KEY, nombre VARCHAR(40), nota FLOAT, "
        "activo BOOL, perfil VECTOR(2), casa POINT)"
    )
    return engine


def insert(engine: Engine, *row: object) -> None:
    engine.execute("INSERT INTO personas VALUES ($1, $2, $3, $4, $5, $6)", parameters=row)


def test_each_json_type_becomes_the_value_of_its_column(personas: Engine):
    insert(personas, 1, "ana", 2.5, True, [0.5, -0.25], LIMA)
    insert(personas, 2, None, None, False, None, None)

    rows = personas.execute("SELECT * FROM personas ORDER BY id").rows

    assert rows == (
        (1, "ana", 2.5, True, (0.5, -0.25), Point(LIMA["lat"], LIMA["lon"])),
        (2, None, None, False, None, None),
    )


def test_an_injection_attempt_is_only_a_text(personas: Engine):
    insert(personas, 1, INJECTION, None, None, None, None)

    found = personas.execute("SELECT id FROM personas WHERE nombre = $1", parameters=[INJECTION])

    assert found.rows == ((1,),)
    assert personas.table_names() == ["personas"]


def test_the_same_parameter_can_be_used_twice(personas: Engine):
    insert(personas, 1, "ana", 1.0, True, None, None)
    insert(personas, 2, "luis", 2.0, True, None, None)

    found = personas.execute(
        "SELECT id FROM personas WHERE nombre = $1 OR nombre > $1 ORDER BY id",
        parameters=["ana"],
    )
    after = personas.execute("SELECT id FROM personas WHERE nombre > $1", parameters=["ana"])

    assert found.rows == ((1,), (2,))
    assert after.rows == ((2,),)


def test_a_multi_row_insert_takes_parameters(personas: Engine):
    personas.execute(
        "INSERT INTO personas (id, nombre) VALUES ($1, $2), ($3, $2)",
        parameters=[1, "ana", 2],
    )

    assert personas.execute("SELECT id, nombre FROM personas ORDER BY id").rows == (
        (1, "ana"),
        (2, "ana"),
    )


def test_parameters_go_in_update_delete_and_limit(personas: Engine):
    for key in range(1, 5):
        insert(personas, key, "x", float(key), True, None, None)

    personas.execute("UPDATE personas SET nombre = $1 WHERE id = $2", parameters=["ana", 2])
    personas.execute("DELETE FROM personas WHERE nota > $1", parameters=[3.5])
    found = personas.execute(
        "SELECT id, nombre FROM personas ORDER BY id LIMIT $1 OFFSET $2", parameters=[2, 1]
    )

    assert found.rows == ((2, "ana"), (3, "x"))


def test_a_point_parameter_feeds_a_spatial_condition(personas: Engine):
    insert(personas, 1, "ana", None, None, None, LIMA)
    insert(personas, 2, "luis", None, None, None, {"lat": 0.0, "lon": 0.0})

    found = personas.execute(
        "SELECT id FROM personas WHERE distancia(casa, $1) < $2",
        parameters=[MIRAFLORES, NEARBY_METERS],
    )

    assert found.rows == ((1,),)


def test_vector_literals_go_in_insert_and_select(personas: Engine):
    personas.execute("INSERT INTO personas (id, perfil) VALUES (1, [0.5, 0.25]), (2, [1, 2])")

    by_literal = personas.execute("SELECT id FROM personas WHERE perfil = [1, 2]")
    by_parameter = personas.execute(
        "SELECT id FROM personas WHERE perfil = $1", parameters=[[0.5, 0.25]]
    )

    assert by_literal.rows == ((2,),)
    assert by_parameter.rows == ((1,),)


@pytest.mark.parametrize(
    "statement",
    ["SELECT id FROM personas ORDER BY [1, 2]", "SELECT MAX([1, 2]) FROM personas"],
)
def test_a_vector_literal_is_a_vector_like_its_column(personas: Engine, statement: str):
    with pytest.raises(ExpressionError, match="VECTOR"):
        personas.execute(statement)


def test_explain_shows_the_value_each_parameter_took(personas: Engine):
    result = personas.execute(
        "EXPLAIN SELECT * FROM personas WHERE id = $1 AND perfil = $2", parameters=[7, [1, 2]]
    )

    assert "id = 7" in str(result.plan)
    assert "[1.0, 2.0]" in str(result.plan)
    assert "$" not in str(result.plan)


@pytest.mark.parametrize(
    ("condition", "value", "message"),
    [
        ("id = $1", "7", "es INT y no se puede comparar con un texto"),
        ("nombre = $1", 5, "es STRING y no se puede comparar con un número"),
        ("perfil = $1", [1, 2, 3], "dimensión 2 .* dimensión 3"),
        ("casa = $1", {"lat": 100, "lon": 0}, "latitud 100"),
        ("activo = $1", [1, 2], "no se puede comparar"),
    ],
)
def test_a_value_of_another_type_fails_before_reading_rows(
    personas: Engine, condition: str, value: object, message: str
):
    with pytest.raises(ExpressionError, match=message):
        personas.execute(f"SELECT * FROM personas WHERE {condition}", parameters=[value])


@pytest.mark.parametrize(
    ("row", "error"),
    [
        (["uno", None, None, None, None, None], InvalidValueError),
        ([1, None, None, None, [1.0], None], InvalidValueError),
        ([1, None, None, None, [1e300, 1.0], None], ValueTooLargeError),
        ([1, None, None, None, None, {"lat": 100, "lon": 0}], ExpressionError),
    ],
)
def test_an_insert_rejects_a_value_its_column_cannot_hold(
    personas: Engine, row: list[object], error: type[DomainError]
):
    with pytest.raises(error):
        insert(personas, *row)

    assert personas.execute("SELECT * FROM personas").rows == ()


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({"lat": 1.0}, "solo se admite un punto"),
        ({"lat": 1.0, "lon": 2.0, "alt": 3.0}, "solo se admite un punto"),
        ({"lat": "1", "lon": 2.0}, "lleva '1' donde va un número"),
        ([], "sin componentes"),
        ([1.0, "2"], "donde va un número"),
        ([1.0, True], "donde va un número"),
        ([[1.0]], "donde va un número"),
        ([10**400], "demasiado grande"),
        (math.nan, "no es un número finito"),
        (math.inf, "no es un número finito"),
        ((1.0, 2.0), "tipo que el gestor no admite"),
    ],
)
def test_a_value_of_no_type_of_the_dialect_is_rejected(value: object, message: str):
    with pytest.raises(ParameterValueError, match=message):
        bound_parameters([value])


def test_each_value_is_the_expression_that_would_be_written():
    assert bound_parameters([1, 2.5, "a", True, None, [1, 2], LIMA]) == (
        Literal(1),
        Literal(2.5),
        Literal("a"),
        Literal(True),
        Literal(None),
        Literal((1.0, 2.0)),
        FunctionCall("POINT", (Literal(LIMA["lat"]), Literal(LIMA["lon"]))),
    )


def test_a_parameter_without_value_is_rejected(personas: Engine):
    with pytest.raises(SqlMissingParameterError, match="\\$2 no tiene valor"):
        personas.execute("SELECT * FROM personas WHERE id = $1 OR id = $2", parameters=[1])


def test_a_parameter_left_over_is_rejected(personas: Engine):
    with pytest.raises(SqlParameterError, match="\\$2 no aparece"):
        personas.execute("SELECT * FROM personas WHERE id = $1", parameters=[1, 2])


def test_a_skipped_parameter_number_is_rejected(personas: Engine):
    with pytest.raises(SqlParameterError, match="\\$2 no aparece"):
        personas.execute("SELECT * FROM personas WHERE id = $1 OR id = $3", parameters=[1, 2, 3])
