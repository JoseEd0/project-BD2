"""El índice de vectores de un índice de similitud, al día con su fuente de verdad.

Lo comparten los dos índices de similitud: el de una columna con rutas de archivos, cuyos
vectores salen del almacén de características, y el de una columna `VECTOR(n)`, cuyos
vectores son los de la tabla. En los dos el índice de vectores (`HNSWIndex` o `IVFIndex`,
sobre FAISS) es un **derivado** de esa fuente, y aquí vive lo que eso implica:

- **Se guarda al cerrar, no en cada COMMIT.** El archivo se reescribe entero, y con 100 000
  vectores pesa más de cien megabytes: escribirlo en cada COMMIT haría que un `INSERT` de
  una fila costara una escritura así. El `flush` del COMMIT borra el archivo si quedó viejo
  y `close` lo vuelve a escribir. Si el proceso termina sin cerrar, al abrir no hay archivo
  y el índice se reconstruye desde la fuente, que sí está al día.
- **Los vectores cero quedan fuera del índice** (ver `_with_zeros`).
- **Otra métrica es una búsqueda exacta** contra todos los vectores de la fuente, que se arma
  una vez y se descarta cuando la tabla cambia: el índice se construyó para ordenar con la
  suya.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from config import EngineConfig
from index.hnsw import HNSWIndex
from index.ivf import IVFIndex
from index.vector import (
    FlatIndex,
    VectorHit,
    VectorIndex,
    VectorIndexFileError,
    VectorMetric,
    VectorSearchStats,
)
from index.vector.base import VectorIds
from index.vector.metrics import VECTOR_DTYPE, Vectors
from storage.atomic import write_atomically

from .errors import SimilarityIndexError

ZEROS_SUFFIX = ".ceros.npy"
HNSW_KIND = "hnsw"
IVF_KIND = "ivf"

VectorBatches = Iterable[tuple[VectorIds, Vectors]]
VectorSource = Callable[[], VectorBatches]


@dataclass(frozen=True, slots=True)
class VectorIndexSettings:
    """Cómo se construye el índice de vectores: el método, la métrica y sus parámetros.

    `vocabulary_size`, si se da, reemplaza al de la configuración para una columna de
    rutas; una columna `VECTOR(n)` no tiene vocabulario.
    """

    kind: str
    metric: VectorMetric
    m: int
    ef_construction: int
    ef_search: int
    lists: int | None = None
    probes: int | None = None
    vocabulary_size: int | None = None

    def create(self, dimension: int, config: EngineConfig) -> VectorIndex:
        if self.kind == HNSW_KIND:
            return HNSWIndex(
                dimension,
                self.metric,
                m=self.m,
                ef_construction=self.ef_construction,
                ef_search=self.ef_search,
                rebuild_fraction=config.vector_rebuild_fraction,
                exact_filter_rows=config.vector_exact_filter_rows,
            )
        return IVFIndex(
            dimension,
            self.metric,
            lists=self.lists,
            probes=self.probes,
            rebuild_fraction=config.vector_rebuild_fraction,
            exact_filter_rows=config.vector_exact_filter_rows,
        )

    def load(self, path: Path, config: EngineConfig) -> VectorIndex:
        kind: type[VectorIndex] = HNSWIndex if self.kind == HNSW_KIND else IVFIndex
        return kind.load(path, config.vector_exact_filter_rows)


@dataclass(frozen=True, slots=True)
class VectorSearchOutcome:
    """Las filas que encontró una búsqueda y lo que costó.

    Attributes:
        hits: los vectores más parecidos, de mayor a menor puntaje.
        stats: el trabajo de la búsqueda.
        exact_reason: por qué se comparó contra todos los vectores en vez de usar el
            índice; `None` si se usó.
    """

    hits: tuple[VectorHit, ...]
    stats: VectorSearchStats
    exact_reason: str | None


class IndexedVectors:
    """El índice de vectores de una columna, derivado de `source`.

    `source` da todos los vectores de la fuente de verdad, por lotes, con el identificador
    de su fila; es lo que lee la búsqueda exacta con otra métrica.
    """

    def __init__(
        self,
        path: Path,
        settings: VectorIndexSettings,
        config: EngineConfig,
        source: VectorSource,
    ) -> None:
        self._path = path
        self._settings = settings
        self._config = config
        self._source = source
        self._index: VectorIndex | None = None
        self._zeros: set[int] = set()
        self._saved = False
        self._exact: dict[VectorMetric, FlatIndex] = {}

    @staticmethod
    def files_of(path: Path) -> list[Path]:
        """El índice de vectores y la lista de las filas de vector cero."""
        return [path, zeros_path(path)]

    @property
    def built(self) -> bool:
        return self._index is not None

    def load(self) -> bool:
        """Abre el índice guardado. Devuelve si lo había y se pudo leer; si estaba dañado,
        lo borra para que se reconstruya desde la fuente."""
        if not (self._path.exists() and zeros_path(self._path).exists()):
            return False
        try:
            index = self._settings.load(self._path, self._config)
            zeros = np.load(zeros_path(self._path), allow_pickle=False)
        except (VectorIndexFileError, OSError, ValueError):
            self._path.unlink(missing_ok=True)
            return False
        self._index = index
        self._zeros = set(zeros.tolist())
        self._saved = True
        return True

    def rebuild(self, dimension: int, batches: VectorBatches) -> None:
        """Un índice nuevo con todos los vectores de `batches`, añadidos de una vez: un IVF
        entrena sus listas con lo primero que recibe, y por lotes entrenaría con el primero.
        Los vectores cero quedan fuera (ver `_with_zeros`)."""
        index = self._settings.create(dimension, self._config)
        collected = list(batches)
        self._zeros = set()
        if collected:
            ids = np.concatenate([batch_ids for batch_ids, _ in collected])
            vectors = np.concatenate([batch_vectors for _, batch_vectors in collected])
            empty = ~vectors.any(axis=1)
            self._zeros = set(ids[empty].tolist())
            if (~empty).any():
                index.add(ids[~empty], vectors[~empty])
        self._index = index
        self._changed()

    def add(self, vector_id: int, vector: Vectors) -> None:
        """Añade el vector de una fila.

        Raises:
            InvalidVectorError: si no tiene la dimensión del índice o no es finito.
        """
        index = self._require_index()
        if vector.any():
            index.add([vector_id], vector[None, :])
        else:
            self._zeros.add(vector_id)
        self._changed()

    def remove(self, vector_id: int) -> bool:
        """Quita el vector de una fila. Devuelve si estaba."""
        if vector_id in self._zeros:
            self._zeros.discard(vector_id)
        elif self._index is not None and self._index.contains(vector_id):
            self._index.remove([vector_id])
        else:
            return False
        self._changed()
        return True

    def search(
        self,
        query: Vectors,
        k: int,
        metric: VectorMetric,
        allowed: list[int] | None,
        effort: int | None,
    ) -> VectorSearchOutcome:
        """Los `k` vectores más parecidos a `query` entre los de `allowed` (todos si es
        `None`): en el índice si la métrica es la suya; si no, comparando contra todos."""
        if metric is not self._settings.metric:
            reason = (
                f"el índice ordena con {self._settings.metric.value} y la consulta pide "
                f"{metric.value}"
            )
            exact = self._exact_index(query.shape[-1], metric).search(query, k, allowed=allowed)
            return VectorSearchOutcome(exact.hits, exact.stats, reason)
        found = self._require_index().search(query, k, allowed=allowed, effort=effort)
        hits = self._with_zeros(found.hits, query, k, allowed, metric)
        return VectorSearchOutcome(hits, found.stats, None)

    def describe(self) -> dict[str, Any]:
        described = self._index.describe() if self._index is not None else {}
        return {**described, "zero_vectors": len(self._zeros)}

    def flush(self) -> None:
        if self._index is not None and self._index.needs_maintenance():
            self._index.maintain()
            self._saved = False
        if not self._saved:
            self._path.unlink(missing_ok=True)

    def close(self) -> None:
        if not self._saved and self._index is not None:
            self.save()

    def save(self) -> None:
        self._require_index().save(self._path)
        zeros = np.array(sorted(self._zeros), dtype=np.int64)
        write_atomically(zeros_path(self._path), lambda file: np.save(file, zeros))
        self._saved = True

    def _changed(self) -> None:
        self._saved = False
        self._exact.clear()

    def _with_zeros(
        self,
        hits: tuple[VectorHit, ...],
        query: Vectors,
        k: int,
        allowed: list[int] | None,
        metric: VectorMetric,
    ) -> tuple[VectorHit, ...]:
        """El resultado del índice con las filas de vector cero que le correspondan.

        Una imagen sin descriptores SIFT tiene el vector TF-IDF cero. En un HNSW con la
        euclidiana esos nodos degradan el grafo (con 100 000 fotos, el recall bajaba de
        0,878 a 0,538), así que no entran al índice; y con el coseno un vector cero no tiene
        dirección. Todos tienen el mismo puntaje contra cualquier consulta —coseno y
        producto punto 0, euclidiana `1 / (1 + ‖q‖)`—: se calcula una vez y las k primeras
        se mezclan con lo que devolvió el índice.
        """
        permitted = self._zeros if allowed is None else self._zeros.intersection(allowed)
        if not permitted:
            return hits
        zero = np.zeros((1, query.shape[-1]), dtype=VECTOR_DTYPE)
        score = float(metric.similarity(query, zero)[0])
        candidates = [
            *hits,
            *(VectorHit(vector_id, score) for vector_id in sorted(permitted)[:k]),
        ]
        candidates.sort(key=lambda hit: (-hit.score, hit.vector_id))
        return tuple(candidates[:k])

    def _exact_index(self, dimension: int, metric: VectorMetric) -> FlatIndex:
        if metric not in self._exact:
            exact = FlatIndex(dimension, metric, self._config.vector_exact_filter_rows)
            for ids, vectors in self._source():
                exact.add(ids, vectors)
            self._exact[metric] = exact
        return self._exact[metric]

    def _require_index(self) -> VectorIndex:
        if self._index is None:
            raise SimilarityIndexError(f"el índice {self._path.name} no está construido")
        return self._index


def zeros_path(path: Path) -> Path:
    """Los identificadores de las filas cuyo vector es cero, que no están en el índice."""
    return path.with_name(path.name + ZEROS_SUFFIX)
