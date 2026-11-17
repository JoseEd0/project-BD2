"""Límites del servicio y su salud: tiempo por sentencia, tamaño del cuerpo, `/ready` y el
apagado ordenado."""

import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from api.service import Service
from config import EngineConfig
from query.engine import Engine

ROWS = 1_500
SLOW_SQL = "SELECT COUNT(*) FROM grande a JOIN grande b ON a.id < b.id"
FAST_SQL = "SELECT * FROM grande WHERE id = 3"
STATEMENT_TIMEOUT = 0.5
# Sin cortar, el producto de 1 500 filas tarda varios segundos: volver antes de esto prueba
# que se cortó y no que terminó.
CUT_WELL_BEFORE_FINISHING = 3.0
WAIT_STEP = 0.01
WAIT_LIMIT = 5.0


def run(client: TestClient, sql: str) -> httpx.Response:
    return client.post("/query", json={"sql": sql})


def load_big_table(client: TestClient) -> None:
    assert run(client, "CREATE TABLE grande (id INT PRIMARY KEY)").status_code == 200
    values = ", ".join(f"({value})" for value in range(ROWS))
    assert run(client, f"INSERT INTO grande VALUES {values}").status_code == 200


def in_background(action: Callable[[], httpx.Response]) -> tuple[threading.Thread, list]:
    responses: list[httpx.Response] = []
    worker = threading.Thread(target=lambda: responses.append(action()))
    worker.start()
    return worker, responses


def wait_until_running(service: Service) -> None:
    """Hasta que la consulta lenta tiene el bloqueo de su tabla: ya está ejecutándose."""
    waited = 0.0
    while not service.locks.holders_of("grande") and waited < WAIT_LIMIT:
        time.sleep(WAIT_STEP)
        waited += WAIT_STEP
    assert service.locks.holders_of("grande"), "la consulta lenta no llegó a empezar"


@pytest.fixture
def client(config: EngineConfig) -> Iterator[TestClient]:
    limited = replace(config, statement_timeout_seconds=STATEMENT_TIMEOUT)
    with TestClient(create_app(limited)) as instance:
        yield instance


def test_a_statement_past_its_timeout_is_cut_while_others_keep_answering(client: TestClient):
    load_big_table(client)
    started = time.perf_counter()
    slow, responses = in_background(lambda: run(client, SLOW_SQL))
    wait_until_running(client.app.state.service)
    fast = run(client, FAST_SQL)
    still_running = slow.is_alive()
    slow.join()
    elapsed = time.perf_counter() - started
    assert fast.status_code == 200
    assert fast.json()["rows"] == [[3]]
    assert still_running
    (cut,) = responses
    assert cut.status_code == 400
    assert cut.json()["detail"]["kind"] == "StatementTimeoutError"
    assert "statement_timeout" in cut.json()["detail"]["error"]
    assert elapsed < CUT_WELL_BEFORE_FINISHING
    assert client.app.state.service.locks.holders_of("grande") == {}


def test_a_cut_statement_inside_a_transaction_keeps_the_transaction(client: TestClient):
    load_big_table(client)
    session = {"session_id": "larga"}
    client.post("/query", json={"sql": "BEGIN", **session})
    client.post("/query", json={"sql": "DELETE FROM grande WHERE id = 0", **session})
    cut = client.post("/query", json={"sql": SLOW_SQL, **session})
    assert cut.json()["detail"]["kind"] == "StatementTimeoutError"
    assert cut.json()["detail"]["in_transaction"] is True
    client.post("/query", json={"sql": "COMMIT", **session})
    assert run(client, "SELECT COUNT(*) FROM grande").json()["rows"] == [[ROWS - 1]]


def test_a_body_that_announces_too_many_bytes_is_refused(config: EngineConfig):
    with TestClient(create_app(replace(config, max_request_bytes=64))) as client:
        response = run(client, "SELECT * FROM t WHERE nombre = '" + "x" * 100 + "'")
    assert response.status_code == 413
    assert response.json()["detail"]["kind"] == "RequestTooLargeError"
    assert "max_request_bytes" in response.json()["detail"]["error"]


def test_a_body_sent_in_chunks_is_cut_when_it_passes_the_limit(config: EngineConfig):
    chunks = [b'{"sql": "SELECT 1 FROM t WHERE x = \'', b"x" * 100, b"'\"}"]
    with TestClient(create_app(replace(config, max_request_bytes=64))) as client:
        response = client.post(
            "/query", content=iter(chunks), headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 413
        assert response.json()["detail"]["kind"] == "RequestTooLargeError"
        assert run(client, "CREATE TABLE t (id INT)").status_code == 200


def test_ready_answers_while_the_catalog_is_open(client: TestClient):
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_closing_cancels_running_statements_and_saves_committed_data(
    config: EngineConfig, client: TestClient
):
    load_big_table(client)
    service: Service = client.app.state.service
    unlimited = replace(service.config, statement_timeout_seconds=60.0)
    service.config = unlimited
    slow, responses = in_background(lambda: run(client, SLOW_SQL))
    wait_until_running(service)
    started = time.perf_counter()
    service.close()
    slow.join()
    assert time.perf_counter() - started < CUT_WELL_BEFORE_FINISHING
    (cancelled,) = responses
    assert cancelled.status_code == 503
    assert cancelled.json()["detail"]["kind"] == "StatementCancelledError"
    assert client.get("/ready").status_code == 503
    refused = run(client, FAST_SQL)
    assert refused.status_code == 503
    assert refused.json()["detail"]["kind"] == "ServiceStoppingError"
    with Engine(config) as reopened:
        assert reopened.execute("SELECT COUNT(*) FROM grande").rows == ((ROWS,),)
