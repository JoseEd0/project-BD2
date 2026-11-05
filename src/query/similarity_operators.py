"""El camino de acceso de una búsqueda por similitud: `VectorSearchScan`.

Sustituye al recorrido cuando el WHERE tiene un `SIMILAR_TO`. Extrae y cuantiza el archivo
de la consulta, busca en el índice HNSW o IVF y entrega las k filas más parecidas con su
puntaje como una columna más, virtual, que `SIMILARITY_SCORE()` lee, de mayor a menor.

Si el resto del WHERE filtra la misma tabla, el filtro va **antes**: sus filas son las
únicas que la búsqueda puede devolver, así que la consulta da las k más parecidas que lo
cumplen y no k menos las que el filtro descarta después. Esas filas salen del camino que
el planificador elegiría para ese filtro solo (un índice si lo hay), y aparecen en el plan
como hijo de este acceso.
"""

from __future__ import annotations

from collections.abc import Iterator
from itertools import tee

from index.similarity import SimilarityIndex, SimilarityResult
from query.comparison import describe_value
from query.expressions import ColumnSlot, ExpressionEvaluator, RowLayout, describe_expression
from query.operators import TableOperator, internal_schema
from query.plan import PlanNode
from query.row_store import AddressedRecord
from query.similarity_search import SIMILARITY_COLUMN, SimilaritySearch
from query.table import Table
from sql.nodes import Expression, IndexType
from storage.record import Record
from storage.record_id import RecordId
from storage.schema import Field
from storage.types import FieldType

SIMILARITY_FIELD = Field(SIMILARITY_COLUMN, FieldType.FLOAT)
EFFORT_NAMES = {IndexType.HNSW: "ef_search", IndexType.IVF: "probes"}


class VectorSearchScan(TableOperator):
    """Las k filas más parecidas al archivo de la consulta, de mayor a menor puntaje, cada
    una con su puntaje al final.

    `prefilter` es el acceso y la condición del resto del WHERE sobre esta tabla, si la
    hay: la búsqueda solo puede devolver sus filas.
    """

    def __init__(
        self,
        table: Table,
        alias: str,
        search: SimilaritySearch,
        prefilter: tuple[TableOperator, Expression] | None,
    ) -> None:
        super().__init__(table, alias)
        self._search = search
        self._prefilter = prefilter
        self._result: SimilarityResult | None = None
        self._candidates: int | None = None
        if prefilter is not None:
            ExpressionEvaluator(self._layout).validate_condition(prefilter[1])
        score = ColumnSlot(alias, SIMILARITY_COLUMN, SIMILARITY_FIELD, virtual=True)
        self._layout = self._layout.concat(RowLayout([score]))
        self._schema = internal_schema([*table.schema.fields, SIMILARITY_FIELD])

    def addressed(self) -> Iterator[AddressedRecord]:
        return (row for row, _ in self._scored())

    def _produce(self) -> Iterator[Record]:
        for (_, record), score in self._scored():
            yield (*self._table.unpack(record), score)

    def _scored(self) -> Iterator[tuple[AddressedRecord, float]]:
        found = self._found().rows
        listed, scored = tee(found)
        rows = self._table.addressed_at(row.record_id for row in listed)
        return zip(rows, (row.score for row in scored), strict=True)

    def _found(self) -> SimilarityResult:
        search = self._search
        self._result = self._index().similar(
            search.query_path, search.k, search.metric, self._allowed(), search.effort
        )
        return self._result

    def _allowed(self) -> list[RecordId] | None:
        """Las filas que cumplen el resto del WHERE, o `None` si no hay filtro."""
        if self._prefilter is None:
            return None
        path, condition = self._prefilter
        evaluator = ExpressionEvaluator(RowLayout.of_table(self._table.schema, self._alias()))
        allowed = [
            address
            for address, record in path.addressed()
            if address is not None and evaluator.matches(condition, self._table.unpack(record))
        ]
        self._candidates = len(allowed)
        return allowed

    def _alias(self) -> str:
        return self._search.reference

    def _index(self) -> SimilarityIndex:
        return self._table.similarity_index(self._search.index_name)

    def _plan_node(self) -> PlanNode:
        """Antes de ejecutar dice qué se busca y cómo; después, cuánto costó: la extracción
        de la consulta, las distancias calculadas y, si las hubo, las pasadas extra o la
        búsqueda exacta y por qué."""
        search = self._search
        effort = EFFORT_NAMES[search.method]
        chosen = "el del índice" if search.effort is None else str(search.effort)
        detail = (
            f"{self._table.name}.{search.column} SIMILAR_TO {describe_value(search.query)} · "
            f"k={search.k} · {search.method.value} {search.metric.value} · {effort} {chosen} "
            f"(índice {search.index_name})"
        )
        children: tuple[PlanNode, ...] = ()
        if self._prefilter is not None:
            path, condition = self._prefilter
            detail = f"{detail} · filtro antes de buscar: {describe_expression(condition)}"
            children = (path.plan(),)
        if self._result is not None:
            detail = f"{detail} · {self._executed(self._result)}"
        return PlanNode("VectorSearchScan", detail, children)

    def _executed(self, result: SimilarityResult) -> str:
        stats = result.stats
        parts = [
            f"consulta extraída en {result.extraction_ms:.1f} ms",
            f"{stats.distance_computations} distancias calculadas",
        ]
        if self._candidates is not None:
            parts.append(f"{self._candidates} filas cumplen el filtro")
        if stats.rounds > 1:
            parts.append(f"{stats.rounds} pasadas hasta {stats.effort}")
        if result.exact_reason is not None:
            parts.append(f"búsqueda exacta: {result.exact_reason}")
        elif stats.exact:
            parts.append("búsqueda exacta sobre las filas del filtro")
        return " · ".join(parts)
