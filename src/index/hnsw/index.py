"""HNSW (Hierarchical Navigable Small World) sobre `faiss.IndexHNSWFlat`.

El grafo de FAISS no admite borrar un nodo: sacarlo rompería los caminos que pasan por él.
Por eso un borrado deja una **lápida**, un bit por nodo que la búsqueda excluye con un
`IDSelector` (el nodo sigue sirviendo de paso, pero nunca sale como resultado). Cuando las
lápidas llegan al 30 % de los nodos, `maintain` reconstruye el grafo con los vivos.

Los nodos tienen identificadores internos consecutivos, los que asigna FAISS; el índice
guarda aparte de qué fila es cada nodo. Así, una fila borrada que vuelve —el `ROLLBACK` de
un `DELETE`— entra como un nodo nuevo, sin chocar con su lápida.
"""

from __future__ import annotations

from typing import Any, Self

import faiss
import numpy as np
import numpy.typing as npt

from config import (
    DEFAULT_HNSW_EF_CONSTRUCTION,
    DEFAULT_HNSW_EF_SEARCH,
    DEFAULT_HNSW_M,
    DEFAULT_VECTOR_EXACT_FILTER_ROWS,
    DEFAULT_VECTOR_REBUILD_FRACTION,
)
from index.vector.base import ID_DTYPE, MISSING_ID, VectorIds, VectorIndex
from index.vector.metrics import VectorMetric, Vectors

NODE_ROWS_ARRAY = "node_rows"
TOMBSTONES_ARRAY = "tombstones"
# FAISS guarda en `levels` el nivel más alto de cada nodo más uno.
FAISS_LEVEL_OFFSET = 1
BASE_LEVEL = 0


