"""Formas de los mensajes que entran y salen del API."""

from __future__ import annotations

from typing import Any, Literal

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
    unique: bool = False


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
    actual_rows: int | None = None
    actual_ms: float | None = None


class PointInfo(BaseModel):
    """Un punto geográfico en grados decimales."""

    lat: float
    lon: float


class OverlayInfo(BaseModel):
    """Una figura de la consulta que el panel de mapa dibuja sobre los puntos.

    `radius` lleva centro, radio y métrica; `nearest`, el punto de referencia de un
    `ORDER BY distancia(…)`; `polygon`, sus vértices.
    """

    kind: Literal["radius", "nearest", "polygon"]
    center: PointInfo | None = None
    radius: float | None = None
    metric: str | None = None
    unit: str | None = None
    vertices: list[PointInfo] = Field(default_factory=list)


class SpatialInfo(BaseModel):
    """Qué tabla y qué columna POINT toca la consulta, y sus figuras."""

    table: str
    column: str
    overlays: list[OverlayInfo] = Field(default_factory=list)


class TablePoints(BaseModel):
    """Puntos de una tabla para pintarla en el mapa; una muestra si la tabla es grande."""

    table: str
    column: str
    total: int = Field(description="Filas de la tabla")
    points: list[tuple[float, float]] = Field(description="Pares (latitud, longitud)")


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
    spatial: SpatialInfo | None = None


class FileUploadResponse(BaseModel):
    """Archivo guardado sin crear tabla: la tabla se crea después con SQL."""

    path: str = Field(description="Ruta para usar en CREATE TABLE ... FROM FILE")
    columns: list[str] = Field(default_factory=list)
