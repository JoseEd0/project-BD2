"""Traducción de los errores del motor a respuestas HTTP.

Todas las respuestas de error tienen la misma forma, que es lo que el frontend lee:

    {"detail": {"error": …, "kind": …, "line": …, "column": …, "in_transaction": …}}

Un `DomainError` es culpa de lo que se pidió y vale un `400`; un cuerpo que no cumple el
esquema de la ruta, un `422`; un archivo más grande que el límite, un `413`. Cualquier otra
excepción es un fallo del servidor y llega como `500`: convertirla en un `400` escondería
el bug detrás de un «error del usuario».
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

BAD_REQUEST = 400
PAYLOAD_TOO_LARGE = 413
UNPROCESSABLE = 422
SERVER_ERROR = 500
UPLOAD_ERROR_KIND = "UploadError"
VALIDATION_ERROR_KIND = "RequestValidationError"


def as_http_error(
    error: Exception,
    in_transaction: bool | None = None,
    line: int | None = None,
    column: int | None = None,
) -> HTTPException:
    """Error del usuario como `400`.

    `in_transaction` dice cómo quedó la sesión: un interbloqueo o una espera agotada abortan
    la transacción, y el cliente tiene que enterarse para no seguir creyéndola abierta.
    """
    return HTTPException(
        status_code=BAD_REQUEST,
        detail=_detail(str(error), type(error).__name__, in_transaction, line, column),
    )


def invalid_request(message: str) -> HTTPException:
    return HTTPException(status_code=BAD_REQUEST, detail=_detail(message, UPLOAD_ERROR_KIND))


def upload_too_large(limit: int) -> HTTPException:
    message = f"el archivo pasa de {limit} bytes, el máximo permitido"
    return HTTPException(
        status_code=PAYLOAD_TOO_LARGE, detail=_detail(message, UPLOAD_ERROR_KIND)
    )


def error_response(error: HTTPException) -> JSONResponse:
    """El error como respuesta, para quien responde antes de llegar a una ruta."""
    return JSONResponse(status_code=error.status_code, content={"detail": error.detail})


def server_failure(error: Exception) -> JSONResponse:
    """Un fallo del servidor como `500` con la forma de siempre, dejando la traza en el log."""
    logging.getLogger(__name__).exception("fallo no previsto atendiendo una petición")
    message = f"error interno del servidor: {error}"
    return JSONResponse(
        status_code=SERVER_ERROR, content={"detail": _detail(message, type(error).__name__)}
    )


async def validation_failed(_: Request, error: Exception) -> JSONResponse:
    """Un cuerpo que no cumple el esquema, con la misma forma que cualquier otro error."""
    problems = error.errors() if isinstance(error, RequestValidationError) else []
    message = "; ".join(_described(problem) for problem in problems) or str(error)
    return JSONResponse(
        status_code=UNPROCESSABLE, content={"detail": _detail(message, VALIDATION_ERROR_KIND)}
    )


def _described(problem: Any) -> str:
    place = ".".join(str(part) for part in problem.get("loc", ()) if part != "body")
    return f"{place}: {problem.get('msg', '')}" if place else str(problem.get("msg", ""))


def _detail(
    message: str,
    kind: str,
    in_transaction: bool | None = None,
    line: int | None = None,
    column: int | None = None,
) -> dict[str, Any]:
    return {
        "error": message,
        "kind": kind,
        "line": line,
        "column": column,
        "in_transaction": in_transaction,
    }
