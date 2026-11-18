"""El cliente síncrono contra el API real del gestor: parámetros, vectores, puntos, atajos,
transacciones, trazas y `Server-Timing`."""

from __future__ import annotations

import logging
import re
from dataclasses import replace

import numpy as np
import pytest
from fastapi import FastAPI

from api.logs import ACCESS_LOGGER
from minigestor_client import (
    Client,
    ConflictError,
    Point,
    ResultTruncatedError,
    request_id_scope,
)

CREATE_PLACES = (
    "CREATE TABLE lugares (id INT PRIMARY KEY, nombre VARCHAR(40), emb VECTOR(3), ubicacion POINT)"
)
INSERT_PLACE = "INSERT INTO lugares VALUES ($1, $2, $3, $4)"
SQL_STAGES = {"parse", "plan", "exec", "txn", "serialize", "total"}
GENERATED_ID = re.compile(r"[0-9a-f]{32}")
TRUNCATING_LIMIT = 2


@pytest.fixture
def places(client: Client) -> Client:
    client.execute(CREATE_PLACES)
    client.execute(
        INSERT_PLACE, [1, "Miraflores", np.array([1.0, 0.0, 0.0]), Point(-12.12, -77.03)]
    )
    client.execute(INSERT_PLACE, [2, "Barranco", [0.0, 1.0, 0.0], Point(-12.14, -77.02)])
    return client


def test_parameters_carry_vectors_and_points(places: Client):
    row = places.fetch_one("SELECT * FROM lugares WHERE id = $1", [1])
    assert row == {
        "id": 1,
        "nombre": "Miraflores",
        "emb": [1.0, 0.0, 0.0],
        "ubicacion": {"lat": -12.12, "lon": -77.03},
    }


def test_a_vector_parameter_drives_a_similarity_search(places: Client):
    places.execute("CREATE INDEX ON lugares USING HNSW (emb)")
    nearest = places.fetch_all(
        "SELECT id FROM lugares WHERE SIMILAR_TO(emb, $1, k = $2)", [np.float32([0, 0.9, 0.1]), 1]
    )
    assert nearest == [{"id": 2}]


def test_a_text_parameter_is_never_read_as_sql(places: Client):
    places.execute(INSERT_PLACE, [3, "'; DROP TABLE lugares; --", None, None])
    assert (
        places.scalar("SELECT nombre FROM lugares WHERE id = $1", [3])
        == "'; DROP TABLE lugares; --"
    )
    assert places.scalar("SELECT COUNT(*) FROM lugares") == 3


def test_the_shortcuts_read_the_result(places: Client):
    assert places.fetch_all("SELECT id FROM lugares ORDER BY id") == [{"id": 1}, {"id": 2}]
    assert places.fetch_one("SELECT id FROM lugares WHERE id = $1", [99]) is None
    assert places.scalar("SELECT id FROM lugares WHERE id = $1", [99]) is None
    assert places.execute("UPDATE lugares SET nombre = $1 WHERE id > $2", ["x", 0]) == 2


def test_a_query_result_is_typed(places: Client):
    result = places.query("SELECT id, nombre FROM lugares WHERE id = $1", [2])
    assert result.columns == ["id", "nombre"]
    assert result.rows == [[2, "Barranco"]]
    assert result.total_rows == 1
    assert [statement.returned_rows for statement in result.statements] == [1]
    assert result.plan is not None
    assert result.in_transaction is False
    assert result.similarity is None


def test_explain_analyze_brings_the_measured_plan(places: Client):
    plan = places.query("EXPLAIN ANALYZE SELECT * FROM lugares WHERE id = 1").plan
    assert plan is not None
    assert plan.actual_rows is not None


def test_fetch_all_refuses_a_truncated_result(places: Client, engine_app: FastAPI):
    service = engine_app.state.service
    service.config = replace(service.config, max_result_rows=TRUNCATING_LIMIT)
    places.execute(INSERT_PLACE, [3, "Surco", None, None])
    assert places.query("SELECT * FROM lugares").truncated
    with pytest.raises(ResultTruncatedError):
        places.fetch_all("SELECT * FROM lugares")


def test_server_timing_comes_back_parsed(places: Client):
    result = places.query("SELECT * FROM lugares WHERE id = 1")
    assert set(result.server_timing) == SQL_STAGES
    assert result.server_timing["total"] >= result.server_timing["exec"]


def test_the_request_id_of_the_scope_reaches_the_engine_log(
    places: Client, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.INFO, logger=ACCESS_LOGGER)
    with request_id_scope("pedido-de-la-app-7"):
        result = places.query("SELECT * FROM lugares")
    assert result.request_id == "pedido-de-la-app-7"
    logged = [record for record in caplog.records if record.name == ACCESS_LOGGER]
    assert [record.request_id for record in logged] == ["pedido-de-la-app-7"]


def test_each_call_outside_a_scope_gets_its_own_request_id(places: Client):
    first = places.query("SELECT 1 FROM lugares").request_id
    second = places.query("SELECT 1 FROM lugares").request_id
    assert GENERATED_ID.fullmatch(first)
    assert first != second


def test_a_transaction_commits_when_the_block_ends(places: Client):
    with places.transaction() as transaction:
        transaction.execute("DELETE FROM lugares WHERE id = $1", [1])
        assert transaction.in_transaction
        assert transaction.scalar("SELECT COUNT(*) FROM lugares") == 1
    assert places.scalar("SELECT COUNT(*) FROM lugares") == 1
    assert places.close_session(transaction.session_id) is False


def test_a_transaction_rolls_back_when_the_block_raises(places: Client):
    with pytest.raises(RuntimeError), places.transaction() as transaction:
        transaction.execute("DELETE FROM lugares")
        raise RuntimeError("la app falló a mitad")
    assert places.scalar("SELECT COUNT(*) FROM lugares") == 2
    assert places.close_session(transaction.session_id) is False


def test_a_failed_statement_inside_a_transaction_keeps_it_open(places: Client):
    with places.transaction() as transaction:
        transaction.execute("DELETE FROM lugares WHERE id = 2")
        with pytest.raises(ConflictError) as failed:
            transaction.execute(INSERT_PLACE, [1, "repetido", None, None])
        assert failed.value.in_transaction is True
        assert transaction.in_transaction
    assert places.fetch_all("SELECT id FROM lugares") == [{"id": 1}]


def test_tables_lists_columns_and_indexes(places: Client):
    (table,) = places.tables()
    assert table.name == "lugares"
    assert table.primary_key == "id"
    assert table.row_count == 2
    assert [column.name for column in table.columns] == ["id", "nombre", "emb", "ubicacion"]
    assert table.columns[0].primary_key


def test_the_engine_is_ready(client: Client):
    assert client.ready() is True
