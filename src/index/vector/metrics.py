"""Métricas de similitud entre vectores: euclidiana, producto punto y coseno.

Las tres responden a la misma pregunta —cuánto se parecen dos vectores— con un puntaje en
el que mayor es siempre más parecido, que es lo que devuelve `SIMILARITY_SCORE()`:

| Métrica | Puntaje | Rango |
|---|---|---|
| euclidiana | `1 / (1 + ‖q - v‖)` | (0, 1] |
| producto punto | `q · v` | cualquier real |
| coseno | `q · v / (‖q‖ ‖v‖)` | [-1, 1] |

FAISS solo conoce dos medidas: la distancia euclidiana al cuadrado (`METRIC_L2`) y el
producto interno (`METRIC_INNER_PRODUCT`). El coseno es el producto interno entre vectores de
norma 1, así que esta métrica normaliza los vectores al guardarlos y la consulta al buscar.
Un vector cero no tiene dirección: se queda en cero y su coseno con cualquier otro es 0.
"""

from __future__ import annotations

from enum import Enum, unique
from typing import TypeAlias

import faiss
import numpy as np
import numpy.typing as npt

from errors import DomainError

Vectors: TypeAlias = npt.NDArray[np.float32]
Scores: TypeAlias = npt.NDArray[np.float64]
VECTOR_DTYPE = np.float32


class UnknownVectorMetricError(DomainError):
    """El nombre no corresponde a ninguna métrica de vectores."""


class InvalidVectorError(DomainError):
    """El vector no tiene la dimensión del índice o trae un valor que no es finito."""


@unique
class VectorMetric(Enum):
    EUCLIDEAN = "euclidean"
    DOT = "dot"
    COSINE = "cosine"

    @property
    def faiss_metric(self) -> int:
        """La medida de FAISS con la que se construye un índice de esta métrica."""
        if self is VectorMetric.EUCLIDEAN:
            return int(faiss.METRIC_L2)
        return int(faiss.METRIC_INNER_PRODUCT)

    def prepare(self, vectors: npt.ArrayLike, dimension: int) -> Vectors:
        """Los vectores como FAISS los guarda: matriz `float32` contigua de `dimension`
        columnas, normalizados si la métrica es el coseno. No modifica la entrada.

        Raises:
            InvalidVectorError: si la dimensión no coincide o algún valor no es finito.
        """
        matrix = np.array(vectors, dtype=VECTOR_DTYPE, ndmin=2, order="C", copy=True)
        if matrix.ndim != 2 or matrix.shape[1] != dimension:
            raise InvalidVectorError(
                f"se esperaban vectores de dimensión {dimension}, no de forma {matrix.shape}"
            )
        if not np.isfinite(matrix).all():
            raise InvalidVectorError("un vector trae un valor infinito o NaN")
        if self is VectorMetric.COSINE:
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            np.divide(matrix, norms, out=matrix, where=norms > 0)
        return matrix

    def scores_from_faiss(self, raw: npt.NDArray[np.float32]) -> Scores:
        """Convierte lo que devuelve una búsqueda de FAISS en el puntaje de la métrica."""
        values = raw.astype(np.float64)
        if self is VectorMetric.EUCLIDEAN:
            return 1.0 / (1.0 + np.sqrt(np.maximum(values, 0.0)))
        return values

    def scores_of_prepared(self, query: Vectors, vectors: Vectors) -> Scores:
        """Puntaje exacto de una consulta ya preparada contra vectores ya preparados."""
        if self is VectorMetric.EUCLIDEAN:
            distances = np.linalg.norm(vectors.astype(np.float64) - query[0], axis=1)
            return 1.0 / (1.0 + distances)
        return vectors.astype(np.float64) @ query[0].astype(np.float64)

    def similarity(self, query: npt.ArrayLike, vectors: npt.ArrayLike) -> Scores:
        """Puntaje exacto de `query` contra cada fila de `vectors`, calculado con numpy.

        Es la definición de la métrica: los índices tienen que dar lo mismo que esto.
        """
        matrix = np.asarray(vectors, dtype=VECTOR_DTYPE)
        dimension = matrix.shape[-1]
        return self.scores_of_prepared(
            self.prepare(query, dimension), self.prepare(matrix, dimension)
        )


_METRICS_BY_NAME: dict[str, VectorMetric] = {
    "euclidean": VectorMetric.EUCLIDEAN,
    "euclidiana": VectorMetric.EUCLIDEAN,
    "l2": VectorMetric.EUCLIDEAN,
    "dot": VectorMetric.DOT,
    "dot_product": VectorMetric.DOT,
    "inner_product": VectorMetric.DOT,
    "producto_punto": VectorMetric.DOT,
    "cosine": VectorMetric.COSINE,
    "coseno": VectorMetric.COSINE,
}


def vector_metric_named(name: str) -> VectorMetric:
    """La métrica de vectores con ese nombre, en inglés o en español y sin importar
    mayúsculas.

    Raises:
        UnknownVectorMetricError: si el nombre no es de ninguna métrica.
    """
    try:
        return _METRICS_BY_NAME[name.strip().lower()]
    except KeyError:
        known = ", ".join(metric.value for metric in VectorMetric)
        raise UnknownVectorMetricError(
            f"métrica de vectores desconocida: {name!r} (se aceptan {known})"
        ) from None
