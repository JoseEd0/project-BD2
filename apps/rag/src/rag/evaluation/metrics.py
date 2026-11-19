"""Métricas de la evaluación: Recall@k y MRR de una lista recuperada, y el acuerdo entre el
juez y una revisión a mano.

Cada pregunta tiene un solo fragmento correcto, así que Recall@k es 1 si aparece entre los
`k` primeros y 0 si no (la media sobre las preguntas es la fracción que lo encuentra), y el
rango recíproco es `1/puesto` del primero que cuenta como correcto, o 0 si no está entre los
`k` primeros (MRR@k).

Qué cuenta como correcto lo dice el nivel de relevancia:

- `exact`: solo el fragmento del que salió la pregunta;
- `neighbour`: también el anterior y el siguiente del mismo paper. Los fragmentos se solapan
  de 50 a 120 palabras, así que la oración que responde puede estar entera en un vecino;
- `paper`: cualquier fragmento del mismo paper (la cita llevaría al documento correcto).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from .dataset import ordinal_of_chunk, paper_of_chunk

NEIGHBOUR_DISTANCE = 1


class Relevance(StrEnum):
    EXACT = "exact"
    NEIGHBOUR = "neighbour"
    PAPER = "paper"


def is_relevant(candidate: str, gold: str, relevance: Relevance) -> bool:
    """Si el fragmento `candidate` cuenta como correcto para `gold` con `relevance`."""
    if candidate == gold:
        return True
    if relevance is Relevance.EXACT or paper_of_chunk(candidate) != paper_of_chunk(gold):
        return False
    if relevance is Relevance.PAPER:
        return True
    return abs(ordinal_of_chunk(candidate) - ordinal_of_chunk(gold)) <= NEIGHBOUR_DISTANCE


def first_relevant_rank(ranked: Sequence[str], gold: str, relevance: Relevance) -> int | None:
    """El puesto (desde 1) del primer fragmento de `ranked` que cuenta como correcto."""
    for rank, candidate in enumerate(ranked, start=1):
        if is_relevant(candidate, gold, relevance):
            return rank
    return None


def recall_at(rank: int | None, k: int) -> float:
    """1 si el correcto está entre los `k` primeros, 0 si no."""
    return 1.0 if rank is not None and rank <= k else 0.0


def reciprocal_rank(rank: int | None, k: int) -> float:
    """`1/rank` si el correcto está entre los `k` primeros, 0 si no."""
    return 1.0 / rank if rank is not None and rank <= k else 0.0


@dataclass(frozen=True, slots=True)
class RetrievalScores:
    """Las medias sobre un grupo de preguntas: Recall@k por cada `k` y MRR@`mrr_depth`."""

    questions: int
    recall: dict[int, float]
    mrr: float
    mrr_depth: int


def summarize(ranks: Sequence[int | None], ks: Sequence[int], mrr_depth: int) -> RetrievalScores:
    """Las medias de Recall@k y MRR@`mrr_depth` sobre los puestos `ranks` (uno por pregunta).

    Raises:
        ValueError: si `ranks` está vacío.
    """
    if not ranks:
        raise ValueError("no hay preguntas que resumir")
    count = len(ranks)
    recall = {k: sum(recall_at(rank, k) for rank in ranks) / count for k in ks}
    mrr = sum(reciprocal_rank(rank, mrr_depth) for rank in ranks) / count
    return RetrievalScores(count, recall, mrr, mrr_depth)


@dataclass(frozen=True, slots=True)
class Agreement:
    """Cuánto coinciden dos etiquetados de los mismos casos: la fracción de casos iguales y
    la kappa de Cohen, que descuenta lo que coincidirían por azar."""

    cases: int
    accuracy: float
    cohen_kappa: float


def agreement(first: Sequence[bool], second: Sequence[bool]) -> Agreement:
    """El acuerdo entre dos etiquetados binarios (por ejemplo, el juez y una revisión a mano).
    Si los dos ponen siempre la misma etiqueta, el azar ya explica todo y kappa es 1 cuando
    coinciden.

    Raises:
        ValueError: si están vacíos o tienen distinto largo.
    """
    if not first or len(first) != len(second):
        raise ValueError("los dos etiquetados tienen que cubrir los mismos casos")
    cases = len(first)
    observed = sum(a == b for a, b in zip(first, second, strict=True)) / cases
    first_yes, second_yes = sum(first) / cases, sum(second) / cases
    expected = first_yes * second_yes + (1 - first_yes) * (1 - second_yes)
    if expected == 1.0:
        return Agreement(cases, observed, 1.0 if observed == 1.0 else 0.0)
    return Agreement(cases, observed, (observed - expected) / (1 - expected))
