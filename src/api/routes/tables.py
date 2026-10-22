"""Panel de archivos e inspección: qué tablas hay, su forma física y sus puntos."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from api.errors import as_http_error
from api.responses import as_response
from api.schemas import QueryResponse, TableInfo, TablePoints
from api.service import ServiceDependency
from errors import DomainError
from sql.nodes import DropTableStatement

router = APIRouter()
SessionQuery = Annotated[
    str | None, Query(description="Sesión del cliente, para leer dentro de su transacción")
]


@router.get("/tables", response_model=list[TableInfo])
def tables(service: ServiceDependency) -> list[TableInfo]:
    return list(service.tables())


@router.get("/tables/{name}/structure")
def table_structure(
    name: str, service: ServiceDependency, session_id: SessionQuery = None
) -> dict[str, Any]:
    """Organización física de la tabla y forma real de sus índices: niveles del B+,
    profundidad global y cubetas del hash. Lee con bloqueo compartido sobre la tabla."""
    with service.session(session_id) as session:
        try:
            return session.inspect([name], lambda: service.engine.describe_table(name))
        except DomainError as error:
            raise as_http_error(error, session.in_transaction) from error


@router.get("/tables/{name}/points", response_model=TablePoints)
def table_points(
    name: str,
    service: ServiceDependency,
    column: Annotated[str | None, Query(description="Columna POINT")] = None,
    limit: Annotated[int | None, Query(ge=1, description="Máximo de puntos")] = None,
    session_id: SessionQuery = None,
) -> TablePoints:
    """Puntos de una columna POINT para el panel de mapa; sin `column`, la primera.

    Con más filas que `limit` devuelve una muestra tomada a intervalos regulares, para
    que el mapa siga siendo fluido y la muestra no se concentre en una zona. Lee con
    bloqueo compartido sobre la tabla.
    """
    with service.session(session_id) as session:
        try:
            return session.inspect([name], lambda: service.points(name, column, limit))
        except DomainError as error:
            raise as_http_error(error, session.in_transaction) from error


@router.delete("/tables", response_model=QueryResponse)
def drop_all_tables(service: ServiceDependency, session_id: SessionQuery = None) -> QueryResponse:
    """Borra todas las tablas y sus archivos. Deja la base de datos vacía.

    Existe para poder empezar una demostración desde cero sin tocar el disco a mano.
    """
    with service.session(session_id) as session:
        names = service.engine.table_names()
        try:
            results = [session.run(DropTableStatement(name=name)) for name in names]
        except DomainError as error:
            raise as_http_error(error, session.in_transaction) from error
        labels = ["DropTable"] * len(results)
        return as_response(
            results, labels, session.in_transaction, service.config.max_result_rows
        )
