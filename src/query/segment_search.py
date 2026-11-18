"""La consulta por segmentos de un audio: las filas más parecidas a cada una de sus ventanas.

Es la que necesita la detección de copyright: una canción subida se corta en ventanas de
`window` segundos cada `hop`, y cada una se busca en el índice HNSW o IVF de una columna de
rutas cuyas filas suelen ser tramos (`pista.mp3#t=10,20`). El audio se decodifica una vez y
todas las ventanas se buscan en un solo lote (ver `MediaSimilarityIndex.similar_segments`).

No tiene forma SQL: el FROM del dialecto nombra tablas, no funciones que devuelvan filas, y
la respuesta es una lista de ventanas, cada una con sus vecinos, no una relación sobre la
tabla. La sirve el API (`POST /media/segments`) con los bloqueos compartidos de un SELECT.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from errors import DomainError
from index.similarity import MediaSimilarityIndex
from index.similarity.media import SegmentSearchResult
from query.catalog import IndexDefinition
from query.similarity_search import answers_similarity
from query.table import Table
from sql.nodes import IndexType
from storage.record import Record
from storage.record_id import RecordId


class SegmentSearchError(DomainError):
    """La consulta por segmentos no tiene un índice que la responda o pide demasiadas filas."""


@dataclass(frozen=True, slots=True)
class SegmentRequest:
    """Qué se pide: la columna (o `None`, la única con índice de similitud), cuántos vecinos
    por ventana y las ventanas, en segundos."""

    column: str | None
    k: int
    window: float
    hop: float


@dataclass(frozen=True, slots=True)
class SegmentSearch:
    """Lo que respondió el índice y las filas que nombra.

    Attributes:
        table: la tabla en la que se buscó.
        column: la columna de las rutas, como la declara la tabla.
        index_name: el índice que respondió.
        method: HNSW o IVF.
        request: lo que se pidió.
        result: las ventanas con sus vecinos y lo que costó cada etapa.
        rows: la fila de cada vecino, por su dirección.
    """

    table: Table
    column: str
    index_name: str
    method: IndexType
    request: SegmentRequest
    result: SegmentSearchResult
    rows: dict[RecordId, Record]


def search_segments(
    table: Table, audio: Path, request: SegmentRequest, max_rows: int
) -> SegmentSearch:
    """Las `request.k` filas de `table` más parecidas a cada ventana del audio de `audio`.

    Raises:
        SegmentSearchError: si la tabla no tiene un único índice de similitud sobre rutas
            que la responda (en `request.column`, si se nombra), o si las ventanas por `k`
            pasan de `max_rows` filas.
        SegmentLimitError: si el audio o las ventanas pasan de un límite de la configuración.
        MediaError: si el archivo no es un audio o no se puede leer.
    """
    definition = _searched_index(table, request.column)
    index = table.similarity_index(definition.name)
    if not isinstance(index, MediaSimilarityIndex):
        raise SegmentSearchError(
            f"la columna '{definition.column}' guarda vectores: la consulta por segmentos es "
            "de una columna con rutas de audios"
        )
    result = index.similar_segments(audio, request.window, request.hop, request.k)
    found = len(result.segments) * request.k
    if found > max_rows:
        raise SegmentSearchError(
            f"{len(result.segments)} ventanas con k = {request.k} son {found} filas y la "
            f"respuesta admite {max_rows}: baja k o sube el salto"
        )
    return SegmentSearch(
        table=table,
        column=definition.column,
        index_name=definition.name,
        method=definition.method,
        request=request,
        result=result,
        rows=_rows_of(table, result),
    )


def _searched_index(table: Table, column: str | None) -> IndexDefinition:
    candidates = [
        index
        for index in table.definition.indexes
        if answers_similarity(index) and (column is None or index.column.lower() == column.lower())
    ]
    if len(candidates) == 1:
        return candidates[0]
    where = "" if column is None else f" en la columna '{column}'"
    if not candidates:
        raise SegmentSearchError(
            f"la tabla '{table.name}' no tiene un índice HNSW ni IVF{where}: créalo sobre la "
            f"columna de las rutas, como CREATE INDEX ON {table.name} USING HNSW (ruta)"
        )
    names = ", ".join(f"{index.name} ({index.column})" for index in candidates)
    raise SegmentSearchError(
        f"la tabla '{table.name}' tiene varios índices de similitud{where} ({names}): elige "
        "la columna"
    )


def _rows_of(table: Table, result: SegmentSearchResult) -> dict[RecordId, Record]:
    addresses = list(
        dict.fromkeys(row.record_id for segment in result.segments for row in segment.rows)
    )
    records = (record for _, record in table.addressed_at(addresses))
    return {
        address: table.unpack(record) for address, record in zip(addresses, records, strict=True)
    }
