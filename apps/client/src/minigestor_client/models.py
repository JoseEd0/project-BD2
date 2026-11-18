"""Las respuestas del gestor como objetos tipados.

Cada modelo nombra en `ENGINE_SCHEMA` el esquema del contrato del gestor
(`apps/client/openapi/engine.json`) del que lee sus campos: un test comprueba que el contrato
los sigue teniendo. Los campos que el gestor añada y aquí no se usen se ignoran.

Las filas llegan como JSON: un `POINT` es `{"lat", "lon"}` y un `VECTOR`, una lista de números.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

Record = dict[str, Any]


class EngineModel(BaseModel):
    """Un mensaje del gestor, inmutable."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    ENGINE_SCHEMA: ClassVar[str]


class TracedModel(EngineModel):
    """Una respuesta entera, con lo que traen sus cabeceras.

    Attributes:
        request_id: el `X-Request-ID` con que respondió el gestor.
        server_timing: los milisegundos de cada etapa de `Server-Timing` (`parse`, `plan`,
            `exec`, `txn`, `serialize`, `total`; solo `total` en las rutas sin SQL).
    """

    request_id: str = ""
    server_timing: dict[str, float] = Field(default_factory=dict)


TRACE_FIELDS = frozenset(TracedModel.model_fields)


class StatementOutcome(EngineModel):
    ENGINE_SCHEMA = "StatementOutcome"

    sql: str
    message: str = ""
    affected_rows: int = 0
    elapsed_ms: float = 0.0
    returned_rows: int = 0


class Plan(EngineModel):
    """Un paso del plan de ejecución; `actual_*` solo con `EXPLAIN ANALYZE`."""

    ENGINE_SCHEMA = "PlanInfo"

    operation: str
    detail: str
    children: list[Plan] = Field(default_factory=list)
    actual_rows: int | None = None
    actual_ms: float | None = None


class SimilarityInfo(EngineModel):
    """Qué buscó el `SIMILAR_TO` de la consulta y en qué columna están los archivos."""

    ENGINE_SCHEMA = "SimilarityInfo"

    table: str
    column: str
    index: str
    method: str
    metric: str
    k: int
    query: str
    media: str


class QueryResult(TracedModel):
    """El resultado de un script: las filas y el plan son los de su última consulta.

    Attributes:
        total_rows: filas que tenía el resultado; `rows` trae como mucho las que el gestor
            devuelve por respuesta (`max_result_rows`).
        in_transaction: si la sesión sigue dentro de una transacción.
    """

    ENGINE_SCHEMA = "QueryResponse"

    statements: list[StatementOutcome] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    total_rows: int = 0
    plan: Plan | None = None
    message: str = ""
    affected_rows: int = 0
    elapsed_ms: float = 0.0
    in_transaction: bool = False
    similarity: SimilarityInfo | None = None

    @property
    def truncated(self) -> bool:
        return self.total_rows > len(self.rows)

    def records(self) -> list[Record]:
        """Cada fila como `{columna: valor}`."""
        return [dict(zip(self.columns, row, strict=True)) for row in self.rows]


class RejectedBatch(EngineModel):
    """Un lote de la carga masiva que no entró: ninguna de sus filas quedó en la tabla."""

    ENGINE_SCHEMA = "RejectedBatch"

    first_line: int
    last_line: int
    rows: int
    line: int | None
    reason: str


class LoadReport(TracedModel):
    """Lo que hizo una carga masiva. Los lotes de `rejected` no entraron y los demás quedaron
    confirmados; tras una interrupción (`interrupted`), la carga se reanuda en la línea
    siguiente a `last_line`."""

    ENGINE_SCHEMA = "RowLoadResponse"

    table: str
    rows_loaded: int
    rows_rejected: int
    batches_committed: int
    batches_rejected: int
    last_line: int
    elapsed_ms: float
    rejected: list[RejectedBatch] = Field(default_factory=list)
    deferred_indexes: list[str] = Field(default_factory=list)
    interrupted: str | None = None


class MediaUpload(EngineModel):
    """El archivo de una consulta, guardado en el gestor: `path` va en `SIMILAR_TO`."""

    ENGINE_SCHEMA = "MediaUploadResponse"

    path: str
    media: str
    name: str


class TimeRange(EngineModel):
    """Un tramo de un audio en segundos; sin `end`, hasta el final."""

    ENGINE_SCHEMA = "TimeRangeInfo"

    start: float
    end: float | None = None


class SegmentNeighbour(EngineModel):
    """Una fila parecida a una ventana: la fila, su archivo sin fragmento, su tramo y su puntaje."""

    ENGINE_SCHEMA = "SegmentNeighbourInfo"

    row: Record
    path: str
    fragment: TimeRange | None = None
    score: float


class Segment(EngineModel):
    """Una ventana del audio consultado y sus vecinos, de mayor a menor puntaje."""

    ENGINE_SCHEMA = "SegmentInfo"

    start: float
    end: float
    neighbours: list[SegmentNeighbour]


class SegmentTimings(EngineModel):
    ENGINE_SCHEMA = "SegmentTimings"

    decode_ms: float
    extraction_ms: float
    search_ms: float


class SegmentSearch(TracedModel):
    """Las filas más parecidas a cada ventana de un audio."""

    ENGINE_SCHEMA = "SegmentSearchResponse"

    table: str
    column: str
    index: str
    method: str
    metric: str
    k: int
    window: float
    hop: float
    duration: float
    columns: list[str]
    segments: list[Segment]
    distance_computations: int
    timings: SegmentTimings


class ColumnInfo(EngineModel):
    ENGINE_SCHEMA = "ColumnInfo"

    name: str
    type: str
    length: int | None = None
    nullable: bool = True
    primary_key: bool = False
    indexed_with: list[str] = Field(default_factory=list)
    unique: bool = False


class TableInfo(EngineModel):
    ENGINE_SCHEMA = "TableInfo"

    name: str
    organization: str
    primary_key: str | None
    row_count: int
    columns: list[ColumnInfo]
    indexes: list[str]


ENGINE_MODELS: tuple[type[EngineModel], ...] = (
    StatementOutcome,
    Plan,
    SimilarityInfo,
    QueryResult,
    RejectedBatch,
    LoadReport,
    MediaUpload,
    TimeRange,
    SegmentNeighbour,
    Segment,
    SegmentTimings,
    SegmentSearch,
    ColumnInfo,
    TableInfo,
)
