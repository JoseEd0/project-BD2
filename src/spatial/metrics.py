"""Métricas de distancia entre puntos, y de un punto a un rectángulo.

Cada métrica aporta dos funciones. `distance` es la distancia entre dos puntos.
`min_distance` es la menor distancia posible entre un punto y cualquier punto de un
rectángulo: es lo que permite al R-Tree descartar un subárbol entero sin abrirlo, y por eso
tiene que ser una cota inferior. Una cota que se pasara de larga, aunque fuera por el
redondeo de la última cifra, perdería resultados.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from math import asin, atan2, cos, degrees, hypot, radians, sin, sqrt

from .geometry import Point, Rectangle

# Radio medio de la Tierra (IUGG). Es el que usa PostGIS al medir sobre la esfera, así que
# las distancias de este motor y las de `ST_DistanceSphere` coinciden.
EARTH_RADIUS_METERS = 6_371_008.8
# Fracción que se le resta a la cota de Haversine. El redondeo puede dejar la cota una
# cifra por encima de la distancia real, y el árbol descartaría un nodo que sí guarda un
# resultado. El error relativo de la fórmula ronda 1e-15 y llega a 1e-10 cerca de las
# antípodas, donde el arcoseno lo amplifica: este margen lo cubre y no le quita poda.
BOUND_SLACK = 1e-9
_BOUND_FACTOR = 1.0 - BOUND_SLACK
FULL_TURN_DEGREES = 360.0
HALF_TURN_DEGREES = 180.0


class UnknownMetricError(Exception):
    """El nombre no corresponde a ninguna métrica implementada."""


class Metric(ABC):
    """Una forma de medir distancias sobre puntos (latitud, longitud).

    Attributes:
        name: nombre con el que la métrica aparece en SQL y en el plan de ejecución.
        unit: unidad en la que se expresan sus distancias.
    """

    name: str
    unit: str

    @abstractmethod
    def distance(self, first: Point, second: Point) -> float:
        """Distancia entre dos puntos."""

    @abstractmethod
    def min_distance(self, point: Point, rectangle: Rectangle) -> float:
        """Menor distancia del punto a cualquier punto del rectángulo; 0 si está dentro."""


class EuclideanMetric(Metric):
    """Distancia en línea recta sobre el plano de coordenadas, en grados."""

    name = "euclidiana"
    unit = "grados"

    def distance(self, first: Point, second: Point) -> float:
        return hypot(first.lat - second.lat, first.lon - second.lon)

    def min_distance(self, point: Point, rectangle: Rectangle) -> float:
        lat_gap = max(rectangle.min_lat - point.lat, 0.0, point.lat - rectangle.max_lat)
        lon_gap = max(rectangle.min_lon - point.lon, 0.0, point.lon - rectangle.max_lon)
        return hypot(lat_gap, lon_gap)


class HaversineMetric(Metric):
    """Distancia geodésica sobre una esfera del radio medio de la Tierra, en metros."""

    name = "haversine"
    unit = "m"

    def distance(self, first: Point, second: Point) -> float:
        return _great_circle(first.lat, second.lat, second.lon - first.lon)

    def min_distance(self, point: Point, rectangle: Rectangle) -> float:
        """Distancia al punto más cercano de un rectángulo de latitudes y longitudes.

        A una latitud dada, el punto más cercano del rectángulo es el de longitud más
        próxima a la del punto consultado. Si la longitud del punto cae dentro del rango,
        ese punto está sobre su mismo meridiano y la distancia es la diferencia de
        latitudes. Si cae fuera, está sobre el borde este u oeste, y falta elegir la
        latitud: la distancia a lo largo de un meridiano tiene un único mínimo, en
        `atan2(sin φ, cos φ · cos Δλ)`, así que basta comparar ese punto con los dos
        extremos del borde.

        El resultado se rebaja en `BOUND_SLACK`, para que el redondeo nunca lo deje por
        encima de la distancia a un punto del rectángulo.
        """
        lon_gap = _longitude_gap(point.lon, rectangle.min_lon, rectangle.max_lon)
        if lon_gap == 0.0:
            lat_gap = max(rectangle.min_lat - point.lat, 0.0, point.lat - rectangle.max_lat)
            return EARTH_RADIUS_METERS * radians(lat_gap) * _BOUND_FACTOR
        candidates = [rectangle.min_lat, rectangle.max_lat]
        closest_lat = degrees(
            atan2(sin(radians(point.lat)), cos(radians(point.lat)) * cos(radians(lon_gap)))
        )
        if rectangle.min_lat <= closest_lat <= rectangle.max_lat:
            candidates.append(closest_lat)
        return min(_great_circle(point.lat, lat, lon_gap) for lat in candidates) * _BOUND_FACTOR


def _great_circle(first_lat: float, second_lat: float, lon_difference: float) -> float:
    """Fórmula de Haversine a partir de las dos latitudes y la diferencia de longitudes."""
    first = radians(first_lat)
    second = radians(second_lat)
    half_lat = (second - first) / 2.0
    half_lon = radians(lon_difference) / 2.0
    chord = sin(half_lat) ** 2 + cos(first) * cos(second) * sin(half_lon) ** 2
    return 2.0 * EARTH_RADIUS_METERS * asin(min(1.0, sqrt(chord)))


def _longitude_gap(lon: float, min_lon: float, max_lon: float) -> float:
    """Menor diferencia angular entre una longitud y un rango de longitudes, en grados."""
    if min_lon <= lon <= max_lon:
        return 0.0
    return min(_angular_difference(lon, min_lon), _angular_difference(lon, max_lon))


def _angular_difference(first: float, second: float) -> float:
    difference = abs(first - second) % FULL_TURN_DEGREES
    return FULL_TURN_DEGREES - difference if difference > HALF_TURN_DEGREES else difference


EUCLIDEAN: Metric = EuclideanMetric()
HAVERSINE: Metric = HaversineMetric()

_METRICS_BY_NAME: dict[str, Metric] = {
    "haversine": HAVERSINE,
    "geodesica": HAVERSINE,
    "geodésica": HAVERSINE,
    "euclidiana": EUCLIDEAN,
    "euclidean": EUCLIDEAN,
    "euclidea": EUCLIDEAN,
    "euclídea": EUCLIDEAN,
}


def metric_named(name: str) -> Metric:
    """Métrica con ese nombre, sin distinguir mayúsculas.

    Raises:
        UnknownMetricError: si el nombre no es de ninguna métrica implementada.
    """
    try:
        return _METRICS_BY_NAME[name.strip().lower()]
    except KeyError:
        known = ", ".join(sorted({metric.name for metric in _METRICS_BY_NAME.values()}))
        raise UnknownMetricError(
            f"métrica desconocida '{name}'; las disponibles son: {known}"
        ) from None
