"""Las técnicas de búsqueda que compara el experimento, detrás de una misma interfaz.

- **búsqueda exacta, IVF y HNSW del gestor:** `FlatIndex`, `IVFIndex` y `HNSWIndex`, es
  decir FAISS más la capa del gestor (identificadores, búsqueda que no se queda corta);
- **IVF y HNSW de FAISS directo:** `IndexIVFFlat` e `IndexHNSWFlat` con los mismos
  parámetros, sin el gestor; y el mismo HNSW sin los vectores cero (los objetos de los que
  no sale ningún descriptor), que en la euclidiana se vuelven un nudo del grafo;
- **HNSW de USearch:** `usearch.index.Index` en `float32`, con el mismo `M` y `ef`.

pgvector está en `pgvector.py`. Cada técnica se construye con los vectores ya calculados,
recibe la consulta ya preparada para ella (fuera del tiempo que se mide) y devuelve las
posiciones de sus vecinos en la colección.
"""

from __future__ import annotations

import tracemalloc
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, unique
from pathlib import Path
from typing import Any, Protocol

import faiss
import numpy as np
import numpy.typing as npt

from benchmarks.common import BYTES_PER_KIB
from benchmarks.multimedia.names import (
    ENGINE_HNSW,
    ENGINE_IVF,
    EXACT,
    FAISS_HNSW,
    FAISS_HNSW_WITHOUT_ZEROS,
    FAISS_IVF,
    USEARCH_HNSW,
)
from index.hnsw import HNSWIndex
from index.ivf import IVFIndex
from index.ivf.index import default_list_count, default_probe_count
from index.vector import FlatIndex, VectorIndex, VectorMetric
from index.vector.metrics import Vectors

SAVED_INDEX = "indice.bin"
USEARCH_DTYPE = "f32"
USEARCH_SEARCH_THREADS = 1
USEARCH_METRICS = {
    VectorMetric.EUCLIDEAN: "l2sq",
    VectorMetric.COSINE: "cos",
    VectorMetric.DOT: "ip",
}


@unique
class SearchEffort(Enum):
    """Qué perilla cambia el esfuerzo de una búsqueda aproximada."""

    PROBES = "probes"
    EF_SEARCH = "ef_search"


class BuiltIndex(Protocol):
    """Un índice construido, listo para responder consultas."""

    def prepare(self, query: Vectors) -> Any:
        """La consulta como la recibe `search`; se prepara fuera del tiempo medido."""

    def search(self, prepared: Any, k: int, effort: int | None) -> list[int]:
        """Las posiciones de los `k` vecinos, del más parecido al menos."""

    def footprint(self, workspace: Path) -> dict[str, float]:
        """Memoria y disco del índice, en KiB, con las claves `memoria_kib` y `disco_kib`
        que la técnica sepa medir."""

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class IndexParameters:
    """Los parámetros con que se construyen todos los índices de una medida."""

    metric: VectorMetric
    hnsw_m: int
    hnsw_ef_construction: int
    hnsw_ef_search: int
    threads: int


@dataclass(frozen=True, slots=True)
class TechniqueSpec:
    """Una técnica: cómo se construye y qué esfuerzos admite su búsqueda.

    `traces_python` dice si vale la pena medir la memoria de Python que retiene: solo la
    capa del gestor guarda mapas de identificadores en objetos de Python.
    """

    name: str
    build: Callable[[Vectors], BuiltIndex]
    effort: SearchEffort | None = None
    default_effort: int | None = None
    max_effort: int | None = None
    traces_python: bool = False


class EngineIndex:
    """Un `VectorIndex` del gestor: la consulta va cruda, porque el gestor la prepara."""

    def __init__(self, index: VectorIndex) -> None:
        self._index = index

    def prepare(self, query: Vectors) -> Any:
        return query

    def search(self, prepared: Any, k: int, effort: int | None) -> list[int]:
        return [hit.vector_id for hit in self._index.search(prepared, k, effort=effort).hits]

    def footprint(self, workspace: Path) -> dict[str, float]:
        path = workspace / SAVED_INDEX
        self._index.save(path)
        size = path.stat().st_size
        path.unlink()
        return {"disco_kib": round(size / BYTES_PER_KIB, 1)}

    def close(self) -> None:
        return None


