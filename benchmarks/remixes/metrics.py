"""Precisión, recall, F1, curva ROC e IoU de la detección contra la verdad de cada remix.

La unidad es la canción copiada dentro de una subida. Un **acierto** es una canción de la
verdad con alguna coincidencia que llega al umbral; un **fallo**, una que no llega; una
**falsa alarma**, una canción que llega al umbral y no está en la verdad de esa subida.
Varias coincidencias de una misma canción cuentan una vez.

La curva ROC necesita negativos: son las subidas sin ninguna canción del catálogo. Su tasa
de verdaderos positivos es el recall y la de falsos positivos, la fracción de esas subidas
con alguna coincidencia que llega al umbral.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from benchmarks.remixes.generator import Span


@dataclass(frozen=True, slots=True)
class Detected:
    """Una coincidencia de la detección: su canción, su puntaje y los dos tramos alineados."""

    song_id: int
    score: float
    upload: Span
    original: Span


@dataclass(frozen=True, slots=True)
class Truth:
    """Un fragmento copiado: su canción y dónde suena en la subida y en el original."""

    song_id: int
    upload: Span
    original: Span


@dataclass(frozen=True, slots=True)
class Case:
    """Una subida: los fragmentos que copia (`truth`, sus tramos ya en el audio subido) y lo
    que detectó el servicio."""

    truth: tuple[Truth, ...]
    detected: tuple[Detected, ...]


@dataclass(frozen=True, slots=True)
class Counts:
    """Aciertos, falsas alarmas y fallos a un umbral. Sin detecciones, la precisión es 0."""

    true_positives: int
    false_positives: int
    false_negatives: int

    @property
    def precision(self) -> float:
        claimed = self.true_positives + self.false_positives
        return self.true_positives / claimed if claimed else 0.0

    @property
    def recall(self) -> float:
        copied = self.true_positives + self.false_negatives
        return self.true_positives / copied if copied else 0.0

    @property
    def f1(self) -> float:
        total = self.precision + self.recall
        return 2 * self.precision * self.recall / total if total else 0.0


@dataclass(frozen=True, slots=True)
class RocPoint:
    threshold: float
    true_positive_rate: float
    false_positive_rate: float

    @property
    def youden(self) -> float:
        return self.true_positive_rate - self.false_positive_rate


def interval_iou(first: Span, second: Span) -> float:
    """Intersección sobre unión de dos tramos; 0 si no se tocan."""
    overlap = min(first.end, second.end) - max(first.start, second.start)
    if overlap <= 0:
        return 0.0
    return overlap / (max(first.end, second.end) - min(first.start, second.start))


def counts(cases: Sequence[Case], threshold: float) -> Counts:
    hits = misses = false_alarms = 0
    for case in cases:
        claimed = {match.song_id for match in case.detected if match.score >= threshold}
        copied = {truth.song_id for truth in case.truth}
        hits += len(claimed & copied)
        misses += len(copied - claimed)
        false_alarms += len(claimed - copied)
    return Counts(hits, false_alarms, misses)


def roc_curve(cases: Sequence[Case], thresholds: Sequence[float]) -> list[RocPoint]:
    """Un punto por umbral, en el orden de `thresholds`.

    Raises:
        ValueError: si no hay copias o no hay subidas sin copias.
    """
    positives = [best_score(case, truth.song_id) for case in cases for truth in case.truth]
    negatives = [max_score(case) for case in cases if not case.truth]
    if not positives or not negatives:
        raise ValueError("la curva ROC necesita copias y subidas sin copias")
    return [
        RocPoint(
            threshold,
            sum(score >= threshold for score in positives) / len(positives),
            sum(score >= threshold for score in negatives) / len(negatives),
        )
        for threshold in thresholds
    ]


def area_under(curve: Sequence[RocPoint]) -> float:
    """Área bajo la curva por trapecios, con los extremos (0, 0) y (1, 1)."""
    points = sorted(
        {(0.0, 0.0), (1.0, 1.0)}
        | {(point.false_positive_rate, point.true_positive_rate) for point in curve}
    )
    return sum((right[0] - left[0]) * (right[1] + left[1]) / 2 for left, right in pairwise(points))


def youden_point(curve: Sequence[RocPoint]) -> RocPoint:
    """El punto que más separa los dos tipos de subida (máxima J = TPR - FPR); a igual J, el
    umbral más alto, que acusa menos."""
    return max(curve, key=lambda point: (round(point.youden, 12), point.threshold))


def best_score(case: Case, song_id: int) -> float:
    return max((match.score for match in case.detected if match.song_id == song_id), default=0.0)


def max_score(case: Case) -> float:
    return max((match.score for match in case.detected), default=0.0)


def best_match(case: Case, song_id: int) -> Detected | None:
    own = [match for match in case.detected if match.song_id == song_id]
    return max(own, key=lambda match: match.score) if own else None
