"""Panel de archivos e inspección: qué tablas hay, su forma física y sus puntos."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from api.errors import DOMAIN_ERRORS, as_http_error
from api.responses import as_response
from api.schemas import QueryResponse, TableInfo, TablePoints
from api.service import ServiceDependency
from sql.nodes import DropTableStatement

router = APIRouter()


@router.get("/tables", response_model=list[TableInfo])
def tables(service: ServiceDependency) -> list[TableInfo]:
    return list(service.tables())


@router.get("/tables/{name}/structure")
def table_structure(name: str, service: ServiceDependency) -> dict[str, Any]:
    """Organización física de la tabla y forma real de sus índices: niveles del B+,
    profundidad global y cubetas del hash."""
    try:
        return service.engine.describe_table(name)
    except DOMAIN_ERRORS as error:
        raise as_http_error(error, None, None) from error


@router.get("/tables/{name}/points", response_model=TablePoints)
def table_points(
    name: str,
    service: ServiceDependency,
    column: Annotated[str | None, Query(description="Columna POINT")] = None,
    limit: Annotated[int | None, Query(ge=1, description="Máximo de puntos")] = None,
) -> TablePoints:
    """Puntos de una columna POINT para el panel de mapa; sin `column`, la primera.

    Con más filas que `limit` devuelve una muestra tomada a intervalos regulares, para
    que el mapa siga siendo fluido y la muestra no se concentre en una zona.
    """
    try:
        return service.points(name, column, limit)
    except DOMAIN_ERRORS as error:
        raise as_http_error(error, None, None) from error


@router.delete("/tables", response_model=QueryResponse)
def drop_all_tables(service: ServiceDependency, session_id: str | None = None) -> QueryResponse:
    """Borra todas las tablas y sus archivos. Deja la base de datos vacía.

    Existe para poder empezar una demostración desde cero sin tocar el disco a mano.
    """
    session = service.session(session_id)
    names = service.engine.table_names()
    try:
        results = [session.run(DropTableStatement(name=name)) for name in names]
    except DOMAIN_ERRORS as error:
        raise as_http_error(error, None, None) from error
    labels = ["DropTable"] * len(results)
    return as_response(results, labels, session.in_transaction)
