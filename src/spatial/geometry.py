"""Puntos, rectángulos y polígonos sobre coordenadas (latitud, longitud).

El orden de las coordenadas es el del enunciado: `POINT(-12.0464, -77.0428)` es latitud y
luego longitud. Rectángulos y polígonos se tratan como figuras planas sobre esos dos ejes,
que es también lo que hace el tipo `geometry` de PostGIS; solo la distancia Haversine tiene
en cuenta la curvatura de la Tierra.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import NamedTuple

MIN_LATITUDE = -90.0
MAX_LATITUDE = 90.0
MIN_LONGITUDE = -180.0
MAX_LONGITUDE = 180.0
MINIMUM_POLYGON_VERTICES = 3


class GeometryError(Exception):
    """Las coordenadas no describen una figura válida."""


class Point(NamedTuple):
    """Punto geográfico en grados decimales."""

    lat: float
    lon: float

    def __str__(self) -> str:
        return f"({self.lat:g}, {self.lon:g})"


def geographic_point(lat: float, lon: float) -> Point:
    """Punto con las coordenadas comprobadas.

    Raises:
        GeometryError: si la latitud o la longitud se salen de su rango.
    """
    if not MIN_LATITUDE <= lat <= MAX_LATITUDE:
        raise GeometryError(
            f"la latitud {lat:g} está fuera de [{MIN_LATITUDE:g}, {MAX_LATITUDE:g}]"
        )
    if not MIN_LONGITUDE <= lon <= MAX_LONGITUDE:
        raise GeometryError(
            f"la longitud {lon:g} está fuera de [{MIN_LONGITUDE:g}, {MAX_LONGITUDE:g}]"
        )
    return Point(float(lat), float(lon))


class Rectangle(NamedTuple):
    """Rectángulo alineado a los ejes: el MBR de los nodos del R-Tree."""

    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float

    @classmethod
    def around(cls, point: Point) -> Rectangle:
        """Rectángulo degenerado que solo contiene al punto."""
        return cls(point.lat, point.lon, point.lat, point.lon)

    @classmethod
    def enclosing(cls, rectangles: Iterable[Rectangle]) -> Rectangle:
        """Menor rectángulo que contiene a todos los dados.

        Raises:
            GeometryError: si no se da ningún rectángulo.
        """
        iterator = iter(rectangles)
        try:
            min_lat, min_lon, max_lat, max_lon = next(iterator)
        except StopIteration:
            raise GeometryError("no hay rectángulos que encerrar") from None
        for other in iterator:
            min_lat = min(min_lat, other.min_lat)
            min_lon = min(min_lon, other.min_lon)
            max_lat = max(max_lat, other.max_lat)
            max_lon = max(max_lon, other.max_lon)
        return cls(min_lat, min_lon, max_lat, max_lon)

    @property
    def area(self) -> float:
        return (self.max_lat - self.min_lat) * (self.max_lon - self.min_lon)

    def union(self, other: Rectangle) -> Rectangle:
        return Rectangle(
            min(self.min_lat, other.min_lat),
            min(self.min_lon, other.min_lon),
            max(self.max_lat, other.max_lat),
            max(self.max_lon, other.max_lon),
        )

    def area_with(self, other: Rectangle) -> float:
        """Área del menor rectángulo que cubre a los dos, sin llegar a construirlo."""
        min_lat, min_lon, max_lat, max_lon = self
        other_min_lat, other_min_lon, other_max_lat, other_max_lon = other
        return (max(max_lat, other_max_lat) - min(min_lat, other_min_lat)) * (
            max(max_lon, other_max_lon) - min(min_lon, other_min_lon)
        )

    @property
    def margin(self) -> float:
        """Semiperímetro: alto más ancho. Distingue rectángulos de área cero entre sí."""
        return (self.max_lat - self.min_lat) + (self.max_lon - self.min_lon)

    def margin_with(self, other: Rectangle) -> float:
        """Semiperímetro del menor rectángulo que cubre a los dos."""
        min_lat, min_lon, max_lat, max_lon = self
        other_min_lat, other_min_lon, other_max_lat, other_max_lon = other
        return (max(max_lat, other_max_lat) - min(min_lat, other_min_lat)) + (
            max(max_lon, other_max_lon) - min(min_lon, other_min_lon)
        )

    def enlargement(self, other: Rectangle) -> float:
        """Área que habría que añadir a este rectángulo para que cubra también a `other`."""
        return self.area_with(other) - self.area

    def intersects(self, other: Rectangle) -> bool:
        return (
            self.min_lat <= other.max_lat
            and other.min_lat <= self.max_lat
            and self.min_lon <= other.max_lon
            and other.min_lon <= self.max_lon
        )

    def contains(self, other: Rectangle) -> bool:
        return (
            self.min_lat <= other.min_lat
            and self.min_lon <= other.min_lon
            and self.max_lat >= other.max_lat
            and self.max_lon >= other.max_lon
        )

    def contains_point(self, point: Point) -> bool:
        return (
            self.min_lat <= point.lat <= self.max_lat
            and self.min_lon <= point.lon <= self.max_lon
        )


@dataclass(frozen=True, slots=True)
class Polygon:
    """Polígono simple dado por sus vértices en orden; el último se une con el primero.

    Raises:
        GeometryError: si tiene menos de tres vértices distintos.
    """

    vertices: tuple[Point, ...]

    def __post_init__(self) -> None:
        if len(set(self.vertices)) < MINIMUM_POLYGON_VERTICES:
            raise GeometryError(
                f"un polígono necesita al menos {MINIMUM_POLYGON_VERTICES} vértices distintos"
            )

    @property
    def bounding_box(self) -> Rectangle:
        return Rectangle.enclosing(Rectangle.around(vertex) for vertex in self.vertices)

    def contains(self, point: Point) -> bool:
        """Si el punto cae dentro del polígono o sobre su borde.

        Regla par-impar con un rayo hacia longitudes crecientes: `O(vértices)`. El borde se
        comprueba aparte porque el rayo, por sí solo, lo decide de forma distinta según la
        arista sobre la que caiga el punto.
        """
        inside = False
        for start, end in self._edges():
            if _on_segment(point, start, end):
                return True
            if _ray_crosses(point, start, end):
                inside = not inside
        return inside

    def _edges(self) -> Iterable[tuple[Point, Point]]:
        return zip(self.vertices, (*self.vertices[1:], self.vertices[0]), strict=True)


def _on_segment(point: Point, start: Point, end: Point) -> bool:
    cross = (end.lat - start.lat) * (point.lon - start.lon) - (end.lon - start.lon) * (
        point.lat - start.lat
    )
    if cross != 0.0:
        return False
    return (
        min(start.lat, end.lat) <= point.lat <= max(start.lat, end.lat)
        and min(start.lon, end.lon) <= point.lon <= max(start.lon, end.lon)
    )


def _ray_crosses(point: Point, start: Point, end: Point) -> bool:
    """Si la arista corta al rayo que sale del punto hacia longitudes mayores.

    El extremo de menor latitud cuenta y el de mayor no: así un rayo que pasa justo por un
    vértice cruza una sola de las dos aristas que se tocan en él.
    """
    if (start.lat > point.lat) == (end.lat > point.lat):
        return False
    crossing_lon = start.lon + (point.lat - start.lat) * (end.lon - start.lon) / (
        end.lat - start.lat
    )
    return crossing_lon > point.lon
