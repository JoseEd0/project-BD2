"""Lo común a los índices de vectores: identificadores, búsqueda y persistencia.

Cada índice concreto —exacto, IVF o HNSW— aporta cómo guarda y busca sus vectores en
FAISS. Esta base pone lo que tiene que ser igual en todos:

- **La búsqueda devuelve k filas si existen.** Una búsqueda aproximada puede quedarse
  corta: IVF solo mira las listas que sondea y HNSW, con un filtro, recorre nodos que el
  filtro descarta. Si faltan filas, se repite con el doble de esfuerzo (listas sondeadas o
  `ef`) hasta encontrarlas o agotar el índice, como los *iterative scans* de pgvector 0.8.
- **Un filtro que deja pocas filas se resuelve comparando contra todas ellas.** Es más
  barato que recorrer el índice esquivando las demás, y exacto.
- **El archivo se escribe entero o no se escribe:** un temporal que se renombra encima.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Self, TypeAlias

import faiss
import numpy as np
import numpy.typing as npt

from config import DEFAULT_VECTOR_EXACT_FILTER_ROWS
from storage.atomic import write_atomically
from storage.types import StorageError

from .metrics import Scores, VectorMetric, Vectors

VectorIds: TypeAlias = npt.NDArray[np.int64]
ID_DTYPE = np.int64
MISSING_ID = -1
FORMAT_VERSION = 1
SEARCH_EFFORT_GROWTH = 2
METADATA_ARRAY = "metadata"
FAISS_ARRAY = "faiss"


class VectorIndexFileError(StorageError):
    """El archivo del índice no existe, está dañado o es de otro tipo de índice."""


class DuplicateVectorIdError(ValueError):
    """Se añadió un identificador que el índice ya tiene. Es un fallo de quien lo llama:
    cada fila entra una sola vez."""


@dataclass(frozen=True, slots=True)
class VectorHit:
    """Un vecino encontrado: su identificador y su puntaje (mayor es más parecido)."""

    vector_id: int
    score: float


@dataclass(slots=True)
class VectorSearchStats:
    """Lo que costó una búsqueda, para el plan de ejecución.

    Attributes:
        distance_computations: vectores con los que se comparó la consulta.
        effort: listas sondeadas (IVF) o `ef` (HNSW) de la última pasada.
        rounds: pasadas; más de una si la primera no encontró k filas.
        exact: si se resolvió comparando contra todas las filas candidatas.
    """

    distance_computations: int = 0
    effort: int = 0
    rounds: int = 0
    exact: bool = False


@dataclass(frozen=True, slots=True)
class VectorSearchResult:
    hits: tuple[VectorHit, ...]
    stats: VectorSearchStats = field(default_factory=VectorSearchStats)


class VectorIndex(ABC):
    """Un índice de vectores de dimensión fija con una métrica.

    Los identificadores son enteros no negativos únicos (ver `ids.py`); cada uno entra una
    sola vez mientras siga vivo. Todos los métodos que reciben vectores los validan y
    preparan con la métrica, y los que reciben una consulta devuelven los vecinos de mayor
    a menor puntaje.
    """

    kind: ClassVar[str]

    def __init__(
        self,
        dimension: int,
        metric: VectorMetric,
        exact_filter_rows: int = DEFAULT_VECTOR_EXACT_FILTER_ROWS,
    ) -> None:
        if dimension < 1:
            raise ValueError(f"la dimensión tiene que ser positiva, no {dimension}")
        self._dimension = dimension
        self._metric = metric
        self._exact_filter_rows = exact_filter_rows

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def metric(self) -> VectorMetric:
        return self._metric

    @property
    @abstractmethod
    def count(self) -> int:
        """Vectores vivos."""

    @abstractmethod
    def contains(self, vector_id: int) -> bool:
        """Si el identificador está vivo en el índice."""

    def add(self, ids: npt.ArrayLike, vectors: npt.ArrayLike) -> None:
        """Añade un vector por identificador.

        Raises:
            InvalidVectorError: si un vector no tiene la dimensión o no es finito.
            DuplicateVectorIdError: si un identificador ya está o se repite.
        """
        id_array = _as_ids(ids)
        prepared = self._metric.prepare(vectors, self._dimension)
        if len(id_array) != len(prepared):
            raise ValueError(f"{len(id_array)} identificadores para {len(prepared)} vectores")
        repeated = [int(vector_id) for vector_id in id_array if self.contains(int(vector_id))]
        if repeated or len(np.unique(id_array)) != len(id_array):
            raise DuplicateVectorIdError(f"identificadores repetidos: {repeated[:5]}")
        if len(id_array):
            self._add(id_array, prepared)

    def remove(self, ids: Iterable[int]) -> int:
        """Quita los identificadores vivos de `ids`. Devuelve cuántos quitó."""
        live = np.unique(_as_ids([vector_id for vector_id in ids if self.contains(vector_id)]))
        if len(live):
            self._remove(live)
        return len(live)

    def search(
        self,
        query: npt.ArrayLike,
        k: int,
        *,
        allowed: Iterable[int] | None = None,
        effort: int | None = None,
    ) -> VectorSearchResult:
        """Los `k` vecinos de mayor puntaje de `query`.

        Args:
            query: un vector de la dimensión del índice.
            k: vecinos pedidos; devuelve menos solo si no hay tantos candidatos.
            allowed: si se da, solo pueden salir estos identificadores (el `WHERE`).
            effort: listas sondeadas (IVF) o `ef` (HNSW) de la primera pasada; por omisión,
                el del índice. El índice exacto lo ignora.

        Raises:
            InvalidVectorError: si la consulta no tiene la dimensión o no es finita.
        """
        if k < 1:
            raise ValueError(f"k tiene que ser positivo, no {k}")
        if effort is not None and effort < 1:
            raise ValueError(f"el esfuerzo de búsqueda tiene que ser positivo, no {effort}")
        prepared = self._metric.prepare(query, self._dimension)
        candidates = None if allowed is None else self._live_among(allowed)
        if candidates is not None and len(candidates) <= self._exact_filter_rows:
            return self._exact(prepared, k, candidates)
        wanted = min(k, self.count if candidates is None else len(candidates))
        if wanted == 0:
            return VectorSearchResult(hits=())
        return self._iterative(prepared, k, wanted, candidates, effort or self._default_effort())

    def save(self, path: Path) -> None:
        """Guarda el índice en `path`, reemplazando de una vez lo que hubiera."""
        metadata = {"format": FORMAT_VERSION, "kind": self.kind, "metric": self._metric.value}
        metadata |= {"dimension": self._dimension, **self._settings()}
        arrays = {
            METADATA_ARRAY: np.frombuffer(json.dumps(metadata).encode(), dtype=np.uint8),
            FAISS_ARRAY: faiss.serialize_index(self._faiss_index()),
            **self._extra_arrays(),
        }
        write_atomically(path, lambda file: np.savez(file, **arrays))

    @classmethod
    def load(cls, path: Path, exact_filter_rows: int = DEFAULT_VECTOR_EXACT_FILTER_ROWS) -> Self:
        """Abre un índice guardado con `save`.

        Raises:
            VectorIndexFileError: si el archivo falta, está dañado o es de otro índice.
        """
        try:
            with np.load(path, allow_pickle=False) as saved:
                arrays = {name: saved[name] for name in saved.files}
            metadata = json.loads(arrays.pop(METADATA_ARRAY).tobytes())
            if metadata.get("format") != FORMAT_VERSION or metadata.get("kind") != cls.kind:
                raise VectorIndexFileError(f"{path} no es un índice {cls.kind} de este motor")
            index = faiss.deserialize_index(arrays.pop(FAISS_ARRAY))
            return cls._restore(metadata, index, arrays, exact_filter_rows)
        except (OSError, ValueError, KeyError, RuntimeError, EOFError) as error:
            raise VectorIndexFileError(f"no se puede leer el índice {path}: {error}") from error

    def needs_maintenance(self) -> bool:
        """Si el índice se degradó lo bastante como para reconstruirlo (ver `maintain`)."""
        return False

    def maintain(self) -> None:  # noqa: B027 - el índice exacto no tiene nada que mantener
        """Reconstruye el índice con sus vectores vivos."""

    def describe(self) -> dict[str, Any]:
        """Forma física del índice, para inspeccionarla."""
        return {
            "kind": self.kind,
            "metric": self._metric.value,
            "dimension": self._dimension,
            "vectors": self.count,
            **self._describe(),
        }

    def _iterative(
        self,
        query: Vectors,
        k: int,
        wanted: int,
        candidates: VectorIds | None,
        effort: int,
    ) -> VectorSearchResult:
        stats = VectorSearchStats()
        limit = max(self._max_effort(), effort)
        while True:
            raw, found, computed = self._approximate(query, k, effort, candidates)
            stats.rounds += 1
            stats.distance_computations += computed
            stats.effort = effort
            present = found >= 0
            if present.sum() >= wanted or effort >= limit:
                break
            effort = min(effort * SEARCH_EFFORT_GROWTH, limit)
        if present.sum() < wanted:
            everything = candidates if candidates is not None else self._live_ids()
            return self._exact(query, k, everything)
        scores = self._metric.scores_from_faiss(raw[present])
        return VectorSearchResult(hits=_hits(found[present], scores), stats=stats)

    def _exact(self, query: Vectors, k: int, ids: VectorIds) -> VectorSearchResult:
        stats = VectorSearchStats(distance_computations=len(ids), rounds=1, exact=True)
        if not len(ids):
            return VectorSearchResult(hits=(), stats=stats)
        scores = self._metric.scores_of_prepared(query, self._vectors_of(ids))
        best = np.lexsort((ids, -scores))[:k]
        return VectorSearchResult(hits=_hits(ids[best], scores[best]), stats=stats)

    def _live_among(self, allowed: Iterable[int]) -> VectorIds:
        return np.unique(_as_ids([vector_id for vector_id in allowed if self.contains(vector_id)]))

    @abstractmethod
    def _add(self, ids: VectorIds, vectors: Vectors) -> None: ...

    @abstractmethod
    def _remove(self, ids: VectorIds) -> None: ...

    @abstractmethod
    def _approximate(
        self, query: Vectors, k: int, effort: int, candidates: VectorIds | None
    ) -> tuple[npt.NDArray[np.float32], VectorIds, int]:
        """Una pasada de búsqueda: lo que devuelve FAISS (con `MISSING_ID` donde no hubo
        vecino), los identificadores y cuántas distancias calculó."""

    @abstractmethod
    def _vectors_of(self, ids: VectorIds) -> Vectors:
        """Los vectores guardados de esos identificadores vivos, ya preparados."""

    @abstractmethod
    def _live_ids(self) -> VectorIds: ...

    @abstractmethod
    def _default_effort(self) -> int: ...

    @abstractmethod
    def _max_effort(self) -> int: ...

    @abstractmethod
    def _faiss_index(self) -> Any: ...

    @abstractmethod
    def _settings(self) -> dict[str, Any]:
        """Los parámetros de construcción que se guardan con el índice."""

    @abstractmethod
    def _extra_arrays(self) -> dict[str, npt.NDArray[Any]]:
        """Lo que el índice guarda además de la estructura de FAISS."""

    @classmethod
    @abstractmethod
    def _restore(
        cls,
        metadata: dict[str, Any],
        index: Any,
        arrays: dict[str, npt.NDArray[Any]],
        exact_filter_rows: int,
    ) -> Self: ...

    @abstractmethod
    def _describe(self) -> dict[str, Any]: ...


def _as_ids(ids: npt.ArrayLike) -> VectorIds:
    array = np.asarray(ids, dtype=ID_DTYPE).reshape(-1)
    if (array < 0).any():
        raise ValueError("los identificadores de vector no pueden ser negativos")
    return np.ascontiguousarray(array)


def _hits(ids: VectorIds, scores: Scores) -> tuple[VectorHit, ...]:
    return tuple(
        VectorHit(vector_id=int(vector_id), score=float(score))
        for vector_id, score in zip(ids, scores, strict=True)
    )
