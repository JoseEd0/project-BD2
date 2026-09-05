"""Evaluación de las expresiones del WHERE, el HAVING y las proyecciones.

El evaluador recorre el AST que produjo el parser y lo resuelve contra una fila concreta.
La resolución de nombres vive en `RowLayout`, que sabe en qué posición de la fila está cada
columna y con qué tabla está cualificada.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from sql.nodes import (
    BetweenPredicate,
    BinaryOperation,
    BinaryOperator,
    ColumnRef,
    Expression,
    FunctionCall,
    InPredicate,
    LikePredicate,
    Literal,
    NullPredicate,
    Star,
    TupleExpression,
    UnaryOperation,
    UnaryOperator,
)
from storage.record import Record
from storage.schema import Schema
from storage.types import Value

LIKE_ANY = "%"
LIKE_ONE = "_"


class ExpressionError(Exception):
    """La expresión no se puede evaluar sobre esta fila."""


class UnknownColumnError(ExpressionError):
    """La columna no existe o no se puede resolver sin ambigüedad."""


class AmbiguousColumnError(ExpressionError):
    """El nombre de columna aparece en más de una tabla del FROM."""


@dataclass(frozen=True, slots=True)
class ColumnSlot:
    """Una columna de la fila en curso, con la tabla de la que viene."""

    qualifier: str | None
    name: str


class RowLayout:
    """Dice en qué posición de la fila está cada columna.

    Un JOIN concatena las filas de las dos tablas, así que el layout resultante es la
    concatenación de los dos layouts y los nombres se desambiguan por su cualificador.
    """

    def __init__(self, slots: Sequence[ColumnSlot]) -> None:
        self._slots = tuple(slots)

    @property
    def slots(self) -> tuple[ColumnSlot, ...]:
        return self._slots

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(slot.name for slot in self._slots)

    def __len__(self) -> int:
        return len(self._slots)

    @classmethod
    def of_table(cls, schema: Schema, qualifier: str) -> RowLayout:
        return cls([ColumnSlot(qualifier=qualifier, name=field.name) for field in schema])

    @classmethod
    def of_names(cls, names: Sequence[str]) -> RowLayout:
        return cls([ColumnSlot(qualifier=None, name=name) for name in names])

    def concat(self, other: RowLayout) -> RowLayout:
        return RowLayout([*self._slots, *other._slots])

    def position_of(self, name: str, qualifier: str | None = None) -> int:
        """Posición de la columna en la fila.

        Raises:
            UnknownColumnError: si ninguna columna encaja.
            AmbiguousColumnError: si encaja más de una.
        """
        matches = [
            position
            for position, slot in enumerate(self._slots)
            if slot.name.lower() == name.lower()
            and (qualifier is None or (slot.qualifier or "").lower() == qualifier.lower())
        ]
        if not matches:
            label = name if qualifier is None else f"{qualifier}.{name}"
            raise UnknownColumnError(f"la columna '{label}' no existe")
        if len(matches) > 1:
            raise AmbiguousColumnError(f"la columna '{name}' es ambigua; cualifícala con la tabla")
        return matches[0]

    def has(self, name: str, qualifier: str | None = None) -> bool:
        try:
            self.position_of(name, qualifier)
        except ExpressionError:
            return False
        return True


class ExpressionEvaluator:
    """Calcula el valor de una expresión sobre una fila."""

    def __init__(self, layout: RowLayout) -> None:
        self._layout = layout

    @property
    def layout(self) -> RowLayout:
        return self._layout

    def evaluate(self, expression: Expression, row: Record) -> Value:
        """Valor de la expresión para esa fila.

        Raises:
            ExpressionError: si la expresión usa algo que el evaluador no soporta.
        """
        if isinstance(expression, Literal):
            return expression.value
        if isinstance(expression, ColumnRef):
            return row[self._layout.position_of(expression.name, expression.qualifier)]
        if isinstance(expression, UnaryOperation):
            return self._unary(expression, row)
        if isinstance(expression, BinaryOperation):
            return self._binary(expression, row)
        if isinstance(expression, BetweenPredicate):
            return self._between(expression, row)
        if isinstance(expression, InPredicate):
            return self._in(expression, row)
        if isinstance(expression, LikePredicate):
            return self._like(expression, row)
        if isinstance(expression, NullPredicate):
            value = self.evaluate(expression.operand, row)
            return (value is not None) if expression.negated else (value is None)
        if isinstance(expression, TupleExpression):
            return tuple(
                float(self._number(self.evaluate(item, row))) for item in expression.elements
            )
        if isinstance(expression, FunctionCall):
            raise ExpressionError(f"la función '{expression.name}' no se puede usar aquí")
        if isinstance(expression, Star):
            raise ExpressionError("'*' no es una expresión de valor")
        raise ExpressionError(f"expresión no soportada: {type(expression).__name__}")

    def validate(self, expression: Expression | None) -> None:
        """Comprueba que la expresión se pueda resolver sobre este layout.

        Se llama al construir el operador, antes de leer ninguna fila. Sin esto, una
        consulta que nombra una columna inexistente sobre una tabla vacía devolvería cero
        filas en silencio en vez de rechazarse, porque el error solo aparecería al evaluar
        la primera fila.

        Raises:
            UnknownColumnError: si alguna columna no existe.
            AmbiguousColumnError: si alguna columna encaja con más de una tabla.
            ExpressionError: si la expresión usa algo que el evaluador no soporta.
        """
        if expression is None:
            return
        if isinstance(expression, Literal):
            return
        if isinstance(expression, ColumnRef):
            self._layout.position_of(expression.name, expression.qualifier)
            return
        if isinstance(expression, FunctionCall):
            raise ExpressionError(f"la función '{expression.name}' no se puede usar aquí")
        if isinstance(expression, Star):
            raise ExpressionError("'*' no es una expresión de valor")
        for operand in _operands_of(expression):
            self.validate(operand)

    def matches(self, expression: Expression | None, row: Record) -> bool:
        """Evalúa un predicado tratando NULL como falso, igual que SQL."""
        if expression is None:
            return True
        return self.evaluate(expression, row) is True

    def _unary(self, expression: UnaryOperation, row: Record) -> Value:
        value = self.evaluate(expression.operand, row)
        if expression.operator is UnaryOperator.NOT:
            return None if value is None else not value
        if value is None:
            return None
        return -self._number(value)

    def _binary(self, expression: BinaryOperation, row: Record) -> Value:
        operator = expression.operator
        if operator is BinaryOperator.AND:
            return self._and(expression, row)
        if operator is BinaryOperator.OR:
            return self._or(expression, row)
        left = self.evaluate(expression.left, row)
        right = self.evaluate(expression.right, row)
        if left is None or right is None:
            return None
        if operator in _COMPARISONS:
            return _COMPARISONS[operator](left, right)
        return self._arithmetic(operator, left, right)

    def _and(self, expression: BinaryOperation, row: Record) -> Value:
        left = self.evaluate(expression.left, row)
        if left is False:
            return False
        right = self.evaluate(expression.right, row)
        if right is False:
            return False
        return None if left is None or right is None else True

    def _or(self, expression: BinaryOperation, row: Record) -> Value:
        left = self.evaluate(expression.left, row)
        if left is True:
            return True
        right = self.evaluate(expression.right, row)
        if right is True:
            return True
        return None if left is None or right is None else False

    def _arithmetic(self, operator: BinaryOperator, left: Value, right: Value) -> Value:
        first, second = self._number(left), self._number(right)
        if operator is BinaryOperator.ADD:
            return first + second
        if operator is BinaryOperator.SUBTRACT:
            return first - second
        if operator is BinaryOperator.MULTIPLY:
            return first * second
        if second == 0:
            raise ExpressionError("división por cero")
        if operator is BinaryOperator.DIVIDE:
            return first / second
        return first % second

    def _between(self, expression: BetweenPredicate, row: Record) -> Value:
        value = self.evaluate(expression.operand, row)
        low = self.evaluate(expression.lower, row)
        high = self.evaluate(expression.upper, row)
        if value is None or low is None or high is None:
            return None
        inside = low <= value <= high  # type: ignore[operator]
        return not inside if expression.negated else inside

    def _in(self, expression: InPredicate, row: Record) -> Value:
        value = self.evaluate(expression.operand, row)
        if value is None:
            return None
        options = [self.evaluate(item, row) for item in expression.values]
        found = value in options
        return not found if expression.negated else found

    def _like(self, expression: LikePredicate, row: Record) -> Value:
        value = self.evaluate(expression.operand, row)
        pattern = self.evaluate(expression.pattern, row)
        if value is None or pattern is None:
            return None
        if not isinstance(value, str) or not isinstance(pattern, str):
            raise ExpressionError("LIKE solo se aplica a texto")
        found = re.fullmatch(like_to_regex(pattern), value) is not None
        return not found if expression.negated else found

    @staticmethod
    def _number(value: Value) -> float:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ExpressionError(f"se esperaba un número y llegó {value!r}")
        return value


def _operands_of(expression: Expression) -> tuple[Expression, ...]:
    """Subexpresiones de un nodo compuesto, para recorrer el árbol sin evaluarlo."""
    if isinstance(expression, UnaryOperation):
        return (expression.operand,)
    if isinstance(expression, BinaryOperation):
        return (expression.left, expression.right)
    if isinstance(expression, BetweenPredicate):
        return (expression.operand, expression.lower, expression.upper)
    if isinstance(expression, InPredicate):
        return (expression.operand, *expression.values)
    if isinstance(expression, LikePredicate):
        return (expression.operand, expression.pattern)
    if isinstance(expression, NullPredicate):
        return (expression.operand,)
    if isinstance(expression, TupleExpression):
        return expression.elements
    raise ExpressionError(f"expresión no soportada: {type(expression).__name__}")


def like_to_regex(pattern: str) -> str:
    """Traduce un patrón LIKE a una expresión regular: `%` es cualquier cosa y `_` un carácter."""
    parts = []
    for character in pattern:
        if character == LIKE_ANY:
            parts.append(".*")
        elif character == LIKE_ONE:
            parts.append(".")
        else:
            parts.append(re.escape(character))
    return "".join(parts)


_COMPARISONS: dict[BinaryOperator, Callable[[Any, Any], bool]] = {
    BinaryOperator.EQUAL: lambda left, right: left == right,
    BinaryOperator.NOT_EQUAL: lambda left, right: left != right,
    BinaryOperator.LESS: lambda left, right: left < right,
    BinaryOperator.LESS_EQUAL: lambda left, right: left <= right,
    BinaryOperator.GREATER: lambda left, right: left > right,
    BinaryOperator.GREATER_EQUAL: lambda left, right: left >= right,
}
