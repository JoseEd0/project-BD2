"""Lo común a las búsquedas que un índice resuelve desde el WHERE y que puntúan cada fila:
`MATCH` con `SCORE()` (texto) y `SIMILAR_TO` con `SIMILARITY_SCORE()` (multimedia).

Las dos tienen la misma forma. La condición va en el WHERE, unida al resto con `AND`, y la
consume el acceso; el puntaje es una función sin argumentos que se lee como una columna
virtual que el acceso agrega a cada fila; y un `ORDER BY` de ese puntaje, de mayor a menor,
es el orden en que el acceso ya entrega las filas. Este módulo hace esa parte una sola vez;
cada búsqueda aporta solo lo suyo: qué columna, qué índice y qué opciones.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from errors import DomainError
from query.expressions import (
    ColumnSlot,
    RowLayout,
    all_of,
    conjuncts,
    order_key,
    rewritten,
    subexpressions,
)
from query.functions import is_call_to
from query.table import Table
from sql.nodes import (
    EMPTY_OPTIONS,
    ColumnRef,
    Expression,
    FunctionCall,
    Projection,
    SelectStatement,
    SortDirection,
    TableRef,
)

SearchErrorType = type[DomainError]


@dataclass(frozen=True, slots=True)
class ScoreFunction:
    """La función que lee el puntaje de una búsqueda.

    Attributes:
        name: como se escribe en SQL, `SCORE` o `SIMILARITY_SCORE`.
        column: el nombre de la columna virtual que la lee. Lleva paréntesis para que no se
            pueda escribir en SQL sin comillas y no choque con ninguna columna de la tabla.
        label: cómo se llama en el resultado una llamada sin alias.
        error: el error de la búsqueda, para quejarse de una llamada mal escrita.
    """

    name: str
    column: str
    label: str
    error: SearchErrorType

    @property
    def reference(self) -> ColumnRef:
        return ColumnRef(self.column)


def calls_to(name: str, expression: Expression | None) -> list[FunctionCall]:
    """Todas las llamadas a esa función dentro de la expresión."""
    if expression is None:
        return []
    if isinstance(expression, FunctionCall) and is_call_to(expression.name, name):
        return [expression]
    return [call for child in subexpressions(expression) for call in calls_to(name, child)]


def calls_anywhere(statement: SelectStatement, name: str) -> bool:
    """Si la función aparece en alguna parte de la consulta."""
    expressions = [
        *(projection.expression for projection in statement.projections),
        *(join.condition for join in statement.joins),
        statement.where,
        *statement.group_by,
        statement.having,
        *(item.expression for item in statement.order_by),
    ]
    return any(calls_to(name, expression) for expression in expressions)


def is_conjunct(statement: SelectStatement, condition: FunctionCall) -> bool:
    """Si la condición está en el WHERE unida al resto con `AND`, no negada ni en un `OR`."""
    return any(part is condition for part in conjuncts(statement.where))


def searched_source(
    statement: SelectStatement, tables: Mapping[str, Table], column: ColumnRef
) -> tuple[TableRef, ColumnSlot]:
    """La tabla del FROM o de un JOIN a la que pertenece la columna, y su posición.

    Raises:
        UnknownColumnError: si ninguna tabla la tiene.
        AmbiguousColumnError: si la tienen varias y no está cualificada.
    """
    owned = [
        (source, slot)
        for source in sources_of(statement)
        for slot in RowLayout.of_table(tables[source.name.lower()].schema, source.reference).slots
    ]
    combined = RowLayout([slot for _, slot in owned])
    return owned[combined.position_of(column.name, column.qualifier)]


def sources_of(statement: SelectStatement) -> list[TableRef]:
    """Las tablas de la consulta: la del FROM y las de cada JOIN."""
    return [statement.source, *(join.table for join in statement.joins)]


def served(
    statement: SelectStatement, condition: FunctionCall, score: ScoreFunction
) -> SelectStatement:
    """La consulta que queda cuando el acceso ya resolvió la búsqueda.

    La condición sale del WHERE: el acceso solo entrega filas que la cumplen. Cada llamada
    al puntaje pasa a leer la columna virtual del acceso, y el `USING` y el `WITH` ya se
    usaron.

    Raises:
        DomainError: el `score.error`, si una llamada al puntaje lleva argumentos.
    """
    residual = [part for part in conjuncts(statement.where) if part is not condition]
    having = statement.having

    def reading(expression: Expression) -> Expression:
        return rewritten(expression, lambda part: _score_reference(part, score))

    def projected(projection: Projection) -> Projection:
        expression = projection.expression
        if _is_call(expression, score) and projection.alias is None:
            return Projection(reading(expression), score.label)
        return Projection(reading(expression), projection.alias)

    return replace(
        statement,
        projections=tuple(map(projected, statement.projections)),
        joins=tuple(replace(join, condition=reading(join.condition)) for join in statement.joins),
        where=all_of([reading(part) for part in residual]),
        group_by=tuple(map(reading, statement.group_by)),
        having=None if having is None else reading(having),
        order_by=tuple(
            replace(item, expression=reading(item.expression)) for item in statement.order_by
        ),
        search_method=None,
        options=EMPTY_OPTIONS,
    )


def ordered_by(statement: SelectStatement, reference: ColumnRef) -> bool:
    """Si la consulta ordena solo por esa columna de mayor a menor —el orden en que el
    acceso entrega las filas—, también a través de un alias del SELECT."""
    if len(statement.order_by) != 1:
        return False
    item = statement.order_by[0]
    if item.direction is not SortDirection.DESCENDING:
        return False
    return order_key(item.expression, statement.projections) == reference


def _score_reference(expression: Expression, score: ScoreFunction) -> Expression | None:
    if not _is_call(expression, score):
        return None
    if isinstance(expression, FunctionCall) and (
        expression.arguments or expression.keyword_arguments
    ):
        raise score.error(f"{score.name}() no recibe argumentos: es el puntaje de la búsqueda")
    return score.reference


def _is_call(expression: Expression, score: ScoreFunction) -> bool:
    return isinstance(expression, FunctionCall) and is_call_to(expression.name, score.name)
