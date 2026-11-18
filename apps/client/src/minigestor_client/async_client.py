"""El cliente asíncrono del gestor, el que usa el API de cada aplicación."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterable, AsyncIterator, Iterable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from types import TracebackType
from typing import Any

import httpx

from . import calls
from .context import current_request_id, new_request_id
from .errors import EngineError, MinigestorError, ServiceUnavailableError, TransportError
from .media import MediaFile, as_media_file
from .models import LoadReport, MediaUpload, QueryResult, SegmentSearch, TableInfo
from .options import PoolLimits, RetryPolicy, Timeouts
from .queries import BEGIN_SQL, COMMIT_SQL, ROLLBACK_SQL, AsyncQueries, Params
from .sync_client import DOWNLOAD_CHUNK_BYTES, new_session_id
from .values import NDJSON_CHUNK_BYTES, async_ndjson_chunks

logger = logging.getLogger(__name__)

Rows = AsyncIterable[Mapping[str, Any]] | Iterable[Mapping[str, Any]]


class AsyncClient(AsyncQueries):
    """La misma conexión que `Client`, con métodos `async`: reintentos, `X-Request-ID`,
    errores y resultados se comportan igual. Se cierra con `aclose()` o con `async with`."""

    def __init__(
        self,
        base_url: str,
        service_key: str | None = None,
        *,
        timeouts: Timeouts | None = None,
        pool: PoolLimits | None = None,
        retry: RetryPolicy | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeouts = timeouts or Timeouts()
        self._retry = retry or RetryPolicy()
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers=calls.authorization_headers(service_key),
            timeout=self._timeouts.for_requests(),
            limits=(pool or PoolLimits()).as_httpx(),
            transport=transport,
        )

    async def __aenter__(self) -> AsyncClient:
        return self

    async def __aexit__(
        self,
        error_type: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Cierra las conexiones del pool."""
        await self._http.aclose()

    async def query(
        self,
        sql: str,
        params: Params = (),
        *,
        session_id: str | None = None,
        idempotent: bool | None = None,
    ) -> QueryResult:
        """Como `Client.query`."""
        call = calls.query_call(sql, params, session_id, idempotent)
        return calls.traced(QueryResult, await self._send(call))

    async def tables(self) -> list[TableInfo]:
        response = await self._send(calls.tables_call())
        return [TableInfo.model_validate(table) for table in response.json()]

    async def ready(self, timeout_seconds: float | None = None) -> bool:
        """Como `Client.ready`: una sola pregunta, `False` con `503`.

        Raises:
            TransportError: si el gestor no responde.
        """
        timeout = None if timeout_seconds is None else httpx.Timeout(timeout_seconds)
        try:
            await self._send(calls.ready_call(timeout))
        except ServiceUnavailableError:
            return False
        return True

    async def bulk_insert(
        self, table: str, rows: Rows, *, chunk_bytes: int = NDJSON_CHUNK_BYTES
    ) -> LoadReport:
        """Como `Client.bulk_insert`; `rows` puede ser síncrono o asíncrono."""
        chunks = async_ndjson_chunks(rows, chunk_bytes)
        call = calls.rows_call(table, chunks, self._timeouts.for_loads())
        return calls.traced(LoadReport, await self._send(call))

    async def upload_media(self, file: MediaFile | Path) -> MediaUpload:
        response = await self._send(calls.upload_call(as_media_file(file)))
        return MediaUpload.model_validate(response.json())

    async def similar_media(
        self,
        file: MediaFile | Path,
        sql: str,
        params: Params = (),
        *,
        session_id: str | None = None,
    ) -> QueryResult:
        """Como `Client.similar_media`: la ruta subida es `$1` y `params` siguen desde `$2`."""
        uploaded = await self.upload_media(file)
        return await self.query(sql, [uploaded.path, *params], session_id=session_id)

    async def segments(
        self,
        file: MediaFile | Path,
        table: str,
        *,
        k: int | None = None,
        window: float | None = None,
        hop: float | None = None,
        column: str | None = None,
        session_id: str | None = None,
    ) -> SegmentSearch:
        """Como `Client.segments`."""
        form = calls.segments_form(table, k, window, hop, column, session_id)
        response = await self._send(calls.segments_call(as_media_file(file), form))
        return calls.traced(SegmentSearch, response)

    async def download(self, path: str) -> bytes:
        return (await self._send(calls.file_call(path))).content

    async def thumbnail(self, path: str) -> bytes:
        return (await self._send(calls.thumbnail_call(path))).content

    @asynccontextmanager
    async def stream_file(
        self, path: str, chunk_bytes: int = DOWNLOAD_CHUNK_BYTES
    ) -> AsyncIterator[AsyncIterator[bytes]]:
        """El archivo `path` por trozos, para reenviarlo sin tenerlo entero en memoria."""
        response = await self._send(calls.file_stream_call(path))
        try:
            yield response.aiter_bytes(chunk_bytes)
        finally:
            await response.aclose()

    async def close_session(self, session_id: str) -> bool:
        response = await self._send(calls.close_session_call(session_id))
        return bool(response.json()["closed"])

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncTransaction]:
        """Como `Client.transaction`."""
        transaction = AsyncTransaction(self, new_session_id())
        try:
            await transaction.query(BEGIN_SQL)
            yield transaction
            await transaction.query(COMMIT_SQL)
        except Exception:
            await self._roll_back(transaction)
            raise
        finally:
            await self._close_session_quietly(transaction.session_id)

    async def _roll_back(self, transaction: AsyncTransaction) -> None:
        if not transaction.in_transaction:
            return
        try:
            await transaction.query(ROLLBACK_SQL)
        except MinigestorError as error:
            logger.warning(
                "no se pudo deshacer la transacción de %s (%s); cerrar la sesión la aborta",
                transaction.session_id,
                error,
            )

    async def _close_session_quietly(self, session_id: str) -> None:
        try:
            await self.close_session(session_id)
        except MinigestorError as error:
            logger.warning("no se pudo cerrar la sesión %s: %s", session_id, error)

    async def _send(self, call: calls.Call) -> httpx.Response:
        request_id = current_request_id() or new_request_id()
        attempts = self._retry.attempts if call.retryable else 1
        for retry in range(1, attempts):
            try:
                return await self._attempt(call, request_id)
            except httpx.TransportError as error:
                calls.log_retry(call, retry, error)
                await asyncio.sleep(self._retry.delay_before(retry))
        try:
            return await self._attempt(call, request_id)
        except httpx.TransportError as error:
            raise TransportError(
                calls.unreachable_message(call, error), request_id=request_id, attempts=attempts
            ) from error

    async def _attempt(self, call: calls.Call, request_id: str) -> httpx.Response:
        request = self._http.build_request(**calls.request_arguments(call, request_id))
        response = await self._http.send(request, stream=call.stream)
        if not response.is_error:
            return response
        await response.aread()
        await response.aclose()
        raise calls.error_of(response, call, request_id)


class AsyncTransaction(AsyncQueries):
    """Las sentencias de una transacción abierta con `AsyncClient.transaction()`."""

    def __init__(self, client: AsyncClient, session_id: str) -> None:
        self._client = client
        self.session_id = session_id
        self.in_transaction = False

    async def query(
        self, sql: str, params: Params = (), *, idempotent: bool | None = None
    ) -> QueryResult:
        try:
            result = await self._client.query(
                sql, params, session_id=self.session_id, idempotent=idempotent
            )
        except EngineError as error:
            if error.in_transaction is not None:
                self.in_transaction = error.in_transaction
            raise
        self.in_transaction = result.in_transaction
        return result