class FaissIndex:
    """Un índice de FAISS sin el gestor; el coseno normaliza los vectores, como el gestor.

    `positions` es la posición en la colección de cada vector del índice, si el índice no
    los tiene todos; sin ella, el vector `i` es el objeto `i`.
    """

    def __init__(
        self,
        index: Any,
        metric: VectorMetric,
        effort: SearchEffort | None,
        positions: npt.NDArray[np.int64] | None = None,
    ) -> None:
        self._index = index
        self._metric = metric
        self._effort = effort
        self._positions = positions

    def prepare(self, query: Vectors) -> Any:
        return self._metric.prepare(query, self._index.d)

    def search(self, prepared: Any, k: int, effort: int | None) -> list[int]:
        _, found = self._index.search(prepared, k, params=self._parameters(effort))
        present = found[0][found[0] >= 0]
        if self._positions is not None:
            present = self._positions[present]
        return [int(position) for position in present]

    def footprint(self, workspace: Path) -> dict[str, float]:
        serialized = faiss.serialize_index(self._index)
        return {"memoria_kib": round(len(serialized) / BYTES_PER_KIB, 1)}

    def close(self) -> None:
        return None

    def _parameters(self, effort: int | None) -> Any:
        if self._effort is SearchEffort.PROBES:
            return faiss.SearchParametersIVF(nprobe=effort)
        if self._effort is SearchEffort.EF_SEARCH:
            return faiss.SearchParametersHNSW(efSearch=effort)
        return None


class USearchIndex:
    """El HNSW de USearch en `float32`; `ef` se fija solo cuando cambia."""

    def __init__(self, index: Any) -> None:
        self._index = index
        self._effort: int | None = None

    def prepare(self, query: Vectors) -> Any:
        return np.ascontiguousarray(query, dtype=np.float32)

    def search(self, prepared: Any, k: int, effort: int | None) -> list[int]:
        if effort != self._effort and effort is not None:
            self._index.expansion_search = effort
            self._effort = effort
        matches = self._index.search(prepared, k, threads=USEARCH_SEARCH_THREADS)
        return [int(key) for key in matches.keys]

    def footprint(self, workspace: Path) -> dict[str, float]:
        path = workspace / SAVED_INDEX
        self._index.save(str(path))
        size = path.stat().st_size
        path.unlink()
        return {
            "memoria_kib": round(self._index.memory_usage / BYTES_PER_KIB, 1),
            "disco_kib": round(size / BYTES_PER_KIB, 1),
        }

    def close(self) -> None:
        self._index.reset()


def engine_techniques(count: int, parameters: IndexParameters) -> list[TechniqueSpec]:
    """La búsqueda exacta, el IVF y el HNSW del gestor sobre `count` vectores."""
    lists = default_list_count(count)
    metric = parameters.metric

    def exact(vectors: Vectors) -> BuiltIndex:
        return EngineIndex(_filled(FlatIndex(vectors.shape[1], metric), vectors))

    def ivf(vectors: Vectors) -> BuiltIndex:
        return EngineIndex(_filled(IVFIndex(vectors.shape[1], metric, lists=lists), vectors))

    def hnsw(vectors: Vectors) -> BuiltIndex:
        index = HNSWIndex(
            vectors.shape[1],
            metric,
            m=parameters.hnsw_m,
            ef_construction=parameters.hnsw_ef_construction,
            ef_search=parameters.hnsw_ef_search,
        )
        return EngineIndex(_filled(index, vectors))

    return [
        TechniqueSpec(EXACT, exact, traces_python=True),
        TechniqueSpec(
            ENGINE_IVF,
            ivf,
            SearchEffort.PROBES,
            default_probe_count(lists),
            lists,
            traces_python=True,
        ),
        TechniqueSpec(
            ENGINE_HNSW,
            hnsw,
            SearchEffort.EF_SEARCH,
            parameters.hnsw_ef_search,
            traces_python=True,
        ),
    ]


