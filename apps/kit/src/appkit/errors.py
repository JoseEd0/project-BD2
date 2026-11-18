"""Las respuestas de error de un servicio, todas con la misma forma:

    {"error": "…", "kind": "…", "request_id": "…"}

Un servicio lanza las suyas como subclases de `ServiceError`, con su estado HTTP. Un error del
gestor que el servicio no atiende se traduce aquí:

| Error del cliente | Estado | Por qué |
|---|---|---|
| sin respuesta, `503`, `5xx` del gestor | `503` | el gestor no está; reintentar puede servir |
| `401`/`403` del gestor | `500` | la clave del servicio está mal configurada |
| `SqlSyntaxError` | `500` | el SQL lo escribió el servicio: es su bug |
| `StatementTimeoutError` | `504` | el gestor cortó la consulta por su plazo |
| `NotFoundError` | `404` | |
| `ConflictError` | `409` | |
| otro `DomainError` | `400` | un valor del usuario que no cabe, por ejemplo |
| `ResultTruncatedError` | `500` | el servicio no paginó un resultado grande |

En los `5xx` el mensaje del gestor va al log y no a quien llamó: puede mostrar el SQL o la
configuración del servicio.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from http import HTTPStatus
from typing import ClassVar

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from minigestor_client import (
    AuthenticationError,
    ConflictError,
    DomainError,
    EngineError,
    MinigestorError,
    NotFoundError,
    PermissionDeniedError,
    ServerError,
    ServiceUnavailableError,
    SqlSyntaxError,
    StatementTimeoutError,
    TransportError,
    current_request_id,
)

ENGINE_UNAVAILABLE_KIND = "EngineUnavailableError"
ENGINE_ACCESS_KIND = "EngineAccessError"
SERVICE_FAILURE_KIND = "ServiceError"
VALIDATION_KIND = "RequestValidationError"
HTTP_ERROR_KIND = "HTTPError"
ENGINE_UNAVAILABLE_MESSAGE = "el gestor no está disponible; intenta de nuevo en unos segundos"
ENGINE_ACCESS_MESSAGE = "el servicio no tiene acceso al gestor"
SERVICE_FAILURE_MESSAGE = "error interno del servicio"

logger = logging.getLogger(__name__)


class ServiceError(Exception):
    """Un error del servicio que se responde con `status` y el nombre de la clase en `kind`."""

    status: ClassVar[HTTPStatus] = HTTPStatus.BAD_REQUEST

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    @property
    def kind(self) -> str:
        return type(self).__name__


class BadRequestError(ServiceError):
    status = HTTPStatus.BAD_REQUEST


class ResourceNotFoundError(ServiceError):
    status = HTTPStatus.NOT_FOUND


class ResourceConflictError(ServiceError):
    status = HTTPStatus.CONFLICT


@dataclass(frozen=True, slots=True)
class EngineFailure:
    """Cómo responde el servicio a un error del gestor. Sin `kind` ni `message`, pasan los del
    gestor; `log_level` es el del registro que deja."""

    status: HTTPStatus
    kind: str | None = None
    message: str | None = None
    log_level: int = logging.INFO


_UNAVAILABLE = EngineFailure(
    HTTPStatus.SERVICE_UNAVAILABLE,
    ENGINE_UNAVAILABLE_KIND,
    ENGINE_UNAVAILABLE_MESSAGE,
    logging.WARNING,
)
_HIDDEN_FAILURE = EngineFailure(
    HTTPStatus.INTERNAL_SERVER_ERROR, SERVICE_FAILURE_KIND, SERVICE_FAILURE_MESSAGE, logging.ERROR
)
# La primera clase que coincide gana: las subclases van antes que `DomainError`.
ENGINE_FAILURES: tuple[tuple[type[MinigestorError], EngineFailure], ...] = (
    (TransportError, _UNAVAILABLE),
    (ServiceUnavailableError, _UNAVAILABLE),
    (ServerError, _UNAVAILABLE),
    (
        AuthenticationError,
        EngineFailure(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            ENGINE_ACCESS_KIND,
            ENGINE_ACCESS_MESSAGE,
            logging.ERROR,
        ),
    ),
    (
        PermissionDeniedError,
        EngineFailure(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            ENGINE_ACCESS_KIND,
            ENGINE_ACCESS_MESSAGE,
            logging.ERROR,
        ),
    ),
    (SqlSyntaxError, _HIDDEN_FAILURE),
    (StatementTimeoutError, EngineFailure(HTTPStatus.GATEWAY_TIMEOUT, log_level=logging.WARNING)),
    (NotFoundError, EngineFailure(HTTPStatus.NOT_FOUND)),
    (ConflictError, EngineFailure(HTTPStatus.CONFLICT)),
    (DomainError, EngineFailure(HTTPStatus.BAD_REQUEST)),
)


def engine_failure_of(error: MinigestorError) -> EngineFailure:
    """La respuesta para `error`; uno que no está en `ENGINE_FAILURES` es un fallo interno."""
    for error_class, failure in ENGINE_FAILURES:
        if isinstance(error, error_class):
            return failure
    return _HIDDEN_FAILURE


def error_response(status: int, message: str, kind: str, request_id: str | None) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"error": message, "kind": kind, "request_id": request_id}
    )


def unexpected_failure_response(request_id: str) -> JSONResponse:
    """Un `500` para una excepción que ningún manejador atendió; la traza queda en el log."""
    return error_response(
        HTTPStatus.INTERNAL_SERVER_ERROR, SERVICE_FAILURE_MESSAGE, SERVICE_FAILURE_KIND, request_id
    )


def install_error_handlers(app: FastAPI) -> None:
    """Responde con la forma común los errores del servicio, los del gestor, los de
    validación y los HTTP de Starlette (una ruta que no existe, un método no permitido)."""
    app.add_exception_handler(ServiceError, _service_error)
    app.add_exception_handler(MinigestorError, _engine_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(HTTPException, _http_error)


async def _service_error(_: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, ServiceError)
    return error_response(error.status, error.message, error.kind, current_request_id())


async def _engine_error(_: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, MinigestorError)
    failure = engine_failure_of(error)
    kind = failure.kind or _kind_of(error)
    logger.log(failure.log_level, "el gestor respondió %s: %s", type(error).__name__, error.message)
    return error_response(
        failure.status, failure.message or error.message, kind, current_request_id()
    )


async def _validation_error(_: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, RequestValidationError)
    message = "; ".join(_described(problem) for problem in error.errors()) or str(error)
    return error_response(
        HTTPStatus.UNPROCESSABLE_ENTITY, message, VALIDATION_KIND, current_request_id()
    )


async def _http_error(_: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, HTTPException)
    return error_response(
        error.status_code, str(error.detail), HTTP_ERROR_KIND, current_request_id()
    )


def _kind_of(error: MinigestorError) -> str:
    return error.kind if isinstance(error, EngineError) else type(error).__name__


def _described(problem: dict[str, object]) -> str:
    location = problem.get("loc", ())
    parts = location if isinstance(location, tuple | list) else ()
    place = ".".join(str(part) for part in parts if part != "body")
    message = str(problem.get("msg", ""))
    return f"{place}: {message}" if place else message
