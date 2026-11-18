"""Distribución de longitudes de los fragmentos: lo que el README y la figura reportan."""

from __future__ import annotations

import math
from collections.abc import Sequence

PERCENTILES = (50, 95)
HISTOGRAM_BIN_WORDS = 10
PERCENT = 100


def percentile(sorted_values: Sequence[int], rank: int) -> int:
    """El percentil `rank` por rango más cercano: el menor valor con al menos `rank` % de
    los datos a su izquierda o igual."""
    position = max(1, math.ceil(rank / PERCENT * len(sorted_values)))
    return sorted_values[position - 1]


def length_distribution(words: Sequence[int], max_words: int) -> dict[str, object]:
    """Mínimo, percentiles, máximo, media e histograma de `words` en barras de
    `HISTOGRAM_BIN_WORDS` palabras de 0 a `max_words`."""
    if not words:
        return {"count": 0}
    ordered = sorted(words)
    bins = math.ceil(max(max_words, ordered[-1]) / HISTOGRAM_BIN_WORDS)
    counts = [0] * bins
    for value in ordered:
        counts[min(value // HISTOGRAM_BIN_WORDS, bins - 1)] += 1
    return {
        "count": len(ordered),
        "min": ordered[0],
        **{f"p{rank}": percentile(ordered, rank) for rank in PERCENTILES},
        "max": ordered[-1],
        "mean": round(sum(ordered) / len(ordered), 1),
        "histogram": {"bin_words": HISTOGRAM_BIN_WORDS, "counts": counts},
    }
