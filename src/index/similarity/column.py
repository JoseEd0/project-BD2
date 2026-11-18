"""Un índice de similitud sobre una columna `VECTOR(n)`: lo que pgvector agrega a PostgreSQL.

Es lo que crea `CREATE INDEX ... USING HNSW (emb)` o `USING IVF (emb)` cuando la columna
guarda vectores ya calculados, como los embeddings de un modelo que corre fuera del
gestor. **Los vectores de la tabla son la fuente de verdad**: no hay vocabulario ni
extracción, y el índice de vectores (`IndexedVectors`) se deriva de ellos.

Como el índice de vectores se guarda al cerrar y no en cada COMMIT, un proceso que termina
sin cerrar deja la tabla al día y el índice sin archivo. Al abrir la tabla de nuevo,
`needs_rows` lo dice y la tabla lo reconstruye con `build` desde sus filas.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path

import numpy as np

from config import EngineConfig
from index.secondary import RowSource
from index.vector import vector_id_of
from index.vector.base import ID_DTYPE, VectorIds
from index.vector.metrics import VECTOR_DTYPE, Vectors
from multimedia import MediaReference
from storage.record import FieldReader
from storage.record_id import RecordId
from storage.types import FieldType, Key

from .base import QueryVector, SimilarityIndex, SimilarityQuery
from .errors import SimilarityIndexError
from .vectors import IndexedVectors, VectorBatches, VectorIndexSettings

# Filas por lote al leer los vectores de la tabla: 4 096 vectores de 384 componentes son
# 6 MiB, y así una tabla de 100 000 no pasa entera por objetos de Python.
VECTOR_BATCH_ROWS = 4_096


class VectorColumnIndex(SimilarityIndex):
    """Índice HNSW o IVF sobre una columna `VECTOR(n)`. Las filas con NULL no entran.

    `rows` da las filas de la tabla con su dirección: es lo que lee la búsqueda exacta con
    otra métrica, que necesita los vectores tal como se guardaron.

    Raises:
        SimilarityIndexError: si la columna no es `VECTOR(n)`.
    """

    def __init__(
        self,
        path: Path,
        serializer: FieldReader,
        column: str,
        config: EngineConfig,
        settings: VectorIndexSettings,
        rows: RowSource,
    ) -> None:
        super().__init__(path, serializer, column, config, settings)
        field = serializer.schema.field_of(column)
        if field.type is not FieldType.VECTOR or field.length is None:
            raise SimilarityIndexError(
                f"la columna '{field.name}' es {field.type.value}, no VECTOR(n)"
            )
        self._dimension = field.length
        self._rows = rows
        self._vectors.load()

    files_of = staticmethod(IndexedVectors.files_of)

    @property
    def needs_rows(self) -> bool:
        """Si el índice no se pudo abrir de su archivo y hay que construirlo con `build`."""
        return not self._vectors.built

    def build(self, rows: Iterable[tuple[bytes, RecordId]]) -> None:
        """Construye el índice de vectores con los de todas las filas, de una vez."""
        self._vectors.rebuild(self._dimension, self._batches(rows))
        self._vectors.save()

    def _add(self, value: Key, record_id: RecordId) -> None:
        self._vectors.add(vector_id_of(record_id), np.asarray(value, dtype=VECTOR_DTYPE))

    def _remove(self, value: Key, record_id: RecordId) -> bool:
        return self._vectors.remove(vector_id_of(record_id))

    def _query_vector(self, query: SimilarityQuery) -> QueryVector:
        if isinstance(query, MediaReference):
            raise SimilarityIndexError(
                f"la columna '{self.column}' es VECTOR({self._dimension}): SIMILAR_TO se "
                "consulta con un vector, no con un archivo"
            )
        return QueryVector(np.asarray(query, dtype=VECTOR_DTYPE), None)

    def _all_vectors(self) -> VectorBatches:
        return self._batches(self._rows())

    def _batches(
        self, rows: Iterable[tuple[bytes, RecordId]]
    ) -> Iterator[tuple[VectorIds, Vectors]]:
        ids: list[int] = []
        vectors: list[Key] = []
        for record, record_id in rows:
            value = self._value_of(record)
            if value is None:
                continue
            ids.append(vector_id_of(record_id))
            vectors.append(value)
            if len(ids) == VECTOR_BATCH_ROWS:
                yield _batch(ids, vectors)
                ids, vectors = [], []
        if ids:
            yield _batch(ids, vectors)


def _batch(ids: list[int], vectors: list[Key]) -> tuple[VectorIds, Vectors]:
    return np.array(ids, dtype=ID_DTYPE), np.array(vectors, dtype=VECTOR_DTYPE)
