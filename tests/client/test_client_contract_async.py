"""El cliente asíncrono contra el API real del gestor, en memoria por `httpx.ASGITransport`."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import httpx
import numpy as np
import pytest
from fastapi import FastAPI
from tests.client.conftest import ASGI_BASE_URL, BATCH_ROWS, IMMEDIATE_RETRIES

from api.logs import ACCESS_LOGGER
from minigestor_client import (
    REQUEST_ID_HEADER,
    AsyncClient,
    ConflictError,
    MediaFile,
    Point,
    SqlSyntaxError,
    TransportError,
    request_id_scope,
)

pytestmark = pytest.mark.anyio

ROWS = 10
REPEATED_ID = 2
CREATE_ITEMS = "CREATE TABLE items (id INT PRIMARY KEY, emb VECTOR(2), lugar POINT)"


class RefusingTransport(httpx.AsyncBaseTransport):
    """Rechaza las primeras `failures` conexiones y después entrega al gestor en memoria."""

    def __init__(self, app: FastAPI, failures: int) -> None:
        self._engine = httpx.ASGITransport(app=app)
        self._failures = failures
        self.request_ids: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.request_ids.append(request.headers[REQUEST_ID_HEADER])
        if len(self.request_ids) <= self._failures:
            raise httpx.ConnectError("conexión rechazada", request=request)
        return await self._engine.handle_async_request(request)


async def items(count: int, repeated: int | None = None) -> AsyncIterator[dict[str, object]]:
    for number in range(1, count + 1):
        identifier = repeated if repeated is not None and number == repeated + 1 else number
        yield {"id": identifier, "emb": np.array([number, 1.0]), "lugar": Point(-12.0, -77.0)}


@pytest.fixture
async def table(async_client: AsyncClient) -> AsyncClient:
    await async_client.execute(CREATE_ITEMS)
    return async_client


async def test_parameters_and_shortcuts(table: AsyncClient):
    await table.execute(
        "INSERT INTO items VALUES ($1, $2, $3)", [1, [0.5, 0.5], Point(-12.1, -77.1)]
    )
    assert await table.fetch_one("SELECT * FROM items WHERE id = $1", [1]) == {
        "id": 1,
        "emb": [0.5, 0.5],
        "lugar": {"lat": -12.1, "lon": -77.1},
    }
    assert await table.scalar("SELECT COUNT(*) FROM items") == 1
    assert await table.fetch_all("SELECT id FROM items") == [{"id": 1}]


async def test_an_async_generator_is_loaded_in_batches(table: AsyncClient):
    report = await table.bulk_insert("items", items(ROWS, repeated=REPEATED_ID), chunk_bytes=1)
    assert (report.rows_loaded, report.rows_rejected) == (ROWS - BATCH_ROWS, BATCH_ROWS)
    assert [batch.line for batch in report.rejected] == [REPEATED_ID + 1]


async def test_a_transaction_commits_or_rolls_back(table: AsyncClient):
    async with table.transaction() as transaction:
        await transaction.execute("INSERT INTO items VALUES ($1, $2, $3)", [1, None, None])
    with pytest.raises(ConflictError):
        async with table.transaction() as transaction:
            await transaction.execute("INSERT INTO items VALUES ($1, $2, $3)", [2, None, None])
            await transaction.execute("INSERT INTO items VALUES ($1, $2, $3)", [1, None, None])
            raise AssertionError("no llega: la inserción repetida lanza")
    assert await table.fetch_all("SELECT id FROM items") == [{"id": 1}]


async def test_errors_are_typed_and_traced(async_client: AsyncClient):
    with request_id_scope("asincrono-1"), pytest.raises(SqlSyntaxError) as raised:
        await async_client.query("SELEC 1")
    assert raised.value.request_id == "asincrono-1"
    assert raised.value.cursor == "SELEC 1\n^"


async def test_the_request_id_of_the_scope_reaches_the_engine_log(
    table: AsyncClient, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.INFO, logger=ACCESS_LOGGER)
    with request_id_scope("asincrono-2"):
        result = await table.query("SELECT * FROM items")
    assert result.request_id == "asincrono-2"
    assert "exec" in result.server_timing
    logged = [record for record in caplog.records if record.name == ACCESS_LOGGER]
    assert [record.request_id for record in logged] == ["asincrono-2"]


async def test_ready_and_tables(table: AsyncClient):
    assert await table.ready() is True
    assert [info.name for info in await table.tables()] == ["items"]


async def test_upload_and_stream_a_file(async_client: AsyncClient):
    uploaded = await async_client.upload_media(MediaFile("tono.wav", b"RIFF-y-algo-mas"))
    async with async_client.stream_file(uploaded.path, chunk_bytes=4) as stream:
        received = [chunk async for chunk in stream.chunks]
    assert b"".join(received) == b"RIFF-y-algo-mas"
    async with async_client.stream_file(uploaded.path, byte_range="bytes=5-") as stream:
        tail = b"".join([chunk async for chunk in stream.chunks])
    assert (stream.status_code, tail) == (206, b"y-algo-mas")
    assert await async_client.download(uploaded.path) == b"RIFF-y-algo-mas"


async def test_reads_are_retried_and_writes_are_not(engine_app: FastAPI, table: AsyncClient):
    reads = RefusingTransport(engine_app, failures=1)
    async with AsyncClient(ASGI_BASE_URL, transport=reads, retry=IMMEDIATE_RETRIES) as flaky:
        assert await flaky.fetch_all("SELECT * FROM items") == []
    assert len(reads.request_ids) == 2
    assert len(set(reads.request_ids)) == 1

    writes = RefusingTransport(engine_app, failures=1)
    async with AsyncClient(ASGI_BASE_URL, transport=writes, retry=IMMEDIATE_RETRIES) as flaky:
        with pytest.raises(TransportError):
            await flaky.execute("INSERT INTO items VALUES (1, NULL, NULL)")
    assert len(writes.request_ids) == 1
