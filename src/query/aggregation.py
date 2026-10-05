"""Funciones de agregación calculadas sobre la marcha.

Una agregación no necesita las filas de su grupo, solo lo que saca de cada una: cuántas ha
visto, cuánto suman, cuál es la menor. Eso es lo que guarda un `Accumulator`, y por eso
agrupar ocupa memoria en proporción al número de grupos y no al de filas.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from operator import gt, lt
from typing import Any

from query.expressions import ExpressionError
from query.functions import AggregateKind
from sql.nodes import Expression
from storage.types import FieldType, Value

NUMERIC_TYPES = frozenset({FieldType.INT, FieldType.FLOAT})
# Un punto o un vector se pueden comparar por igualdad, pero ninguno es «menor» que otro.
UNORDERED_TYPES = frozenset({FieldType.POINT, FieldType.VECTOR})
ADDING_KINDS = frozenset({AggregateKind.SUM, AggregateKind.AVG})
EXTREME_KINDS = frozenset({AggregateKind.MIN, AggregateKind.MAX})


@dataclass(frozen=True, slots=True)
class Aggregate:
    """Una función de agregación aplicada a una expresión (o a `*` en el caso de COUNT)."""

    kind: AggregateKind
    argument: Expression | None
    label: str


class Accumulator(ABC):
    """Resultado parcial de una agregación sobre los valores que ha recibido hasta ahora."""

    @abstractmethod
    def add(self, value: Value) -> None:
        """Incorpora un valor, que nunca es NULL: las agregaciones los ignoran."""

    @abstractmethod
    def result(self) -> Value:
        """Valor de la agregación; NULL si no recibió nada, salvo en `COUNT`."""


class _Count(Accumulator):
    def __init__(self) -> None:
        self._count = 0

    def add(self, value: Value) -> None:
        self._count += 1

    def result(self) -> Value:
        return self._count


class _Sum(Accumulator):
    """Suma exacta de enteros y suma compensada de reales (Neumaier).

    Sumar reales de uno en uno arrastra el error de redondeo de cada paso. La compensación
    guarda aparte lo que cada suma pierde y lo devuelve al final, así que el total no
    depende de cuántas filas tenga el grupo ni de en qué orden lleguen.
    """

    def __init__(self, label: str) -> None:
        self._label = label
        self._count = 0
        self._total: int | float = 0
        self._lost = 0.0

    def add(self, value: Value) -> None:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ExpressionError(f"{self._label} necesita valores numéricos y llegó {value!r}")
        self._count += 1
        if isinstance(value, int) and isinstance(self._total, int):
            self._total += value
            return
        partial = self._total + value
        if abs(self._total) >= abs(value):
            self._lost += (self._total - partial) + value
        else:
            self._lost += (value - partial) + self._total
        self._total = partial

    def result(self) -> Value:
        return self._sum() if self._count else None

    def _sum(self) -> int | float:
        if isinstance(self._total, int) or not math.isfinite(self._lost):
            return self._total
        return self._total + self._lost


class _Average(_Sum):
    def result(self) -> Value:
        return self._sum() / self._count if self._count else None


class _Extreme(Accumulator):
    """El menor o el mayor valor recibido; entre iguales, el primero."""

    def __init__(self, beats: Callable[[Any, Any], bool]) -> None:
        self._beats = beats
        self._best: Value = None

    def add(self, value: Value) -> None:
        if self._best is None or self._beats(value, self._best):
            self._best = value

    def result(self) -> Value:
        return self._best


def check_argument_type(aggregate: Aggregate, argument: FieldType) -> None:
    """Comprueba que la agregación se pueda aplicar a valores de ese tipo.

    Se hace al planificar, con el tipo de la expresión: así `SUM(nombre)` se rechaza
    también sobre una tabla vacía, en vez de devolver NULL sin avisar.

    Raises:
        ExpressionError: si se suma algo que no es un número o se busca el menor o el
            mayor de valores que no tienen orden.
    """
    if aggregate.kind in ADDING_KINDS and argument not in NUMERIC_TYPES:
        raise ExpressionError(
            f"{aggregate.label} necesita valores numéricos y su argumento es {argument.value}"
        )
    if aggregate.kind in EXTREME_KINDS and argument in UNORDERED_TYPES:
        raise ExpressionError(
            f"{aggregate.label} necesita valores con orden y un {argument.value} no lo tiene"
        )


def accumulator_for(aggregate: Aggregate) -> Accumulator:
    """Acumulador vacío para una agregación."""
    if aggregate.kind is AggregateKind.COUNT:
        return _Count()
    if aggregate.kind is AggregateKind.SUM:
        return _Sum(aggregate.label)
    if aggregate.kind is AggregateKind.AVG:
        return _Average(aggregate.label)
    return _Extreme(lt if aggregate.kind is AggregateKind.MIN else gt)
