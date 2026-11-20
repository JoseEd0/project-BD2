"""`GET /metrics` del gestor: peticiones por plantilla de ruta y estado, latencia y CPU."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from api.auth import Role, ServiceKey, digest_of
from api.main import create_app
from api.metrics import UNMATCHED_ROUTE
from config import EngineConfig

KEY = "clave-del-servicio-de-metricas-0123456789"


@pytest.fixture
def client(config: EngineConfig) -> Iterator[TestClient]:
    keys = (ServiceKey("metricas", Role.ADMIN, digest_of(KEY)),)
    with TestClient(create_app(config, service_keys=keys)) as instance:
        instance.headers["Authorization"] = f"Bearer {KEY}"
        yield instance


def samples(client: TestClient) -> dict[tuple[str, frozenset[tuple[str, str]]], float]:
    response = client.get("/metrics", headers={"Authorization": ""})
    assert response.status_code == 200
    return {
        (sample.name, frozenset(sample.labels.items())): sample.value
        for family in text_string_to_metric_families(response.text)
        for sample in family.samples
    }


def requests_of(
    found: dict[tuple[str, frozenset[tuple[str, str]]], float], route: str, status: str
) -> float:
    labels = frozenset({("method", "GET"), ("route", route), ("status", status)})
    return found.get(("http_requests_total", labels), 0.0)


def test_metrics_need_no_key_and_count_requests_by_route_template(client: TestClient):
    before = samples(client)
    client.post("/query", json={"sql": "CREATE TABLE t (id INT PRIMARY KEY)"})
    client.get("/tables/t/structure")
    client.get("/tables/t/structure")
    after = samples(client)
    route = "/tables/{name}/structure"
    assert requests_of(after, route, "200") - requests_of(before, route, "200") == 2
    assert not any("/tables/t/structure" in str(labels) for _, labels in after)


def test_rejected_and_unknown_requests_are_counted_too(client: TestClient):
    before = samples(client)
    client.get("/tables", headers={"Authorization": "Bearer otra"})
    client.get("/no-existe")
    after = samples(client)
    assert requests_of(after, "/tables", "401") - requests_of(before, "/tables", "401") == 1
    assert requests_of(after, UNMATCHED_ROUTE, "404") - requests_of(before, UNMATCHED_ROUTE, "404") == 1


def test_latency_is_a_histogram_per_route(client: TestClient):
    client.get("/tables")
    found = samples(client)
    count = ("http_request_duration_seconds_count", frozenset({("method", "GET"), ("route", "/tables")}))
    assert found[count] >= 1


def test_the_metrics_route_is_not_part_of_the_contract(client: TestClient):
    assert "/metrics" not in client.get("/openapi.json").json()["paths"]
