"""Panel de consultas: ejecuta el SQL del editor."""

from __future__ import annotations

from fastapi import APIRouter

from api.errors import as_http_error
from api.responses import as_response, describe_statement
from api.schemas import QueryRequest, QueryResponse
from api.service import ServiceDependency
from errors import DomainError
from sql import parse_script
from sql.errors import SqlPositionError
from sql.nodes import BeginTransactionStatement
from txn import SessionError

router = APIRouter()


@router.post("/query", response_model=QueryResponse)
def query(request: QueryRequest, service: ServiceDependency) -> QueryResponse:
    """Ejecuta el script del editor y devuelve las filas y el plan de la última consulta.

    Las sentencias corren en orden y el script se detiene en la primera que falle: las
    anteriores ya quedaron aplicadas, igual que en cualquier gestor fuera de una
    transacción. Un `BEGIN` necesita `session_id`: sin sesión no habría dónde guardar la
    transacción entre una petición y la siguiente.
    """
    try:
        statements = parse_script(request.sql)
    except SqlPositionError as error:
        raise as_http_error(error, line=error.line, column=error.column) from error
    if request.session_id is None and any(
        isinstance(statement, BeginTransactionStatement) for statement in statements
    ):
        raise as_http_error(SessionError("BEGIN necesita un session_id para seguir la transacción"))
    with service.session(request.session_id) as session:
        try:
            results = [session.run(statement) for statement in statements]
        except SqlPositionError as error:
            raise as_http_error(
                error, session.in_transaction, error.line, error.column
            ) from error
        except DomainError as error:
            raise as_http_error(error, session.in_transaction) from error
        labels = [describe_statement(statement) for statement in statements]
        return as_response(
            results, labels, session.in_transaction, service.config.max_result_rows
        )
