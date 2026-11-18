"""El gestor real en el mismo proceso, y un cliente síncrono y otro asíncrono contra él."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import replace

import httpx
import pytest
from fastapi import FastAPI
from tests.client.engine_server import serving

from api.main import create_app
from config import EngineConfig
from minigestor_client import AsyncClient, Client, RetryPolicy

# Lotes de cuatro filas: una carga de una docena ya tiene lotes que entran y uno rechazado.
BATCH_ROWS = 4
ASGI_BASE_URL = "http://gestor"
IMMEDIATE_RETRIES = RetryPolicy(attempts=3, base_delay_seconds=0.0, max_delay_seconds=0.0)


@pytest.fixture
def engine_config(config: EngineConfig) -> EngineConfig:
    return replace(config, ingest_batch_rows=BATCH_ROWS)


@pytest.fixture
def engine_app(engine_config: EngineConfig) -> Iterator[FastAPI]:
    app = create_app(engine_config)
    yield app
    app.state.service.close()


@pytest.fixture
def engine_url(engine_app: FastAPI) -> Iterator[str]:
    with serving(engine_app) as url:
        yield url


@pytest.fixture
def client(engine_url: str) -> Iterator[Client]:
    with Client(engine_url, retry=IMMEDIATE_RETRIES) as instance:
        yield instance


@pytest.fixture
async def async_client(engine_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = httpx.ASGITransport(app=engine_app)
    async with AsyncClient(ASGI_BASE_URL, transport=transport, retry=IMMEDIATE_RETRIES) as instance:
        yield instance
