"""Las lecturas del gestor se reintentan solo mientras su catálogo está ocupado."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import anyio
import pytest

from minigestor_client import DomainError, Record
from rag.engine_reader import (
    BUSY_MESSAGE,
    ENGINE_BUSY_KIND,
    BusyRetry,
    BusyRetryingReader,
    EngineBusyError,
)

ROWS = [{"paper_id": "2501.00001"}]


def engine_error(kind: str) -> DomainError:
    return DomainError("fallo", request_id="r", status=400, kind=kind)


class FlakyEngine:
    """Falla con `failure` las primeras `failures` lecturas y después devuelve `ROWS`."""

    def __init__(self, failures: int, failure: DomainError) -> None:
        self.failures = failures
        self.failure = failure
        self.calls = 0

    async def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[Record]:
        self.calls += 1
        if self.calls <= self.failures:
            raise self.failure
        return ROWS  # type: ignore[return-value]

    async def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> Record | None:
        rows = await self.fetch_all(sql, params)
        return rows[0]


def read(engine: FlakyEngine, retries: int = 3) -> tuple[list[Record], list[float]]:
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    reader = BusyRetryingReader(engine, BusyRetry(retries=retries, backoff_seconds=0.5), sleep)
    return anyio.run(reader.fetch_all, "SELECT 1"), waits


def test_a_busy_catalog_is_retried_with_growing_waits():
    engine = FlakyEngine(failures=2, failure=engine_error(ENGINE_BUSY_KIND))
    rows, waits = read(engine)
    assert rows == ROWS
    assert waits == [0.5, 1.0]
    assert engine.calls == 3


def test_a_catalog_that_stays_busy_is_a_specific_unavailable_error():
    engine = FlakyEngine(failures=10, failure=engine_error(ENGINE_BUSY_KIND))
    with pytest.raises(EngineBusyError) as raised:
        read(engine, retries=2)
    assert raised.value.message == BUSY_MESSAGE
    assert raised.value.status == 503
    assert engine.calls == 3


def test_other_engine_errors_are_not_retried():
    engine = FlakyEngine(failures=1, failure=engine_error("UnknownTableError"))
    with pytest.raises(DomainError):
        read(engine)
    assert engine.calls == 1


def test_without_retries_a_busy_catalog_fails_at_once():
    engine = FlakyEngine(failures=1, failure=engine_error(ENGINE_BUSY_KIND))
    with pytest.raises(EngineBusyError):
        read(engine, retries=0)


def test_fetch_one_is_retried_too():
    engine = FlakyEngine(failures=1, failure=engine_error(ENGINE_BUSY_KIND))

    async def sleep(_: float) -> None:
        return None

    reader = BusyRetryingReader(engine, BusyRetry(retries=1, backoff_seconds=0.1), sleep)
    assert anyio.run(reader.fetch_one, "SELECT 1") == ROWS[0]
