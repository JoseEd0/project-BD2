"""Formas de los mensajes que entran y salen del API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    """Una consulta enviada por el editor del frontend."""

    sql: str = Field(min_length=1, description="Sentencia SQL a ejecutar")
    session_id: str | None = Field(
        default=None,
        description="Identificador de sesión; sin él la sentencia va en autocommit",
    )


class ColumnInfo(BaseModel):
    """Una columna, tal como la muestra el panel de archivos."""

    name: str
    type: str
    length: int | None = None
    nullable: bool = True
    primary_key: bool = False
    indexed_with: str | None = None


class TableInfo(BaseModel):
    """Estructura de una tabla para el panel de archivos."""

    name: str
    organization: str
    primary_key: str | None
    row_count: int
    columns: list[ColumnInfo]
    indexes: list[str]


class PlanInfo(BaseModel):
    """Un paso del plan de ejecución."""

    operation: str
    detail: str
    children: list[PlanInfo] = Field(default_factory=list)


class QueryResponse(BaseModel):
    """Resultado de una sentencia."""

    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    plan: PlanInfo | None = None
    message: str = ""
    affected_rows: int = 0
    elapsed_ms: float = 0.0
    in_transaction: bool = False


class ErrorResponse(BaseModel):
    """Error devuelto al frontend con la posición si el parser la conoce."""

    error: str
    kind: str
    line: int | None = None
    column: int | None = None
