"""La caché de respuestas: acierto, fallo, invalidación por generación y Redis caído."""

from __future__ import annotations

import fakeredis
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

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


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class BrokenRedis(fakeredis.FakeAsyncRedis):
    async def execute_command(self, *args: object, **options: object) -> object:
        raise RedisConnectionError("sin conexión")


def cache_on(client: fakeredis.FakeAsyncRedis, version: str = "v1") -> ResponseCache:
    return ResponseCache(client, TTL_SECONDS, version, RETRY_SECONDS)


async def test_a_stored_response_is_a_hit():
    cache = cache_on(fakeredis.FakeAsyncRedis())
    missed = await cache.lookup(GUITAR)
    await cache.store(missed, b"{}")
    hit = await cache.lookup(GUITAR)
    assert (missed.status, hit.status, hit.payload) == (CacheStatus.MISS, CacheStatus.HIT, b"{}")


async def test_other_parameters_or_version_miss():
    client = fakeredis.FakeAsyncRedis()
    cache = cache_on(client)
    await cache.store(await cache.lookup(GUITAR), b"{}")
    other = CacheEntry("producto", GUITAR.entities, {"limite": 5})
    assert (await cache.lookup(other)).status is CacheStatus.MISS
    assert (await cache_on(client, "v2").lookup(GUITAR)).status is CacheStatus.MISS


async def test_responses_expire_with_their_ttl():
    client = fakeredis.FakeAsyncRedis()
    cache = cache_on(client)
    lookup = await cache.lookup(GUITAR)
    await cache.store(lookup, b"{}")
    assert lookup.key is not None
    assert 0 < await client.ttl(lookup.key) <= TTL_SECONDS


async def test_invalidating_a_product_or_a_user_drops_what_depends_on_it():
    cache = cache_on(fakeredis.FakeAsyncRedis())
    for entry in (GUITAR, FOR_USER):
        await cache.store(await cache.lookup(entry), b"{}")
    await cache.invalidate([], ["U"])
    assert (await cache.lookup(GUITAR)).status is CacheStatus.HIT
    assert (await cache.lookup(FOR_USER)).status is CacheStatus.MISS
    await cache.invalidate(["G"], [])
    assert (await cache.lookup(GUITAR)).status is CacheStatus.MISS


async def test_a_response_computed_before_an_invalidation_is_never_served():
    cache = cache_on(fakeredis.FakeAsyncRedis())
    before = await cache.lookup(GUITAR)
    await cache.invalidate(["G"], [])
    await cache.store(before, b"viejo")
    assert (await cache.lookup(GUITAR)).status is CacheStatus.MISS


async def test_without_redis_everything_bypasses():
    cache = ResponseCache(None, TTL_SECONDS, "v1", RETRY_SECONDS)
    lookup = await cache.lookup(GUITAR)
    await cache.store(lookup, b"{}")
    await cache.invalidate(["G"], ["U"])
    assert (lookup.status, lookup.key) == (CacheStatus.BYPASS, None)


async def test_a_broken_redis_is_logged_once_and_retried_later(caplog: pytest.LogCaptureFixture):
    clock = Clock()
    broken = BrokenRedis()
    cache = ResponseCache(broken, TTL_SECONDS, "v1", RETRY_SECONDS, clock)
    assert (await cache.lookup(GUITAR)).status is CacheStatus.BYPASS
    assert (await cache.lookup(GUITAR)).status is CacheStatus.BYPASS
    await cache.invalidate(["G"], [])
    assert len([record for record in caplog.records if "no responde" in record.message]) == 1
    clock.now = RETRY_SECONDS + 1
    assert (await cache.lookup(GUITAR)).status is CacheStatus.BYPASS
