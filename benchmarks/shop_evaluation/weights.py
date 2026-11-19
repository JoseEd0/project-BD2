"""La búsqueda de los pesos de la fusión: una rejilla sobre el símplex, medida en validación.

Solo importa la proporción entre los pesos: las señales que se ordenan valen de 0 a 1 tras el
min-max y la cercanía ya es una fracción, así que multiplicar todos los pesos por lo mismo
multiplica cada suma y deja la lista igual. Basta con recorrer los pesos que suman 1.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import astuple, dataclass

from shop.recommend.candidates import Signal
from shop.recommend.fusion import FusionWeights

WEIGHT_DECIMALS = 6


@dataclass(frozen=True, slots=True)
class WeightScore:
    """Unos pesos con el NDCG y el Hit medios que dieron."""

    weights: FusionWeights
    ndcg: float
    hit: float


def simplex(steps: int, signals: Sequence[Signal]) -> list[FusionWeights]:
    """Todos los pesos múltiplos de `1 / steps` de `signals` que suman 1 (las demás señales
    valen 0). Con `n` señales son `C(steps + n - 1, n - 1)` combinaciones.

    Raises:
        ValueError: si `steps` no es positivo o no hay señales.
    """
    if steps < 1 or not signals:
        raise ValueError("la rejilla necesita al menos un paso y una señal")
    return [
        _weights(dict(zip(signals, parts, strict=True)), steps)
        for parts in _compositions(steps, len(signals))
    ]


def search(
    grid: Sequence[FusionWeights], score: Callable[[FusionWeights], tuple[float, float]]
) -> list[WeightScore]:
    """Cada peso de `grid` con su `(ndcg, hit)`, del mejor al peor: más NDCG, después más
    Hit y, en un empate de los dos, los pesos menores en orden (compras, visual, texto,
    cercanía), para que la misma entrada dé siempre el mismo ganador."""
    scored = [WeightScore(weights, *score(weights)) for weights in grid]
    return sorted(scored, key=lambda found: (-found.ndcg, -found.hit, astuple(found.weights)))


def label(weights: FusionWeights) -> str:
    """Los cuatro pesos en el orden compras, visual, texto y cercanía, con coma decimal."""
    return " · ".join(f"{value:.2f}".replace(".", ",") for value in astuple(weights))


def _compositions(total: int, parts: int) -> Iterator[tuple[int, ...]]:
    if parts == 1:
        yield (total,)
        return
    for first in range(total + 1):
        for rest in _compositions(total - first, parts - 1):
            yield (first, *rest)


def _weights(parts: dict[Signal, int], steps: int) -> FusionWeights:
    def share(signal: Signal) -> float:
        return round(parts.get(signal, 0) / steps, WEIGHT_DECIMALS)

    return FusionWeights(
        copurchase=share(Signal.COPURCHASE),
        visual=share(Signal.VISUAL),
        text=share(Signal.TEXT),
        nearby=share(Signal.NEARBY),
    )
