"""Los errores del gestor como excepciones tipadas.

El gestor responde todo error con la misma forma:

    {"detail": {"error": …, "kind": …, "line": …, "column": …, "in_transaction": …}}

`kind` es el nombre de la clase del error en el motor. La clase de aquí sale del estado HTTP
y, en un `400`, de `kind`:

| Estado | `kind` | Excepción |
|---|---|---|
| sin respuesta | — | `TransportError` |
| `401` | — | `AuthenticationError` |
| `403` | — | `PermissionDeniedError` |
| `404` | — | `NotFoundError` |
| `503` | — | `ServiceUnavailableError` |
| `5xx` | — | `ServerError` |
| `4xx` | `SqlSyntaxError`, `SqlLexicalError` | `SqlSyntaxError` |
| `4xx` | `UnknownTableError`, `UnknownIndexError` | `NotFoundError` |
| `4xx` | `DefinitionMismatchError` | `DefinitionMismatchError` |
| `4xx` | claves repetidas, interbloqueos, esperas de bloqueo | `ConflictError` |
| `4xx` | `StatementTimeoutError` | `StatementTimeoutError` |
| `4xx` | cualquier otro | `DomainError` |

Todas llevan el `X-Request-ID` de la petición, para buscarla en los logs del gestor.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

UNAUTHORIZED = 401
FORBIDDEN = 403
NOT_FOUND = 404
FIRST_SERVER_ERROR = 500
SERVICE_UNAVAILABLE = 503
CARET = "^"


class MinigestorError(Exception):
    """Base de todo lo que lanza el cliente al hablar con el gestor.

    Attributes:
        message: la causa, tal como la escribió el gestor.
        request_id: el `X-Request-ID` con que salió la petición.
    """

    def __init__(self, message: str, *, request_id: str) -> None:
        super().__init__(message)
        self.message = message
        self.request_id = request_id


class TransportError(MinigestorError):
    """No llegó ninguna respuesta: la conexión falló o se agotó un plazo, tras `attempts`
    intentos."""

    def __init__(self, message: str, *, request_id: str, attempts: int) -> None:
        super().__init__(message, request_id=request_id)
        self.attempts = attempts


class ResultTruncatedError(MinigestorError):
    """El resultado tenía más filas de las que el gestor devuelve en una respuesta."""


class EngineError(MinigestorError):
    """El gestor respondió con un error.

    Attributes:
        status: el estado HTTP.
        kind: la clase del error en el motor (`detail.kind`).
        line, column: dónde está el problema en el SQL, si el motor lo sabe.
        in_transaction: cómo quedó la sesión; `None` si el error no pasó por una.
    """

    def __init__(
        self,
        message: str,
        *,
        request_id: str,
        status: int,
        kind: str,
        line: int | None = None,
        column: int | None = None,
        in_transaction: bool | None = None,
    ) -> None:
        super().__init__(message, request_id=request_id)
        self.status = status
        self.kind = kind
        self.line = line
        self.column = column
        self.in_transaction = in_transaction


class AuthenticationError(EngineError):
    """`401`: falta la clave del servicio o no es de ningún servicio."""


class PermissionDeniedError(EngineError):
    """`403`: el rol de la clave no alcanza para lo pedido."""


class ServiceUnavailableError(EngineError):
    """`503`: el gestor se está apagando o canceló la sentencia al apagarse."""


class ServerError(EngineError):
    """`5xx`: un fallo del gestor, no de lo que se pidió."""


class DomainError(EngineError):
    """`4xx`: el gestor no puede hacer lo pedido tal como se pidió."""


class SqlSyntaxError(DomainError):
    """El SQL no se pudo leer; `line` y `column` dicen dónde."""

    def __init__(self, message: str, *, sql: str | None = None, **fields: Any) -> None:
        super().__init__(message, **fields)
        self.sql = sql

    @property
    def cursor(self) -> str | None:
        """La línea culpable del SQL enviado con un `^` debajo de la columna; `None` si no se
        conoce la posición o el SQL."""
        if self.sql is None or self.line is None or self.column is None:
            return None
        lines = self.sql.splitlines()
        if not 1 <= self.line <= len(lines):
            return None
        return f"{lines[self.line - 1]}\n{' ' * (self.column - 1)}{CARET}"


class NotFoundError(DomainError):
    """La ruta, la tabla o el índice no existen."""


class ConflictError(DomainError):
    """Choca con lo que ya hay: una clave repetida, un objeto que ya existe, un bloqueo."""


class DefinitionMismatchError(ConflictError):
    """Un `CREATE ... IF NOT EXISTS` encontró el objeto con otra definición."""


class StatementTimeoutError(DomainError):
    """La sentencia pasó del plazo del gestor y se deshizo."""


SYNTAX_KINDS = frozenset({"SqlSyntaxError", "SqlLexicalError"})
NOT_FOUND_KINDS = frozenset({"UnknownTableError", "UnknownIndexError"})
CONFLICT_KINDS = frozenset(
    {
        "DuplicateTableError",
        "DuplicateIndexError",
        "DuplicateError",
        "DuplicatePrimaryKeyError",
        "DuplicateValueError",
        "DeadlockError",
        "LockTimeoutError",
    }
)
_CLASS_BY_KIND: dict[str, type[DomainError]] = {
    **dict.fromkeys(SYNTAX_KINDS, SqlSyntaxError),
    **dict.fromkeys(NOT_FOUND_KINDS, NotFoundError),
    **dict.fromkeys(CONFLICT_KINDS, ConflictError),
    "DefinitionMismatchError": DefinitionMismatchError,
    "StatementTimeoutError": StatementTimeoutError,
}
_CLASS_BY_STATUS: dict[int, type[EngineError]] = {
    UNAUTHORIZED: AuthenticationError,
    FORBIDDEN: PermissionDeniedError,
    NOT_FOUND: NotFoundError,
    SERVICE_UNAVAILABLE: ServiceUnavailableError,
}


def error_from_response(
    status: int, body: object, *, request_id: str, sql: str | None = None
) -> EngineError:
    """La excepción que corresponde a una respuesta de error del gestor.

    `body` es el JSON de la respuesta, o lo que haya llegado si no era JSON (un proxy puede
    responder con HTML); `sql`, el que se envió, para señalar la posición de un error de
    sintaxis.
    """
    detail = _detail_of(body)
    error_class = _class_for(status, detail.get("kind"))
    fields: dict[str, Any] = {
        "request_id": request_id,
        "status": status,
        "kind": _text(detail.get("kind")) or error_class.__name__,
        "line": _integer(detail.get("line")),
        "column": _integer(detail.get("column")),
        "in_transaction": _boolean(detail.get("in_transaction")),
    }
    message = _text(detail.get("error")) or f"el gestor respondió {status}"
    if error_class is SqlSyntaxError:
        return SqlSyntaxError(message, sql=sql, **fields)
    return error_class(message, **fields)


def _class_for(status: int, kind: object) -> type[EngineError]:
    if status in _CLASS_BY_STATUS:
        return _CLASS_BY_STATUS[status]
    if status >= FIRST_SERVER_ERROR:
        return ServerError
    return _CLASS_BY_KIND.get(_text(kind) or "", DomainError)


def _detail_of(body: object) -> Mapping[str, object]:
    detail = body.get("detail") if isinstance(body, Mapping) else body
    if isinstance(detail, Mapping):
        return detail
    if isinstance(detail, str) and detail:
        return {"error": detail}
    return {}


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _boolean(value: object) -> bool | None:
    return value if isinstance(value, bool) else None
