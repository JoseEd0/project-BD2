"""Geometría y métricas de distancia para los datos espaciales.

Es el único lugar donde viven las distancias euclidiana y Haversine: el índice R-Tree, el
evaluador de expresiones y el recorrido secuencial usan exactamente las mismas funciones,
así que un resultado no puede depender del camino que eligió el planificador.
"""

from .geometry import GeometryError, Point, Polygon, Rectangle
from .metrics import (
    EARTH_RADIUS_METERS,
    EUCLIDEAN,
    HAVERSINE,
    Metric,
    UnknownMetricError,
    metric_named,
)

__all__ = [
    "EARTH_RADIUS_METERS",
    "EUCLIDEAN",
    "HAVERSINE",
    "GeometryError",
    "Metric",
    "Point",
    "Polygon",
    "Rectangle",
    "UnknownMetricError",
    "metric_named",
]
