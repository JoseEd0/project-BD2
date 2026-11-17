"""`X-Request-ID`, `Server-Timing` y la línea JSON de log de cada petición."""

import json
import logging
import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from api.auth import Role, ServiceKey, digest_of
from api.logs import ACCESS_LOGGER, JsonFormatter
from api.main import create_app
from api.tracing import REQUEST_ID_HEADER, RequestTrace, request_id_from, tracing
from config import EngineConfig

KEY = "clave-del-servicio-de-ventas-0123456789"
SERVICE = "ventas"
GENERATED_ID = re.compile(r"[0-9a-f]{32}")


@pytest.fixture
def client(config: EngineConfig) -> Iterator[TestClient]:
    keys = (ServiceKey(SERVICE, Role.ADMIN, digest_of(KEY)),)
    with TestClient(create_app(config, service_keys=keys)) as instance:
        instance.headers["Authorization"] = f"Bearer {KEY}"
        yield instance


def test_the_request_id_received_is_the_one_returned(client: TestClient):
    response = client.get("/tables", headers={REQUEST_ID_HEADER: "pedido-42.a:b_c"})
    assert response.headers[REQUEST_ID_HEADER] == "pedido-42.a:b_c"


def test_a_request_id_is_generated_when_missing(client: TestClient):
    first = client.get("/tables").headers[REQUEST_ID_HEADER]
    second = client.get("/tables").headers[REQUEST_ID_HEADER]
    assert GENERATED_ID.fullmatch(first)
    assert first != second


@pytest.mark.parametrize("received", ["con espacio", "x" * 129, "comilla\"", ""])
def test_an_invalid_request_id_is_replaced(received: str):
    assert GENERATED_ID.fullmatch(request_id_from(received))


def test_errors_carry_the_request_id_too(client: TestClient):
    response = client.get("/tables", headers={"Authorization": "Bearer otra", REQUEST_ID_HEADER: "r1"})
    assert response.status_code == 401
    assert response.headers[REQUEST_ID_HEADER] == "r1"


def test_server_timing_reports_each_stage_of_a_query(client: TestClient):
    client.post("/query", json={"sql": "CREATE TABLE t (id INT PRIMARY KEY)"})
    response = client.post("/query", json={"sql": "SELECT * FROM t WHERE id = 1"})
    metrics = server_timing(response.headers["Server-Timing"])
    assert set(metrics) == {"parse", "plan", "exec", "txn", "serialize", "total"}
    assert all(value >= 0 for value in metrics.values())
    assert metrics["total"] >= metrics["plan"] + metrics["exec"]


def server_timing(header: str) -> dict[str, float]:
    metrics = {}
    for item in header.split(","):
        name, _, duration = item.strip().partition(";dur=")
        metrics[name] = float(duration)
    return metrics


def test_each_request_leaves_one_access_line(client: TestClient, caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.INFO, logger=ACCESS_LOGGER)
    client.get("/tables", headers={REQUEST_ID_HEADER: "traza-1"})
    (record,) = [item for item in caplog.records if item.name == ACCESS_LOGGER]
    line = json.loads(JsonFormatter().format(record))
    assert line["request_id"] == "traza-1"
    assert (line["method"], line["path"], line["status"]) == ("GET", "/tables", 200)
    assert line["service"] == SERVICE
    assert line["duration_ms"] >= 0
    assert line["level"] == "INFO"
    assert line["logger"] == ACCESS_LOGGER


def test_a_rejected_caller_is_logged_without_a_service(
    client: TestClient, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.INFO, logger=ACCESS_LOGGER)
    client.get("/tables", headers={"Authorization": "Bearer otra"})
    (record,) = [item for item in caplog.records if item.name == ACCESS_LOGGER]
    assert (record.status, record.service) == (401, "-")


def test_any_log_line_inside_a_request_carries_its_id():
    record = logging.LogRecord("motor", logging.WARNING, __file__, 1, "aviso", None, None)
    with tracing(RequestTrace("dentro")):
        line = json.loads(JsonFormatter().format(record))
    assert (line["request_id"], line["message"]) == ("dentro", "aviso")
    assert json.loads(JsonFormatter().format(record))["request_id"] is None
