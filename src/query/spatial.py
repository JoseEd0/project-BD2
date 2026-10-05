"""Reconocimiento de las consultas espaciales dentro de una sentencia.

Aquí se decide si una expresión del `WHERE` o del `ORDER BY` es una búsqueda que un R-Tree
sabe resolver. El planificador lo usa para elegir el índice y el motor para decirle al
panel de mapa qué figura dibujar, de modo que los dos miran la consulta con los mismos ojos.

| Forma en SQL | Búsqueda |
|---|---|
| `distancia(columna, POINT(…)) < radio` | por radio |
| `ORDER BY distancia(columna, POINT(…))` | vecinos más cercanos |
| `intersecta(columna, POLYGON(…))` | puntos dentro de un polígono |

El punto y el polígono tienen que ser constantes: si dependieran de la fila no habría un
único lugar del árbol por el que empezar a buscar.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from query.expressions import (
    NO_COLUMNS,
    NO_ROW,
    ExpressionError,
    ExpressionEvaluator,
    RowLayout,
    conjuncts,
    constant_value,
    order_key,
)
from query.functions import (
    DISTANCE,
    INTERSECTS,
    FunctionError,
    as_point,
    is_call_to,
    metric_of,
)
from query.table import Table
from spatial.geometry import GeometryError, Point, Polygon
from spatial.metrics import Metric, UnknownMetricError
from sql.nodes import (
    BinaryOperation,
    BinaryOperator,
    ColumnRef,
    Expression,
    FunctionCall,
    SelectStatement,
    SortDirection,
    TableRef,
)
from storage.types import FieldType, ValueTooLargeError, real_from

UPPER_BOUND_OPERATORS = frozenset({BinaryOperator.LESS, BinaryOperator.LESS_EQUAL})
LOWER_BOUND_OPERATORS = frozenset({BinaryOperator.GREATER, BinaryOperator.GREATER_EQUAL})
EXPECTED_ARGUMENTS = 2
_UNUSABLE = (ExpressionError, FunctionError, GeometryError, UnknownMetricError)


@dataclass(frozen=True, slots=True)
class DistanceTarget:
    """`distancia(columna, punto)`, con el punto y la métrica ya resueltos."""

    column: ColumnRef
    center: Point
    metric: Metric


@dataclass(frozen=True, slots=True)
class RadiusSearch:
    """Filas a no más de `radius` del centro, en la unidad de la métrica."""

    target: DistanceTarget
    radius: float


@dataclass(frozen=True, slots=True)
class PolygonSearch:
    """Filas cuyo punto cae dentro del polígono."""

    column: ColumnRef
    polygon: Polygon


@dataclass(frozen=True, slots=True)
class SpatialView:
    """Lo que el panel de mapa necesita saber de una consulta para dibujarla.

    Attributes:
        table: tabla cuyos puntos sirven de fondo al mapa.
        column: columna POINT de esa tabla.
        radius_searches: círculos del `WHERE`.
        nearest_searches: puntos de referencia del `ORDER BY`.
        polygon_searches: polígonos del `WHERE`.
    """

    table: str
    column: str
    radius_searches: tuple[RadiusSearch, ...] = ()
    nearest_searches: tuple[DistanceTarget, ...] = ()
    polygon_searches: tuple[PolygonSearch, ...] = ()


def distance_target(expression: Expression) -> DistanceTarget | None:
    """Reconoce `distancia(columna, punto constante)`, en cualquiera de los dos órdenes."""
    call = _call_named(expression, DISTANCE)
    if call is None:
        return None
    for column, other in (call.arguments, reversed(call.arguments)):
        if not isinstance(column, ColumnRef):
            continue
        try:
            center = as_point(constant_value(other))
            metric = metric_of(ExpressionEvaluator(NO_COLUMNS).options_of(call, NO_ROW))
        except _UNUSABLE:
            continue
        return DistanceTarget(column, center, metric)
    return None


def radius_search(condition: Expression) -> RadiusSearch | None:
    """Reconoce `distancia(…) < radio`, `<=` y sus formas invertidas `radio > distancia(…)`."""
    if not isinstance(condition, BinaryOperation):
        return None
    if condition.operator in UPPER_BOUND_OPERATORS:
        measured, limit = condition.left, condition.right
    elif condition.operator in LOWER_BOUND_OPERATORS:
        measured, limit = condition.right, condition.left
    else:
        return None
    target = distance_target(measured)
    radius = _radius_of(limit)
    if target is None or radius is None:
        return None
    return RadiusSearch(target, radius)


def polygon_search(condition: Expression) -> PolygonSearch | None:
    """Reconoce `intersecta(columna, polígono constante)`."""
    call = _call_named(condition, INTERSECTS)
    if call is None:
        return None
    column, shape = call.arguments
    polygon = constant_value(shape)
    if not isinstance(column, ColumnRef) or not isinstance(polygon, Polygon):
        return None
    return PolygonSearch(column, polygon)


def nearest_search(statement: SelectStatement, layout: RowLayout) -> DistanceTarget | None:
    """Reconoce un `ORDER BY distancia(columna, punto)` ascendente como única clave.

    La clave puede ser un alias del `SELECT`: `SELECT distancia(…) AS metros … ORDER BY
    metros`. `layout` es el de las filas que se ordenan, y decide cuándo ese nombre es
    en realidad una columna (ver `order_key`).
    """
    if len(statement.order_by) != 1:
        return None
    item = statement.order_by[0]
    if item.direction is not SortDirection.ASCENDING:
        return None
    return distance_target(order_key(item.expression, statement.projections, layout))


def spatial_view(statement: SelectStatement, tables: Mapping[str, Table]) -> SpatialView | None:
    """Tabla, columna y figuras que el mapa debe mostrar; `None` si no hay nada espacial."""
    conditions = conjuncts(statement.where)
    radii = tuple(found for found in map(radius_search, conditions) if found is not None)
    polygons = tuple(found for found in map(polygon_search, conditions) if found is not None)
    nearest = nearest_search(statement, _layout_of(statement, tables))
    used = [search.target.column for search in radii]
    used.extend(search.column for search in polygons)
    if nearest is not None:
        used.append(nearest.column)
    located = _point_column(statement, tables, used)
    if located is None:
        return None
    table, column = located
    return SpatialView(
        table=table,
        column=column,
        radius_searches=radii,
        nearest_searches=() if nearest is None else (nearest,),
        polygon_searches=polygons,
    )


def _point_column(
    statement: SelectStatement, tables: Mapping[str, Table], used: list[ColumnRef]
) -> tuple[str, str] | None:
    """Columna POINT a la que se refiere la consulta o, si no nombra ninguna, la primera
    que tenga alguna de sus tablas."""
    sources = [statement.source, *(join.table for join in statement.joins)]
    for reference in used:
        for source in sources:
            table = tables[source.name.lower()]
            if _refers_to(reference, source) and _is_point(table, reference.name):
                return table.name, table.schema.field_of(reference.name).name
    for source in sources:
        table = tables[source.name.lower()]
        for field in table.schema:
            if field.type is FieldType.POINT:
                return table.name, field.name
    return None


def _refers_to(reference: ColumnRef, source: TableRef) -> bool:
    if reference.qualifier is None:
        return True
    return reference.qualifier.lower() in (source.name.lower(), (source.alias or "").lower())


def _is_point(table: Table, column: str) -> bool:
    return table.schema.has_field(column) and table.schema.field_of(column).type is FieldType.POINT


def _layout_of(statement: SelectStatement, tables: Mapping[str, Table]) -> RowLayout:
    """Columnas de las filas que la consulta ordena: las de todas sus tablas."""
    layout = NO_COLUMNS
    for source in (statement.source, *(join.table for join in statement.joins)):
        table = tables[source.name.lower()]
        layout = layout.concat(RowLayout.of_table(table.schema, source.alias or source.name))
    return layout


def _radius_of(limit: Expression) -> float | None:
    """Radio constante de una búsqueda, o `None` si no es un número que quepa en un real."""
    radius = constant_value(limit)
    if isinstance(radius, bool) or not isinstance(radius, int | float):
        return None
    try:
        return real_from(radius)
    except ValueTooLargeError:
        return None


def _call_named(expression: Expression, name: str) -> FunctionCall | None:
    """La expresión, si es una llamada a esa función con sus dos argumentos."""
    if not isinstance(expression, FunctionCall) or not is_call_to(expression.name, name):
        return None
    return expression if len(expression.arguments) == EXPECTED_ARGUMENTS else None

