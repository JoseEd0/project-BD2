"""Elección de subárbol y división cuadrática de nodos (Guttman, 1984).

Son las dos decisiones que determinan la calidad del árbol: dónde se inserta una entrada
y cómo se reparten las de un nodo que se desborda. Las dos persiguen lo mismo, que los MBR
queden pequeños, porque cuanto menos área cubre un nodo menos consultas tienen que abrirlo.

El criterio de Guttman es el área, y el área no distingue nada cuando los puntos están
alineados: sobre un mismo paralelo o meridiano todos los MBR miden cero. Por eso cada
comparación desempata por el **semiperímetro**, que en ese caso es la longitud del
segmento. Con puntos en posición general el área nunca empata y el resultado es el de
Guttman; con puntos alineados el árbol se organiza como un índice de una dimensión en
lugar de repartir al azar.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

from spatial.geometry import Rectangle

from .node import Bounded, BranchEntry

EntryType = TypeVar("EntryType", bound=Bounded)


def choose_subtree(entries: Sequence[BranchEntry], bounds: Rectangle) -> int:
    """Posición del hijo cuyo MBR menos crece al añadirle `bounds`.

    A igualdad de crecimiento en área gana el que menos crece en semiperímetro, y después
    el de menor área. Coste `O(entradas)`.
    """
    best_index = 0
    best_growth = float("inf")
    best_tiebreak = (float("inf"), float("inf"))
    for index, entry in enumerate(entries):
        cover = entry.bounds
        area = cover.area
        growth = cover.area_with(bounds) - area
        if growth > best_growth:
            continue
        tiebreak = (cover.margin_with(bounds) - cover.margin, area)
        if growth < best_growth or tiebreak < best_tiebreak:
            best_index, best_growth, best_tiebreak = index, growth, tiebreak
    return best_index


def quadratic_split(
    entries: Sequence[EntryType], minimum: int
) -> tuple[list[EntryType], list[EntryType]]:
    """Reparte las entradas de un nodo desbordado en dos grupos de al menos `minimum`.

    Primero elige como semillas las dos entradas que más espacio desperdiciarían juntas,
    y luego asigna las demás de una en una, empezando siempre por la que tiene más clara
    su preferencia por un grupo. Coste `O(entradas²)`.
    """
    boxes = [entry.bounds for entry in entries]
    first_seed, second_seed = _pick_seeds(boxes)
    groups: tuple[list[int], list[int]] = ([first_seed], [second_seed])
    covers = [boxes[first_seed], boxes[second_seed]]
    remaining = [index for index in range(len(boxes)) if index not in (first_seed, second_seed)]
    while remaining:
        starved = _group_that_needs_the_rest(groups, len(remaining), minimum)
        if starved is not None:
            groups[starved].extend(remaining)
            break
        position, target = _pick_next(remaining, boxes, covers, groups)
        index = remaining.pop(position)
        groups[target].append(index)
        covers[target] = covers[target].union(boxes[index])
    return [entries[index] for index in groups[0]], [entries[index] for index in groups[1]]


def _pick_seeds(boxes: Sequence[Rectangle]) -> tuple[int, int]:
    """Par de entradas cuyo MBR común desperdicia más área; a igual área, más semiperímetro.

    El semiperímetro solo se calcula cuando el área empata, que con puntos en posición
    general no ocurre: el bucle, que es el coste de toda la división, no lo paga.
    """
    areas = [box.area for box in boxes]
    worst_waste = float("-inf")
    worst_spread = float("-inf")
    seeds = (0, 1)
    for first in range(len(boxes) - 1):
        first_box = boxes[first]
        for second in range(first + 1, len(boxes)):
            waste = first_box.area_with(boxes[second]) - areas[first] - areas[second]
            if waste < worst_waste:
                continue
            spread = _margin_waste(first_box, boxes[second])
            if waste > worst_waste or spread > worst_spread:
                worst_waste, worst_spread, seeds = waste, spread, (first, second)
    return seeds


def _margin_waste(first: Rectangle, second: Rectangle) -> float:
    return first.margin_with(second) - first.margin - second.margin


def _group_that_needs_the_rest(
    groups: tuple[list[int], list[int]], remaining: int, minimum: int
) -> int | None:
    """Grupo que solo llega al mínimo si se queda con todas las entradas que faltan."""
    for position, group in enumerate(groups):
        if len(group) + remaining == minimum:
            return position
    return None


def _pick_next(
    remaining: Sequence[int],
    boxes: Sequence[Rectangle],
    covers: Sequence[Rectangle],
    groups: tuple[list[int], list[int]],
) -> tuple[int, int]:
    """Entrada con la preferencia más marcada y el grupo al que va.

    La preferencia es la diferencia entre lo que crecería cada grupo al recibirla, medida
    en área y, solo si en área empata con la mejor vista hasta ahora, en semiperímetro. Los
    empates que queden se resuelven por el grupo de menor área y después por el que tiene
    menos entradas, para no desequilibrar la división.
    """
    first_cover, second_cover = covers
    first_area, second_area = first_cover.area, second_cover.area
    best_position = 0
    best_difference = -1.0
    best_spread = -1.0
    best_growths = ((0.0, 0.0), (0.0, 0.0))
    for position, index in enumerate(remaining):
        box = boxes[index]
        first_growth = first_cover.area_with(box) - first_area
        second_growth = second_cover.area_with(box) - second_area
        difference = abs(first_growth - second_growth)
        if difference < best_difference:
            continue
        first_margin = first_cover.margin_with(box) - first_cover.margin
        second_margin = second_cover.margin_with(box) - second_cover.margin
        spread = abs(first_margin - second_margin)
        if difference > best_difference or spread > best_spread:
            best_position, best_difference, best_spread = position, difference, spread
            best_growths = ((first_growth, first_margin), (second_growth, second_margin))
    if best_growths[0] != best_growths[1]:
        return best_position, 0 if best_growths[0] < best_growths[1] else 1
    if first_area != second_area:
        return best_position, 0 if first_area < second_area else 1
    return best_position, 0 if len(groups[0]) <= len(groups[1]) else 1
