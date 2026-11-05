"""IVF (Inverted File Index) sobre `faiss.IndexIVFFlat`.

k-means reparte los vectores en listas, una por centroide; una consulta compara contra los
centroides y recorre solo las `probes` listas más cercanas. Es el mismo esquema que el índice
invertido de texto, con los centroides en lugar de los términos.

- **Listas:** las que se pidan o, por omisión, `√N`, la regla de la wiki de FAISS para
  colecciones de hasta un millón de vectores, sin bajar de 39 vectores de entrenamiento por
  lista, por debajo de lo cual FAISS avisa de que k-means no tiene con qué trabajar.
- **Probes:** por omisión `√listas`, el punto de partida que recomienda pgvector.
- **Borrar** es real: FAISS quita el vector de su lista (`remove_ids`).
- **Las listas envejecen:** se entrenaron con los vectores de un momento. Cuando el número de
  vectores cambia un 30 % desde entonces, `maintain` vuelve a entrenar con los vivos.
"""

from __future__ import annotations

import math
from typing import Any, Self

import faiss
import numpy as np
import numpy.typing as npt

from config import DEFAULT_VECTOR_EXACT_FILTER_ROWS, DEFAULT_VECTOR_REBUILD_FRACTION
from index.vector.base import ID_DTYPE, VectorIds, VectorIndex
from index.vector.metrics import VectorMetric, Vectors

# FAISS pide al menos 39 puntos por centroide para entrenar k-means sin avisar.
MIN_TRAINING_VECTORS_PER_LIST = 39
SINGLE_LIST = 1


def default_list_count(vectors: int) -> int:
    """Listas para `vectors` vectores cuando no se piden: `√N`, sin dejar a ninguna con
    menos vectores de entrenamiento de los que k-means necesita."""
    trainable = max(SINGLE_LIST, vectors // MIN_TRAINING_VECTORS_PER_LIST)
    return max(SINGLE_LIST, min(round(math.sqrt(vectors)), trainable))


def default_probe_count(lists: int) -> int:
    """Listas que sondea una consulta cuando no se pide: `√listas`."""
    return max(SINGLE_LIST, round(math.sqrt(lists)))


class IVFIndex(VectorIndex):
    """Índice IVF con el número de listas y de `probes` configurables."""

    kind = "ivf"

    def __init__(
        self,
        dimension: int,
        metric: VectorMetric,
        lists: int | None = None,
        probes: int | None = None,
        rebuild_fraction: float = DEFAULT_VECTOR_REBUILD_FRACTION,
        exact_filter_rows: int = DEFAULT_VECTOR_EXACT_FILTER_ROWS,
    ) -> None:
        super().__init__(dimension, metric, exact_filter_rows)
        for name, value in (("listas", lists), ("probes", probes)):
            if value is not None and value < 1:
                raise ValueError(f"el número de {name} tiene que ser positivo, no {value}")
        self._requested_lists = lists
        self._requested_probes = probes
        self._rebuild_fraction = rebuild_fraction
        self._index: Any = None
        self._ids: set[int] = set()
        self._trained_on = 0

    @property
    def count(self) -> int:
        return len(self._ids)

    @property
    def lists(self) -> int:
        """Listas del índice entrenado; 0 mientras no haya visto ningún vector."""
        return 0 if self._index is None else int(self._index.nlist)

    @property
    def probes(self) -> int:
        lists = max(self.lists, SINGLE_LIST)
        return min(self._requested_probes or default_probe_count(lists), lists)

    def contains(self, vector_id: int) -> bool:
        return vector_id in self._ids

    def needs_maintenance(self) -> bool:
        if self._index is None:
            return False
        return abs(self.count - self._trained_on) >= self._rebuild_fraction * self._trained_on

    def maintain(self) -> None:
        live = self._live_ids()
        vectors = self._vectors_of(live) if len(live) else None
        self._index = None
        self._ids = set()
        self._trained_on = 0
        if vectors is not None:
            self._add(live, vectors)

    def _add(self, ids: VectorIds, vectors: Vectors) -> None:
        if self._index is None:
            self._index = self._trained_on_vectors(vectors)
            self._trained_on = len(vectors)
        self._index.add_with_ids(vectors, ids)
        self._ids.update(ids.tolist())

    def _trained_on_vectors(self, vectors: Vectors) -> Any:
        lists = min(self._requested_lists or default_list_count(len(vectors)), len(vectors))
        quantizer = faiss.IndexFlat(self.dimension, self.metric.faiss_metric)
        index = faiss.IndexIVFFlat(quantizer, self.dimension, lists, self.metric.faiss_metric)
        if lists == SINGLE_LIST:
            # Con una sola lista no hay nada que agrupar: el centroide es la media.
            quantizer.add(vectors.mean(axis=0, keepdims=True))
            index.is_trained = True
        else:
            index.train(vectors)
        index.set_direct_map_type(faiss.DirectMap.Hashtable)
        return index

    def _remove(self, ids: VectorIds) -> None:
        self._index.remove_ids(ids)
        self._ids.difference_update(ids.tolist())

    def _approximate(
        self, query: Vectors, k: int, effort: int, candidates: VectorIds | None
    ) -> tuple[npt.NDArray[np.float32], VectorIds, int]:
        selector = None if candidates is None else faiss.IDSelectorBatch(candidates)
        parameters = faiss.SearchParametersIVF(nprobe=min(effort, self.lists), sel=selector)
        faiss.cvar.indexIVF_stats.reset()
        raw, found = self._index.search(query, k, params=parameters)
        computed = int(faiss.cvar.indexIVF_stats.ndis) + self.lists
        return raw[0], found[0], computed

    def _vectors_of(self, ids: VectorIds) -> Vectors:
        return np.stack([self._index.reconstruct(vector_id) for vector_id in ids.tolist()])

    def _live_ids(self) -> VectorIds:
        return np.fromiter(self._ids, dtype=ID_DTYPE, count=len(self._ids))

    def _default_effort(self) -> int:
        return self.probes

    def _max_effort(self) -> int:
        return self.lists

    def _faiss_index(self) -> Any:
        if self._index is None:
            return faiss.IndexFlat(self.dimension, self.metric.faiss_metric)
        return self._index

    def _settings(self) -> dict[str, Any]:
        return {
            "lists": self._requested_lists,
            "probes": self._requested_probes,
            "rebuild_fraction": self._rebuild_fraction,
            "trained_on": self._trained_on,
        }

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
        restored = cls(
            metadata["dimension"],
            VectorMetric(metadata["metric"]),
            lists=metadata["lists"],
            probes=metadata["probes"],
            rebuild_fraction=metadata["rebuild_fraction"],
            exact_filter_rows=exact_filter_rows,
        )
        if metadata["trained_on"]:
            restored._index = index
            restored._trained_on = metadata["trained_on"]
            restored._ids = set(restored._stored_ids().tolist())
        return restored

    def _stored_ids(self) -> VectorIds:
        lists = self._index.invlists
        parts = [
            faiss.rev_swig_ptr(lists.get_ids(number), lists.list_size(number)).copy()
            for number in range(self.lists)
            if lists.list_size(number)
        ]
        return np.concatenate(parts) if parts else np.empty(0, dtype=ID_DTYPE)

    def _describe(self) -> dict[str, Any]:
        sizes = (
            [int(self._index.invlists.list_size(number)) for number in range(self.lists)]
            if self._index is not None
            else []
        )
        return {
            "lists": self.lists,
            "probes": self.probes,
            "trained_on": self._trained_on,
            "list_sizes": sizes,
            "imbalance": float(self._index.invlists.imbalance_factor()) if sizes else 0.0,
        }
