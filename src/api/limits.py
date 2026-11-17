"""Tamaño máximo del cuerpo de una petición, anunciado o no.

Un cuerpo que anuncia en `Content-Length` más del límite se rechaza sin leerlo. Uno sin
`Content-Length` (enviado por trozos) se cuenta mientras llega y se corta en cuanto pasa:
sin eso, el servidor lo leería entero, a memoria o a un temporal, antes de llegar a la ruta.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import HTTPException
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from api.errors import error_response, request_too_large, upload_too_large
from api.routes.uploads import MULTIPART_ALLOWANCE_BYTES, UPLOAD_PATHS
from config import EngineConfig

CONTENT_LENGTH_HEADER = "content-length"
BodyLimit = tuple[int, HTTPException]


def body_limit_for(path: str, config: EngineConfig) -> BodyLimit:
    """Los bytes que admite la ruta y el error que explica el límite al pasarlos."""
    if path in UPLOAD_PATHS:
        allowed = config.max_upload_bytes + MULTIPART_ALLOWANCE_BYTES
        return allowed, upload_too_large(config.max_upload_bytes)
    return config.max_request_bytes, request_too_large(config.max_request_bytes)


class BodySizeLimit:
    """Middleware ASGI que aplica `limit_for(ruta)` al cuerpo de cada petición HTTP.

    El exceso se avisa lanzando el `HTTPException` desde `receive`: FastAPI deja pasar los
    `HTTPException` que surgen al leer el cuerpo y responde con su forma de siempre.
    """

    def __init__(self, app: ASGIApp, limit_for: Callable[[str], BodyLimit]) -> None:
        self._app = app
        self._limit_for = limit_for

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        limit, error = self._limit_for(scope["path"])
        declared = _declared_length(scope)
        if declared is not None and declared > limit:
            await error_response(error)(scope, receive, send)
            return
        received = 0

        async def counted() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise error
            return message

        await self._app(scope, counted, send)


def _declared_length(scope: Scope) -> int | None:
    """Sin un `Content-Length` válido no se decide aquí: lo decide el conteo."""
    try:
        return int(Headers(scope=scope).get(CONTENT_LENGTH_HEADER, ""))
    except ValueError:
        return None
