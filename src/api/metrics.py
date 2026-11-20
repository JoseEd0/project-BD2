"""Métricas de Prometheus del gestor: peticiones por ruta y estado, su latencia y la CPU y
memoria del proceso, en `GET /metrics` (sin clave, como `/health`).

El gestor vive en una red interna: solo llegan Prometheus y las API de las aplicaciones. La
consola no reenvía `/api/metrics`. La ruta es la plantilla (`/tables/{name}/structure`), no
la URL, para que el número de series no crezca con los nombres de las tablas.

Las métricas van en un registro propio y no en el global de `prometheus_client`: los tests
crean varias aplicaciones en un mismo proceso, y el kit de los servicios (`appkit.metrics`)
usa los mismos nombres.
"""

from __future__ import annotations

from fastapi import Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    ProcessCollector,
    generate_latest,
)
from starlette.types import Scope

UNMATCHED_ROUTE = "unmatched"
# De 5 ms a 30 s: una búsqueda por el B+ tarda milisegundos, y un `CREATE INDEX` o una
# detección de audio, segundos.
LATENCY_BUCKETS_SECONDS = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0,
)

REGISTRY = CollectorRegistry()
ProcessCollector(registry=REGISTRY)
REQUESTS = Counter(
    "http_requests_total",
    "Peticiones respondidas, por método, ruta y estado",
    ["method", "route", "status"],
    registry=REGISTRY,
)
LATENCY = Histogram(
    "http_request_duration_seconds",
    "Tiempo de respuesta de cada petición, por método y ruta",
    ["method", "route"],
    buckets=LATENCY_BUCKETS_SECONDS,
    registry=REGISTRY,
)


def route_of(scope: Scope) -> str:
    """La plantilla de la ruta que atendió la petición, o `unmatched` si ninguna."""
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else UNMATCHED_ROUTE


def observe_request(method: str, route: str, status: int, seconds: float) -> None:
    """Cuenta una petición respondida y su duración."""
    REQUESTS.labels(method, route, str(status)).inc()
    LATENCY.labels(method, route).observe(seconds)


def metrics_response() -> Response:
    """Las métricas en el formato de texto de Prometheus."""
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
