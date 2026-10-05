"""Planificador: convierte una sentencia SELECT en un árbol de operadores.

Su única decisión interesante es la **elección del camino de acceso**: si el WHERE contiene
una condición sobre una columna indexada, se usa el índice; si no, se recorre la tabla. Esa
decisión es la que el panel de plan de ejecución enseña al usuario.

Las consultas espaciales siguen la misma regla con el R-Tree: un radio o un polígono en el
WHERE, o un ORDER BY por distancia, se resuelven con el índice si la columna lo tiene.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from config import EngineConfig
from query.aggregation import Aggregate
from query.catalog import IndexDefinition, Organization
from query.expressions import (
    NOT_CONSTANT,
    ExpressionEvaluator,
    RowLayout,
    UnknownColumnError,
    column_literal,
    conjuncts,
    constant_value,
    describe_expression,
    order_key,
    rewritten,
    subexpressions,
)
from query.functions import AGGREGATE_NAMES
from query.operators import (
    DISTINCT_DIRECTORY,
    GROUP_DIRECTORY,
    JOIN_DIRECTORY,
    SORT_DIRECTORY,
    Distinct,
    Filter,
    HashAggregate,
    HashJoin,
    IndexLookup,
    IndexRange,
    LimitOffset,
    NestedLoopJoin,
    Operator,
    OrderedScan,
    PrimaryKeyLookup,
    PrimaryKeyRange,
    Projection,
    SequentialScan,
    Sort,
    UnsupportedQueryError,
    grouping_label,
)
from query.spatial import nearest_search, polygon_search, radius_search
from query.spatial_operators import SpatialNearestScan, SpatialPolygonScan, SpatialRangeScan
from query.table import Table
from sql.nodes import (
    BetweenPredicate,
    BinaryOperation,
    BinaryOperator,
    ColumnRef,
    Expression,
    FunctionCall,
    IndexType,
    Join,
    SelectStatement,
    SortDirection,
    Star,
    TableRef,
)
from storage.types import StorageError, Value, codec_for

ORDERED_ORGANIZATIONS = frozenset({Organization.SEQUENTIAL, Organization.CLUSTERED_BTREE})
# Cada comparación de orden y la que resulta de intercambiar sus lados: `5 < id` es `id > 5`.
MIRRORED_OPERATORS = {
    BinaryOperator.LESS: BinaryOperator.GREATER,
    BinaryOperator.LESS_EQUAL: BinaryOperator.GREATER_EQUAL,
    BinaryOperator.GREATER: BinaryOperator.LESS,
    BinaryOperator.GREATER_EQUAL: BinaryOperator.LESS_EQUAL,
}
RANGE_OPERATORS = frozenset(MIRRORED_OPERATORS)


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
        aggregates, labels = self._aggregates_of(statement)
        grouped = bool(aggregates or statement.group_by)
        source = self._scan_of(statement.source, statement.where)
        presorted = self._nearest_scan(statement, source, grouped)
        if presorted is None:
            presorted = self._key_ordered(statement, source, grouped)
        operator = self._join_all(presorted or source, statement)
        if statement.where is not None:
            operator = Filter(operator, statement.where)
        computed = _Computed({}, ())
        if grouped:
            keys = [
                order_key(key, statement.projections, operator.layout) for key in statement.group_by
            ]
            computed = _Computed(labels, _grouped_expressions(keys))
            operator = HashAggregate(
                operator, keys, aggregates, self._temporary(GROUP_DIRECTORY), self._config
            )
            if statement.having is not None:
                operator = Filter(operator, computed.reading(statement.having))
        elif statement.having is not None:
            raise UnsupportedQueryError("HAVING necesita GROUP BY o funciones de agregación")
        if presorted is None:
            operator = self._order(operator, statement, computed)
        expressions, names = self._selected(operator.layout, statement, computed)
        if statement.distinct:
            operator = Distinct(
                operator, expressions, self._temporary(DISTINCT_DIRECTORY), self._config
            )
        operator = Projection(operator, expressions, names)
        if statement.limit is not None or statement.offset is not None:
            operator = LimitOffset(operator, statement.limit, statement.offset)
        return operator

    def _scan_of(self, reference: TableRef, where: Expression | None) -> Operator:
        table = self._table(reference.name)
        alias = reference.alias or reference.name
        return self._access_path(table, alias, where)

    def _access_path(self, table: Table, alias: str, where: Expression | None) -> Operator:
        """Elige entre índice y recorrido completo mirando las condiciones del WHERE."""
        for condition in conjuncts(where):
            path = self._path_for(table, alias, condition)
            if path is not None:
                return path
        return SequentialScan(table, alias)

    def _path_for(self, table: Table, alias: str, condition: Expression) -> Operator | None:
        equality = self._equality_on_column(condition, alias)
        if equality is not None:
            return self._equality_path(table, alias, *equality)
        interval = self._range_on_column(condition, alias)
        if interval is not None:
            return self._range_path(table, alias, *interval)
        return self._spatial_path(table, alias, condition)

    def _spatial_path(self, table: Table, alias: str, condition: Expression) -> Operator | None:
        """Búsqueda por radio o por polígono, si la columna tiene un R-Tree."""
        radius = radius_search(condition)
        if radius is not None:
            index = self._spatial_index_on(table, alias, radius.target.column)
            return None if index is None else SpatialRangeScan(table, alias, index.name, radius)
        polygon = polygon_search(condition)
        if polygon is not None:
            index = self._spatial_index_on(table, alias, polygon.column)
            return None if index is None else SpatialPolygonScan(table, alias, index.name, polygon)
        return None

    def _nearest_scan(
        self, statement: SelectStatement, source: Operator, grouped: bool
    ) -> Operator | None:
        """R-Tree en lugar de recorrer y ordenar, si el ORDER BY es por distancia a un punto.

        El índice entrega las filas ya en orden de cercanía, de modo que el plan no lleva
        ordenamiento y un `LIMIT k` corta tras la k-ésima fila. Un filtro o un `DISTINCT`
        conservan ese orden; un JOIN o una agrupación no, así que con ellos no se aplica.
        Tampoco si el WHERE ya eligió otro índice: el acceso a la tabla es uno solo.
        """
        if not isinstance(source, SequentialScan) or grouped or statement.joins:
            return None
        target = nearest_search(statement, source.layout)
        if target is None:
            return None
        table = self._table(statement.source.name)
        alias = statement.source.alias or statement.source.name
        index = self._spatial_index_on(table, alias, target.column)
        return None if index is None else SpatialNearestScan(table, alias, index.name, target)

    def _key_ordered(
        self, statement: SelectStatement, source: Operator, grouped: bool
    ) -> Operator | None:
        """El acceso a la tabla, si ya entrega las filas en el orden que pide el ORDER BY.

        Un archivo secuencial y un B+ agrupado guardan las filas por clave primaria, así
        que un `ORDER BY` ascendente por esa clave no necesita ordenar: basta leerlas.
        Vale para el recorrido completo y para un rango de claves; un JOIN o una
        agrupación deshacen ese orden.
        """
        if grouped or statement.joins or len(statement.order_by) != 1:
            return None
        item = statement.order_by[0]
        table = self._table(statement.source.name)
        alias = statement.source.alias or statement.source.name
        if item.direction is not SortDirection.ASCENDING:
            return None
        if table.organization not in ORDERED_ORGANIZATIONS:
            return None
        column = self._column_named(
            order_key(item.expression, statement.projections, source.layout), alias
        )
        if column is None or not self._is_primary_key(table, column):
            return None
        if isinstance(source, PrimaryKeyRange | PrimaryKeyLookup):
            return source
        return OrderedScan(table, alias) if isinstance(source, SequentialScan) else None

    def _spatial_index_on(
        self, table: Table, alias: str, column: ColumnRef
    ) -> IndexDefinition | None:
        name = self._column_named(column, alias)
        if name is None:
            return None
        index = table.definition.index_on(name)
        return index if index is not None and index.method is IndexType.RTREE else None

    def _equality_on_column(self, condition: Expression, alias: str) -> tuple[str, Value] | None:
        if not isinstance(condition, BinaryOperation):
            return None
        if condition.operator is not BinaryOperator.EQUAL:
            return None
        sides = ((condition.left, condition.right), (condition.right, condition.left))
        for column_side, value_side in sides:
            column = self._column_named(column_side, alias)
            value = constant_value(value_side)
            if column is not None and value is not NOT_CONSTANT:
                return column, value
        return None

    def _range_on_column(
        self, condition: Expression, alias: str
    ) -> tuple[str, Value | None, Value | None] | None:
        if isinstance(condition, BetweenPredicate) and not condition.negated:
            return self._between_bounds(condition, alias)
        if not isinstance(condition, BinaryOperation) or condition.operator not in RANGE_OPERATORS:
            return None
        sides = (
            (condition.left, condition.right, condition.operator),
            (condition.right, condition.left, MIRRORED_OPERATORS[condition.operator]),
        )
        for column_side, value_side, operator in sides:
            column = self._column_named(column_side, alias)
            value = constant_value(value_side)
            if column is None or value is NOT_CONSTANT:
                continue
            if operator in (BinaryOperator.LESS, BinaryOperator.LESS_EQUAL):
                return column, None, value
            return column, value, None
        return None

    def _between_bounds(
        self, condition: BetweenPredicate, alias: str
    ) -> tuple[str, Value | None, Value | None] | None:
        column = self._column_named(condition.operand, alias)
        lower, upper = constant_value(condition.lower), constant_value(condition.upper)
        if column is None or lower is NOT_CONSTANT or upper is NOT_CONSTANT:
            return None
        return column, lower, upper

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
    ) -> Operator | None:
        """Un índice explícito gana a la organización: en un heap file buscar por clave
        primaria sin índice sería un recorrido completo."""
        key = _stored_key(table, column, value)
        if key is None:
            return None
        index = table.definition.index_on(column)
        if index is not None:
            return IndexLookup(table, alias, index.name, key)
        if self._is_primary_key(table, column):
            return PrimaryKeyLookup(table, alias, key)
        return None

    def _range_path(
        self, table: Table, alias: str, column: str, low: Value | None, high: Value | None
    ) -> Operator | None:
        if not table.schema.has_field(column):
            return None
        low, high = _bound(table, column, low), _bound(table, column, high)
        if self._is_primary_key(table, column) and table.organization in ORDERED_ORGANIZATIONS:
            return PrimaryKeyRange(table, alias, low, high)
        index = table.definition.index_on(column)
        if index is None or index.method is not IndexType.BTREE:
            return None
        return IndexRange(table, alias, index.name, low, high)

    @staticmethod
    def _is_primary_key(table: Table, column: str) -> bool:
        key = table.primary_key
        return key is not None and key.lower() == column.lower()

    def _join_all(self, operator: Operator, statement: SelectStatement) -> Operator:
        for join in statement.joins:
            table = self._table(join.table.name)
            alias = join.table.alias or join.table.name
            right = self._access_path(table, alias, statement.where)
            operator = self._join(operator, right, join)
        return operator

    def _join(self, left: Operator, right: Operator, join: Join) -> Operator:
        """Hash join si el `ON` iguala alguna columna de cada lado; si no, bucles anidados.

        Las igualdades son la clave por la que se reparten las filas, y lo que quede de
        la condición se comprueba sobre cada par que comparte clave. El `ON` entero se
        valida antes contra las dos tablas juntas: una columna sin cualificar que está en
        ambas es ambigua, aunque partida la condición cada mitad se entendiera.
        """
        ExpressionEvaluator(left.layout.concat(right.layout)).validate(join.condition)
        left_keys, right_keys, rest = _split_join_condition(
            join.condition, left.layout, right.layout
        )
        directory = self._temporary(JOIN_DIRECTORY)
        if not left_keys:
            return NestedLoopJoin(left, right, join.kind, join.condition, directory, self._config)
        return HashJoin(
            left, right, join.kind, left_keys, right_keys, _all_of(rest), directory, self._config
        )

    def _order(
        self, operator: Operator, statement: SelectStatement, computed: _Computed
    ) -> Operator:
        if not statement.order_by:
            return operator
        keys = [
            (
                computed.reading(
                    order_key(item.expression, statement.projections, operator.layout)
                ),
                item.direction,
            )
            for item in statement.order_by
        ]
        return Sort(operator, keys, self._temporary(SORT_DIRECTORY), self._config)

    def _selected(
        self, layout: RowLayout, statement: SelectStatement, computed: _Computed
    ) -> tuple[list[Expression], list[str]]:
        """Expresiones del SELECT, con `*` ya desplegado, y el nombre de cada columna."""
        expressions: list[Expression] = []
        names: list[str] = []
        for projection in statement.projections:
            if isinstance(projection.expression, Star):
                if computed.aggregates:
                    raise UnsupportedQueryError(
                        "'*' no se puede combinar con funciones de agregación"
                    )
                self._expand_star(layout, projection.expression, expressions, names)
                continue
            expressions.append(computed.reading(projection.expression))
            names.append(projection.alias or _column_name(projection.expression))
        return expressions, names

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
        """Reúne las agregaciones de la consulta, sin calcular dos veces la misma.

        Pueden estar en el SELECT, en el HAVING o en el ORDER BY, solas o dentro de una
        expresión (`SUM(total) / COUNT(*)`). Devuelve también el mapa firma → etiqueta,
        que es lo que permite reescribir esas expresiones para que lean la columna que
        produjo cada agregación. La que el SELECT nombra con un alias se llama como él.
        """
        found: dict[str, Aggregate] = {}
        labels: dict[str, str] = {}
        for projection in statement.projections:
            expression = projection.expression
            if isinstance(expression, FunctionCall) and _is_aggregate(expression):
                aggregate = _build_aggregate(expression, projection.alias)
                found[aggregate.label] = aggregate
                labels.setdefault(_signature(expression), aggregate.label)
        sources = (
            *(projection.expression for projection in statement.projections),
            statement.having,
            *(item.expression for item in statement.order_by),
        )
        for call in (call for source in sources for call in _aggregate_calls(source)):
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


def _stored_key(table: Table, column: str, value: Value) -> Value | None:
    """El literal de una igualdad como valor que la columna podría guardar.

    Devuelve `None` cuando ninguna fila puede tenerlo —un NULL, un texto más largo que la
    columna, un real con decimales en una columna entera—: entonces no se usa el índice,
    y el recorrido con su filtro responde lo mismo sin pedirle al índice una clave que no
    sabe representar.

    Raises:
        ExpressionError: si el literal es de un tipo que no se compara con la columna.
    """
    if value is None or not table.schema.has_field(column):
        return None
    field = table.schema.field_of(column)
    key = column_literal(field, value)
    try:
        codec_for(field.type, field.length).to_slots(key)
    except StorageError:
        return None
    return key


def _bound(table: Table, column: str, value: Value | None) -> Value | None:
    """Extremo de un rango, llevado al tipo de la columna; `None` si no hay extremo."""
    if value is None:
        return None
    return column_literal(table.schema.field_of(column), value)


def _split_join_condition(
    condition: Expression, left: RowLayout, right: RowLayout
) -> tuple[list[Expression], list[Expression], list[Expression]]:
    """Separa el `ON` en las igualdades entre una columna de cada lado y el resto.

    Devuelve las columnas de la izquierda, las de la derecha —emparejadas por posición—
    y las condiciones que no son una igualdad de ese tipo.
    """
    left_keys: list[Expression] = []
    right_keys: list[Expression] = []
    rest: list[Expression] = []
    for part in conjuncts(condition):
        sides = _equated_columns(part, left, right)
        if sides is None:
            rest.append(part)
            continue
        left_keys.append(sides[0])
        right_keys.append(sides[1])
    return left_keys, right_keys, rest


def _equated_columns(
    condition: Expression, left: RowLayout, right: RowLayout
) -> tuple[Expression, Expression] | None:
    """Las dos columnas de `a.x = b.y`, la de la izquierda primero; `None` si no lo es."""
    if not isinstance(condition, BinaryOperation) or condition.operator is not BinaryOperator.EQUAL:
        return None
    for first, second in ((condition.left, condition.right), (condition.right, condition.left)):
        if _belongs_to(first, left) and _belongs_to(second, right):
            return first, second
    return None


def _all_of(conditions: Sequence[Expression]) -> Expression | None:
    """Las condiciones unidas por AND, o `None` si no hay ninguna."""
    combined: Expression | None = None
    for condition in conditions:
        combined = (
            condition
            if combined is None
            else BinaryOperation(BinaryOperator.AND, combined, condition)
        )
    return combined


def _belongs_to(expression: Expression, layout: RowLayout) -> bool:
    return isinstance(expression, ColumnRef) and layout.has(expression.name, expression.qualifier)


@dataclass(frozen=True, slots=True)
class _Computed:
    """Lo que una agrupación ya calculó, y con qué nombre de columna lo entrega.

    Attributes:
        aggregates: firma de cada agregación → columna que la contiene.
        keys: cada clave de agrupación que es una expresión, con la columna que la
            contiene. Es una lista y no un mapa porque una expresión con argumentos con
            nombre no se puede usar de clave de diccionario; son pocas y se comparan.
    """

    aggregates: Mapping[str, str]
    keys: Sequence[tuple[Expression, str]]

    def reading(self, expression: Expression) -> Expression:
        """La expresión, leyendo de esas columnas lo que la agrupación ya calculó.

        Lo que queda fuera se evalúa después sobre cada grupo, así que el SELECT, el HAVING
        y el ORDER BY pueden combinar agregaciones y claves en una misma expresión.
        """
        if not self.aggregates and not self.keys:
            return expression
        return rewritten(expression, self._column_of)

    def _column_of(self, expression: Expression) -> Expression | None:
        if isinstance(expression, FunctionCall) and _is_aggregate(expression):
            return ColumnRef(self.aggregates[_signature(expression)])
        for key, label in self.keys:
            if key == expression:
                return ColumnRef(label)
        return None


def _grouped_expressions(keys: Sequence[Expression]) -> list[tuple[Expression, str]]:
    """Claves de agrupación que no son una columna, con el nombre de la suya en el resultado."""
    return [(key, grouping_label(key)) for key in keys if not isinstance(key, ColumnRef)]


def _signature(call: FunctionCall) -> str:
    """Texto que identifica una agregación: dos llamadas iguales se calculan una sola vez."""
    if not call.arguments or isinstance(call.arguments[0], Star):
        return f"{call.name.upper()}(*)"
    argument = call.arguments[0]
    shown = argument.name if isinstance(argument, ColumnRef) else describe_expression(argument)
    return f"{call.name.upper()}({shown})"


def _aggregate_calls(expression: Expression | None) -> list[FunctionCall]:
    """Todas las llamadas de agregación que aparecen dentro de una expresión."""
    if expression is None:
        return []
    if isinstance(expression, FunctionCall) and _is_aggregate(expression):
        return [expression]
    return [call for child in subexpressions(expression) for call in _aggregate_calls(child)]


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
    return _signature(call) if alias is None else alias


def _column_name(expression: Expression) -> str:
    if isinstance(expression, ColumnRef):
        return expression.name
    if isinstance(expression, FunctionCall) and _is_aggregate(expression):
        return _aggregate_label(expression, None)
    if isinstance(expression, FunctionCall):
        return expression.name.lower()
    return describe_expression(expression)
