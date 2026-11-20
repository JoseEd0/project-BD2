"""La distancia de edición con transposiciones (alineamiento óptimo de cadenas)."""

from __future__ import annotations


def edit_distance(left: str, right: str, limit: int) -> int:
    """Inserciones, borrados, sustituciones y transposiciones de letras vecinas que llevan
    `left` a `right`; si pasa de `limit`, devuelve `limit + 1` sin terminar la tabla.

    Complejidad: O(len(left) · len(right)), con corte temprano por fila.
    """
    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous_previous: list[int] = []
    previous = list(range(len(right) + 1))
    for row, left_char in enumerate(left, start=1):
        current = [row] + [0] * len(right)
        for column, right_char in enumerate(right, start=1):
            cost = 0 if left_char == right_char else 1
            best = min(previous[column] + 1, current[column - 1] + 1, previous[column - 1] + cost)
            if (
                row > 1
                and column > 1
                and left_char == right[column - 2]
                and left[row - 2] == right_char
            ):
                best = min(best, previous_previous[column - 2] + 1)
            current[column] = best
        if min(current) > limit:
            return limit + 1
        previous_previous, previous = previous, current
    return min(previous[-1], limit + 1)
