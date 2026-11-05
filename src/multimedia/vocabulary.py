"""Vocabulario visual o auditivo: K palabras que resumen los descriptores locales.

Una imagen da cientos de descriptores SIFT y un audio uno MFCC por ventana; ninguno de los
dos tiene un tamaño fijo. k-means agrupa una muestra de descriptores de toda la colección
en K centroides, las **palabras**, y cada objeto pasa a ser cuántos de sus descriptores
caen en cada palabra: un histograma de dimensión K, comparable entre objetos.

k-means es el de FAISS (`faiss.Kmeans`), y la palabra de cada descriptor sale de una
búsqueda exacta del centroide más cercano sobre `IndexFlatL2`, en un solo lote por objeto.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TypeAlias

import faiss
import numpy as np
import numpy.typing as npt

from errors import DomainError

from .descriptors import DESCRIPTOR_DTYPE, Descriptors

WordCounts: TypeAlias = npt.NDArray[np.float32]
NEAREST_WORD = 1


class NotEnoughDescriptorsError(DomainError):
    """La colección no tiene descriptores suficientes para un vocabulario de ese tamaño."""


class Vocabulary:
    """K centroides de dimensión d y la búsqueda de la palabra de cada descriptor."""

    def __init__(self, centroids: npt.ArrayLike) -> None:
        words = np.ascontiguousarray(centroids, dtype=DESCRIPTOR_DTYPE)
        if words.ndim != 2 or not len(words):
            raise ValueError(f"los centroides tienen que ser una matriz no vacía, no {words.shape}")
        self._centroids = words
        self._nearest = faiss.IndexFlatL2(words.shape[1])
        self._nearest.add(words)

    @classmethod
    def train(cls, descriptors: npt.ArrayLike, size: int, iterations: int, seed: int) -> Vocabulary:
        """Entrena `size` palabras con k-means sobre `descriptors`.

        Raises:
            NotEnoughDescriptorsError: si hay menos descriptores que palabras.
        """
        sample = np.ascontiguousarray(descriptors, dtype=DESCRIPTOR_DTYPE)
        if sample.ndim != 2 or len(sample) < size:
            raise NotEnoughDescriptorsError(
                f"el vocabulario pide {size} palabras y la colección dio "
                f"{len(sample) if sample.ndim == 2 else 0} descriptores"
            )
        kmeans = faiss.Kmeans(sample.shape[1], size, niter=iterations, seed=seed)
        kmeans.train(sample)
        return cls(kmeans.centroids)

    @property
    def size(self) -> int:
        return int(self._centroids.shape[0])

    @property
    def descriptor_dimension(self) -> int:
        return int(self._centroids.shape[1])

    @property
    def centroids(self) -> Descriptors:
        return self._centroids

    def word_counts(self, descriptors: npt.ArrayLike) -> WordCounts:
        """Cuántos descriptores caen en cada palabra; ceros si el objeto no tiene ninguno.

        Raises:
            ValueError: si los descriptores no tienen la dimensión del vocabulario.
        """
        matrix = np.ascontiguousarray(descriptors, dtype=DESCRIPTOR_DTYPE)
        counts = np.zeros(self.size, dtype=np.float32)
        if not matrix.size:
            return counts
        if matrix.ndim != 2 or matrix.shape[1] != self.descriptor_dimension:
            raise ValueError(
                f"descriptores de forma {matrix.shape} para un vocabulario de dimensión "
                f"{self.descriptor_dimension}"
            )
        _, words = self._nearest.search(matrix, NEAREST_WORD)
        counts += np.bincount(words.ravel(), minlength=self.size)
        return counts


def training_sample(
    batches: Iterable[npt.ArrayLike], limit: int, seed: int
) -> Descriptors:
    """Una muestra uniforme de hasta `limit` descriptores de todos los lotes, sin tenerlos
    todos en memoria (muestreo de reservorio, algoritmo R de Vitter).

    Raises:
        ValueError: si los lotes no tienen todos la misma dimensión.
    """
    rng = np.random.default_rng(seed)
    reservoir: Descriptors | None = None
    seen = 0
    for batch in batches:
        rows = np.asarray(batch, dtype=DESCRIPTOR_DTYPE)
        if not rows.size:
            continue
        if reservoir is None:
            reservoir = np.empty((limit, rows.shape[1]), dtype=DESCRIPTOR_DTYPE)
        if rows.ndim != 2 or rows.shape[1] != reservoir.shape[1]:
            raise ValueError("los descriptores de la muestra no tienen todos la misma dimensión")
        seen = _offer(reservoir, rows, seen, rng)
    if reservoir is None:
        return np.empty((0, 0), dtype=DESCRIPTOR_DTYPE)
    return reservoir[: min(seen, limit)].copy()


def _offer(
    reservoir: Descriptors, rows: Descriptors, seen: int, rng: np.random.Generator
) -> int:
    """Pasa `rows` por el reservorio y devuelve cuántos descriptores se han visto."""
    limit = len(reservoir)
    free = max(0, min(limit - seen, len(rows)))
    reservoir[seen : seen + free] = rows[:free]
    positions = np.arange(seen + free, seen + len(rows))
    slots = (rng.random(len(positions)) * (positions + 1)).astype(np.int64)
    kept = slots < limit
    reservoir[slots[kept]] = rows[free:][kept]
    return seen + len(rows)