class HNSWIndex(VectorIndex):
    """Índice HNSW con `M`, `ef_construction` y `ef_search` configurables."""

    kind = "hnsw"

    def __init__(
        self,
        dimension: int,
        metric: VectorMetric,
        m: int = DEFAULT_HNSW_M,
        ef_construction: int = DEFAULT_HNSW_EF_CONSTRUCTION,
        ef_search: int = DEFAULT_HNSW_EF_SEARCH,
        rebuild_fraction: float = DEFAULT_VECTOR_REBUILD_FRACTION,
        exact_filter_rows: int = DEFAULT_VECTOR_EXACT_FILTER_ROWS,
    ) -> None:
        super().__init__(dimension, metric, exact_filter_rows)
        parameters = {"M": m, "ef_construction": ef_construction, "ef_search": ef_search}
        for name, value in parameters.items():
            if value < 1:
                raise ValueError(f"{name} tiene que ser positivo, no {value}")
        self._m = m
        self._ef_construction = ef_construction
        self._ef_search = ef_search
        self._rebuild_fraction = rebuild_fraction
        self._reset()

    @property
    def count(self) -> int:
        return len(self._node_of_row)

    @property
    def nodes(self) -> int:
        """Nodos del grafo, vivos o con lápida."""
        return int(self._index.ntotal)

    @property
    def tombstones(self) -> int:
        return self.nodes - self.count

    def contains(self, vector_id: int) -> bool:
        return vector_id in self._node_of_row

    def needs_maintenance(self) -> bool:
        return self.nodes > 0 and self.tombstones / self.nodes >= self._rebuild_fraction

    def maintain(self) -> None:
        live = self._live_ids()
        vectors = self._vectors_of(live)
        self._reset()
        self._add(live, vectors)

    def _reset(self) -> None:
        self._index = faiss.IndexHNSWFlat(self.dimension, self._m, self.metric.faiss_metric)
        self._index.hnsw.efConstruction = self._ef_construction
        self._row_of_node: list[int] = []
        self._dead = bytearray()
        self._node_of_row: dict[int, int] = {}
        self._forget_tombstone_selector()

    def _forget_tombstone_selector(self) -> None:
        self._dead_selector: Any = None
        self._dead_bitmap: npt.NDArray[np.uint8] | None = None

    def _add(self, ids: VectorIds, vectors: Vectors) -> None:
        first = self.nodes
        self._index.add(vectors)
        rows = ids.tolist()
        self._row_of_node.extend(rows)
        self._dead.extend(bytes(len(rows)))
        self._node_of_row.update(zip(rows, range(first, first + len(rows)), strict=True))
        self._forget_tombstone_selector()

    def _remove(self, ids: VectorIds) -> None:
        for row in ids.tolist():
            self._dead[self._node_of_row.pop(row)] = True
        self._forget_tombstone_selector()

    def _approximate(
        self, query: Vectors, k: int, effort: int, candidates: VectorIds | None
    ) -> tuple[npt.NDArray[np.float32], VectorIds, int]:
        if candidates is None:
            selector = self._tombstone_selector()
        else:
            selector = faiss.IDSelectorBatch(self._nodes_of(candidates))
        parameters = faiss.SearchParametersHNSW(efSearch=effort, sel=selector)
        faiss.cvar.hnsw_stats.reset()
        raw, nodes = self._index.search(query, k, params=parameters)
        computed = int(faiss.cvar.hnsw_stats.ndis)
        present = nodes[0] >= 0
        rows = np.full(k, MISSING_ID, dtype=ID_DTYPE)
        rows[present] = [self._row_of_node[node] for node in nodes[0][present].tolist()]
        return raw[0], rows, computed

    def _tombstone_selector(self) -> Any:
        """Excluye los nodos con lápida; se rehace solo cuando cambian."""
        if not self.tombstones:
            return None
        if self._dead_selector is None:
            # El selector lee el mapa de bits sin copiarlo: el arreglo se guarda en el índice
            # para que viva tanto como el selector.
            dead = np.frombuffer(self._dead, dtype=bool)
            self._dead_bitmap = np.packbits(dead, bitorder="little")
            bitmap = faiss.IDSelectorBitmap(len(self._dead), faiss.swig_ptr(self._dead_bitmap))
            self._dead_selector = faiss.IDSelectorNot(bitmap)
        return self._dead_selector

    def _nodes_of(self, rows: VectorIds) -> VectorIds:
        return np.fromiter((self._node_of_row[row] for row in rows.tolist()), dtype=ID_DTYPE)

    def _vectors_of(self, ids: VectorIds) -> Vectors:
        return self._index.reconstruct_batch(self._nodes_of(ids))

    def _live_ids(self) -> VectorIds:
        return np.fromiter(self._node_of_row, dtype=ID_DTYPE, count=self.count)

    def _default_effort(self) -> int:
        return self._ef_search

    def _max_effort(self) -> int:
        return max(self.nodes, self._ef_search)

    def _faiss_index(self) -> Any:
        return self._index

    def _settings(self) -> dict[str, Any]:
        return {
            "m": self._m,
            "ef_construction": self._ef_construction,
            "ef_search": self._ef_search,
            "rebuild_fraction": self._rebuild_fraction,
        }

    def _extra_arrays(self) -> dict[str, npt.NDArray[Any]]:
        return {
            NODE_ROWS_ARRAY: np.array(self._row_of_node, dtype=ID_DTYPE),
            TOMBSTONES_ARRAY: np.frombuffer(self._dead, dtype=bool),
        }

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
            m=metadata["m"],
            ef_construction=metadata["ef_construction"],
            ef_search=metadata["ef_search"],
            rebuild_fraction=metadata["rebuild_fraction"],
            exact_filter_rows=exact_filter_rows,
        )
        node_rows = arrays[NODE_ROWS_ARRAY].astype(ID_DTYPE)
        dead = arrays[TOMBSTONES_ARRAY].astype(bool)
        if len(node_rows) != index.ntotal or len(dead) != index.ntotal:
            raise ValueError("los nodos guardados no coinciden con el grafo")
        alive = np.flatnonzero(~dead)
        restored._index = index
        restored._row_of_node = node_rows.tolist()
        restored._dead = bytearray(dead.tobytes())
        restored._node_of_row = dict(zip(node_rows[alive].tolist(), alive.tolist(), strict=True))
        return restored

    def _describe(self) -> dict[str, Any]:
        return {
            "m": self._m,
            "ef_construction": self._ef_construction,
            "ef_search": self._ef_search,
            "nodes": self.nodes,
            "tombstones": self.tombstones,
            "nodes_per_level": self._nodes_per_level(),
            "mean_base_degree": self._mean_base_degree(),
        }

    def _nodes_per_level(self) -> list[int]:
        """Cuántos nodos llegan a cada nivel, del 0 (todos) hacia arriba."""
        if not self.nodes:
            return []
        top_levels = faiss.vector_to_array(self._index.hnsw.levels) - FAISS_LEVEL_OFFSET
        reached = np.bincount(top_levels)[::-1].cumsum()[::-1]
        return [int(nodes) for nodes in reached]

    def _mean_base_degree(self) -> float:
        """Vecinos por nodo en el nivel 0, el que recorren todas las búsquedas."""
        if not self.nodes:
            return 0.0
        hnsw = self._index.hnsw
        neighbors = faiss.vector_to_array(hnsw.neighbors)
        starts = faiss.vector_to_array(hnsw.offsets)[:-1].astype(ID_DTYPE)
        slots = starts[:, None] + np.arange(hnsw.nb_neighbors(BASE_LEVEL))
        return float((neighbors[slots] >= 0).sum() / self.nodes)
