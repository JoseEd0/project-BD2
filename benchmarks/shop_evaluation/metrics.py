"""Las medidas de una lista recomendada con una sola compra que acertar, y sus intervalos.

Con un único producto relevante, el DCG ideal vale 1 (el producto en el primer puesto), así
que el NDCG@k es `1 / log2(puesto + 1)` si el producto está entre los k primeros y 0 si no.
Los intervalos son bootstrap por percentiles sobre los usuarios: las medidas de un usuario
no son normales (Hit es 0 o 1, NDCG casi siempre 0) y el bootstrap no supone nada de eso.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

PERCENT = 100.0


@dataclass(frozen=True, slots=True)
class Interval:
    """Una media con su intervalo de confianza."""

    mean: float
    low: float
    high: float


def rank_of(target: str, ranked: Sequence[str]) -> int | None:
    """El puesto de `target` en `ranked`, contando desde 1, o `None` si no está."""
    for position, product in enumerate(ranked, start=1):
        if product == target:
            return position
    return None


def hit_at(rank: int | None, k: int) -> float:
    """1 si el producto quedó entre los `k` primeros, 0 si no."""
    return 1.0 if rank is not None and rank <= k else 0.0


def ndcg_at(rank: int | None, k: int) -> float:
    """El NDCG@k con un único producto relevante en el puesto `rank`."""
    if rank is None or rank > k:
        return 0.0
    return 1.0 / math.log2(rank + 1)


def bootstrap_mean(
    values: Sequence[float], resamples: int, confidence: float, seed: int
) -> Interval:
    """La media de `values` con su intervalo bootstrap por percentiles.

    Raises:
        ValueError: si `values` está vacío.
    """
    if not values:
        raise ValueError("no hay valores con que calcular una media")
    sample = np.asarray(values, dtype=np.float64)
    means = _resampled_means(sample, resamples, seed)
    low, high = _percentiles(means, confidence)
    return Interval(float(sample.mean()), low, high)


def paired_difference(
    first: Sequence[float],
    second: Sequence[float],
    resamples: int,
    confidence: float,
    seed: int,
) -> Interval:
    """La diferencia media `first - second` medida sobre los mismos usuarios, con su
    intervalo bootstrap: se remuestrean usuarios, no medidas sueltas.

    Raises:
        ValueError: si las dos listas no tienen el mismo largo o están vacías.
    """
    if len(first) != len(second):
        raise ValueError("las dos medidas tienen que ser de los mismos usuarios")
    differences = [a - b for a, b in zip(first, second, strict=True)]
    return bootstrap_mean(differences, resamples, confidence, seed)


def coverage(recommended: Sequence[Sequence[str]], catalog_size: int) -> float:
    """La fracción del catálogo que aparece en alguna de las listas."""
    distinct = {product for ranked in recommended for product in ranked}
    return len(distinct) / catalog_size


def mean_popularity(
    recommended: Sequence[Sequence[str]], popularity: Mapping[str, int]
) -> float:
    """Las compras de entrenamiento medias de un producto recomendado, contando cada puesto
    de cada lista; 0 si no se recomendó nada."""
    counts = [popularity.get(product, 0) for ranked in recommended for product in ranked]
    return sum(counts) / len(counts) if counts else 0.0


def _resampled_means(sample: np.ndarray, resamples: int, seed: int) -> np.ndarray:
    generator = np.random.default_rng(seed)
    picks = generator.integers(0, len(sample), size=(resamples, len(sample)))
    return sample[picks].mean(axis=1)


def _percentiles(means: np.ndarray, confidence: float) -> tuple[float, float]:
    tail = (1.0 - confidence) / 2 * PERCENT
    low, high = np.percentile(means, [tail, PERCENT - tail])
    return float(low), float(high)
