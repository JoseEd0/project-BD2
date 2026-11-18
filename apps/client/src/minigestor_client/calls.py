"""Las rutas del gestor que usa el cliente: cómo se arma cada petición y cómo se lee su
respuesta. Lo comparten el cliente síncrono y el asíncrono, que solo difieren en cómo envían.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, TypedDict, TypeVar
from urllib.parse import quote

import httpx

from .context import REQUEST_ID_HEADER
from .errors import EngineError, error_from_response
from .media import MediaFile
from .models import TracedModel
from .statements import is_read_only
from .timing import SERVER_TIMING_HEADER, parse_server_timing
from .values import encode_json

GET = "GET"
POST = "POST"
QUERY_PATH = "/query"
TABLES_PATH = "/tables"
ROWS_PATH = "/tables/{name}/rows"
SESSION_CLOSE_PATH = "/sessions/{identifier}/close"
MEDIA_QUERIES_PATH = "/media/queries"
MEDIA_SEGMENTS_PATH = "/media/segments"
MEDIA_FILE_PATH = "/media/file"
MEDIA_THUMBNAIL_PATH = "/media/thumbnail"
READY_PATH = "/ready"
ENGINE_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        (POST, QUERY_PATH),
        (GET, TABLES_PATH),
        (POST, ROWS_PATH),
        (POST, SESSION_CLOSE_PATH),
        (POST, MEDIA_QUERIES_PATH),
        (POST, MEDIA_SEGMENTS_PATH),
        (GET, MEDIA_FILE_PATH),
        (GET, MEDIA_THUMBNAIL_PATH),
        (GET, READY_PATH),
    }
)
MEDIA_FILE_FIELD = "file"
MEDIA_PATH_PARAMETER = "path"
JSON_CONTENT_TYPE = "application/json"
NDJSON_CONTENT_TYPE = "application/x-ndjson"
CONTENT_TYPE_HEADER = "Content-Type"
AUTHORIZATION_HEADER = "Authorization"
BEARER_PREFIX = "Bearer "

logger = logging.getLogger(__name__)

Body = bytes | Iterator[bytes] | AsyncIterator[bytes]
Model = TypeVar("Model", bound=TracedModel)


class QueryBody(TypedDict):
    """El cuerpo de `POST /query`."""

    sql: str
    params: Sequence[Any]
    session_id: str | None


class SegmentsForm(TypedDict, total=False):
    """Los campos del formulario de `POST /media/segments`, además del archivo."""

    table: str
    column: str
    k: int
    window: float
    hop: float
    session_id: str


@dataclass(frozen=True, slots=True)
class Call:
    """Una petición al gestor.

    `retryable` dice si se puede repetir cuando no llegó respuesta: solo lo que no cambia
    nada en el gestor. `stream` deja el cuerpo de la respuesta sin leer, para quien lo
    consume por partes.
    """

    method: str
    path: str
    retryable: bool
    content: Body | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    query: Mapping[str, str] = field(default_factory=dict)
    form: Mapping[str, str] = field(default_factory=dict)
    upload: MediaFile | None = None
    timeout: httpx.Timeout | None = None
    stream: bool = False
    sql: str | None = None


def authorization_headers(service_key: str | None) -> dict[str, str]:
    return {} if service_key is None else {AUTHORIZATION_HEADER: BEARER_PREFIX + service_key}


def query_call(
    sql: str, params: Sequence[Any], session_id: str | None, idempotent: bool | None
) -> Call:
    """Sin `idempotent`, se reintenta si el script solo lee (`statements.is_read_only`)."""
    body = QueryBody(sql=sql, params=list(params), session_id=session_id)
    return Call(
        POST,
        QUERY_PATH,
        retryable=is_read_only(sql) if idempotent is None else idempotent,
        content=encode_json(body),
        headers={CONTENT_TYPE_HEADER: JSON_CONTENT_TYPE},
        sql=sql,
    )


def rows_call(table: str, chunks: Body, timeout: httpx.Timeout) -> Call:
    """La carga masiva escribe y su cuerpo se consume al enviarlo: nunca se reintenta."""
    return Call(
        POST,
        ROWS_PATH.format(name=quote(table, safe="")),
        retryable=False,
        content=chunks,
        headers={CONTENT_TYPE_HEADER: NDJSON_CONTENT_TYPE},
        timeout=timeout,
    )


def tables_call() -> Call:
    return Call(GET, TABLES_PATH, retryable=True)


def ready_call(timeout: httpx.Timeout | None) -> Call:
    """Una sonda dice cómo está el gestor ahora: un reintento escondería la caída."""
    return Call(GET, READY_PATH, retryable=False, timeout=timeout)


def close_session_call(session_id: str) -> Call:
    """Cerrar una sesión ya cerrada no hace nada: se puede repetir."""
    path = SESSION_CLOSE_PATH.format(identifier=quote(session_id, safe=""))
    return Call(POST, path, retryable=True)


def upload_call(file: MediaFile) -> Call:
    """Subir la consulta solo guarda un archivo nuevo, que el gestor poda: se puede repetir."""
    return Call(POST, MEDIA_QUERIES_PATH, retryable=True, upload=file)


def segments_form(
    table: str,
    k: int | None,
    window: float | None,
    hop: float | None,
    column: str | None,
    session_id: str | None,
) -> SegmentsForm:
    """El formulario sin los campos que no se dan, que el gestor toma de su configuración."""
    form = SegmentsForm(table=table)
    if k is not None:
        form["k"] = k
    if window is not None:
        form["window"] = window
    if hop is not None:
        form["hop"] = hop
    if column is not None:
        form["column"] = column
    if session_id is not None:
        form["session_id"] = session_id
    return form


def segments_call(file: MediaFile, form: SegmentsForm) -> Call:
    fields = {name: str(value) for name, value in form.items()}
    return Call(POST, MEDIA_SEGMENTS_PATH, retryable=True, upload=file, form=fields)


def file_call(path: str) -> Call:
    return Call(GET, MEDIA_FILE_PATH, retryable=True, query={MEDIA_PATH_PARAMETER: path})


def file_stream_call(path: str) -> Call:
    return replace(file_call(path), stream=True)


def thumbnail_call(path: str) -> Call:
    return Call(GET, MEDIA_THUMBNAIL_PATH, retryable=True, query={MEDIA_PATH_PARAMETER: path})


def request_arguments(call: Call, request_id: str) -> dict[str, Any]:
    """Los argumentos de `httpx.Client.build_request` para `call`."""
    arguments: dict[str, Any] = {
        "method": call.method,
        "url": call.path,
        "headers": {REQUEST_ID_HEADER: request_id, **call.headers},
    }
    if call.query:
        arguments["params"] = dict(call.query)
    if call.content is not None:
        arguments["content"] = call.content
    if call.upload is not None:
        upload = call.upload
        arguments["files"] = {MEDIA_FILE_FIELD: (upload.name, upload.content, upload.content_type)}
        arguments["data"] = dict(call.form)
    if call.timeout is not None:
        arguments["timeout"] = call.timeout
    return arguments


def error_of(response: httpx.Response, call: Call, request_id: str) -> EngineError:
    """El error de una respuesta fallida cuyo cuerpo ya se leyó."""
    try:
        body: object = response.json()
    except ValueError:
        body = response.text
    return error_from_response(
        response.status_code, body, request_id=response_id(response, request_id), sql=call.sql
    )


def log_retry(call: Call, retry: int, error: httpx.TransportError) -> None:
    logger.warning("reintento %d de %s %s: %s", retry, call.method, call.path, type(error).__name__)


def unreachable_message(call: Call, error: httpx.TransportError) -> str:
    return f"el gestor no respondió a {call.method} {call.path}: {type(error).__name__} {error}"


def response_id(response: httpx.Response, sent: str) -> str:
    return str(response.headers.get(REQUEST_ID_HEADER, sent))


def traced(model: type[Model], response: httpx.Response) -> Model:
    """La respuesta como `model`, con su `X-Request-ID` y su `Server-Timing`."""
    payload = response.json()
    payload["request_id"] = response.headers.get(REQUEST_ID_HEADER, "")
    payload["server_timing"] = parse_server_timing(response.headers.get(SERVER_TIMING_HEADER))
    return model.model_validate(payload)
