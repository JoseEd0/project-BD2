"""Reciprocal Rank Fusion (Cormack, Clarke y Büttcher, SIGIR 2009).

Cada lista aporta `1 / (k + rango)` a cada documento que trae, con el rango desde 1, y la
lista fusionada se ordena por la suma. Solo usa los rangos: BM25 da puntajes sin techo y el
coseno da números entre -1 y 1, y sumarlos exigiría normalizarlos con supuestos que se
rompen de una consulta a otra.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

# La constante del paper: amortigua el peso de los primeros puestos para que un documento
# que aparece bien en las dos listas gane a uno que solo encabeza una.
RRF_K = 60


@dataclass(frozen=True, slots=True)
class FusedRank:
    """Un documento de la lista fusionada: su puntaje RRF y su rango en cada lista de
    entrada (desde 1), o `None` en las que no aparece."""

    key: str
    score: float
    ranks: tuple[int | None, ...]


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[str]], k: int = RRF_K
) -> list[FusedRank]:
    """Fusiona listas de claves ordenadas de mejor a peor.

    Un empate de puntaje lo decide el mejor rango que tenga cada documento en alguna lista
    y, si sigue, la clave: el resultado es determinista.

    Raises:
        ValueError: si `k` es negativo o una lista repite una clave.
    """
    if k < 0:
        raise ValueError(f"k de RRF tiene que ser 0 o más, no {k}")
    ranks: dict[str, list[int | None]] = {}
    for position, ranking in enumerate(rankings):
        if len(set(ranking)) != len(ranking):
            raise ValueError(f"la lista {position + 1} repite claves")
        for rank, key in enumerate(ranking, start=1):
            ranks.setdefault(key, [None] * len(rankings))[position] = rank
    fused = [
        FusedRank(key, sum(1 / (k + rank) for rank in found if rank is not None), tuple(found))
        for key, found in ranks.items()
    ]
    return sorted(fused, key=lambda item: (-item.score, _best_rank(item), item.key))


def _best_rank(item: FusedRank) -> int:
    return min(rank for rank in item.ranks if rank is not None)
