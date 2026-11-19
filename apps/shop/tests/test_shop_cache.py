"""La caché de respuestas: acierto, fallo, TTL, invalidación por generación y Redis caído.
Lo de prescindir de Redis (avisar una vez, la ventana de reintento) es de `appkit` y se
prueba allí."""

from __future__ import annotations

from collections.abc import AsyncIterator

import fakeredis
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from appkit import OptionalRedis
from shop.recommend.cache import (
    PRODUCT_ENTITY,
    USER_ENTITY,
    CacheEntry,
    CacheStatus,
    ResponseCache,
)

pytestmark = pytest.mark.anyio

TTL_SECONDS = 60
RETRY_SECONDS = 5.0
GUITAR = CacheEntry("producto", ((PRODUCT_ENTITY, "G"),), {"limite": 10})
FOR_USER = CacheEntry("producto", ((PRODUCT_ENTITY, "G"), (USER_ENTITY, "U")), {"limite": 10})


class BrokenRedis(fakeredis.FakeAsyncRedis):
    async def execute_command(self, *args: object, **options: object) -> object:
        raise RedisConnectionError("sin conexión")


@pytest.fixture
def server() -> fakeredis.FakeServer:
    return fakeredis.FakeServer()


@pytest.fixture
async def redis(server: fakeredis.FakeServer) -> AsyncIterator[OptionalRedis]:
    optional = OptionalRedis(lambda: fakeredis.FakeAsyncRedis(server=server), RETRY_SECONDS)
    async with optional.connected():
        yield optional


async def test_a_stored_response_is_a_hit(redis: OptionalRedis):
    cache = ResponseCache(redis, TTL_SECONDS, "v1")
    missed = await cache.lookup(GUITAR)
    await cache.store(missed, b"{}")
    hit = await cache.lookup(GUITAR)
    assert (missed.status, hit.status, hit.payload) == (CacheStatus.MISS, CacheStatus.HIT, b"{}")


async def test_other_parameters_or_version_miss(redis: OptionalRedis):
    cache = ResponseCache(redis, TTL_SECONDS, "v1")
    await cache.store(await cache.lookup(GUITAR), b"{}")
    other = CacheEntry("producto", GUITAR.entities, {"limite": 5})
    assert (await cache.lookup(other)).status is CacheStatus.MISS
    assert (await ResponseCache(redis, TTL_SECONDS, "v2").lookup(GUITAR)).status is CacheStatus.MISS


async def test_responses_expire_with_their_ttl(redis: OptionalRedis, server: fakeredis.FakeServer):
    cache = ResponseCache(redis, TTL_SECONDS, "v1")
    lookup = await cache.lookup(GUITAR)
    await cache.store(lookup, b"{}")
    assert lookup.key is not None
    assert 0 < await fakeredis.FakeAsyncRedis(server=server).ttl(lookup.key) <= TTL_SECONDS


async def test_invalidating_a_product_or_a_user_drops_what_depends_on_it(redis: OptionalRedis):
    cache = ResponseCache(redis, TTL_SECONDS, "v1")
    for entry in (GUITAR, FOR_USER):
        await cache.store(await cache.lookup(entry), b"{}")
    await cache.invalidate([], ["U"])
    assert (await cache.lookup(GUITAR)).status is CacheStatus.HIT
    assert (await cache.lookup(FOR_USER)).status is CacheStatus.MISS
    await cache.invalidate(["G"], [])
    assert (await cache.lookup(GUITAR)).status is CacheStatus.MISS


async def test_a_response_computed_before_an_invalidation_is_never_served(redis: OptionalRedis):
    cache = ResponseCache(redis, TTL_SECONDS, "v1")
    before = await cache.lookup(GUITAR)
    await cache.invalidate(["G"], [])
    await cache.store(before, b"viejo")
    assert (await cache.lookup(GUITAR)).status is CacheStatus.MISS


async def test_without_redis_everything_bypasses():
    optional = OptionalRedis(None, RETRY_SECONDS)
    async with optional.connected():
        cache = ResponseCache(optional, TTL_SECONDS, "v1")
        lookup = await cache.lookup(GUITAR)
        await cache.store(lookup, b"{}")
        await cache.invalidate(["G"], ["U"])
    assert (lookup.status, lookup.key) == (CacheStatus.BYPASS, None)


async def test_a_broken_redis_bypasses_and_degrades_the_service():
    optional = OptionalRedis(BrokenRedis, RETRY_SECONDS)
    async with optional.connected():
        cache = ResponseCache(optional, TTL_SECONDS, "v1")
        assert (await cache.lookup(GUITAR)).status is CacheStatus.BYPASS
        await cache.invalidate(["G"], [])
        assert optional.degradation() is not None
