"""El servicio de ejemplo con su configuración en el entorno y un gestor de mentira."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from apps.kit.tests.kit_example import ENV_PREFIX, ExampleSettings, create_example_app
from apps.kit.tests.kit_fakes import ENGINE_URL, FakeEngine
from fastapi.testclient import TestClient

from appkit import load_settings

RUNTIME_KEY = "clave-de-la-app"


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setenv(f"{ENV_PREFIX}ENGINE_URL", ENGINE_URL)
    monkeypatch.setenv(f"{ENV_PREFIX}ENGINE_KEY", RUNTIME_KEY)
    monkeypatch.setenv(f"{ENV_PREFIX}ENGINE_RETRY_ATTEMPTS", "1")
    monkeypatch.setenv(f"{ENV_PREFIX}READY_CACHE_SECONDS", "0")
    return monkeypatch


@pytest.fixture
def settings(environment: pytest.MonkeyPatch) -> ExampleSettings:
    return load_settings(ExampleSettings)


@pytest.fixture
def engine() -> FakeEngine:
    return FakeEngine()


@pytest.fixture
def service(settings: ExampleSettings, engine: FakeEngine) -> Iterator[TestClient]:
    with TestClient(create_example_app(settings, engine.transport())) as client:
        yield client
