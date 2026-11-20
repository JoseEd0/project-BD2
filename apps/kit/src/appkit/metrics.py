"""Métricas de Prometheus de un servicio: peticiones por ruta y estado, su latencia, los
aciertos de su caché y la CPU y memoria del proceso, en `GET /metrics`.

Los nombres son los del gestor (`src/api/metrics.py`), así un mismo panel de Grafana compara
la API de una app con el gestor. Cada réplica cuenta lo suyo; Prometheus las encuentra por el
DNS de Docker y las suma. El nginx de la app no reenvía `/api/metrics`: solo Prometheus llega,
por la red de la app. La ruta es la plantilla (`/productos/{product_id}`), no la URL.

    count_cache_lookup("recomendaciones", CACHE_HIT)
"""

from __future__ import annotations

from fastapi import FastAPI, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    ProcessCollector,
    generate_latest,
)
from starlette.types import Scope

from appkit.logs import METRICS_PATH

UNMATCHED_ROUTE = "unmatched"
CACHE_HIT = "hit"
CACHE_MISS = "miss"
CACHE_BYPASS = "bypass"
# De 5 ms a 30 s: una lectura del catálogo tarda milisegundos, y una detección de audio o
# una foto de 50 MP, segundos.
LATENCY_BUCKETS_SECONDS = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0,
)

# Un registro propio y no el global de `prometheus_client`: los tests importan el gestor y
# los servicios en el mismo proceso, con los mismos nombres de métrica.
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
CACHE_LOOKUPS = Counter(
    "cache_lookups_total",
    "Búsquedas en la caché del servicio, por caché y resultado (hit, miss, bypass)",
    ["cache", "result"],
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


def count_cache_lookup(cache: str, result: str) -> None:
    """Cuenta una búsqueda en `cache`: `CACHE_HIT`, `CACHE_MISS` o `CACHE_BYPASS` (sin
    caché ahora)."""
    CACHE_LOOKUPS.labels(cache, result).inc()


def add_metrics_route(app: FastAPI) -> None:
    """`GET /metrics` en el formato de texto de Prometheus, fuera del contrato OpenAPI."""

    @app.get(METRICS_PATH, include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
