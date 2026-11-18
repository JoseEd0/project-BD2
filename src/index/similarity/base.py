"""Lo común a los índices de similitud: un índice secundario que responde `SIMILAR_TO`.

Hay dos, según lo que guarda la columna: rutas de imágenes o audios (`MediaSimilarityIndex`),
cuyos vectores se extraen de los archivos, o vectores ya calculados (`VectorColumnIndex`),
como los embeddings de un modelo. Los dos mantienen un índice de vectores derivado de su
fuente (`IndexedVectors`) y buscan igual en él; cada uno aporta de dónde salen los vectores
y cómo se convierte la consulta en uno.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias

from config import EngineConfig
from index.secondary import ColumnIndex
from index.vector import VectorMetric, VectorSearchStats, record_id_of, vector_id_of
from index.vector.metrics import Vectors
from storage.record import FieldReader
from storage.record_id import RecordId
from storage.types import Key

from .errors import SimilarityEqualityError
from .vectors import IndexedVectors, VectorBatches, VectorIndexSettings

# La consulta de un `SIMILAR_TO`: el archivo de una imagen o un audio, o un vector.
SimilarityQuery: TypeAlias = Path | tuple[float, ...]


@dataclass(frozen=True, slots=True)
class SimilarRow:
    record_id: RecordId
    score: float


@dataclass(frozen=True, slots=True)
class QueryVector:
    """La consulta convertida en vector y lo que tardó extraerla de su archivo; `None` si la
    consulta ya era un vector."""

    vector: Vectors
    extraction_ms: float | None


@dataclass(frozen=True, slots=True)
class SimilarityResult:
    """Lo que devolvió un `SIMILAR_TO` y lo que costó, para el plan.

    Attributes:
        rows: las filas más parecidas, de mayor a menor puntaje.
        stats: el trabajo de la búsqueda en el índice de vectores.
        metric: la métrica con que se puntuó.
        exact_reason: por qué se comparó contra todas las filas en vez de usar el índice;
            `None` si se usó.
        extraction_ms: lo que tardó extraer y cuantizar la consulta; `None` si la consulta
            ya era un vector.
    """

    rows: tuple[SimilarRow, ...]
    stats: VectorSearchStats
    metric: VectorMetric
    exact_reason: str | None
    extraction_ms: float | None


class SimilarityIndex(ColumnIndex):
    """Índice HNSW o IVF sobre una columna, consultado con `SIMILAR_TO`."""

    def __init__(
        self,
        path: Path,
        serializer: FieldReader,
        column: str,
        config: EngineConfig,
        settings: VectorIndexSettings,
    ) -> None:
        super().__init__(serializer, column)
        self._column = column
        self._path = path
        self._settings = settings
        self._vectors = IndexedVectors(path, settings, config, self._all_vectors)

    @property
    def settings(self) -> VectorIndexSettings:
        return self._settings

    @property
    def column(self) -> str:
        return self._column

    def similar(
        self,
        query: SimilarityQuery,
        k: int,
        metric: VectorMetric | None = None,
        allowed: Iterable[RecordId] | None = None,
        effort: int | None = None,
    ) -> SimilarityResult:
        """Las `k` filas más parecidas a `query`, entre las de `allowed` si se da.

        Con la métrica del índice busca en él; con otra, compara contra todas las filas, ya
        que el índice se construyó para ordenar con la suya.

        Raises:
            SimilarityIndexError: si la consulta no es de lo que guarda la columna.
            InvalidVectorError: si un vector de consulta no tiene la dimensión de la columna.
            MediaError: si el archivo de la consulta no se puede leer.
        """
        chosen = metric or self._settings.metric
        prepared = self._query_vector(query)
        ids = None if allowed is None else [vector_id_of(record_id) for record_id in allowed]
        found = self._vectors.search(prepared.vector, k, chosen, ids, effort)
        rows = tuple(SimilarRow(record_id_of(hit.vector_id), hit.score) for hit in found.hits)
        return SimilarityResult(
            rows, found.stats, chosen, found.exact_reason, prepared.extraction_ms
        )

    def describe(self) -> dict[str, Any]:
        return {
            "kind": self._settings.kind,
            **self._vectors.describe(),
            "metric": self._settings.metric.value,
        }

    def flush(self) -> None:
        self._vectors.flush()

    def close(self) -> None:
        self._vectors.close()

    def _find(self, value: Key) -> list[RecordId]:
        raise SimilarityEqualityError(
            f"un índice {self._settings.kind.upper()} no responde igualdades: se consulta con "
            "SIMILAR_TO"
        )

    @abstractmethod
    def _query_vector(self, query: SimilarityQuery) -> QueryVector:
        """La consulta como un vector de la dimensión del índice.

        Raises:
            SimilarityIndexError: si la consulta no es de lo que guarda la columna.
        """

    @abstractmethod
    def _all_vectors(self) -> VectorBatches:
        """Todos los vectores de la fuente de verdad, por lotes, con el identificador de su
        fila: los que compara la búsqueda exacta con otra métrica."""
