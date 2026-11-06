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
    indexed_with: list[str] = Field(default_factory=list)
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


class TextSearchInfo(BaseModel):
    """Dónde buscó el `MATCH` de la consulta y con qué términos, para resaltarlos."""

    table: str
    column: str
    query: str = Field(description="La consulta tal como se escribió")
    method: Literal["TF_IDF", "BM25"]
    terms: list[str] = Field(
        default_factory=list,
        description=(
            "Términos ya analizados: plegados, sin stopwords y reducidos a su raíz; de una "
            "consulta booleana, solo los positivos"
        ),
    )
    k1: float | None = Field(default=None, description="El k1 con que puntuó BM25")
    b: float | None = Field(default=None, description="El b con que puntuó BM25")


class SimilarityInfo(BaseModel):
    """Qué buscó el `SIMILAR_TO` de la consulta y en qué columna están los archivos, para
    mostrarlos como galería."""

    table: str
    column: str = Field(description="La columna con las rutas de los archivos")
    index: str
    method: str = Field(description="HNSW o IVF")
    metric: Literal["euclidean", "dot", "cosine"]
    k: int
    query: str = Field(description="La ruta de la consulta tal como se escribió")
    media: Literal["image", "audio"]


class MediaUploadResponse(BaseModel):
    """El archivo de una consulta, guardado para usarlo en `SIMILAR_TO`."""

    path: str = Field(description="La ruta que va en SIMILAR_TO('...')")
    media: Literal["image", "audio"]
    name: str = Field(description="El nombre con que se subió")


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
    total_rows: int = Field(
        default=0, description="Filas del resultado; puede haber más que las que trae `rows`"
    )
    plan: PlanInfo | None = None
    message: str = ""
    affected_rows: int = 0
    elapsed_ms: float = 0.0
    in_transaction: bool = False
    spatial: SpatialInfo | None = None
    text: TextSearchInfo | None = None
    similarity: SimilarityInfo | None = None


class FileUploadResponse(BaseModel):
    """Archivo guardado sin crear tabla: la tabla se crea después con SQL."""

    path: str = Field(description="Ruta para usar en CREATE TABLE ... FROM FILE")
    columns: list[str] = Field(default_factory=list)
