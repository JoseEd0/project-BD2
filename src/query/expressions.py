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

from query.comparison import (
    ComparisonError,
    comparable,
    describe_value,
    literal_for,
    orderable,
    plainly_comparable,
)
from query.functions import (
    AGGREGATE_NAMES,
    FunctionError,
    ScalarFunction,
    scalar_function,
    scalar_function_names,
)
from spatial.geometry import GeometryError
from spatial.metrics import UnknownMetricError
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
    Projection,
    Star,
    TupleExpression,
    UnaryOperation,
    UnaryOperator,
)
from storage.record import Record
from storage.schema import Field, Schema
from storage.types import FieldType, Value, ValueTooLargeError, real_from

LIKE_ANY = "%"
LIKE_ONE = "_"
# Una columna de texto necesita longitud; la de un literal vacío no puede ser cero.
MINIMUM_TEXT_LENGTH = 1
NO_ROW: Record = ()
NOT_CONSTANT = object()


class ExpressionError(Exception):
    """La expresión no se puede evaluar sobre esta fila."""


class UnknownColumnError(ExpressionError):
    """La columna no existe o no se puede resolver sin ambigüedad."""


class AmbiguousColumnError(ExpressionError):
    """El nombre de columna aparece en más de una tabla del FROM."""


@dataclass(frozen=True, slots=True)
class ColumnSlot:
    """Una columna de la fila en curso, con la tabla de la que viene.

    `field` es su definición cuando la columna sale tal cual de una tabla; permite
    comprobar los tipos de una condición antes de leer ninguna fila.
    """

    qualifier: str | None
    name: str
    field: Field | None = None


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

    @classmethod
    def of_table(cls, schema: Schema, qualifier: str) -> RowLayout:
        return cls(
            [ColumnSlot(qualifier=qualifier, name=field.name, field=field) for field in schema]
        )

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


# Layout de las expresiones que no pueden nombrar columnas, como los valores de un INSERT.
NO_COLUMNS = RowLayout(())


