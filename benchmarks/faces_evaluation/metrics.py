"""Cómo se califica un reconocedor: exactitud por pliegues, TAR a un FAR fijo, curva ROC,
rank-1 y los errores del conjunto abierto.

Un par se acepta como «la misma persona» si su coseno es **mayor o igual** que el umbral;
todas las funciones siguen esa regla. Son puras y trabajan sobre arreglos de numpy, así que
se prueban con puntajes inventados.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypeAlias

import numpy as np
import numpy.typing as npt

Scores: TypeAlias = npt.NDArray[np.float64]
Labels: TypeAlias = npt.NDArray[np.bool_]


@dataclass(frozen=True, slots=True)
class FoldResult:
    """Un pliegue de la validación cruzada: el umbral elegido con los demás pliegues, la
    exactitud con que los separa y la que da con el pliegue que no vio."""

    fold: int
    threshold: float
    train_accuracy: float
    test_accuracy: float


@dataclass(frozen=True, slots=True)
class OperatingPoint:
    """Un umbral y lo que da: la fracción de pares de la misma persona aceptados (TAR) y la
    de pares de personas distintas aceptados (FAR), con sus cuentas."""

    threshold: float
    tar: float
    far: float
    accepted_impostors: int
    impostors: int


@dataclass(frozen=True, slots=True)
class OpenSetErrors:
    """FNIR: sondas de la galería que no salen como ellas mismas con el umbral; FPIR:
    sondas de fuera de la galería que salen como alguien."""

    fnir: float
    fpir: float


def accuracy_at(scores: Scores, same: Labels, threshold: float) -> float:
    """La fracción de pares bien clasificados con `threshold`."""
    return float(np.mean((scores >= threshold) == same))


def best_threshold(scores: Scores, same: Labels) -> tuple[float, float]:
    """El umbral que más pares clasifica bien y esa exactitud, en O(n log n).

    Se prueban todos los cortes entre dos puntajes distintos consecutivos —y los de los
    extremos— y el umbral es el punto medio del corte: así no depende de cuántos valores
    haya entre dos puntajes. Con empates gana el corte más bajo.

    Raises:
        ValueError: si no hay pares.
    """
    if scores.size == 0:
        raise ValueError("no hay pares con que elegir un umbral")
    order = np.argsort(scores, kind="stable")
    sorted_scores = scores[order]
    sorted_same = same[order]
    # Cortar antes de la posición i acepta las posiciones i en adelante.
    rejected_different = np.concatenate(([0], np.cumsum(~sorted_same)))
    accepted_same = np.concatenate(([0], np.cumsum(sorted_same[::-1])))[::-1]
    correct = rejected_different + accepted_same
    cut_is_possible = np.ones(scores.size + 1, dtype=bool)
    cut_is_possible[1:-1] = sorted_scores[1:] > sorted_scores[:-1]
    correct = np.where(cut_is_possible, correct, -1)
    cut = int(np.argmax(correct))
    return _threshold_at_cut(sorted_scores, cut), float(correct[cut] / scores.size)


def _threshold_at_cut(sorted_scores: Scores, cut: int) -> float:
    if cut == 0:
        return float(sorted_scores[0])
    if cut == sorted_scores.size:
        return float(np.nextafter(sorted_scores[-1], np.inf))
    return float((sorted_scores[cut - 1] + sorted_scores[cut]) / 2)


def cross_validate(scores: Scores, same: Labels, folds: npt.NDArray[np.int_]) -> list[FoldResult]:
    """El protocolo de LFW: por cada pliegue, el umbral se elige con los otros y se prueba
    con él. `folds[i]` es el pliegue del par `i`."""
    results = []
    for fold in np.unique(folds):
        held_out = folds == fold
        threshold, train_accuracy = best_threshold(scores[~held_out], same[~held_out])
        test_accuracy = accuracy_at(scores[held_out], same[held_out], threshold)
        results.append(FoldResult(int(fold), threshold, train_accuracy, test_accuracy))
    return results


def threshold_at_far(impostor_scores: Scores, far: float) -> float:
    """El umbral más bajo que acepta a lo sumo `⌊far · n⌋` de los `n` impostores.

    Raises:
        ValueError: si no hay impostores.
    """
    if impostor_scores.size == 0:
        raise ValueError("no hay pares de personas distintas")
    allowed = math.floor(far * impostor_scores.size)
    if allowed >= impostor_scores.size:
        return float(np.min(impostor_scores))
    descending = np.sort(impostor_scores)[::-1]
    return float(np.nextafter(descending[allowed], np.inf))


def operating_point(
    genuine_scores: Scores, impostor_scores: Scores, threshold: float
) -> OperatingPoint:
    accepted = int(np.count_nonzero(impostor_scores >= threshold))
    return OperatingPoint(
        threshold=threshold,
        tar=float(np.mean(genuine_scores >= threshold)),
        far=accepted / impostor_scores.size,
        accepted_impostors=accepted,
        impostors=int(impostor_scores.size),
    )


def tar_at_far(genuine_scores: Scores, impostor_scores: Scores, far: float) -> OperatingPoint:
    """El punto de operación con el umbral de `threshold_at_far`: su FAR real puede quedar
    por debajo de `far` si hay empates o pocos impostores."""
    return operating_point(genuine_scores, impostor_scores, threshold_at_far(impostor_scores, far))


def roc_curve(
    genuine_scores: Scores, impostor_scores: Scores, thresholds: Sequence[float]
) -> list[OperatingPoint]:
    """Un punto de operación por umbral, en el orden de `thresholds`; ordena cada lista una
    vez, así que cuesta O((n + m) log(n + m)) con cualquier número de umbrales."""
    genuine = np.sort(genuine_scores)
    impostor = np.sort(impostor_scores)
    cuts = np.asarray(thresholds, dtype=np.float64)
    accepted_genuine = genuine.size - np.searchsorted(genuine, cuts, side="left")
    accepted_impostors = impostor.size - np.searchsorted(impostor, cuts, side="left")
    return [
        OperatingPoint(
            threshold=float(threshold),
            tar=int(genuine_count) / genuine.size,
            far=int(impostor_count) / impostor.size,
            accepted_impostors=int(impostor_count),
            impostors=int(impostor.size),
        )
        for threshold, genuine_count, impostor_count in zip(
            cuts, accepted_genuine, accepted_impostors, strict=True
        )
    ]


def rank_one(predicted: Sequence[str], expected: Sequence[str]) -> float:
    """La fracción de sondas cuyo primer vecino es de su misma persona.

    Raises:
        ValueError: si las dos listas no tienen el mismo largo o están vacías.
    """
    if len(predicted) != len(expected) or not expected:
        raise ValueError("se esperaba una predicción por sonda")
    return sum(p == e for p, e in zip(predicted, expected, strict=True)) / len(expected)


def open_set_errors(
    genuine_correct: Labels,
    genuine_scores: Scores,
    impostor_scores: Scores,
    threshold: float,
) -> OpenSetErrors:
    """Los errores del conjunto abierto con `threshold`. Una sonda de la galería se pierde
    si su primer vecino es de otra persona o si su coseno no llega al umbral.

    Raises:
        ValueError: si no hay sondas de la galería o de fuera.
    """
    if genuine_scores.size == 0 or impostor_scores.size == 0:
        raise ValueError("hacen falta sondas de la galería y de fuera de ella")
    recognized = genuine_correct & (genuine_scores >= threshold)
    return OpenSetErrors(
        fnir=float(1.0 - np.mean(recognized)),
        fpir=float(np.mean(impostor_scores >= threshold)),
    )
