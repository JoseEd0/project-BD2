"""Traducción de los errores del motor a respuestas HTTP."""

from __future__ import annotations

from fastapi import HTTPException

from query.catalog import CatalogError
from query.engine import EngineError
from query.expressions import ExpressionError
from query.loader import LoaderError
from query.operators import UnsupportedQueryError
from sql.errors import SqlError
from storage.types import StorageError
from txn import SessionError
from txn.lock_manager import LockError
from txn.transaction import TransactionError

BAD_REQUEST = 400
UPLOAD_ERROR_KIND = "UploadError"

# Solo estos errores son culpa de lo que pidió el usuario y merecen un 400. Cualquier otra
# excepción es un fallo del servidor y debe llegar como 500, no disfrazarse de error suyo.
DOMAIN_ERRORS: tuple[type[Exception], ...] = (
    SqlError,
    StorageError,
    CatalogError,
    ExpressionError,
    LoaderError,
    EngineError,
    UnsupportedQueryError,
    LockError,
    TransactionError,
    SessionError,
)


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
        detail={
            "error": str(error),
            "kind": type(error).__name__,
            "line": line,
            "column": column,
            "in_transaction": in_transaction,
        },
    )


def invalid_request(message: str) -> HTTPException:
    return HTTPException(
        status_code=BAD_REQUEST,
        detail={
            "error": message,
            "kind": UPLOAD_ERROR_KIND,
            "line": None,
            "column": None,
            "in_transaction": None,
        },
    )
