"""`X-Request-ID` y `Server-Timing` de un servicio: el id llega al cliente del gestor y vuelve
en la respuesta, también en un `500`."""

from __future__ import annotations

import logging
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appkit.errors import install_error_handlers
from appkit.logs import ACCESS_LOGGER
from appkit.tracing import (
    InvalidStageNameError,
    RequestTrace,
    RequestTracingMiddleware,
    record_stage,
    request_id_from,
    timed,
)
from minigestor_client import REQUEST_ID_HEADER, current_request_id, parse_server_timing

GENERATED_ID = re.compile(r"[0-9a-f]{32}")


@pytest.fixture
def traced() -> TestClient:
    app = FastAPI()
    app.add_middleware(RequestTracingMiddleware, service="prueba")
    install_error_handlers(app)

    @app.get("/id")
    async def request_id() -> dict[str, str | None]:
        with timed("trabajo"):
            record_stage("modelo", 2.0)
        return {"id": current_request_id()}

    @app.get("/id-en-hilo")
    def request_id_in_a_thread() -> dict[str, str | None]:
        return {"id": current_request_id()}

    @app.get("/fallo")
    async def failure() -> None:
        raise RuntimeError("bug")

    return TestClient(app)


def test_the_received_id_is_the_one_the_engine_client_sees(traced: TestClient):
    response = traced.get("/id", headers={REQUEST_ID_HEADER: "del-navegador-1"})
    assert response.json() == {"id": "del-navegador-1"}
    assert response.headers[REQUEST_ID_HEADER] == "del-navegador-1"


def test_a_sync_route_sees_the_id_too(traced: TestClient):
    response = traced.get("/id-en-hilo", headers={REQUEST_ID_HEADER: "en-hilo"})
    assert response.json() == {"id": "en-hilo"}


@pytest.mark.parametrize("received", [None, "con espacio", "x" * 129])
def test_a_missing_or_invalid_id_is_replaced(traced: TestClient, received: str | None):
    headers = {} if received is None else {REQUEST_ID_HEADER: received}
    response = traced.get("/id", headers=headers)
    assert GENERATED_ID.fullmatch(response.headers[REQUEST_ID_HEADER])
    assert response.json()["id"] == response.headers[REQUEST_ID_HEADER]


def test_server_timing_has_the_stages_and_the_total(traced: TestClient):
    metrics = parse_server_timing(traced.get("/id").headers["Server-Timing"])
    assert list(metrics) == ["modelo", "trabajo", "total"]
    assert metrics["modelo"] == 2.0
    assert metrics["total"] >= metrics["trabajo"]


def test_an_unexpected_failure_is_a_500_with_its_id(
    traced: TestClient, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.INFO)
    response = traced.get("/fallo", headers={REQUEST_ID_HEADER: "con-bug"})
    assert response.status_code == 500
    assert response.json() == {
        "error": "error interno del servicio",
        "kind": "ServiceError",
        "request_id": "con-bug",
    }
    assert response.headers[REQUEST_ID_HEADER] == "con-bug"
    (access,) = [record for record in caplog.records if record.name == ACCESS_LOGGER]
    assert (access.status, access.request_id) == (500, "con-bug")
    assert any(
        "RuntimeError: bug" in record.exc_text for record in caplog.records if record.exc_text
    )


def test_each_request_leaves_its_access_line(traced: TestClient, caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.INFO, logger=ACCESS_LOGGER)
    traced.get("/id", headers={REQUEST_ID_HEADER: "linea-1"})
    (record,) = caplog.records
    assert (record.method, record.path, record.status, record.service) == (
        "GET",
        "/id",
        200,
        "prueba",
    )
    assert record.request_id == "linea-1"


def test_outside_a_request_recording_does_nothing():
    record_stage("suelta", 1.0)


def test_a_stage_name_must_fit_in_the_header():
    with pytest.raises(InvalidStageNameError):
        RequestTrace("x").record("dos palabras", 1.0)


def test_a_valid_id_is_kept():
    assert request_id_from("01HZY-ab.c:d_e") == "01HZY-ab.c:d_e"
