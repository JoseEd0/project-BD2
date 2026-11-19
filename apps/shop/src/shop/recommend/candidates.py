"""Lo que entrega cada señal: candidatos con su puntaje y, si la señal la tiene, la evidencia
con que se explica la recomendación."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

VisualMethod = Literal["clip", "sift"]
CLIP_METHOD: VisualMethod = "clip"
SIFT_METHOD: VisualMethod = "sift"


class Signal(StrEnum):
    """Las cuatro señales, cada una de una clase de datos del gestor."""

    COPURCHASE = "compras"
    VISUAL = "visual"
    TEXT = "texto"
    NEARBY = "cercania"


@dataclass(frozen=True, slots=True)
class CopurchaseEvidence:
    """`customers` compraron el candidato junto con `purchases` de los productos de partida
    (uno, para un producto; varias de sus compras, para un usuario)."""

    customers: int
    purchases: int


@dataclass(frozen=True, slots=True)
class StockEvidence:
    """El candidato está en stock en `store`, a `meters` de quien pregunta."""

    store: str
    meters: float


Evidence = CopurchaseEvidence | StockEvidence


@dataclass(frozen=True, slots=True)
class Candidate:
    """Un producto propuesto por una señal, con el puntaje crudo de esa señal (clientes,
    coseno, BM25 o cercanía) y su evidencia."""

    product: str
    score: float
    evidence: Evidence | None = None


@dataclass(frozen=True, slots=True)
class SignalRun:
    """Una señal ejecutada: la consulta exacta, sus parámetros tal como se muestran, los
    candidatos y los milisegundos que tardó, medidos desde el servicio."""

    signal: Signal
    sql: str
    parameters: tuple[object, ...]
    candidates: tuple[Candidate, ...]
    milliseconds: float
