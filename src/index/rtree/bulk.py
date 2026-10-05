"""Agrupación Sort-Tile-Recursive para la carga masiva del R-Tree.

Leutenegger, López y Edgington (1997). Insertar un millón de puntos de uno en uno baja un
millón de veces por el árbol y divide miles de nodos; si los puntos ya se conocen todos, es
mejor decidir de antemano qué va en cada nodo:

1. ordenar las entradas por latitud y cortarlas en franjas verticales de igual tamaño;
2. ordenar cada franja por longitud y cortarla en nodos.

Con `√P` franjas para `P` nodos, cada nodo cubre una baldosa casi cuadrada y las baldosas
no se solapan, que es justo lo que hace rápidas las búsquedas. Aplicado otra vez sobre los
nodos recién creados sale el nivel de arriba, y así hasta la raíz.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from itertools import islice
from math import ceil, sqrt
from typing import TypeVar

from .node import Bounded

EntryType = TypeVar("EntryType", bound=Bounded)


def tile(entries: Sequence[EntryType], group_size: int, minimum: int) -> Iterator[list[EntryType]]:
    """Reparte las entradas en nodos de hasta `group_size`, ordenándolas en memoria.

    Es lo que se usa para los niveles internos, que tienen `N / entradas por nodo`
    entradas y caben en memoria aunque las hojas no quepan.
    """
    by_latitude = sorted(entries, key=_center_latitude)
    return tile_sorted(iter(by_latitude), len(by_latitude), group_size, minimum)


def tile_sorted(
    entries: Iterator[EntryType], total: int, group_size: int, minimum: int
) -> Iterator[list[EntryType]]:
    """Igual que `tile`, pero sobre entradas que ya llegan ordenadas por latitud.

    Solo mantiene en memoria una franja a la vez, `√(total · group_size)` entradas, así
    que sirve para un flujo que sale de un ordenamiento externo. Ningún grupo queda con
    menos de `minimum` entradas, salvo que en total no haya tantas.
    """
    stripes = _batches(entries, _stripe_size(total, group_size))
    for stripe in _without_short_tail(stripes, minimum):
        stripe.sort(key=_center_longitude)
        yield from _without_short_tail(_batches(iter(stripe), group_size), minimum)


def _stripe_size(total: int, group_size: int) -> int:
    groups = ceil(total / group_size)
    return ceil(sqrt(groups)) * group_size


def _batches(entries: Iterator[EntryType], size: int) -> Iterator[list[EntryType]]:
    while batch := list(islice(entries, size)):
        yield batch


def _without_short_tail(
    batches: Iterator[list[EntryType]], minimum: int
) -> Iterator[list[EntryType]]:
    """Evita que el último lote quede por debajo del mínimo de ocupación de un nodo.

    Todos los lotes llegan llenos menos, quizá, el último. Si ese se queda corto, se junta
    con el anterior y el conjunto se parte por la mitad: dos lotes medianos en vez de uno
    lleno y otro casi vacío.
    """
    previous: list[EntryType] | None = None
    for batch in batches:
        if previous is None:
            previous = batch
            continue
        if len(batch) < minimum:
            merged = previous + batch
            middle = len(merged) // 2
            yield merged[:middle]
            previous = merged[middle:]
            continue
        yield previous
        previous = batch
    if previous is not None:
        yield previous


def _center_latitude(entry: Bounded) -> float:
    bounds = entry.bounds
    return bounds.min_lat + bounds.max_lat


def _center_longitude(entry: Bounded) -> float:
    bounds = entry.bounds
    return bounds.min_lon + bounds.max_lon
