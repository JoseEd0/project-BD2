"""`GET /metrics` de un servicio del kit: peticiones por plantilla de ruta y estado, latencia
y búsquedas en la caché."""

from __future__ import annotations

from apps.kit.tests.kit_fakes import FakeEngine
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from appkit import CACHE_HIT, CACHE_MISS, count_cache_lookup
from appkit.metrics import UNMATCHED_ROUTE

Samples = dict[tuple[str, frozenset[tuple[str, str]]], float]


def samples(service: TestClient) -> Samples:
    response = service.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    return {
        (sample.name, frozenset(sample.labels.items())): sample.value
        for family in text_string_to_metric_families(response.text)
        for sample in family.samples
    }


def value(found: Samples, name: str, **labels: str) -> float:
    return found.get((name, frozenset(labels.items())), 0.0)


def requests(found: Samples, route: str, status: str) -> float:
    return value(found, "http_requests_total", method="GET", route=route, status=status)


def counted(before: Samples, after: Samples, route: str, status: str) -> float:
    return requests(after, route, status) - requests(before, route, status)


def test_requests_are_counted_by_route_template_and_status(service: TestClient, engine: FakeEngine):
    engine.rows[7] = [7, "hola"]
    before = samples(service)
    service.get("/notas/7")
    service.get("/notas/99")
    after = samples(service)
    assert counted(before, after, "/notas/{note_id}", "200") == 1
    assert counted(before, after, "/notas/{note_id}", "404") == 1
    assert not any("/notas/7" in str(labels) for _, labels in after)


def test_an_unknown_route_and_a_failure_are_counted(service: TestClient):
    before = samples(service)
    service.get("/no-existe")
    service.get("/fallo")
    after = samples(service)
    assert counted(before, after, UNMATCHED_ROUTE, "404") == 1
    assert counted(before, after, "/fallo", "500") == 1


def test_latency_is_a_histogram_per_route(service: TestClient):
    service.get("/health")
    found = samples(service)
    assert value(found, "http_request_duration_seconds_count", method="GET", route="/health") >= 1
    bucket = {"method": "GET", "route": "/health", "le": "+Inf"}
    assert value(found, "http_request_duration_seconds_bucket", **bucket) >= 1


def test_cache_lookups_are_counted_by_result(service: TestClient):
    before = samples(service)
    count_cache_lookup("prueba", CACHE_HIT)
    count_cache_lookup("prueba", CACHE_HIT)
    count_cache_lookup("prueba", CACHE_MISS)
    after = samples(service)
    hits = value(after, "cache_lookups_total", cache="prueba", result=CACHE_HIT)
    misses = value(after, "cache_lookups_total", cache="prueba", result=CACHE_MISS)
    assert hits - value(before, "cache_lookups_total", cache="prueba", result=CACHE_HIT) == 2
    assert misses - value(before, "cache_lookups_total", cache="prueba", result=CACHE_MISS) == 1


def test_the_metrics_route_is_not_part_of_the_contract(service: TestClient):
    assert "/metrics" not in service.get("/openapi.json").json()["paths"]
