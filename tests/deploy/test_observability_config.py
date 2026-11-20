"""El perfil `observability` sin levantarlo: Prometheus lee servicios que existen y por redes
a las que está conectado, y el tablero de Grafana solo pide métricas que alguien exporta."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from api import metrics as engine_metrics
from appkit import metrics as service_metrics

ROOT = Path(__file__).resolve().parents[2]
OBSERVABILITY = ROOT / "deploy" / "observability"
APP_COMPOSE_FILES = sorted(ROOT.glob("apps/*/compose.yaml")) + [ROOT / "deploy/example/compose.yaml"]
DASHBOARD = OBSERVABILITY / "grafana" / "dashboards" / "minigestor.json"
SERVICE_PORT = 8000
# Lo que no es una métrica en una expresión de PromQL: funciones, agregaciones y palabras.
PROMQL_WORDS = {"sum", "by", "rate", "count", "histogram_quantile", "up", "le"}
METRIC_NAME = re.compile(r"\b([a-z_]+)(?=\{|\[| \))")


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def services() -> dict[str, Any]:
    found: dict[str, Any] = {}
    for path in [ROOT / "compose.yaml", *APP_COMPOSE_FILES, OBSERVABILITY / "compose.yaml"]:
        found.update(load_yaml(path)["services"])
    return found


def scrape_targets() -> dict[str, tuple[str, int]]:
    config = load_yaml(OBSERVABILITY / "prometheus.yml")
    targets = {}
    for job in config["scrape_configs"]:
        (discovery,) = job["dns_sd_configs"]
        (name,) = discovery["names"]
        targets[job["job_name"]] = (name, discovery["port"])
    return targets


def test_every_scraped_name_is_a_service_on_a_network_prometheus_joins():
    known = services()
    prometheus_networks = set(known["prometheus"]["networks"])
    for job, (name, port) in scrape_targets().items():
        assert name in known, job
        assert port == SERVICE_PORT, job
        assert set(known[name]["networks"]) & prometheus_networks, job


def test_every_api_and_the_engine_are_scraped():
    scraped = {name for name, _ in scrape_targets().values()}
    apis = {name for name in services() if name.endswith("-api")}
    assert apis | {"engine"} <= scraped


def test_both_observability_services_are_behind_their_profile():
    known = services()
    assert known["prometheus"]["profiles"] == ["observability"]
    assert known["grafana"]["profiles"] == ["observability"]


def test_grafana_is_published_only_on_localhost():
    (port,) = services()["grafana"]["ports"]
    assert port.startswith("127.0.0.1:")


def dashboard_expressions() -> list[str]:
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    return [target["expr"] for panel in dashboard["panels"] for target in panel["targets"]]


SAMPLE_SUFFIXES = {"counter": ("_total",), "histogram": ("_bucket", "_count", "_sum")}


def exported_metrics() -> set[str]:
    """Los nombres de muestra de cada métrica de los dos registros, aunque todavía no se haya
    contado nada en ellas."""
    return {
        family.name + suffix
        for registry in (engine_metrics.REGISTRY, service_metrics.REGISTRY)
        for family in registry.collect()
        for suffix in SAMPLE_SUFFIXES.get(family.type, ("",))
    }


def test_the_dashboard_asks_only_for_exported_metrics():
    # El colector del proceso solo exporta en Linux, donde corren los contenedores.
    exported = exported_metrics() | {"process_cpu_seconds_total", "process_resident_memory_bytes"}
    asked = {
        name
        for expression in dashboard_expressions()
        for name in METRIC_NAME.findall(expression)
        if name not in PROMQL_WORDS
    }
    assert asked
    assert asked <= exported, asked - exported


def test_the_dashboard_uses_the_provisioned_datasource():
    datasource = load_yaml(OBSERVABILITY / "grafana/provisioning/datasources/prometheus.yml")
    (provisioned,) = datasource["datasources"]
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    assert {panel["datasource"]["uid"] for panel in dashboard["panels"]} == {provisioned["uid"]}
