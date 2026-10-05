"""Lo que comparten las dos formas de reunir tablas: por hash y por bucles anidados.

Una reunión entrega pares `(fila izquierda, fila derecha)`. En una reunión externa, la fila
que no encontró pareja se entrega igualmente, con `None` en el lugar de la otra.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Flag, auto

Pair = tuple[bytes | None, bytes | None]
PairTest = Callable[[bytes, bytes], bool]


class Unmatched(Flag):
    """De qué lado se conservan las filas que no encontraron pareja."""

    NONE = 0
    LEFT = auto()
    RIGHT = auto()
    BOTH = LEFT | RIGHT


def any_pair(_left: bytes, _right: bytes) -> bool:
    """Condición de una reunión sin más requisito que el que ya aplicó quien la llama."""
    return True
