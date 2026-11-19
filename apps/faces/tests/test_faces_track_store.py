"""Los rastros de una sesión en Redis: ida y vuelta, TTL, sin embeddings y sin Redis."""

from __future__ import annotations

import json

import pytest
from fakeredis import FakeAsyncRedis, FakeServer

from appkit import OptionalRedis
from faces.api.realtime.tracks import KEY_PREFIX, TrackStore, book_from_json, book_to_json
from faces.detection import BoundingBox
from faces.tracking import Identity, Track, TrackBook

pytestmark = pytest.mark.anyio

SESSION = "0123456789abcdef0123456789abcdef"
TTL_SECONDS = 60
RETRY_SECONDS = 5.0
BOOK = TrackBook(
    (
        Track(3, BoundingBox(1.5, 2, 100, 120), 10.0, Identity("Eileen Collins", 0.81, 9.0)),
        Track(4, BoundingBox(300, 2, 50, 60), 10.0, Identity(None, 0.12, 9.5)),
        Track(5, BoundingBox(500, 2, 20, 24), 10.0),
    ),
    next_id=6,
)


def store_on(server: FakeServer) -> tuple[OptionalRedis, TrackStore]:
    cache = OptionalRedis(lambda: FakeAsyncRedis(server=server), RETRY_SECONDS)
    return cache, TrackStore(cache, TTL_SECONDS)


def test_a_book_survives_json():
    assert book_from_json(book_to_json(BOOK)) == BOOK


@pytest.mark.parametrize("stored", ["no es json", "{}", '{"tracks": [{"id": 1}], "next_id": 2}'])
def test_tracks_that_cannot_be_read_are_none(stored: str):
    assert book_from_json(stored) == TrackBook()


async def test_tracks_round_trip_through_redis_with_a_ttl():
    server = FakeServer()
    cache, store = store_on(server)
    async with cache.connected():
        assert await store.load(SESSION) == TrackBook()
        await store.save(SESSION, BOOK)
        assert await store.load(SESSION) == BOOK
        client = cache.client()
        assert client is not None
        assert 0 < await client.ttl(KEY_PREFIX + SESSION) <= TTL_SECONDS
        stored = json.loads(await client.get(KEY_PREFIX + SESSION))
    assert set(stored["tracks"][0]) == {"id", "box", "last_seen", "identity"}
    assert set(stored["tracks"][0]["identity"]) == {"name", "score", "verified_at"}


async def test_without_redis_there_are_no_tracks_and_the_service_is_degraded():
    server = FakeServer()
    cache, store = store_on(server)
    async with cache.connected():
        server.connected = False
        assert await store.load(SESSION) is None
        await store.save(SESSION, BOOK)
        assert cache.degradation() is not None
        server.connected = True
        assert await store.load(SESSION) is None


async def test_without_a_configured_redis_there_are_no_tracks():
    cache = OptionalRedis(None, RETRY_SECONDS)
    store = TrackStore(cache, TTL_SECONDS)
    async with cache.connected():
        assert await store.load(SESSION) is None
        assert cache.degradation() is None
