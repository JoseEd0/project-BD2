"""El servicio de rostros contra el gestor real en el mismo proceso, con un reloj que el
test adelanta. Como `tests/kit/`, vive en el árbol del gestor porque construye su API."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import Role, ServiceKey, digest_of
from api.main import create_app as create_engine_app
from config import EngineConfig
from faces.api import create_app
from faces.api.settings import FacesSettings
from faces.models import MODELS_DIRECTORY_VARIABLE
from faces.pose import Pose

WRITE_KEY = "clave-de-escritura-de-rostros-0123456789"
ADMIN_KEY = "clave-de-administracion-de-rostros-0123456789"
ENGINE_URL = "http://gestor"
START_SECONDS = 1_800_000_000.0
# Lo bastante largo para que el borrado periódico no corra solo durante un test: los tests
# lo llaman a mano.
PURGE_INTERVAL_SECONDS = 3600.0


class FakeClock:
    def __init__(self) -> None:
        self.now = START_SECONDS

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(scope="session")
def models_dir() -> Path:
    directory = os.environ.get(MODELS_DIRECTORY_VARIABLE)
    if not directory:
        pytest.skip(f"sin {MODELS_DIRECTORY_VARIABLE}: `python -m faces models --salida <carpeta>`")
    return Path(directory)


@pytest.fixture
def engine_app(tmp_path: Path) -> Iterator[FastAPI]:
    """Con las páginas por omisión: una fila con un `VECTOR(128)` no cabe en las de 256 bytes
    que usan los tests del motor."""
    config = EngineConfig(data_directory=tmp_path)
    keys = (
        ServiceKey("faces", Role.WRITE, digest_of(WRITE_KEY)),
        ServiceKey("faces-migraciones", Role.ADMIN, digest_of(ADMIN_KEY)),
    )
    app = create_engine_app(config, service_keys=keys)
    yield app
    app.state.service.close()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


ServiceFactory = Callable[..., AbstractContextManager[TestClient]]


@pytest.fixture
def start_service(engine_app: FastAPI, models_dir: Path, clock: FakeClock) -> ServiceFactory:
    """Arranca el servicio con la configuración de los tests y `overrides` encima."""

    @contextmanager
    def started(**overrides: Any) -> Iterator[TestClient]:
        values: dict[str, Any] = {
            "engine_url": ENGINE_URL,
            "engine_key": WRITE_KEY,
            "migration_key": ADMIN_KEY,
            "models_dir": models_dir,
            "ready_cache_seconds": 0,
            "analysis_workers": 1,
            "enrollment_poses": (Pose.FRONT, Pose.FRONT),
            "purge_interval_seconds": PURGE_INTERVAL_SECONDS,
        }
        settings = FacesSettings(**{**values, **overrides})
        transport = httpx.ASGITransport(app=engine_app)
        with TestClient(create_app(settings, engine_transport=transport, clock=clock)) as client:
            yield client

    return started


@pytest.fixture
def service(start_service: ServiceFactory) -> Iterator[TestClient]:
    with start_service() as client:
        yield client


@pytest.fixture
def engine_rows(engine_app: FastAPI) -> Callable[[str], list[dict[str, Any]]]:
    """Las filas de un `SELECT` hecho directo al gestor, sin pasar por el servicio."""

    def rows(sql: str) -> list[dict[str, Any]]:
        headers = {"Authorization": f"Bearer {ADMIN_KEY}"}
        response = TestClient(engine_app).post("/query", json={"sql": sql}, headers=headers)
        assert response.status_code == 200, response.text
        result = response.json()
        return [dict(zip(result["columns"], row, strict=True)) for row in result["rows"]]

    return rows
