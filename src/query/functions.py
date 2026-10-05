"""Funciones del dialecto SQL: las de agregación y las escalares.

Una función escalar devuelve un valor por fila y se puede usar en cualquier expresión
(`WHERE`, `SELECT`, `ORDER BY`, `VALUES`). Las de agregación resumen un grupo de filas y
solo las entiende el operador de agrupación; aquí únicamente se declaran sus nombres, para
que el evaluador pueda distinguir "esa función no va aquí" de "esa función no existe".

Las funciones espaciales son las de la Parte 2:

| Función | Devuelve |
|---|---|
| `POINT(latitud, longitud)` | un punto geográfico |
| `POLYGON(p1, p2, p3, …)` | un polígono; cada vértice es `(lat, lon)` o `POINT(lat, lon)` |
| `distancia(a, b)` | metros entre dos puntos, por Haversine |
| `distancia(a, b, metrica='euclidiana')` | distancia euclidiana, en grados |
| `intersecta(punto, POLYGON(…))` | si el punto cae dentro del polígono o en su borde |
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum, unique
from typing import Any

from spatial.geometry import MINIMUM_POLYGON_VERTICES, Point, Polygon, geographic_point
from spatial.metrics import HAVERSINE, Metric, metric_named
from storage.types import FieldType, ValueTooLargeError, real_from

POINT = "POINT"
POLYGON = "POLYGON"
DISTANCE = "distancia"
INTERSECTS = "intersecta"
METRIC_KEYWORDS = ("metrica", "metric")
COORDINATES_PER_POINT = 2
BINARY_ARGUMENTS = 2
NUMERIC_COLUMNS = frozenset({FieldType.INT, FieldType.FLOAT})
POINT_COLUMNS = frozenset({FieldType.POINT})
# Un polígono solo existe como valor de una consulta: ninguna columna puede serlo.
NO_COLUMN: frozenset[FieldType] = frozenset()


class FunctionError(Exception):
    """Los argumentos no son los que la función espera."""


@unique
class AggregateKind(Enum):
    COUNT = "COUNT"
    SUM = "SUM"
    AVG = "AVG"
    MIN = "MIN"
    MAX = "MAX"


AGGREGATE_NAMES = {kind.value: kind for kind in AggregateKind}

Implementation = Callable[[Sequence[Any], Mapping[str, Any]], Any]


@dataclass(frozen=True, slots=True)
class ScalarFunction:
    """Una función escalar y la forma de llamarla.

    Attributes:
        name: nombre tal como se escribe en SQL; al llamarla no se distinguen mayúsculas.
        minimum_arguments: argumentos posicionales obligatorios.
        maximum_arguments: máximo de posicionales; `None` si admite cualquier número.
        keywords: nombres de los argumentos con nombre que acepta.
        implementation: recibe los argumentos ya evaluados y devuelve el resultado.
        result: tipo de columna de lo que devuelve; `None` si no se puede guardar en una.
        column_types: tipos de columna que admite cada argumento posicional; el último
            vale también para los que le sigan.

    Toda función admite NULL en cualquier argumento y entonces devuelve NULL. Un valor del
    tipo equivocado se rechaza aunque otro argumento sea NULL: es lo que permite ensayar
    una llamada antes de ejecutarla, con NULL en lo que depende de la fila.
    """

    name: str
    minimum_arguments: int
    maximum_arguments: int | None
    keywords: tuple[str, ...]
    implementation: Implementation
    result: FieldType | None
    column_types: tuple[frozenset[FieldType], ...]

    def check_call(self, positional: int, keywords: Sequence[str]) -> None:
        """Comprueba el número de argumentos y los nombres, sin evaluarlos.

        Raises:
            FunctionError: si la llamada no encaja con la firma.
        """
        if positional < self.minimum_arguments:
            raise FunctionError(
                f"{self.name} necesita al menos {self.minimum_arguments} argumento(s) "
                f"y recibió {positional}"
            )
        if self.maximum_arguments is not None and positional > self.maximum_arguments:
            raise FunctionError(
                f"{self.name} admite como mucho {self.maximum_arguments} argumento(s) "
                f"y recibió {positional}"
            )
        for keyword in keywords:
            if keyword not in self.keywords:
                raise FunctionError(f"{self.name} no tiene el argumento '{keyword}'")

    def check_column(self, position: int, column: str, kind: FieldType) -> None:
        """Comprueba que una columna de ese tipo pueda ir en ese argumento.

        Es lo que permite rechazar `distancia(nombre, …)` antes de leer ninguna fila.

        Raises:
            FunctionError: si la función no admite ahí una columna de ese tipo.
        """
        allowed = self.column_types[min(position, len(self.column_types) - 1)]
        if kind not in allowed:
            raise FunctionError(
                f"el argumento {position + 1} no puede ser la columna '{column}', "
                f"que es {kind.value}"
            )


def scalar_function(name: str) -> ScalarFunction | None:
    """Función escalar con ese nombre, sin distinguir mayúsculas; `None` si no existe."""
    return _SCALAR_FUNCTIONS.get(name.upper())


def scalar_function_names() -> list[str]:
    return sorted(function.name for function in _SCALAR_FUNCTIONS.values())


def is_call_to(call_name: str, function_name: str) -> bool:
    """Si un nombre escrito en SQL se refiere a esa función."""
    return call_name.upper() == function_name.upper()


def metric_of(keywords: Mapping[str, Any]) -> Metric:
    """Métrica pedida con `metrica=…`; Haversine si no se pide ninguna.

    Raises:
        FunctionError: si el valor no es un nombre de métrica.
        UnknownMetricError: si el nombre no corresponde a ninguna métrica.
    """
    for keyword in METRIC_KEYWORDS:
        if keyword in keywords:
            name = keywords[keyword]
            if not isinstance(name, str):
                raise FunctionError(f"la métrica se indica por su nombre, llegó {name!r}")
            return metric_named(name)
    return HAVERSINE


def as_point(value: Any) -> Point:
    """Punto a partir de un `POINT(…)`, de una columna POINT o de un par `(lat, lon)`.

    Raises:
        FunctionError: si el valor no es un par de números.
        GeometryError: si las coordenadas están fuera de rango.
    """
    if isinstance(value, Point):
        return value
    if isinstance(value, tuple) and len(value) == COORDINATES_PER_POINT:
        return geographic_point(_number(value[0]), _number(value[1]))
    raise FunctionError(f"se esperaba un punto y llegó {value!r}")


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise FunctionError(f"se esperaba un número y llegó {value!r}")
    try:
        return real_from(value)
    except ValueTooLargeError as error:
        raise FunctionError(str(error)) from error


def _point(arguments: Sequence[Any], _: Mapping[str, Any]) -> Point | None:
    latitude, longitude = (None if value is None else _number(value) for value in arguments)
    if latitude is None or longitude is None:
        return None
    return geographic_point(latitude, longitude)


def _polygon(arguments: Sequence[Any], _: Mapping[str, Any]) -> Polygon | None:
    vertices = [None if vertex is None else as_point(vertex) for vertex in arguments]
    if any(vertex is None for vertex in vertices):
        return None
    return Polygon(tuple(vertex for vertex in vertices if vertex is not None))


def _distance(arguments: Sequence[Any], keywords: Mapping[str, Any]) -> float | None:
    first, second = arguments
    metric = metric_of(keywords)
    start = None if first is None else as_point(first)
    end = None if second is None else as_point(second)
    if start is None or end is None:
        return None
    return metric.distance(start, end)


def _intersects(arguments: Sequence[Any], _: Mapping[str, Any]) -> bool | None:
    point, polygon = arguments
    if polygon is not None and not isinstance(polygon, Polygon):
        raise FunctionError("el segundo argumento de intersecta debe ser un POLYGON(…)")
    located = None if point is None else as_point(point)
    if located is None or polygon is None:
        return None
    return polygon.contains(located)


_SCALAR_FUNCTIONS: dict[str, ScalarFunction] = {
    function.name.upper(): function
    for function in (
        ScalarFunction(
            name=POINT,
            minimum_arguments=COORDINATES_PER_POINT,
            maximum_arguments=COORDINATES_PER_POINT,
            keywords=(),
            implementation=_point,
            result=FieldType.POINT,
            column_types=(NUMERIC_COLUMNS,),
        ),
        ScalarFunction(
            name=POLYGON,
            minimum_arguments=MINIMUM_POLYGON_VERTICES,
            maximum_arguments=None,
            keywords=(),
            implementation=_polygon,
            result=None,
            column_types=(POINT_COLUMNS,),
        ),
        ScalarFunction(
            name=DISTANCE,
            minimum_arguments=BINARY_ARGUMENTS,
            maximum_arguments=BINARY_ARGUMENTS,
            keywords=METRIC_KEYWORDS,
            implementation=_distance,
            result=FieldType.FLOAT,
            column_types=(POINT_COLUMNS,),
        ),
        ScalarFunction(
            name=INTERSECTS,
            minimum_arguments=BINARY_ARGUMENTS,
            maximum_arguments=BINARY_ARGUMENTS,
            keywords=(),
            implementation=_intersects,
            result=FieldType.BOOL,
            column_types=(POINT_COLUMNS, NO_COLUMN),
        ),
    )
}
