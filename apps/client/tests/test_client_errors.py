"""Cada respuesta de error del gestor como su excepción tipada."""

from __future__ import annotations

import pytest

from minigestor_client import (
    AuthenticationError,
    ConflictError,
    DefinitionMismatchError,
    DomainError,
    NotFoundError,
    PermissionDeniedError,
    ServerError,
    ServiceUnavailableError,
    SqlSyntaxError,
    StatementTimeoutError,
)
from minigestor_client.errors import error_from_response

REQUEST_ID = "pedido-1"


def detail(kind: str, **fields: object) -> dict[str, object]:
    return {"detail": {"error": f"falló: {kind}", "kind": kind, **fields}}


@pytest.mark.parametrize(
    ("status", "kind", "expected"),
    [
        (401, "AuthenticationError", AuthenticationError),
        (403, "PermissionError", PermissionDeniedError),
        (404, "Whatever", NotFoundError),
        (503, "ServiceStoppingError", ServiceUnavailableError),
        (500, "KeyError", ServerError),
        (502, "BadGateway", ServerError),
        (400, "SqlSyntaxError", SqlSyntaxError),
        (400, "SqlLexicalError", SqlSyntaxError),
        (400, "UnknownTableError", NotFoundError),
        (400, "DuplicatePrimaryKeyError", ConflictError),
        (400, "DeadlockError", ConflictError),
        (400, "DefinitionMismatchError", DefinitionMismatchError),
        (400, "StatementTimeoutError", StatementTimeoutError),
        (400, "ExpressionError", DomainError),
        (413, "RequestTooLargeError", DomainError),
        (422, "RequestValidationError", DomainError),
    ],
)
def test_the_status_and_kind_pick_the_exception(status: int, kind: str, expected: type):
    error = error_from_response(status, detail(kind), request_id=REQUEST_ID)
    assert type(error) is expected
    assert (error.status, error.kind, error.request_id) == (status, kind, REQUEST_ID)
    assert error.message == f"falló: {kind}"


def test_a_definition_mismatch_is_a_conflict():
    assert issubclass(DefinitionMismatchError, ConflictError)


def test_a_syntax_error_points_at_its_column_in_the_sql_sent():
    sql = "SELECT *\nFROM t WHERE WHERE"
    body = detail("SqlSyntaxError", line=2, column=14, in_transaction=False)
    error = error_from_response(400, body, request_id=REQUEST_ID, sql=sql)
    assert isinstance(error, SqlSyntaxError)
    assert (error.line, error.column, error.in_transaction) == (2, 14, False)
    assert error.cursor == "FROM t WHERE WHERE\n             ^"


def test_a_syntax_error_without_position_has_no_cursor():
    error = error_from_response(400, detail("SqlSyntaxError"), request_id=REQUEST_ID, sql="x")
    assert isinstance(error, SqlSyntaxError)
    assert error.cursor is None


def test_a_body_that_is_not_the_engine_shape_still_gives_an_error():
    error = error_from_response(502, "<html>bad gateway</html>", request_id=REQUEST_ID)
    assert isinstance(error, ServerError)
    assert error.message == "<html>bad gateway</html>"
    assert error.kind == "ServerError"


def test_a_plain_string_detail_is_the_message():
    error = error_from_response(404, {"detail": "Not Found"}, request_id=REQUEST_ID)
    assert isinstance(error, NotFoundError)
    assert error.message == "Not Found"


def test_an_empty_body_names_the_status():
    error = error_from_response(500, None, request_id=REQUEST_ID)
    assert error.message == "el gestor respondió 500"
