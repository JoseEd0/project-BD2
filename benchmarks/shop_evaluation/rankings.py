"""Las listas que se evalúan: cada señal sola, la fusión del recomendador con unos pesos, y
las dos referencias (lo más vendido en el entrenamiento y al azar)."""

from __future__ import annotations

import random
from collections.abc import Collection, Mapping, Sequence

from shop.recommend.candidates import Candidate, Signal
from shop.recommend.fusion import FusionWeights, fuse

# Si una fusión es un arranque en frío no depende de los pesos, solo de las compras conjuntas.
COLD_START_PROBE_WEIGHTS = FusionWeights(1.0, 1.0, 1.0, 1.0)


def alone(candidates: Sequence[Candidate], excluded: Collection[str], k: int) -> list[str]:
    """Los `k` primeros candidatos de una señal, en el orden en que los devuelve el gestor
    (de mayor a menor puntaje), sin los productos de `excluded`."""
    return [candidate.product for candidate in candidates if candidate.product not in excluded][
        :k
    ]


def fused(
    found: Mapping[Signal, Sequence[Candidate]],
    weights: FusionWeights,
    excluded: Collection[str],
    k: int,
) -> list[str]:
    """Los `k` primeros de la fusión del recomendador (`shop.recommend.fusion.fuse`)."""
    return [product.product for product in fuse(found, weights, excluded, k).ranked]


def is_cold_start(found: Mapping[Signal, Sequence[Candidate]], excluded: Collection[str]) -> bool:
    """Si la fusión no tiene compras conjuntas y reparte su peso entre imagen y texto."""
    return fuse(found, COLD_START_PROBE_WEIGHTS, excluded, 0).cold_start


def most_popular(ordered: Sequence[str], excluded: Collection[str], k: int) -> list[str]:
    """Los `k` más vendidos de `ordered` (ya ordenado) que no estén en `excluded`."""
    picked: list[str] = []
    for product in ordered:
        if len(picked) == k:
            break
        if product not in excluded:
            picked.append(product)
    return picked


def at_random(
    catalog: Sequence[str], excluded: Collection[str], k: int, generator: random.Random
) -> list[str]:
    """`k` productos distintos del catálogo al azar, sin los de `excluded`."""
    drawn = generator.sample(catalog, min(len(catalog), k + len(excluded)))
    return [product for product in drawn if product not in excluded][:k]

