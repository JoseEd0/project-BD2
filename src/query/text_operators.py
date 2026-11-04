"""El camino de acceso de una búsqueda de texto: `TextSearchScan`.

Es un acceso como `IndexLookup` o `SpatialNearestScan`: sustituye al recorrido cuando el
WHERE tiene un `MATCH` sobre una columna con índice invertido. Entrega cada fila con su
puntaje como una columna más, virtual, que `SCORE()` lee, y en orden de puntaje, así que un
`ORDER BY SCORE() DESC` no necesita ordenar. Tras ejecutarse informa del trabajo del
ranking: los términos con su df, los postings leídos y los documentos puntuados.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from itertools import tee

from index.inverted import Ranker, RankingMethod, RankingStats, ScoredDocument
from query.comparison import describe_value
from query.expressions import ColumnSlot, RowLayout
from query.operators import TableOperator, internal_schema
from query.plan import PlanNode
from query.row_store import AddressedRecord
from query.table import Table
from query.text_search import SCORE_COLUMN, TextSearch
from storage.record import Record
from storage.schema import Field
from storage.types import FieldType

SCORE_FIELD = Field(SCORE_COLUMN, FieldType.FLOAT)


class TextSearchScan(TableOperator):
    """Las filas con algún término de la consulta, de mayor a menor puntaje y, a igual
    puntaje, en el orden de su dirección; cada una con su puntaje al final.

    Con `limit` pide al ranking solo los `limit` mejores (top-k con un montículo acotado):
    el planificador se lo da cuando nada entre este acceso y el `LIMIT` puede descartar ni
    reordenar filas. Sin él los saca uno a uno del orden perezoso del ranking, para que un
    filtro encima consuma solo los que necesita.
    """

    def __init__(self, table: Table, alias: str, search: TextSearch, limit: int | None) -> None:
        super().__init__(table, alias)
        self._search = search
        self._limit = limit
        self._stats = RankingStats()
        score = ColumnSlot(alias, SCORE_COLUMN, SCORE_FIELD, virtual=True)
        self._layout = self._layout.concat(RowLayout([score]))
        self._schema = internal_schema([*table.schema.fields, SCORE_FIELD])

    def addressed(self) -> Iterator[AddressedRecord]:
        return (row for row, _ in self._scored())

    def _produce(self) -> Iterator[Record]:
        for (_, record), score in self._scored():
            yield (*self._table.unpack(record), score)

    def _scored(self) -> Iterator[tuple[AddressedRecord, float]]:
        listed, scored = tee(self._documents())
        rows = self._table.addressed_at(document.record_id for document in listed)
        return zip(rows, (document.score for document in scored), strict=True)

    def _documents(self) -> Iterable[ScoredDocument]:
        search = self._search
        ranker = self._ranker()
        if self._limit is None:
            return ranker.ranked(search.query, search.method, search.parameters, self._stats)
        return ranker.top_k(
            search.query, search.method, self._limit, search.parameters, self._stats
        )

    def _ranker(self) -> Ranker:
        return self._table.text_search_index(self._search.index_name).ranker()

    def _plan_node(self) -> PlanNode:
        """Antes de ejecutar ya dice qué términos se buscan y su df, que salen del léxico
        sin leer ninguna lista; después, cuántos postings leyó y cuántos documentos puntuó."""
        search = self._search
        detail = (
            f"{self._table.name}.{search.column} MATCH {describe_value(search.query)} · "
            f"{self._ranking()} · {self._extent()} (índice {search.index_name}, INVERTED)"
        )
        executed = self._actual_rows is not None
        terms = self._stats.document_frequencies if executed else self._ranker().analyzed(
            search.query
        )
        listed = ", ".join(f"{term} (df {frequency})" for term, frequency in terms.items())
        detail = f"{detail} · términos: {listed or 'ninguno'}"
        if executed:
            detail = (
                f"{detail} · {self._stats.postings_read} postings leídos, "
                f"{self._stats.documents_scored} documentos puntuados"
            )
            if self._stats.pruned_from is not None:
                detail = f"{detail}, poda MaxScore desde {self._stats.pruned_from}"
        return PlanNode("TextSearchScan", detail)

    def _ranking(self) -> str:
        parameters = self._search.parameters
        if parameters is None:
            return f"{RankingMethod.TF_IDF.value} + coseno"
        return f"BM25 k1={parameters.k1:g} b={parameters.b:g}"

    def _extent(self) -> str:
        if self._limit is None:
            return "en orden de puntaje, a demanda"
        return f"top {self._limit}"
