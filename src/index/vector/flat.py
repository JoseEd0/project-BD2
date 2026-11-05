"""Búsqueda exacta: la consulta se compara contra todos los vectores.

Es lo que hace el plan cuando no hay un índice con la métrica pedida, y la verdad contra la
que se mide el Recall@10 de IVF y HNSW. FAISS la resuelve en un solo recorrido con BLAS, y
`IndexIDMap2` le pone los identificadores de las filas y permite borrar de verdad.
"""

from __future__ import annotations

from typing import Any, Self

import faiss
import numpy as np
import numpy.typing as npt

from config import DEFAULT_VECTOR_EXACT_FILTER_ROWS

from .base import ID_DTYPE, VectorIds, VectorIndex
from .metrics import VectorMetric, Vectors

EXACT_EFFORT = 1


class FlatIndex(VectorIndex):
    kind = "flat"

    def __init__(
        self,
        dimension: int,
        metric: VectorMetric,
        exact_filter_rows: int = DEFAULT_VECTOR_EXACT_FILTER_ROWS,
    ) -> None:
        super().__init__(dimension, metric, exact_filter_rows)
        self._index = faiss.IndexIDMap2(faiss.IndexFlat(dimension, metric.faiss_metric))
        self._ids: set[int] = set()

    @property
    def count(self) -> int:
        return len(self._ids)

    def contains(self, vector_id: int) -> bool:
        return vector_id in self._ids

    def _add(self, ids: VectorIds, vectors: Vectors) -> None:
        self._index.add_with_ids(vectors, ids)
        self._ids.update(ids.tolist())

    def _remove(self, ids: VectorIds) -> None:
        self._index.remove_ids(ids)
        self._ids.difference_update(ids.tolist())

    def _approximate(
        self, query: Vectors, k: int, effort: int, candidates: VectorIds | None
    ) -> tuple[npt.NDArray[np.float32], VectorIds, int]:
        selector = None if candidates is None else faiss.IDSelectorBatch(candidates)
        raw, found = self._index.search(query, k, params=faiss.SearchParameters(sel=selector))
        computed = self.count if candidates is None else len(candidates)
        return raw[0], found[0], computed

    def _vectors_of(self, ids: VectorIds) -> Vectors:
        return self._index.reconstruct_batch(ids)

    def _live_ids(self) -> VectorIds:
        return np.fromiter(self._ids, dtype=ID_DTYPE, count=len(self._ids))

    def _default_effort(self) -> int:
        return EXACT_EFFORT

    def _max_effort(self) -> int:
        return EXACT_EFFORT

    def _faiss_index(self) -> Any:
        return self._index

    def _settings(self) -> dict[str, Any]:
        return {}

    def _extra_arrays(self) -> dict[str, npt.NDArray[Any]]:
        return {}

    @classmethod
    def _restore(
        cls,
        metadata: dict[str, Any],
        index: Any,
        arrays: dict[str, npt.NDArray[Any]],
        exact_filter_rows: int,
    ) -> Self:
        restored = cls(metadata["dimension"], VectorMetric(metadata["metric"]), exact_filter_rows)
        restored._index = index
        restored._ids = set(faiss.vector_to_array(index.id_map).tolist())
        return restored

    def _describe(self) -> dict[str, Any]:
        return {}
