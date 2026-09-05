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


class StatementOutcome(BaseModel):
    """Qué pasó con una de las sentencias de un script."""

    sql: str
    message: str = ""
    affected_rows: int = 0
    elapsed_ms: float = 0.0
    returned_rows: int = 0


class QueryResponse(BaseModel):
    """Resultado de un script. Las filas y el plan son los de la última consulta."""

    statements: list[StatementOutcome] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    plan: PlanInfo | None = None
    message: str = ""
    affected_rows: int = 0
    elapsed_ms: float = 0.0
    in_transaction: bool = False


class UploadForm(BaseModel):
    """Parámetros que acompañan al archivo en una carga."""

    name: str = Field(min_length=1, description="Nombre de la tabla a crear")
    organization: str = Field(default="heap", description="heap | sequential | clustered_btree")
    key_column: str | None = Field(default=None, description="Columna clave si se ordena")


class ErrorResponse(BaseModel):
    """Error devuelto al frontend con la posición si el parser la conoce."""

    error: str
    kind: str
    line: int | None = None
    column: int | None = None
    statement_index: int | None = None
