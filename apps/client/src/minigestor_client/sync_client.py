"""El cliente síncrono del gestor, para guiones y trabajos de carga."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
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
from .queries import BEGIN_SQL, COMMIT_SQL, ROLLBACK_SQL, Params, SyncQueries
from .values import NDJSON_CHUNK_BYTES, ndjson_chunks

DOWNLOAD_CHUNK_BYTES = 64 * 1024
SESSION_PREFIX = "tx-"

logger = logging.getLogger(__name__)


def new_session_id() -> str:
    return f"{SESSION_PREFIX}{uuid.uuid4().hex}"


class Client(SyncQueries):
    """Conexión con el API del gestor, con un pool de conexiones que reutiliza.

    `base_url` es la dirección del gestor y `service_key`, la clave del servicio que se
    envía como `Authorization: Bearer`; sin clave, solo sirve con el gestor abierto. Cada
    petición lleva el `X-Request-ID` de `context.current_request_id()` o uno nuevo.

    Una operación que no recibió respuesta (conexión rechazada o cortada, plazo agotado) se
    reintenta según `retry` solo si no cambia nada en el gestor: un script que solo lee, una
    subida de consulta, una descarga. Una escritura nunca se repite sola.

    `transport` reemplaza la red, para los tests. Se cierra con `close()` o con `with`.
    """

    def __init__(
        self,
        base_url: str,
        service_key: str | None = None,
        *,
        timeouts: Timeouts | None = None,
        pool: PoolLimits | None = None,
        retry: RetryPolicy | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._timeouts = timeouts or Timeouts()
        self._retry = retry or RetryPolicy()
        self._http = httpx.Client(
            base_url=base_url,
            headers=calls.authorization_headers(service_key),
            timeout=self._timeouts.for_requests(),
            limits=(pool or PoolLimits()).as_httpx(),
            transport=transport,
        )

    def __enter__(self) -> Client:
        return self

    def __exit__(
        self,
        error_type: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Cierra las conexiones del pool."""
        self._http.close()

    def query(
        self,
        sql: str,
        params: Params = (),
        *,
        session_id: str | None = None,
        idempotent: bool | None = None,
    ) -> QueryResult:
        """Ejecuta `sql` con `params` como `$1`, `$2`, … (ver `values` para los tipos).

        Con `params`, `sql` es una sola sentencia. `session_id` la ejecuta en esa sesión;
        sin él, en autocommit. `idempotent` decide si se reintenta sin respuesta; por
        omisión, solo si el script solo lee.

        Raises:
            EngineError: la subclase que corresponde al error del gestor.
            TransportError: si no llegó respuesta tras los intentos permitidos.
            UnencodableValueError: si un parámetro no tiene forma JSON.
        """
        call = calls.query_call(sql, params, session_id, idempotent)
        return calls.traced(QueryResult, self._send(call))

    def tables(self) -> list[TableInfo]:
        """Las tablas del gestor con sus columnas e índices."""
        response = self._send(calls.tables_call())
        return [TableInfo.model_validate(table) for table in response.json()]

    def ready(self, timeout_seconds: float | None = None) -> bool:
        """`True` si el gestor está listo para atender; `False` si responde que no (`503`).

        Pregunta una sola vez, sin reintentos.

        Raises:
            TransportError: si el gestor no responde.
        """
        timeout = None if timeout_seconds is None else httpx.Timeout(timeout_seconds)
        try:
            self._send(calls.ready_call(timeout))
        except ServiceUnavailableError:
            return False
        return True

    def bulk_insert(
        self,
        table: str,
        rows: Iterable[Mapping[str, Any]],
        *,
        chunk_bytes: int = NDJSON_CHUNK_BYTES,
    ) -> LoadReport:
        """Carga `rows` en `table` por `POST /tables/{name}/rows`, pidiendo cada fila a `rows`
        recién cuando se va a enviar: una carga de millones de filas no las tiene en memoria.

        Cada fila es `{columna: valor}`; una columna que falta queda en NULL. Los lotes que el
        gestor rechaza vienen en `LoadReport.rejected` y no detienen la carga. Nunca se
        reintenta: el iterable ya se consumió.

        Raises:
            EngineError: si la tabla no existe o la clave no tiene rol `write`.
            TransportError: si la conexión se corta; los lotes confirmados quedan.
            UnencodableValueError: si una fila tiene un valor sin forma JSON; la carga se corta
                ahí y los lotes ya confirmados quedan.
        """
        chunks = ndjson_chunks(rows, chunk_bytes)
        call = calls.rows_call(table, chunks, self._timeouts.for_loads())
        return calls.traced(LoadReport, self._send(call))

    def upload_media(self, file: MediaFile | Path) -> MediaUpload:
        """Sube la imagen o el audio de una consulta; su `path` va en `SIMILAR_TO`."""
        response = self._send(calls.upload_call(as_media_file(file)))
        return MediaUpload.model_validate(response.json())

    def similar_media(
        self,
        file: MediaFile | Path,
        sql: str,
        params: Params = (),
        *,
        session_id: str | None = None,
    ) -> QueryResult:
        """Sube `file` y ejecuta `sql` con su ruta en el gestor como `$1` y `params` desde `$2`:

        client.similar_media(foto, "SELECT id, SIMILARITY_SCORE() FROM fotos "
                                   "WHERE SIMILAR_TO($1, k = $2)", [10])
        """
        uploaded = self.upload_media(file)
        return self.query(sql, [uploaded.path, *params], session_id=session_id)

    def segments(
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
        """Las `k` filas de `table` más parecidas a cada ventana de `window` segundos, cada
        `hop`, del audio `file` (`POST /media/segments`). Lo que no se da toma el valor del
        gestor."""
        form = calls.segments_form(table, k, window, hop, column, session_id)
        response = self._send(calls.segments_call(as_media_file(file), form))
        return calls.traced(SegmentSearch, response)

    def download(self, path: str) -> bytes:
        """El archivo `path` del gestor entero (`GET /media/file`)."""
        return self._send(calls.file_call(path)).content

    def thumbnail(self, path: str) -> bytes:
        """La miniatura JPEG de la imagen `path` (`GET /media/thumbnail`)."""
        return self._send(calls.thumbnail_call(path)).content

    @contextmanager
    def stream_file(
        self, path: str, chunk_bytes: int = DOWNLOAD_CHUNK_BYTES
    ) -> Iterator[Iterator[bytes]]:
        """El archivo `path` por trozos de hasta `chunk_bytes`, sin tenerlo entero en memoria."""
        response = self._send(calls.file_stream_call(path))
        try:
            yield response.iter_bytes(chunk_bytes)
        finally:
            response.close()

    def close_session(self, session_id: str) -> bool:
        """Cierra la sesión y aborta su transacción; `False` si no existía."""
        return bool(self._send(calls.close_session_call(session_id)).json()["closed"])

    @contextmanager
    def transaction(self) -> Iterator[Transaction]:
        """Una transacción en una sesión nueva: `BEGIN` al entrar, `COMMIT` al salir y
        `ROLLBACK` si el bloque lanza una excepción. Al final la sesión se cierra siempre,
        lo que también aborta una transacción que el `ROLLBACK` no alcanzó a deshacer."""
        transaction = Transaction(self, new_session_id())
        try:
            transaction.query(BEGIN_SQL)
            yield transaction
            transaction.query(COMMIT_SQL)
        except Exception:
            self._roll_back(transaction)
            raise
        finally:
            self._close_session_quietly(transaction.session_id)

    def _roll_back(self, transaction: Transaction) -> None:
        if not transaction.in_transaction:
            return
        try:
            transaction.query(ROLLBACK_SQL)
        except MinigestorError as error:
            logger.warning(
                "no se pudo deshacer la transacción de %s (%s); cerrar la sesión la aborta",
                transaction.session_id,
                error,
            )

    def _close_session_quietly(self, session_id: str) -> None:
        """Un cierre fallido no tapa el resultado del bloque: el gestor cierra igual la sesión
        cuando pasa `session_idle_seconds` sin usarse."""
        try:
            self.close_session(session_id)
        except MinigestorError as error:
            logger.warning("no se pudo cerrar la sesión %s: %s", session_id, error)

    def _send(self, call: calls.Call) -> httpx.Response:
        request_id = current_request_id() or new_request_id()
        attempts = self._retry.attempts if call.retryable else 1
        for retry in range(1, attempts):
            try:
                return self._attempt(call, request_id)
            except httpx.TransportError as error:
                calls.log_retry(call, retry, error)
                time.sleep(self._retry.delay_before(retry))
        try:
            return self._attempt(call, request_id)
        except httpx.TransportError as error:
            raise TransportError(
                calls.unreachable_message(call, error), request_id=request_id, attempts=attempts
            ) from error

    def _attempt(self, call: calls.Call, request_id: str) -> httpx.Response:
        request = self._http.build_request(**calls.request_arguments(call, request_id))
        response = self._http.send(request, stream=call.stream)
        if not response.is_error:
            return response
        response.read()
        response.close()
        raise calls.error_of(response, call, request_id)


class Transaction(SyncQueries):
    """Las sentencias de una transacción abierta con `Client.transaction()`.

    `in_transaction` sigue lo que dice el gestor tras cada sentencia: un interbloqueo o una
    espera agotada abortan la transacción aunque el bloque no haya terminado.
    """

    def __init__(self, client: Client, session_id: str) -> None:
        self._client = client
        self.session_id = session_id
        self.in_transaction = False

    def query(
        self, sql: str, params: Params = (), *, idempotent: bool | None = None
    ) -> QueryResult:
        try:
            result = self._client.query(
                sql, params, session_id=self.session_id, idempotent=idempotent
            )
        except EngineError as error:
            if error.in_transaction is not None:
                self.in_transaction = error.in_transaction
            raise
        self.in_transaction = result.in_transaction
        return result
