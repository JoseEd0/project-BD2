"""La caché de lecturas del catálogo: aciertos, vida corta mientras se carga y una sola
consulta para las peticiones simultáneas."""

from __future__ import annotations

import asyncio

from songcheck.read_cache import CachePolicy, ReadCache

POLICY = CachePolicy(ready_seconds=100.0, loading_seconds=5.0)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Loader:
    def __init__(self, value: str, delay: float = 0.0) -> None:
        self.value = value
        self.delay = delay
        self.calls = 0

    async def __call__(self) -> str:
        self.calls += 1
        await asyncio.sleep(self.delay)
        return self.value


def test_a_ready_value_is_served_from_memory_until_it_expires() -> None:
    clock = Clock()
    cache: ReadCache[str] = ReadCache(POLICY, clock)
    load = Loader("listo")

    async def scenario() -> list[str]:
        first = await cache.get("k", load, lambda _: True)
        clock.now = 99.0
        second = await cache.get("k", load, lambda _: True)
        clock.now = 101.0
        third = await cache.get("k", load, lambda _: True)
        return [first, second, third]

    assert asyncio.run(scenario()) == ["listo"] * 3
    assert load.calls == 2


def test_a_catalog_still_loading_is_kept_only_briefly() -> None:
    clock = Clock()
    cache: ReadCache[str] = ReadCache(POLICY, clock)
    load = Loader("cargando")

    async def scenario() -> None:
        await cache.get("k", load, lambda _: False)
        clock.now = 4.0
        await cache.get("k", load, lambda _: False)
        clock.now = 6.0
        await cache.get("k", load, lambda _: False)

    asyncio.run(scenario())
    assert load.calls == 2


def test_simultaneous_misses_share_one_load_per_key() -> None:
    cache: ReadCache[str] = ReadCache(POLICY, Clock())
    load = Loader("página", delay=0.05)
    other = Loader("otra", delay=0.05)

    async def scenario() -> list[str]:
        requests = [cache.get("k", load, lambda _: True) for _ in range(5)]
        return list(await asyncio.gather(*requests, cache.get("j", other, lambda _: True)))

    assert asyncio.run(scenario()) == ["página"] * 5 + ["otra"]
    assert (load.calls, other.calls) == (1, 1)
