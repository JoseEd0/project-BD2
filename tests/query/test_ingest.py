"""Lotes de la carga masiva en el motor: filas JSON que entran todas o ninguna, y la fila
que lo impidió."""

import math

import pytest

from query.deadline import Deadline, StatementTimeoutError, statement_deadline
from query.engine import Engine
from query.ingest import LoadRowsStatement, RejectedRowError, RowFormatError, decode_row
from spatial.geometry import Point

SCHEMA = "id INT PRIMARY KEY, nombre VARCHAR(12) NOT NULL, emb VECTOR(3), lugar POINT, nota TEXT"
# Un plazo ya vencido al empezar: la primera fila lo nota.
EXPIRED_SECONDS = 1e-9


@pytest.fixture
def tabla(engine: Engine) -> Engine:
    engine.execute(f"CREATE TABLE t ({SCHEMA})")
    engine.execute("INSERT INTO t VALUES (100, 'previa', NULL, NULL, NULL)")
    return engine


def load(engine: Engine, *rows: dict[str, object]) -> int:
    return engine.run(LoadRowsStatement("t", rows)).affected_rows


def ids(engine: Engine) -> list[int]:
    return [row[0] for row in engine.execute("SELECT id FROM t ORDER BY id").rows]


def rejected(engine: Engine, *rows: dict[str, object]) -> RejectedRowError:
    with pytest.raises(RejectedRowError) as caught:
        load(engine, *rows)
    assert ids(engine) == [100], "un lote rechazado no deja ninguna fila"
    return caught.value


@pytest.mark.parametrize(
    ("line", "message"),
    [
        (b'{"id": 1', "no es JSON válido"),
        (b"[1, 2]", "es un list de JSON"),
        (b'"texto"', "es un str de JSON"),
        (b"\xff\xfe\xfa", "no es JSON válido"),
    ],
)
def test_a_line_that_is_not_a_json_object_is_not_a_row(line: bytes, message: str):
    with pytest.raises(RowFormatError, match=message):
        decode_row(line)


def test_json_values_follow_the_parameter_rules(tabla: Engine):
    row = {"id": 1, "nombre": "ana", "emb": [1, 0.5, 0], "lugar": {"lat": -12.0, "lon": -77.0}}
    assert load(tabla, row, {"id": 2, "nombre": "luis", "nota": "larga " * 50}) == 2
    stored = tabla.execute("SELECT id, nombre, emb, lugar, nota FROM t WHERE id <= 2 ORDER BY id")
    first, second = stored.rows
    assert first == (1, "ana", (1.0, 0.5, 0.0), Point(-12.0, -77.0), None)
    assert second[:4] == (2, "luis", None, None)
    assert second[4] == "larga " * 50


def test_column_names_ignore_case(tabla: Engine):
    load(tabla, {"ID": 3, "Nombre": "eva"})
    assert tabla.execute("SELECT nombre FROM t WHERE id = 3").rows == (("eva",),)


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ({"id": 1, "nombre": "ana", "color": "rojo"}, "la tabla no tiene la columna 'color'"),
        ({"id": "uno", "nombre": "ana"}, "entero"),
        ({"id": 1, "nombre": "ana", "emb": [1, 2]}, "3"),
        ({"id": 1, "nombre": "ana", "emb": [1, 2, math.inf]}, "no es un número finito"),
        ({"id": 1, "nombre": "ana", "lugar": {"lat": 1}}, "solo se admite un punto"),
        ({"id": 1}, "nombre"),
        ({"nombre": "ana"}, "id"),
        ({"id": 1, "nombre": "un nombre demasiado largo"}, "12"),
    ],
)
def test_a_row_that_does_not_fit_rejects_its_batch_and_says_which(
    tabla: Engine, row: dict[str, object], message: str
):
    error = rejected(tabla, {"id": 1, "nombre": "ana"}, {"id": 2, "nombre": "luis"}, row)
    assert error.position == 2
    assert message in str(error)


def test_a_repeated_key_rejects_the_batch_at_the_row_that_repeats_it(tabla: Engine):
    within = rejected(
        tabla, {"id": 1, "nombre": "a"}, {"id": 2, "nombre": "b"}, {"id": 1, "nombre": "c"}
    )
    assert within.position == 2
    existing = rejected(tabla, {"id": 1, "nombre": "a"}, {"id": 100, "nombre": "b"})
    assert existing.position == 1
    assert "la clave primaria 100 ya existe" in str(existing)


def test_a_repeated_unique_value_says_which_row(engine: Engine):
    engine.execute("CREATE TABLE t (id INT PRIMARY KEY, correo VARCHAR(20) UNIQUE)")
    load(engine, {"id": 1, "correo": "a@x"})
    with pytest.raises(RejectedRowError, match="UNIQUE") as caught:
        load(engine, {"id": 2, "correo": "b@x"}, {"id": 3, "correo": "a@x"})
    assert caught.value.position == 1


def test_an_empty_batch_loads_nothing(tabla: Engine):
    assert load(tabla) == 0
    assert ids(tabla) == [100]


def test_a_batch_past_its_deadline_writes_nothing(tabla: Engine):
    with statement_deadline(Deadline(EXPIRED_SECONDS)), pytest.raises(StatementTimeoutError):
        load(tabla, {"id": 1, "nombre": "ana"})
    assert ids(tabla) == [100]
