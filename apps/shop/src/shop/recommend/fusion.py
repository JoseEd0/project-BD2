"""La fusión: de los candidatos de cada señal a una sola lista ordenada.

Cada señal puntúa en su escala —clientes que compraron el par, coseno, BM25— así que antes de
sumar se normaliza **por min-max** dentro de su lista: el mejor candidato de la señal vale 1 y
el peor 0. Se eligió sobre una normalización por rango (RRF) porque la distancia entre
puntajes dice algo: un par comprado por 120 clientes no está «un puesto» por delante de uno
comprado por 3, y el BM25 de un título casi idéntico se separa del resto. Los pesos de la
configuración (#82 los ajusta en la validación) multiplican esas fracciones, así que cada
señal aporta como mucho su peso. La cercanía ya es una fracción con sentido propio,
`1 - metros / radio`, y entra tal cual: min-max haría «la tienda más cercana» igual de buena
a 100 m que a 4 km.

Sin compras conjuntas (un producto que nadie compró con otro, o un usuario sin compras de
entrenamiento) es el **arranque en frío**: el peso de las compras pasa a la imagen y al
texto, en proporción a los suyos. La cercanía solo refuerza candidatos de las otras tres
señales: decir qué hay cerca no dice qué le gusta a nadie.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Mapping, Sequence
from dataclasses import asdict, dataclass

from .candidates import Candidate, Evidence, Signal

ABSOLUTE_SIGNALS = frozenset({Signal.NEARBY})
RANKING_SIGNALS = (Signal.COPURCHASE, Signal.VISUAL, Signal.TEXT)
COLD_START_RECIPIENTS = (Signal.VISUAL, Signal.TEXT)
VERSION_CHARACTERS = 12


@dataclass(frozen=True, slots=True)
class FusionWeights:
    """El peso de cada señal en la suma; no tienen que sumar 1."""

    copurchase: float
    visual: float
    text: float
    nearby: float

    def of(self, signal: Signal) -> float:
        return {
            Signal.COPURCHASE: self.copurchase,
            Signal.VISUAL: self.visual,
            Signal.TEXT: self.text,
            Signal.NEARBY: self.nearby,
        }[signal]

    def for_cold_start(self) -> FusionWeights:
        """Los pesos sin compras conjuntas: el de las compras se reparte entre imagen y texto
        en proporción a sus pesos, o a partes iguales si los dos son 0."""
        receivers = self.visual + self.text
        if receivers == 0:
            share = self.copurchase / len(COLD_START_RECIPIENTS)
            return FusionWeights(0.0, share, share, self.nearby)
        visual = self.visual + self.copurchase * self.visual / receivers
        text = self.text + self.copurchase * self.text / receivers
        return FusionWeights(0.0, visual, text, self.nearby)

    def version(self) -> str:
        """Una huella de los pesos: la caché de respuestas la lleva en cada clave, así que
        cambiar un peso no sirve respuestas calculadas con los anteriores."""
        encoded = json.dumps(asdict(self), sort_keys=True).encode()
        return hashlib.sha256(encoded).hexdigest()[:VERSION_CHARACTERS]


@dataclass(frozen=True, slots=True)
class Contribution:
    """Lo que aporta una señal a un producto: su puntaje normalizado, multiplicado por el
    peso, y la evidencia de la señal."""

    signal: Signal
    normalized: float
    weighted: float
    evidence: Evidence | None


@dataclass(frozen=True, slots=True)
class FusedProduct:
    """Un producto recomendado: la suma de los aportes y los aportes, de mayor a menor."""

    product: str
    score: float
    contributions: tuple[Contribution, ...]


@dataclass(frozen=True, slots=True)
class Fusion:
    """La lista fundida, los pesos con que se calculó y si fue un arranque en frío."""

    weights: FusionWeights
    cold_start: bool
    ranked: tuple[FusedProduct, ...]


def min_max(candidates: Sequence[Candidate]) -> dict[str, float]:
    """El puntaje de cada candidato llevado a `[0, 1]` dentro de su lista. Si todos empatan
    (o hay uno solo), todos valen 1: ninguno es peor que otro."""
    if not candidates:
        return {}
    scores = [candidate.score for candidate in candidates]
    low, high = min(scores), max(scores)
    if high == low:
        return {candidate.product: 1.0 for candidate in candidates}
    return {candidate.product: (candidate.score - low) / (high - low) for candidate in candidates}


def fuse(
    candidates: Mapping[Signal, Sequence[Candidate]],
    weights: FusionWeights,
    excluded: Collection[str],
    limit: int,
) -> Fusion:
    """Los `limit` mejores productos de la suma ponderada, sin los de `excluded`.

    Un empate en la suma lo gana el producto que aparece en más señales, y después el de
    menor id, para que la misma consulta dé siempre la misma lista.
    """
    kept = {
        signal: [candidate for candidate in found if candidate.product not in excluded]
        for signal, found in candidates.items()
    }
    cold_start = not kept.get(Signal.COPURCHASE)
    effective = weights.for_cold_start() if cold_start else weights
    proposed = {
        candidate.product for signal in RANKING_SIGNALS for candidate in kept.get(signal, [])
    }
    contributions: dict[str, list[Contribution]] = {product: [] for product in proposed}
    for signal, found in kept.items():
        normalized = _normalized(signal, found)
        for candidate in found:
            if candidate.product not in contributions:
                continue
            share = normalized[candidate.product]
            contributions[candidate.product].append(
                Contribution(signal, share, share * effective.of(signal), candidate.evidence)
            )
    ranked = sorted(
        (_fused(product, parts) for product, parts in contributions.items()),
        key=lambda fused: (-fused.score, -len(fused.contributions), fused.product),
    )
    return Fusion(effective, cold_start, tuple(ranked[:limit]))


def _normalized(signal: Signal, candidates: Sequence[Candidate]) -> dict[str, float]:
    if signal in ABSOLUTE_SIGNALS:
        return {candidate.product: candidate.score for candidate in candidates}
    return min_max(candidates)


def _fused(product: str, parts: list[Contribution]) -> FusedProduct:
    ordered = sorted(parts, key=lambda part: (-part.weighted, list(Signal).index(part.signal)))
    return FusedProduct(product, sum(part.weighted for part in parts), tuple(ordered))
