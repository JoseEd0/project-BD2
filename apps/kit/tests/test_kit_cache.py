"""La caché opcional en Redis: sin configurar, sana, caída y de vuelta; y lo que dicen las
sondas cuando falta."""

from __future__ import annotations

import logging

import pytest
from apps.kit.tests.kit_example import NAME, VERSION, ExampleSettings
from apps.kit.tests.kit_fakes import FakeEngine
from fakeredis import FakeAsyncRedis, FakeServer
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from appkit import OptionalRedis, create_service_app, redis_factory

pytestmark = pytest.mark.anyio

RETRY_SECONDS = 5.0
KEY = "clave"


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def server() -> FakeServer:
    return FakeServer()


@pytest.fixture
def clock() -> Clock:
    return Clock()


def cache_on(server: FakeServer, clock: Clock) -> OptionalRedis:
    return OptionalRedis(lambda: FakeAsyncRedis(server=server), RETRY_SECONDS, clock)


async def test_without_a_url_there_is_no_cache_and_nothing_is_degraded():
    cache = OptionalRedis(None, RETRY_SECONDS)
    async with cache.connected():
        assert not cache.configured
        assert cache.client() is None
        assert cache.degradation() is None


async def test_a_healthy_redis_is_used(server: FakeServer, clock: Clock):
    cache = cache_on(server, clock)
    assert cache.client() is None
    async with cache.connected():
        client = cache.client()
        assert client is not None
        await client.set(KEY, b"valor")
        assert await client.get(KEY) == b"valor"
    assert cache.client() is None


async def test_a_failure_opens_a_retry_window_logged_once(
    server: FakeServer, clock: Clock, caplog: pytest.LogCaptureFixture
):
    cache = cache_on(server, clock)
    async with cache.connected():
        with caplog.at_level(logging.WARNING, logger="appkit.cache"):
            cache.failed(RedisConnectionError("conexión rechazada"))
            clock.now += RETRY_SECONDS
            cache.failed(RedisConnectionError("conexión rechazada"))
        assert len(caplog.records) == 1
        assert cache.client() is None
        assert cache.degradation() == "sin caché: Redis no responde (conexión rechazada)"
        clock.now += RETRY_SECONDS
        assert cache.client() is not None
        cache.succeeded()
        assert cache.degradation() is None


async def test_a_stopped_redis_raises_what_failed_expects(server: FakeServer, clock: Clock):
    cache = cache_on(server, clock)
    async with cache.connected():
        server.connected = False
        client = cache.client()
        assert client is not None
        with pytest.raises(RedisConnectionError):
            await client.get(KEY)


async def test_the_real_client_fails_fast_against_nothing():
    """Un puerto donde no escucha nadie: falla con un error de Redis, no se queda colgado."""
    cache = OptionalRedis(redis_factory("redis://127.0.0.1:1/0", 0.2), RETRY_SECONDS)
    async with cache.connected():
        client = cache.client()
        assert client is not None
        with pytest.raises(RedisConnectionError):
            await client.ping()


def test_the_probes_say_the_service_is_degraded_without_changing_status(
    settings: ExampleSettings, engine: FakeEngine
):
    cache = OptionalRedis(lambda: FakeAsyncRedis(server=FakeServer()), RETRY_SECONDS)
    app = create_service_app(
        settings,
        name=NAME,
        version=VERSION,
        engine_transport=engine.transport(),
        degradables=[cache],
    )
    with TestClient(app) as service:
        assert service.get("/health").json() == {"status": "ok", "version": VERSION}
        assert service.get("/ready").json() == {"status": "ready"}
        cache.failed(RedisConnectionError("sin red"))
        degraded = {"redis": "sin caché: Redis no responde (sin red)"}
        health = service.get("/health")
        assert (health.status_code, health.json()["degraded"]) == (200, degraded)
        ready = service.get("/ready")
        assert ready.status_code == 200
        assert ready.json() == {"status": "ready", "degraded": degraded}
