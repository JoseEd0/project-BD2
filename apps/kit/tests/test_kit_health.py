"""La sonda de `/ready`: pregunta al gestor y recuerda la respuesta un rato."""

from __future__ import annotations

import pytest
from apps.kit.tests.kit_fakes import ENGINE_URL, FakeEngine

from appkit import ReadinessProbe
from minigestor_client import AsyncClient, RetryPolicy

pytestmark = pytest.mark.anyio

CACHE_SECONDS = 2.0
TIMEOUT_SECONDS = 1.0


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


def probe_for(engine: FakeEngine, clock: Clock) -> ReadinessProbe:
    client = AsyncClient(ENGINE_URL, transport=engine.transport(), retry=RetryPolicy(attempts=3))
    return ReadinessProbe(client, CACHE_SECONDS, TIMEOUT_SECONDS, clock)


async def test_a_ready_engine_makes_the_service_ready(engine: FakeEngine, clock: Clock):
    readiness = await probe_for(engine, clock).check()
    assert readiness.ready
    assert readiness.reason is None


async def test_the_answer_is_cached_for_a_while(engine: FakeEngine, clock: Clock):
    probe = probe_for(engine, clock)
    await probe.check()
    engine.ready = False
    clock.now += CACHE_SECONDS / 2
    assert (await probe.check()).ready
    assert len(engine.requests) == 1
    clock.now += CACHE_SECONDS
    assert not (await probe.check()).ready
    assert len(engine.requests) == 2


async def test_an_engine_that_says_no_is_not_ready(engine: FakeEngine, clock: Clock):
    engine.ready = False
    readiness = await probe_for(engine, clock).check()
    assert not readiness.ready
    assert readiness.reason == "el gestor responde que no está listo"


async def test_an_unreachable_engine_is_asked_once_and_not_ready(engine: FakeEngine, clock: Clock):
    engine.reachable = False
    readiness = await probe_for(engine, clock).check()
    assert not readiness.ready
    assert readiness.reason is not None
    assert readiness.reason.startswith("el gestor no responde")
    assert len(engine.requests) == 1
