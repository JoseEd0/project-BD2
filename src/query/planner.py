"""Planificador: convierte una sentencia SELECT en un árbol de operadores.

Su única decisión interesante es la **elección del camino de acceso**: si el WHERE contiene
una condición sobre una columna indexada, se usa el índice; si no, se recorre la tabla. Esa
decisión es la que el panel de plan de ejecución enseña al usuario.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from config import EngineConfig
from query.catalog import Organization
from query.expressions import RowLayout, UnknownColumnError
from query.operators import (
    AGGREGATE_NAMES,
    GROUP_DIRECTORY,
    JOIN_DIRECTORY,
    SORT_DIRECTORY,
    Aggregate,
    Distinct,
    Filter,
    HashAggregate,
    HashJoin,
    IndexLookup,
    IndexRange,
    LimitOffset,
    Operator,
    PrimaryKeyLookup,
    PrimaryKeyRange,
    Projection,
    SequentialScan,
    Sort,
    UnsupportedQueryError,
)
from query.table import Table
from sql.nodes import (
    BetweenPredicate,
    BinaryOperation,
    BinaryOperator,
    ColumnRef,
    Expression,
    FunctionCall,
    IndexType,
    JoinKind,
    Literal,
    SelectStatement,
    Star,
    TableRef,
    UnaryOperation,
)
from sql.nodes import (
    Projection as SqlProjection,
)
from storage.types import Value

ORDERED_ORGANIZATIONS = frozenset({Organization.SEQUENTIAL, Organization.CLUSTERED_BTREE})
RANGE_OPERATORS = {
    BinaryOperator.LESS,
    BinaryOperator.LESS_EQUAL,
    BinaryOperator.GREATER,
    BinaryOperator.GREATER_EQUAL,
}


@dataclass(frozen=True, slots=True)
class AccessPath:
    """Camino de acceso elegido para una tabla."""

    operator: Operator
    used_index: str | None


class Planner:
    """Construye el árbol de operadores de una consulta."""

    def __init__(self, tables: Mapping[str, Table], config: EngineConfig) -> None:
        self._tables = tables
        self._config = config
        self._temporaries = 0
        self._single_source = True

    def plan(self, statement: SelectStatement) -> Operator:
        """Árbol de operadores listo para ejecutar.

        Raises:
            UnsupportedQueryError: si la consulta usa algo que el ejecutor no implementa.
        """
        self._single_source = not statement.joins
        source = self._scan_of(statement.source, statement.where)
        operator = self._join_all(source, statement)
        if statement.where is not None:
            operator = Filter(operator, statement.where)
        aggregates, labels = self._aggregates_of(statement)
        if aggregates or statement.group_by:
            operator = HashAggregate(
                operator,
                statement.group_by,
                aggregates,
                self._temporary(GROUP_DIRECTORY),
                self._config,
            )
            if statement.having is not None:
                operator = Filter(operator, _replace_aggregates(statement.having, labels))
        elif statement.having is not None:
            raise UnsupportedQueryError("HAVING necesita GROUP BY o funciones de agregación")
        operator = self._order(operator, statement)
        operator = self._project(operator, statement, aggregates)
        if statement.distinct:
            operator = Distinct(operator)
        if statement.limit is not None or statement.offset is not None:
            operator = LimitOffset(operator, statement.limit, statement.offset)
        return operator

    def _scan_of(self, reference: TableRef, where: Expression | None) -> Operator:
        table = self._table(reference.name)
        alias = reference.alias or reference.name
        return self._access_path(table, alias, where).operator

    def _access_path(self, table: Table, alias: str, where: Expression | None) -> AccessPath:
        """Elige entre índice y recorrido completo mirando las condiciones del WHERE."""
        for condition in _conjuncts(where):
            path = self._path_for(table, alias, condition)
            if path is not None:
                return path
        return AccessPath(SequentialScan(table, alias), used_index=None)

    def _path_for(self, table: Table, alias: str, condition: Expression) -> AccessPath | None:
        equality = self._equality_on_column(condition, alias)
        if equality is not None:
            return self._equality_path(table, alias, *equality)
        interval = self._range_on_column(condition, alias)
        if interval is not None:
            return self._range_path(table, alias, *interval)
        return None

    def _equality_on_column(self, condition: Expression, alias: str) -> tuple[str, Value] | None:
        if not isinstance(condition, BinaryOperation):
            return None
        if condition.operator is not BinaryOperator.EQUAL:
            return None
        sides = ((condition.left, condition.right), (condition.right, condition.left))
        for column_side, value_side in sides:
            column = self._column_named(column_side, alias)
            if column is not None and isinstance(value_side, Literal):
                return column, value_side.value
        return None

    def _range_on_column(
        self, condition: Expression, alias: str
    ) -> tuple[str, Value | None, Value | None] | None:
        if isinstance(condition, BetweenPredicate) and not condition.negated:
            return self._between_bounds(condition, alias)
        if not isinstance(condition, BinaryOperation) or condition.operator not in RANGE_OPERATORS:
            return None
        column = self._column_named(condition.left, alias)
        if column is None or not isinstance(condition.right, Literal):
            return None
        value = condition.right.value
        if condition.operator in (BinaryOperator.LESS, BinaryOperator.LESS_EQUAL):
            return column, None, value
        return column, value, None

    def _between_bounds(
        self, condition: BetweenPredicate, alias: str
    ) -> tuple[str, Value | None, Value | None] | None:
        column = self._column_named(condition.operand, alias)
        if column is None or not isinstance(condition.lower, Literal):
            return None
        if not isinstance(condition.upper, Literal):
            return None
        return column, condition.lower.value, condition.upper.value

    def _column_named(self, expression: Expression, alias: str) -> str | None:
        """Nombre de columna si la condición se refiere sin ambigüedad a esta tabla.

        Con varias tablas en juego, una columna sin cualificar podría ser de cualquiera de
        ellas: acotar el acceso por ella dejaría fuera filas que sí cumplen. Por eso solo se
        aprovecha una condición sin cualificar cuando hay una única tabla.
        """
        if not isinstance(expression, ColumnRef):
            return None
        if expression.qualifier is None:
            return expression.name if self._single_source else None
        return expression.name if expression.qualifier.lower() == alias.lower() else None

    def _equality_path(
        self, table: Table, alias: str, column: str, value: Value
    ) -> AccessPath | None:
        """Un índice explícito gana a la organización: en un heap file buscar por clave
        primaria sin índice sería un recorrido completo."""
        index = table.definition.index_on(column)
        if index is not None:
            return AccessPath(IndexLookup(table, alias, index.name, value), used_index=index.name)
        if self._is_primary_key(table, column):
            return AccessPath(PrimaryKeyLookup(table, alias, value), used_index=None)
        return None

    def _range_path(
        self, table: Table, alias: str, column: str, low: Value | None, high: Value | None
    ) -> AccessPath | None:
        if self._is_primary_key(table, column) and table.organization in ORDERED_ORGANIZATIONS:
            return AccessPath(PrimaryKeyRange(table, alias, low, high), used_index=None)
        index = table.definition.index_on(column)
        if index is None or index.method is not IndexType.BTREE:
            return None
        return AccessPath(
            IndexRange(table, alias, index.name, low, high), used_index=index.name
        )

    @staticmethod
    def _is_primary_key(table: Table, column: str) -> bool:
        key = table.primary_key
        return key is not None and key.lower() == column.lower()

    def _join_all(self, operator: Operator, statement: SelectStatement) -> Operator:
        for join in statement.joins:
            if join.kind is not JoinKind.INNER:
                raise UnsupportedQueryError(
                    f"solo se implementa INNER JOIN; llegó {join.kind.value}"
                )
            table = self._table(join.table.name)
            alias = join.table.alias or join.table.name
            right = self._access_path(table, alias, statement.where).operator
            left_key, right_key = _equi_join_keys(join.condition, operator.layout, right.layout)
            operator = HashJoin(
                operator,
                right,
                left_key,
                right_key,
                self._temporary(JOIN_DIRECTORY),
                self._config,
            )
        return operator

    def _order(self, operator: Operator, statement: SelectStatement) -> Operator:
        if not statement.order_by:
            return operator
        aliases = _select_aliases(statement.projections)
        keys = [
            (_resolve_alias(item.expression, aliases, operator.layout), item.direction)
            for item in statement.order_by
        ]
        return Sort(operator, keys, self._temporary(SORT_DIRECTORY), self._config)

    def _project(
        self, operator: Operator, statement: SelectStatement, aggregates: Sequence[Aggregate]
    ) -> Operator:
        expressions: list[Expression] = []
        names: list[str] = []
        for projection in statement.projections:
            if isinstance(projection.expression, Star):
                self._expand_star(operator.layout, projection.expression, expressions, names)
                continue
            expression = projection.expression
            if isinstance(expression, FunctionCall) and _is_aggregate(expression):
                expression = ColumnRef(_aggregate_label(expression, projection.alias))
            expressions.append(expression)
            names.append(projection.alias or _column_name(projection.expression))
        if aggregates and any(isinstance(item.expression, Star) for item in statement.projections):
            raise UnsupportedQueryError("'*' no se puede combinar con funciones de agregación")
        return Projection(operator, expressions, names)

    @staticmethod
    def _expand_star(
        layout: RowLayout, star: Star, expressions: list[Expression], names: list[str]
    ) -> None:
        for slot in layout.slots:
            if star.qualifier is not None and (slot.qualifier or "").lower() != (
                star.qualifier.lower()
            ):
                continue
            expressions.append(ColumnRef(slot.name, slot.qualifier))
            names.append(slot.name)
        if not expressions:
            raise UnknownColumnError(f"'{star.qualifier}.*' no encaja con ninguna tabla del FROM")

    @staticmethod
    def _aggregates_of(
        statement: SelectStatement,
    ) -> tuple[tuple[Aggregate, ...], dict[str, str]]:
        """Reúne las agregaciones del SELECT y del HAVING, sin calcular dos veces la misma.

        Devuelve también el mapa firma → etiqueta, que es lo que permite reescribir el
        HAVING para que apunte a la columna que produjo la agregación.
        """
        found: dict[str, Aggregate] = {}
        labels: dict[str, str] = {}
        for projection in statement.projections:
            expression = projection.expression
            if isinstance(expression, FunctionCall) and _is_aggregate(expression):
                aggregate = _build_aggregate(expression, projection.alias)
                found[aggregate.label] = aggregate
                labels[_signature(expression)] = aggregate.label
        for call in _aggregate_calls(statement.having):
            signature = _signature(call)
            if signature in labels:
                continue
            aggregate = _build_aggregate(call, None)
            found[aggregate.label] = aggregate
            labels[signature] = aggregate.label
        return tuple(found.values()), labels

    def _table(self, name: str) -> Table:
        try:
            return self._tables[name.lower()]
        except KeyError:
            raise UnsupportedQueryError(f"la tabla '{name}' no está abierta") from None

    def _temporary(self, name: str) -> Path:
        """Directorio propio para cada operador que se apoya en disco.

        Dos operadores del mismo plan —dos JOIN encadenados, por ejemplo— escribirían
        particiones con el mismo nombre y se pisarían: el de arriba sobrescribe los
        archivos que el de abajo todavía está leyendo.
        """
        self._temporaries += 1
        return self._config.data_directory / f"{name}-{self._temporaries}"


def _conjuncts(expression: Expression | None) -> list[Expression]:
    """Descompone la condición en los AND de primer nivel."""
    if expression is None:
        return []
    if isinstance(expression, BinaryOperation) and expression.operator is BinaryOperator.AND:
        return [*_conjuncts(expression.left), *_conjuncts(expression.right)]
    return [expression]


def _equi_join_keys(
    condition: Expression, left: RowLayout, right: RowLayout
) -> tuple[Expression, Expression]:
    """Separa `a.x = b.y` en la clave de cada lado.

    Raises:
        UnsupportedQueryError: si la condición del ON no es una igualdad simple.
    """
    if not isinstance(condition, BinaryOperation) or condition.operator is not BinaryOperator.EQUAL:
        raise UnsupportedQueryError("el ON de un JOIN debe ser una igualdad entre columnas")
    for first, second in ((condition.left, condition.right), (condition.right, condition.left)):
        if _belongs_to(first, left) and _belongs_to(second, right):
            return first, second
    raise UnsupportedQueryError("el ON debe comparar una columna de cada tabla")


def _belongs_to(expression: Expression, layout: RowLayout) -> bool:
    return isinstance(expression, ColumnRef) and layout.has(expression.name, expression.qualifier)


def _select_aliases(projections: Sequence[SqlProjection]) -> dict[str, Expression]:
    return {
        projection.alias.lower(): projection.expression
        for projection in projections
        if projection.alias is not None
    }


def _resolve_alias(
    expression: Expression, aliases: Mapping[str, Expression], layout: RowLayout
) -> Expression:
    """Un ORDER BY puede nombrar un alias del SELECT; se sustituye por su expresión."""
    if not isinstance(expression, ColumnRef) or expression.qualifier is not None:
        return expression
    if layout.has(expression.name):
        return expression
    replacement = aliases.get(expression.name.lower())
    if replacement is None:
        return expression
    if isinstance(replacement, FunctionCall) and _is_aggregate(replacement):
        return ColumnRef(expression.name)
    return replacement


def _signature(call: FunctionCall) -> str:
    argument = _describe_argument(call)
    return f"{call.name.upper()}({argument})"


def _describe_argument(call: FunctionCall) -> str:
    if not call.arguments or isinstance(call.arguments[0], Star):
        return "*"
    return _column_name(call.arguments[0])


def _aggregate_calls(expression: Expression | None) -> list[FunctionCall]:
    """Todas las llamadas de agregación que aparecen dentro de una expresión."""
    if expression is None:
        return []
    if isinstance(expression, FunctionCall):
        return [expression] if _is_aggregate(expression) else []
    return [
        call for child in _children(expression) for call in _aggregate_calls(child)
    ]


def _replace_aggregates(expression: Expression, labels: Mapping[str, str]) -> Expression:
    """Sustituye cada agregación por la columna que la agrupación ya calculó."""
    if isinstance(expression, FunctionCall) and _is_aggregate(expression):
        return ColumnRef(labels[_signature(expression)])
    if isinstance(expression, BinaryOperation):
        return BinaryOperation(
            expression.operator,
            _replace_aggregates(expression.left, labels),
            _replace_aggregates(expression.right, labels),
        )
    if isinstance(expression, UnaryOperation):
        return UnaryOperation(expression.operator, _replace_aggregates(expression.operand, labels))
    if isinstance(expression, BetweenPredicate):
        return BetweenPredicate(
            _replace_aggregates(expression.operand, labels),
            _replace_aggregates(expression.lower, labels),
            _replace_aggregates(expression.upper, labels),
            expression.negated,
        )
    return expression


def _children(expression: Expression) -> list[Expression]:
    if isinstance(expression, BinaryOperation):
        return [expression.left, expression.right]
    if isinstance(expression, UnaryOperation):
        return [expression.operand]
    if isinstance(expression, BetweenPredicate):
        return [expression.operand, expression.lower, expression.upper]
    return []


def _is_aggregate(call: FunctionCall) -> bool:
    return call.name.upper() in AGGREGATE_NAMES


def _build_aggregate(call: FunctionCall, alias: str | None) -> Aggregate:
    kind = AGGREGATE_NAMES[call.name.upper()]
    argument = call.arguments[0] if call.arguments else None
    if isinstance(argument, Star):
        argument = None
    if argument is None and kind is not AGGREGATE_NAMES["COUNT"]:
        raise UnsupportedQueryError(f"{call.name} necesita un argumento")
    return Aggregate(kind=kind, argument=argument, label=_aggregate_label(call, alias))


def _aggregate_label(call: FunctionCall, alias: str | None) -> str:
    if alias is not None:
        return alias
    if call.arguments and isinstance(call.arguments[0], ColumnRef):
        return f"{call.name.upper()}({call.arguments[0].name})"
    return f"{call.name.upper()}(*)"


def _column_name(expression: Expression) -> str:
    if isinstance(expression, ColumnRef):
        return expression.name
    if isinstance(expression, FunctionCall):
        return _aggregate_label(expression, None)
    return type(expression).__name__.lower()
