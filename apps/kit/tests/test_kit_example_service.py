"""El servicio de ejemplo con un gestor de mentira: salud, preparación, errores uniformes, el
`X-Request-ID` que pasa de la petición al gestor, su fábrica y su carga inicial."""

from __future__ import annotations

import pytest
from apps.kit.tests import kit_example
from apps.kit.tests.kit_example import (
    BROKEN_SQL,
    NAME,
    NOTES_STAGE,
    SEED_NOTE_ID,
    VERSION,
    WRITE_NOTE,
    ExampleSettings,
    create_app,
    seed,
)
from apps.kit.tests.kit_fakes import FakeEngine
from fastapi.testclient import TestClient

from minigestor_client import REQUEST_ID_HEADER, parse_server_timing


def error(response) -> tuple[int, str]:
    body = response.json()
    assert set(body) == {"error", "kind", "request_id"}
    assert body["request_id"] == response.headers[REQUEST_ID_HEADER]
    return response.status_code, body["kind"]


def test_health_answers_without_the_engine(service: TestClient, engine: FakeEngine):
    engine.reachable = False
    response = service.get("/health")
    assert response.json() == {"status": "ok", "version": VERSION}


def test_ready_follows_the_engine(service: TestClient, engine: FakeEngine):
    assert service.get("/ready").json() == {"status": "ready"}
    engine.ready = False
    response = service.get("/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"


def test_ready_reports_an_unreachable_engine(service: TestClient, engine: FakeEngine):
    engine.reachable = False
    response = service.get("/ready")
    assert response.status_code == 503
    assert "no responde" in response.json()["reason"]


def test_the_request_id_reaches_the_engine_and_comes_back(service: TestClient, engine: FakeEngine):
    engine.rows[7] = [7, "hola"]
    response = service.get("/notas/7", headers={REQUEST_ID_HEADER: "del-navegador"})
    assert response.json() == {"id": 7, "texto": "hola"}
    assert response.headers[REQUEST_ID_HEADER] == "del-navegador"
    assert engine.requests[-1].headers[REQUEST_ID_HEADER] == "del-navegador"
    assert NOTES_STAGE in parse_server_timing(response.headers["Server-Timing"])


def test_a_service_error_keeps_its_status(service: TestClient):
    assert error(service.get("/notas/99")) == (404, "ResourceNotFoundError")


def test_an_invalid_body_is_a_422(service: TestClient):
    assert error(service.post("/notas", json={"id": "uno"})) == (422, "RequestValidationError")


def test_an_unknown_route_is_a_404(service: TestClient):
    assert error(service.get("/no-existe")) == (404, "HTTPError")


@pytest.mark.parametrize(
    ("sql", "engine_status", "engine_kind", "status", "kind"),
    [
        (WRITE_NOTE, 400, "DuplicatePrimaryKeyError", 409, "DuplicatePrimaryKeyError"),
        (WRITE_NOTE, 400, "InvalidValueError", 400, "InvalidValueError"),
        (WRITE_NOTE, 400, "StatementTimeoutError", 504, "StatementTimeoutError"),
        (WRITE_NOTE, 503, "ServiceStoppingError", 503, "EngineUnavailableError"),
        (WRITE_NOTE, 500, "KeyError", 503, "EngineUnavailableError"),
        (WRITE_NOTE, 403, "PermissionError", 500, "EngineAccessError"),
    ],
)
def test_engine_errors_are_mapped(
    service, engine: FakeEngine, sql, engine_status, engine_kind, status, kind
):
    engine.failures[sql] = (engine_status, engine_kind)
    assert error(service.post("/notas", json={"id": 1, "texto": "x"})) == (status, kind)


def test_an_engine_failure_never_shows_its_message(service: TestClient, engine: FakeEngine):
    engine.failures[BROKEN_SQL] = (400, "SqlSyntaxError")
    response = service.get("/sql-roto")
    assert error(response) == (500, "ServiceError")
    assert "SqlSyntaxError" not in response.json()["error"]


def test_an_unreachable_engine_is_a_503(service: TestClient, engine: FakeEngine):
    engine.reachable = False
    assert error(service.get("/notas/1")) == (503, "EngineUnavailableError")


def test_an_unexpected_failure_is_a_500(service: TestClient):
    assert error(service.get("/fallo")) == (500, "ServiceError")


def test_the_factory_reads_its_settings_from_the_environment(
    settings: ExampleSettings, monkeypatch: pytest.MonkeyPatch
):
    # configure_logging cambia el logging de todo el proceso; aquí solo importa que se llame.
    levels: list[str] = []
    monkeypatch.setattr(kit_example, "configure_logging", levels.append)
    assert create_app().title == NAME
    assert levels == [settings.log_level]


@pytest.mark.anyio
async def test_the_seed_writes_its_note_once(settings: ExampleSettings, engine: FakeEngine):
    assert await seed(settings, engine.transport())
    engine.rows[SEED_NOTE_ID] = [SEED_NOTE_ID, "ya estaba"]
    assert not await seed(settings, engine.transport())
    writes = [sql for sql, _ in engine.queries() if sql == WRITE_NOTE]
    assert len(writes) == 1
