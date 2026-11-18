"""El servicio de ejemplo del kit (`apps/kit/tests/kit_example.py`) contra el gestor real en el
mismo proceso: migra con su clave de administración, atiende con su clave de escritura y
una petición deja el mismo `X-Request-ID` en los logs de los dos lados."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import httpx
import pytest
from apps.kit.tests.kit_example import ENV_PREFIX, NAME, ExampleSettings, create_example_app
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import Role, ServiceKey, digest_of
from api.logs import ACCESS_LOGGER as ENGINE_ACCESS_LOGGER
from api.logs import JsonFormatter as EngineJsonFormatter
from api.main import create_app
from appkit import MigrationError, load_settings
from appkit.logs import ACCESS_LOGGER as SERVICE_ACCESS_LOGGER
from appkit.logs import JsonFormatter as ServiceJsonFormatter
from config import EngineConfig
from minigestor_client import REQUEST_ID_HEADER

WRITE_KEY = "clave-de-escritura-del-ejemplo-0123456789"
ADMIN_KEY = "clave-de-administracion-del-ejemplo-0123456789"
ENGINE_URL = "http://gestor"


@pytest.fixture
def engine_app(config: EngineConfig) -> Iterator[FastAPI]:
    keys = (
        ServiceKey("ejemplo", Role.WRITE, digest_of(WRITE_KEY)),
        ServiceKey("ejemplo-migraciones", Role.ADMIN, digest_of(ADMIN_KEY)),
    )
    app = create_app(config, service_keys=keys)
    yield app
    app.state.service.close()


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setenv(f"{ENV_PREFIX}ENGINE_URL", ENGINE_URL)
    monkeypatch.setenv(f"{ENV_PREFIX}ENGINE_KEY", WRITE_KEY)
    monkeypatch.setenv(f"{ENV_PREFIX}MIGRATION_KEY", ADMIN_KEY)
    monkeypatch.setenv(f"{ENV_PREFIX}READY_CACHE_SECONDS", "0")
    return monkeypatch


def started(engine_app: FastAPI) -> TestClient:
    settings = load_settings(ExampleSettings)
    return TestClient(create_example_app(settings, httpx.ASGITransport(app=engine_app)))


@pytest.fixture
def service(environment: pytest.MonkeyPatch, engine_app: FastAPI) -> Iterator[TestClient]:
    with started(engine_app) as client:
        yield client


def logged_lines(
    caplog: pytest.LogCaptureFixture, logger: str, formatter: logging.Formatter
) -> list[dict[str, object]]:
    return [
        json.loads(formatter.format(record)) for record in caplog.records if record.name == logger
    ]


def test_a_request_id_is_followed_from_the_service_to_the_engine(
    service: TestClient, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.INFO)
    created = service.post(
        "/notas", json={"id": 1, "texto": "hola"}, headers={REQUEST_ID_HEADER: "e2e-1"}
    )
    assert created.status_code == 201
    assert created.headers[REQUEST_ID_HEADER] == "e2e-1"

    (service_line,) = logged_lines(caplog, SERVICE_ACCESS_LOGGER, ServiceJsonFormatter())
    (engine_line,) = logged_lines(caplog, ENGINE_ACCESS_LOGGER, EngineJsonFormatter())
    assert (service_line["request_id"], service_line["service"], service_line["path"]) == (
        "e2e-1",
        NAME,
        "/notas",
    )
    assert (engine_line["request_id"], engine_line["service"], engine_line["path"]) == (
        "e2e-1",
        "ejemplo",
        "/query",
    )
    assert set(service_line) == set(engine_line)


def test_the_service_reads_what_it_wrote_with_parameters(service: TestClient):
    service.post("/notas", json={"id": 2, "texto": "'; DROP TABLE ej_notas; --"})
    assert service.get("/notas/2").json() == {"id": 2, "texto": "'; DROP TABLE ej_notas; --"}


def test_engine_errors_reach_the_caller_mapped(service: TestClient):
    service.post("/notas", json={"id": 3, "texto": "una"})
    repeated = service.post("/notas", json={"id": 3, "texto": "otra"})
    assert (repeated.status_code, repeated.json()["kind"]) == (409, "DuplicatePrimaryKeyError")
    missing = service.get("/notas/404")
    assert (missing.status_code, missing.json()["kind"]) == (404, "ResourceNotFoundError")
    broken = service.get("/sql-roto")
    assert (broken.status_code, broken.json()["kind"]) == (500, "ServiceError")


def engine_request(
    engine_app: FastAPI, method: str, path: str, **arguments: object
) -> httpx.Response:
    """Directo al gestor, sin su `lifespan`: salir de él cerraría el gestor del test."""
    headers = {"Authorization": f"Bearer {ADMIN_KEY}"}
    return TestClient(engine_app).request(method, path, headers=headers, **arguments)


def test_the_migrations_are_repeatable(environment: pytest.MonkeyPatch, engine_app: FastAPI):
    for _ in range(2):
        with started(engine_app) as client:
            assert client.get("/ready").status_code == 200
    (table,) = engine_request(engine_app, "GET", "/tables").json()
    assert table["name"] == "ej_notas"
    assert table["indexes"].count("ej_notas_texto") == 1


def test_a_table_with_another_definition_stops_the_start(
    environment: pytest.MonkeyPatch, engine_app: FastAPI
):
    other = "CREATE TABLE ej_notas (id INT PRIMARY KEY, texto VARCHAR(10))"
    assert engine_request(engine_app, "POST", "/query", json={"sql": other}).status_code == 200
    with pytest.raises(MigrationError, match="otra definición"), started(engine_app):
        pass


def test_a_write_key_alone_cannot_migrate(environment: pytest.MonkeyPatch, engine_app: FastAPI):
    environment.delenv(f"{ENV_PREFIX}MIGRATION_KEY")
    with pytest.raises(MigrationError, match="rol admin"), started(engine_app):
        pass


def test_ready_follows_the_real_engine(service: TestClient, engine_app: FastAPI):
    assert service.get("/ready").json() == {"status": "ready"}
    engine_app.state.service.close()
    stopped = service.get("/ready")
    assert stopped.status_code == 503
    assert stopped.json()["status"] == "unavailable"