def faiss_techniques(count: int, parameters: IndexParameters) -> list[TechniqueSpec]:
    """El IVF y el HNSW de FAISS, con los parámetros del gestor y sin su capa."""
    lists = default_list_count(count)
    metric = parameters.metric

    def ivf(vectors: Vectors) -> BuiltIndex:
        prepared = metric.prepare(vectors, vectors.shape[1])
        quantizer = faiss.IndexFlat(prepared.shape[1], metric.faiss_metric)
        index = faiss.IndexIVFFlat(quantizer, prepared.shape[1], lists, metric.faiss_metric)
        index.train(prepared)
        index.add(prepared)
        return FaissIndex(index, metric, SearchEffort.PROBES)

    def hnsw(vectors: Vectors) -> BuiltIndex:
        prepared = metric.prepare(vectors, vectors.shape[1])
        return FaissIndex(_hnsw_of(prepared, parameters), metric, SearchEffort.EF_SEARCH)

    def hnsw_without_zeros(vectors: Vectors) -> BuiltIndex:
        prepared = metric.prepare(vectors, vectors.shape[1])
        kept = np.flatnonzero(prepared.any(axis=1))
        index = _hnsw_of(np.ascontiguousarray(prepared[kept]), parameters)
        return FaissIndex(index, metric, SearchEffort.EF_SEARCH, kept)

    ef_search = parameters.hnsw_ef_search
    return [
        TechniqueSpec(FAISS_IVF, ivf, SearchEffort.PROBES, default_probe_count(lists), lists),
        TechniqueSpec(FAISS_HNSW, hnsw, SearchEffort.EF_SEARCH, ef_search),
        TechniqueSpec(
            FAISS_HNSW_WITHOUT_ZEROS, hnsw_without_zeros, SearchEffort.EF_SEARCH, ef_search
        ),
    ]


def _hnsw_of(prepared: Vectors, parameters: IndexParameters) -> Any:
    index = faiss.IndexHNSWFlat(
        prepared.shape[1], parameters.hnsw_m, parameters.metric.faiss_metric
    )
    index.hnsw.efConstruction = parameters.hnsw_ef_construction
    index.add(prepared)
    return index


def usearch_technique(parameters: IndexParameters) -> TechniqueSpec:
    """El HNSW de USearch. `usearch` es un extra opcional (`.[usearch]`): se importa solo
    si se mide."""
    from usearch.index import Index

    def hnsw(vectors: Vectors) -> BuiltIndex:
        index = Index(
            ndim=vectors.shape[1],
            metric=USEARCH_METRICS[parameters.metric],
            dtype=USEARCH_DTYPE,
            connectivity=parameters.hnsw_m,
            expansion_add=parameters.hnsw_ef_construction,
            expansion_search=parameters.hnsw_ef_search,
        )
        index.add(np.arange(len(vectors)), vectors, threads=parameters.threads)
        return USearchIndex(index)

    return TechniqueSpec(USEARCH_HNSW, hnsw, SearchEffort.EF_SEARCH, parameters.hnsw_ef_search)


def retained_python_kib(build: Callable[[], BuiltIndex]) -> float:
    """KiB de objetos de Python que siguen vivos al terminar de construir.

    `tracemalloc` ve los objetos de Python y los arreglos de numpy, no la memoria que FAISS
    pide en C++: mide lo que la capa del gestor añade, no la estructura del índice.
    """
    tracemalloc.start()
    try:
        built = build()
        retained, _ = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    built.close()
    return round(retained / BYTES_PER_KIB, 1)


def _filled(index: VectorIndex, vectors: Vectors) -> VectorIndex:
    index.add(np.arange(len(vectors)), vectors)
    return index