class ExpressionEvaluator:
    """Calcula el valor de una expresión sobre una fila.

    `validate` hace además el trabajo que no depende de la fila: resuelve cada función y
    calcula una sola vez las llamadas constantes, como el `POINT(…)` o el `POLYGON(…)` de
    una consulta espacial, que de otro modo se reconstruirían por cada fila recorrida.
    """

    def __init__(self, layout: RowLayout) -> None:
        self._layout = layout
        self._positions: dict[ColumnRef, int] = {}
        # Las claves son identidades de nodos del AST; guardar el nodo junto al valor
        # impide que se libere y que otro objeto herede su identidad.
        self._functions: dict[int, tuple[FunctionCall, ScalarFunction]] = {}
        self._folded: dict[int, tuple[FunctionCall, Value]] = {}

    def evaluate(self, expression: Expression, row: Record) -> Value:
        """Valor de la expresión para esa fila.

        Raises:
            ExpressionError: si la expresión usa algo que el evaluador no soporta.
        """
        if isinstance(expression, Literal):
            return expression.value
        if isinstance(expression, ColumnRef):
            return row[self._position_of(expression)]
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
            return self._coordinates(expression, row)
        if isinstance(expression, FunctionCall):
            return self._call(expression, row)
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
            ExpressionError: si la expresión usa algo que el evaluador no soporta,
                compara una columna con una constante de otro tipo o contiene una
                constante que no se puede calcular, como `1 / 0`.
        """
        if expression is None:
            return
        if isinstance(expression, Literal):
            return
        if isinstance(expression, ColumnRef):
            self._position_of(expression)
            return
        if isinstance(expression, FunctionCall):
            self._validate_call(expression)
            return
        if isinstance(expression, Star):
            raise ExpressionError("'*' no es una expresión de valor")
        for operand in _operands_of(expression):
            self.validate(operand)
        if _is_constant(expression):
            self.evaluate(expression, NO_ROW)
        for column, value in _column_constant_pairs(expression):
            field = self._layout.slots[self._position_of(column)].field
            if field is not None and value is not None:
                column_literal(field, value)

    def _coordinates(self, expression: TupleExpression, row: Record) -> Value:
        """Una tupla de números; NULL si alguno de sus componentes lo es."""
        values = [self.evaluate(item, row) for item in expression.elements]
        if any(value is None for value in values):
            return None
        return tuple(self._real(value) for value in values)

    def matches(self, expression: Expression | None, row: Record) -> bool:
        """Evalúa un predicado tratando NULL como falso, igual que SQL."""
        if expression is None:
            return True
        return self.evaluate(expression, row) is True

    def _position_of(self, column: ColumnRef) -> int:
        position = self._positions.get(column)
        if position is None:
            position = self._layout.position_of(column.name, column.qualifier)
            self._positions[column] = position
        return position

    def _validate_call(self, call: FunctionCall) -> None:
        function = self._function_of(call)
        for argument in call.arguments:
            self.validate(argument)
        options = call.keyword_arguments.values()
        for option in options:
            if _symbol_of(option) is None:
                self.validate(option)
        try:
            self._check_column_arguments(function, call)
        except FunctionError as error:
            raise ExpressionError(f"{call.name}: {error}") from error
        if _is_constant(call):
            self._folded[id(call)] = (call, self._call(call, NO_ROW))
        elif all(map(_is_constant_option, options)):
            self._rehearse(function, call)

    def _rehearse(self, function: ScalarFunction, call: FunctionCall) -> None:
        """Ensaya la llamada con NULL en los argumentos que dependen de la fila.

        Toda función admite NULL en cualquier argumento, así que si el ensayo falla es por
        un argumento constante o por una opción: un error que ninguna fila va a arreglar,
        y que así se detecta también sobre una tabla vacía.
        """
        arguments = [
            self.evaluate(argument, NO_ROW) if _is_constant(argument) else None
            for argument in call.arguments
        ]
        try:
            function.implementation(arguments, self.options_of(call, NO_ROW))
        except (FunctionError, GeometryError, UnknownMetricError) as error:
            raise ExpressionError(f"{call.name}: {error}") from error

    def _check_column_arguments(self, function: ScalarFunction, call: FunctionCall) -> None:
        """Comprueba el tipo de los argumentos que son una columna de una tabla."""
        for position, argument in enumerate(call.arguments):
            if not isinstance(argument, ColumnRef):
                continue
            field = self._layout.slots[self._position_of(argument)].field
            if field is not None:
                function.check_column(position, field.name, field.type)

    def _call(self, call: FunctionCall, row: Record) -> Value:
        folded = self._folded.get(id(call))
        if folded is not None:
            return folded[1]
        function = self._function_of(call)
        arguments = [self.evaluate(argument, row) for argument in call.arguments]
        try:
            result: Value = function.implementation(arguments, self.options_of(call, row))
        except (FunctionError, GeometryError, UnknownMetricError) as error:
            raise ExpressionError(f"{call.name}: {error}") from error
        return result

    def options_of(self, call: FunctionCall, row: Record) -> dict[str, Value]:
        """Valores de los argumentos con nombre de una llamada, como `metrica='euclidiana'`."""
        return {
            name: _symbol_of(option) or self.evaluate(option, row)
            for name, option in call.keyword_arguments.items()
        }

    def _function_of(self, call: FunctionCall) -> ScalarFunction:
        """Función a la que llama el nodo, con la forma de la llamada ya comprobada.

        Raises:
            ExpressionError: si la función no existe, es de agregación o recibe un número
                de argumentos que no admite.
        """
        resolved = self._functions.get(id(call))
        if resolved is not None:
            return resolved[1]
        function = scalar_function(call.name)
        if function is None:
            raise ExpressionError(_unusable_function_message(call.name))
        try:
            function.check_call(len(call.arguments), list(call.keyword_arguments))
        except FunctionError as error:
            raise ExpressionError(str(error)) from error
        self._functions[id(call)] = (call, function)
        return function

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
            if plainly_comparable(left, right):
                return _COMPARISONS[operator](left, right)
            return _compare(operator, left, right)
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
        inside = _compare(BinaryOperator.GREATER_EQUAL, value, low) and _compare(
            BinaryOperator.LESS_EQUAL, value, high
        )
        return not inside if expression.negated else inside

    def _in(self, expression: InPredicate, row: Record) -> Value:
        value = self.evaluate(expression.operand, row)
        if value is None:
            return None
        options = [self.evaluate(item, row) for item in expression.values]
        found = any(
            option is not None and _compare(BinaryOperator.EQUAL, value, option)
            for option in options
        )
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

    def _real(self, value: Value) -> float:
        try:
            return real_from(self._number(value))
        except ValueTooLargeError as error:
            raise ExpressionError(str(error)) from error


def column_literal(field: Field, value: Value) -> Value:
    """El literal de una condición como valor de la columna con la que se compara.

    Raises:
        ExpressionError: si un valor así no se puede comparar con esa columna.
    """
    try:
        return literal_for(field, value)
    except ComparisonError as error:
        raise ExpressionError(str(error)) from error


def constant_value(expression: Expression) -> Any:
    """Valor de una expresión que no depende de ninguna fila, o `NOT_CONSTANT`.

    Es lo que permite tratar `-5`, `2 + 8` o `POINT(-12.0, -77.0)` igual que un literal:
    comprobar su tipo antes de ejecutar y buscarlo en un índice.
    """
    if not _is_constant(expression):
        return NOT_CONSTANT
    try:
        return ExpressionEvaluator(NO_COLUMNS).evaluate(expression, NO_ROW)
    except ExpressionError:
        return NOT_CONSTANT


def describe_expression(expression: Expression) -> str:
    """Texto de una expresión tal como se escribiría en SQL: nombra una columna calculada
    del resultado y un paso del plan de ejecución."""
    if isinstance(expression, ColumnRef):
        prefix = "" if expression.qualifier is None else f"{expression.qualifier}."
        return f"{prefix}{expression.name}"
    if isinstance(expression, Literal):
        return describe_value(expression.value)
    if isinstance(expression, Star):
        return "*"
    if isinstance(expression, FunctionCall):
        arguments = ", ".join(describe_expression(item) for item in expression.arguments)
        return f"{expression.name}({arguments})"
    if isinstance(expression, UnaryOperation):
        separator = " " if expression.operator is UnaryOperator.NOT else ""
        return f"{expression.operator.value}{separator}{describe_expression(expression.operand)}"
    if isinstance(expression, BinaryOperation):
        level = _PRECEDENCE[expression.operator]
        left = _describe_operand(expression.left, level)
        right = _describe_operand(expression.right, level + 1)
        return f"{left} {expression.operator.value} {right}"
    if isinstance(expression, TupleExpression):
        return f"({', '.join(describe_expression(item) for item in expression.elements)})"
    return _describe_predicate(expression)


def _describe_operand(operand: Expression, minimum_level: int) -> str:
    """Texto de un operando, entre paréntesis si sin ellos se leería con otra precedencia.

    Al operando derecho se le exige un nivel más: `a - (b - c)` necesita los suyos.
    """
    shown = describe_expression(operand)
    loose = isinstance(operand, BinaryOperation) and _PRECEDENCE[operand.operator] < minimum_level
    return f"({shown})" if loose else shown


def _describe_predicate(expression: Expression) -> str:
    if isinstance(expression, NullPredicate):
        ending = "IS NOT NULL" if expression.negated else "IS NULL"
        return f"{describe_expression(expression.operand)} {ending}"
    if isinstance(expression, BetweenPredicate | InPredicate | LikePredicate):
        operand = describe_expression(expression.operand)
        negation = "NOT " if expression.negated else ""
        if isinstance(expression, BetweenPredicate):
            low, high = describe_expression(expression.lower), describe_expression(expression.upper)
            return f"{operand} {negation}BETWEEN {low} AND {high}"
        if isinstance(expression, InPredicate):
            options = ", ".join(describe_expression(item) for item in expression.values)
            return f"{operand} {negation}IN ({options})"
        return f"{operand} {negation}LIKE {describe_expression(expression.pattern)}"
    return type(expression).__name__


def subexpressions(expression: Expression) -> tuple[Expression, ...]:
    """Operandos directos de una expresión; vacío si es una columna, un literal o `*`.

    De los argumentos con nombre de una función solo cuentan los que son valores: un
    `metrica=euclidiana` es el nombre de una opción, no una columna.
    """
    if isinstance(expression, Literal | ColumnRef | Star):
        return ()
    if isinstance(expression, FunctionCall):
        options = [
            item for item in expression.keyword_arguments.values() if _symbol_of(item) is None
        ]
        return (*expression.arguments, *options)
    return _operands_of(expression)


def rewritten(
    expression: Expression, replace: Callable[[Expression], Expression | None]
) -> Expression:
    """Copia de la expresión con cada subexpresión que `replace` reconoce sustituida.

    `replace` devuelve la sustituta, o `None` para que se siga buscando en sus operandos.
    """
    replacement = replace(expression)
    if replacement is not None:
        return replacement

    def again(operand: Expression) -> Expression:
        return rewritten(operand, replace)

    if isinstance(expression, UnaryOperation):
        return UnaryOperation(expression.operator, again(expression.operand))
    if isinstance(expression, BinaryOperation):
        return BinaryOperation(expression.operator, again(expression.left), again(expression.right))
    if isinstance(expression, BetweenPredicate):
        return BetweenPredicate(
            again(expression.operand),
            again(expression.lower),
            again(expression.upper),
            expression.negated,
        )
    if isinstance(expression, InPredicate):
        return InPredicate(
            again(expression.operand), tuple(map(again, expression.values)), expression.negated
        )
    if isinstance(expression, LikePredicate):
        return LikePredicate(
            again(expression.operand), again(expression.pattern), expression.negated
        )
    if isinstance(expression, NullPredicate):
        return NullPredicate(again(expression.operand), expression.negated)
    if isinstance(expression, TupleExpression):
        return TupleExpression(tuple(map(again, expression.elements)))
    if isinstance(expression, FunctionCall):
        options = {
            name: option if _symbol_of(option) is not None else again(option)
            for name, option in expression.keyword_arguments.items()
        }
        return FunctionCall(expression.name, tuple(map(again, expression.arguments)), options)
    return expression


def result_field(name: str, expression: Expression, layout: RowLayout, schema: Schema) -> Field:
    """Columna que puede guardar el resultado de la expresión, con el nombre dado.

    Hace falta cuando una columna calculada tiene que pasar por disco, como la clave de un
    `GROUP BY` que después se ordena. `layout` y `schema` son los de las filas sobre las
    que se evalúa.

    Raises:
        ExpressionError: si el resultado no es de un tipo que se pueda guardar.
    """
    kind, length = _result_type(expression, layout, schema)
    return Field(name, kind, length)


def _result_type(
    expression: Expression, layout: RowLayout, schema: Schema
) -> tuple[FieldType, int | None]:
    if isinstance(expression, ColumnRef):
        source = schema.fields[layout.position_of(expression.name, expression.qualifier)]
        return source.type, source.length
    if isinstance(expression, Literal):
        return _literal_type(expression.value)
    if isinstance(expression, UnaryOperation) and expression.operator is UnaryOperator.NEGATE:
        return _result_type(expression.operand, layout, schema)
    if isinstance(expression, BinaryOperation) and expression.operator in _ARITHMETIC_OPERATORS:
        sides = {
            _result_type(operand, layout, schema)[0]
            for operand in (expression.left, expression.right)
        }
        whole = sides == {FieldType.INT} and expression.operator is not BinaryOperator.DIVIDE
        return (FieldType.INT if whole else FieldType.FLOAT), None
    if isinstance(expression, FunctionCall):
        return _function_result_type(expression), None
    if isinstance(expression, TupleExpression | Star):
        shown = describe_expression(expression)
        raise ExpressionError(f"'{shown}' no se puede guardar en una columna")
    return FieldType.BOOL, None


def _literal_type(value: object) -> tuple[FieldType, int | None]:
    if isinstance(value, bool):
        return FieldType.BOOL, None
    if isinstance(value, float):
        return FieldType.FLOAT, None
    if isinstance(value, str):
        return FieldType.STRING, max(len(value.encode()), MINIMUM_TEXT_LENGTH)
    return FieldType.INT, None


def _function_result_type(call: FunctionCall) -> FieldType:
    function = scalar_function(call.name)
    if function is None or function.result is None:
        raise ExpressionError(f"el resultado de '{call.name}' no se puede guardar en una columna")
    return function.result


def order_key(
    expression: Expression, projections: Sequence[Projection], layout: RowLayout
) -> Expression:
    """Expresión a la que se refiere una clave del ORDER BY o del GROUP BY.

    La clave puede nombrar un alias del SELECT, que se sustituye por su expresión. Si
    hay una columna real con ese nombre, gana la columna. Un alias de una agregación se
    deja como está: la agrupación ya produjo una columna con ese nombre. Un entero es
    la posición de una columna del SELECT: `ORDER BY 2` ordena por la segunda.

    Raises:
        ExpressionError: si la posición no corresponde a ninguna columna del SELECT.
    """
    if isinstance(expression, Literal):
        return _projection_at(expression, projections)
    if not isinstance(expression, ColumnRef) or expression.qualifier is not None:
        return expression
    if layout.has(expression.name):
        return expression
    for projection in projections:
        if projection.alias is None or projection.alias.lower() != expression.name.lower():
            continue
        aliased = projection.expression
        if isinstance(aliased, FunctionCall) and aliased.name.upper() in AGGREGATE_NAMES:
            return expression
        return aliased
    return expression


def _projection_at(position: Literal, projections: Sequence[Projection]) -> Expression:
    """Expresión de la columna del SELECT que ocupa esa posición, contando desde 1."""
    number = position.value
    if isinstance(number, bool) or not isinstance(number, int):
        return position
    if not 1 <= number <= len(projections):
        raise ExpressionError(
            f"la posición {number} no existe: el SELECT tiene {len(projections)} columna(s)"
        )
    chosen = projections[number - 1].expression
    if isinstance(chosen, Star):
        raise ExpressionError("una posición no puede referirse a '*': nombra la columna")
    return chosen


def conjuncts(expression: Expression | None) -> list[Expression]:
    """Descompone una condición en los AND de primer nivel."""
    if expression is None:
        return []
    if isinstance(expression, BinaryOperation) and expression.operator is BinaryOperator.AND:
        return [*conjuncts(expression.left), *conjuncts(expression.right)]
    return [expression]


def _unusable_function_message(name: str) -> str:
    if name.upper() in AGGREGATE_NAMES:
        return f"la función de agregación '{name}' no se puede usar aquí"
    available = ", ".join(scalar_function_names())
    return f"la función '{name}' no existe; las disponibles son: {available}"


def _symbol_of(option: Expression) -> str | None:
    """Nombre de un argumento con nombre escrito sin comillas, como `metrica=euclidiana`.

    El parser lo entrega como referencia a columna, pero en esa posición es el nombre de
    una opción: la misma convención que `WITH METRIC=cosine`.
    """
    if isinstance(option, ColumnRef) and option.qualifier is None:
        return option.name
    return None


def _is_constant_option(option: Expression) -> bool:
    """Si un argumento con nombre vale lo mismo para todas las filas."""
    return _symbol_of(option) is not None or _is_constant(option)


def _is_constant(expression: Expression) -> bool:
    """Si el valor de la expresión no depende de la fila."""
    if isinstance(expression, Literal):
        return True
    if isinstance(expression, ColumnRef | Star):
        return False
    if isinstance(expression, FunctionCall):
        options = [
            item for item in expression.keyword_arguments.values() if _symbol_of(item) is None
        ]
        return all(_is_constant(item) for item in (*expression.arguments, *options))
    return all(_is_constant(operand) for operand in _operands_of(expression))


def _column_constant_pairs(expression: Expression) -> list[tuple[ColumnRef, Any]]:
    """Cada columna que la expresión compara directamente con una constante, y su valor."""
    if isinstance(expression, BinaryOperation) and expression.operator in _COMPARISONS:
        candidates = [(expression.left, expression.right), (expression.right, expression.left)]
    elif isinstance(expression, BetweenPredicate):
        candidates = [(expression.operand, bound) for bound in (expression.lower, expression.upper)]
    elif isinstance(expression, InPredicate):
        candidates = [(expression.operand, option) for option in expression.values]
    else:
        return []
    return [
        (column, value)
        for column, other in candidates
        if isinstance(column, ColumnRef) and (value := constant_value(other)) is not NOT_CONSTANT
    ]


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


def _compare(operator: BinaryOperator, left: Value, right: Value) -> bool:
    """Resultado de comparar dos valores no nulos.

    Raises:
        ExpressionError: si no son comparables entre sí, o no con ese operador.
    """
    prepare = comparable if operator in _EQUALITY_OPERATORS else orderable
    try:
        first, second = prepare(left, right)
    except ComparisonError as error:
        raise ExpressionError(str(error)) from error
    return _COMPARISONS[operator](first, second)


_EQUALITY_OPERATORS = frozenset({BinaryOperator.EQUAL, BinaryOperator.NOT_EQUAL})
_PRECEDENCE = {
    BinaryOperator.OR: 1,
    BinaryOperator.AND: 2,
    BinaryOperator.EQUAL: 3,
    BinaryOperator.NOT_EQUAL: 3,
    BinaryOperator.LESS: 3,
    BinaryOperator.LESS_EQUAL: 3,
    BinaryOperator.GREATER: 3,
    BinaryOperator.GREATER_EQUAL: 3,
    BinaryOperator.ADD: 4,
    BinaryOperator.SUBTRACT: 4,
    BinaryOperator.MULTIPLY: 5,
    BinaryOperator.DIVIDE: 5,
    BinaryOperator.MODULO: 5,
}
_ARITHMETIC_OPERATORS = frozenset(
    {
        BinaryOperator.ADD,
        BinaryOperator.SUBTRACT,
        BinaryOperator.MULTIPLY,
        BinaryOperator.DIVIDE,
        BinaryOperator.MODULO,
    }
)
_COMPARISONS: dict[BinaryOperator, Callable[[Any, Any], bool]] = {
    BinaryOperator.EQUAL: lambda left, right: left == right,
    BinaryOperator.NOT_EQUAL: lambda left, right: left != right,
    BinaryOperator.LESS: lambda left, right: left < right,
    BinaryOperator.LESS_EQUAL: lambda left, right: left <= right,
    BinaryOperator.GREATER: lambda left, right: left > right,
    BinaryOperator.GREATER_EQUAL: lambda left, right: left >= right,
}
