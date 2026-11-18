"""Carga masiva de filas: `POST /tables/{name}/rows` con un objeto JSON por línea."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from api.auth import CallerDependency, Role, requires
from api.errors import as_http_error
from api.ingest import LineBatcher, RowLoad
from api.schemas import RowLoadResponse
from api.service import ServiceDependency
from errors import DomainError

ROWS_PATH = "/tables/{name}/rows"
ROWS_PATH_PATTERN = re.compile(r"/tables/[^/]+/rows")
NDJSON_MEDIA_TYPE = "application/x-ndjson"
NDJSON_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "description": (
            'Un objeto JSON por línea, {"columna": valor}, con los valores de los parámetros '
            "$n: número, texto, booleano, null, lista de números (VECTOR) u objeto "
            '{"lat", "lon"} (POINT). Una columna que falta queda en NULL.'
        ),
        "content": {NDJSON_MEDIA_TYPE: {"schema": {"type": "string"}}},
    }
}
DISCONNECTED = "el cliente cortó la conexión"

router = APIRouter()


def is_rows_path(path: str) -> bool:
    return ROWS_PATH_PATTERN.fullmatch(path) is not None


# La ruta es `async` para leer el cuerpo según llega sin ocupar un hilo mientras espera al
# cliente; cada lote se escribe en el pool de hilos, porque puede esperar un bloqueo.
@router.post(
    ROWS_PATH,
    response_model=RowLoadResponse,
    dependencies=[requires(Role.WRITE)],
    openapi_extra=NDJSON_BODY,
)
async def load_rows(
    name: str, request: Request, service: ServiceDependency, caller: CallerDependency
) -> RowLoadResponse:
    """Carga en la tabla las filas del cuerpo, que lee por partes, en lotes de
    `ingest_batch_rows` que entran enteros o no entran.

    Un lote rechazado no detiene la carga: lo dice `rejected`, con la línea culpable, y la
    carga sigue. Si el cliente corta la conexión, los lotes confirmados quedan y el que se
    estaba juntando se descarta; si el servidor se apaga, se deshace el lote en curso y la
    respuesta lo dice en `interrupted`.
    """
    with service.session(caller.service, None) as session:
        try:
            load = await run_in_threadpool(RowLoad, service, session, name)
            await run_in_threadpool(load.start)
        except DomainError as error:
            raise as_http_error(error) from error
        batcher = LineBatcher(
            service.config.max_ingest_line_bytes, service.config.ingest_batch_rows
        )
        try:
            await _stream(request, load, batcher)
        finally:
            response = await run_in_threadpool(load.finish)
    return response


async def _stream(request: Request, load: RowLoad, batcher: LineBatcher) -> None:
    try:
        async for chunk in request.stream():
            for batch in batcher.feed(chunk):
                await run_in_threadpool(load.load, batch)
                if load.stopped:
                    return
        for batch in batcher.finish():
            await run_in_threadpool(load.load, batch)
            if load.stopped:
                return
    except ClientDisconnect:
        load.interrupt(f"{DISCONNECTED} tras la línea {batcher.lines_read}", batcher.pending)
