"""Qué tan buena es una respuesta: Recall@k contra la búsqueda exacta y vecinos de la clase.

- **Recall@k** mide al índice: qué parte de sus k vecinos está entre los k verdaderos. La
  verdad se calcula aparte, con `VectorMetric.similarity` en `float64` sobre todos los
  vectores, sin pasar por ninguna de las bibliotecas que se comparan. Un vecino cuenta si
  su puntaje exacto llega al k-ésimo mejor: dos fotos con el mismo vector empatan, y un
  índice que devuelve cualquiera de las dos acierta.
- **Precisión por clase@k** mide la representación: qué parte de los k vecinos es de la
  categoría (ABO) o de la palabra (Speech Commands) de la consulta. Es lo que distingue a
  la euclidiana del coseno, porque la búsqueda exacta tiene Recall@k 1 con las dos.
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from index.vector import VectorMetric
from index.vector.metrics import Vectors

# Las bibliotecas calculan en `float32`: en 256 dimensiones un mismo puntaje difiere en la
# séptima cifra del de `float64`. Un vecino a menos de esta fracción del k-ésimo puntaje es
# un empate, no un error.
RECALL_RELATIVE_TOLERANCE = 1e-5
RECALL_ABSOLUTE_TOLERANCE = 1e-9


def recall_at_k(found_scores: Sequence[float], kth_score: float, k: int) -> float:
    """Fracción de los `k` primeros encontrados cuyo puntaje exacto llega al k-ésimo mejor.

    Si la técnica devolvió menos de `k`, los huecos cuentan como fallos: el denominador es
    siempre `k`.
    """
    margin = RECALL_RELATIVE_TOLERANCE * abs(kth_score) + RECALL_ABSOLUTE_TOLERANCE
    return sum(1 for score in found_scores[:k] if score >= kth_score - margin) / k


def class_precision_at_k(found_labels: Sequence[str], label: str, k: int) -> float:
    """Fracción de los `k` primeros encontrados que son de la clase `label`; denominador `k`."""
    return sum(1 for found in found_labels[:k] if found == label) / k


def random_class_precision(labels: Sequence[str], query_labels: Sequence[str]) -> float:
    """La precisión por clase que daría elegir los vecinos al azar: la parte de la base que
    es de la clase de cada consulta, en promedio."""
    counts = Counter(labels)
    return float(np.mean([counts[label] / len(labels) for label in query_labels]))


class GroundTruth:
    """Los puntajes exactos de cada consulta contra la colección, con una métrica."""

    def __init__(self, vectors: Vectors, queries: Vectors, metric: VectorMetric, k: int) -> None:
        dimension = vectors.shape[1]
        self._metric = metric
        self._k = k
        self._vectors = metric.prepare(vectors, dimension)
        self._queries = [metric.prepare(query, dimension) for query in queries]
        self._kth_scores = [self._kth_best(query) for query in self._queries]

    @property
    def k(self) -> int:
        return self._k

    def recall(self, query: int, found: Sequence[int]) -> float:
        """Recall@k de lo que una técnica encontró para la consulta número `query`."""
        if not found:
            return 0.0
        scores = self._metric.scores_of_prepared(
            self._queries[query], self._vectors[np.asarray(found[: self._k])]
        )
        return recall_at_k(scores.tolist(), self._kth_scores[query], self._k)

    def _kth_best(self, query: Vectors) -> float:
        scores = self._metric.scores_of_prepared(query, self._vectors)
        return float(np.partition(scores, -self._k)[-self._k])


@dataclass(frozen=True, slots=True)
class GalleryCase:
    """Una consulta de la galería y las posiciones de sus primeros vecinos."""

    query: int
    neighbours: tuple[int, ...]
    precision: float


def gallery_cases(
    found: Sequence[Sequence[int]],
    precisions: Sequence[float],
    labels: Sequence[str],
    cases: int,
    neighbours: int,
    seed: int,
) -> tuple[list[GalleryCase], list[GalleryCase]]:
    """Las `cases` consultas con más vecinos de su clase (los éxitos) y las `cases` con
    menos (los errores), cada una de una clase distinta: si no, en una colección donde la
    mitad son fundas de celular, todos los éxitos serían fundas. Entre consultas empatadas
    decide un orden al azar fijo por la semilla, para no elegir siempre las primeras."""
    order = list(range(len(found)))
    random.Random(seed).shuffle(order)
    ranked = sorted(order, key=lambda query: -precisions[query])
    chosen = [
        GalleryCase(query, tuple(found[query][:neighbours]), precisions[query]) for query in ranked
    ]
    return _one_per_label(chosen, labels, cases), _one_per_label(chosen[::-1], labels, cases)


def _one_per_label(
    ranked: Sequence[GalleryCase], labels: Sequence[str], cases: int
) -> list[GalleryCase]:
    taken: list[GalleryCase] = []
    seen: set[str] = set()
    for case in ranked:
        if labels[case.query] in seen:
            continue
        seen.add(labels[case.query])
        taken.append(case)
        if len(taken) == cases:
            break
    return taken
