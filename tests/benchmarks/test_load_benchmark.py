"""La prueba de carga sin Docker: los escenarios que prepara para k6, el fragmento de audio,
lo que guarda de un resumen de k6 y que su compose tenga una imagen propia por servicio."""

import json
from pathlib import Path

import httpx
import numpy as np
import soundfile
import yaml
from benchmarks.load_benchmark import (
    APPS,
    COLD_REQUESTS,
    COMPOSE_FILES,
    COPYRIGHT_CLIP_SECONDS,
    K6_SCRIPT,
    K6_SCRIPT_DIRECTORY,
    PAPERS_SEARCH,
    SCENARIOS,
    SHOP_COLD,
    SHOP_WARM,
    WARM_PRODUCTS,
    measurement_of,
    prepare_papers,
    prepare_shop,
    wav_fragment,
)

PRODUCTS = [f"B{number:09d}" for number in range(120)]
SAMPLE_RATE = 8000


def shop_api() -> httpx.Client:
    def answer(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/busqueda/texto"
        found = [{"producto": {"id": product}, "puntaje": 1.0} for product in PRODUCTS]
        return httpx.Response(200, json=found)

    return httpx.Client(base_url="http://tienda", transport=httpx.MockTransport(answer))


def written(workspace: Path, key: str) -> dict:
    return json.loads((workspace / SCENARIOS / f"{key}.json").read_text())


def test_every_cold_shop_request_has_its_own_cache_key(tmp_path: Path):
    facts = prepare_shop(shop_api(), tmp_path, 1, tmp_path)
    cold = [request["path"] for request in written(tmp_path, SHOP_COLD.key)["requests"]]
    assert len(cold) == len(set(cold)) == COLD_REQUESTS
    assert facts["productos"] == len(PRODUCTS)


def test_the_warm_shop_requests_repeat_a_few_products(tmp_path: Path):
    prepare_shop(shop_api(), tmp_path, 1, tmp_path)
    warm = [request["path"] for request in written(tmp_path, SHOP_WARM.key)["requests"]]
    assert len(set(warm)) == WARM_PRODUCTS
    assert all(path.split("/")[2] in PRODUCTS for path in warm)


def test_the_papers_search_asks_every_question_in_hybrid_mode(tmp_path: Path):
    facts = prepare_papers(shop_api(), tmp_path, 1, tmp_path)
    scenario = written(tmp_path, PAPERS_SEARCH.key)
    assert scenario["method"] == "POST"
    assert len(scenario["requests"]) == facts["preguntas"] > 0
    assert {request["body"]["mode"] for request in scenario["requests"]} == {"hybrid"}


def test_the_detection_fragment_is_a_wav_of_the_asked_seconds(tmp_path: Path):
    track = tmp_path / "pista.wav"
    soundfile.write(track, np.zeros((SAMPLE_RATE * 30, 2), dtype=np.int16), SAMPLE_RATE)
    fragment = tmp_path / "fragmento.wav"
    fragment.write_bytes(wav_fragment(track))
    info = soundfile.info(fragment)
    assert (info.format, info.channels) == ("WAV", 2)
    assert info.frames == SAMPLE_RATE * COPYRIGHT_CLIP_SECONDS


def k6_summary(cache: dict[str, int] | None = None) -> dict:
    metrics = {
        "http_reqs": {"values": {"rate": 123.456, "count": 3704}},
        "http_req_failed": {"values": {"rate": 0.25}},
        "http_req_duration": {"values": {"med": 10.123, "p(95)": 20.5, "p(99)": 31.0}},
    }
    for result, count in (cache or {}).items():
        metrics[f"cache_results{{result:{result}}}"] = {"values": {"count": count}}
    metrics["responses{status:2xx}"] = {"values": {"count": 3700}}
    metrics["responses{status:5xx}"] = {"values": {"count": 4}}
    metrics["responses{status:4xx}"] = {"values": {"count": 0}}
    return {"metrics": metrics, "stages": {"gestor": {"values": {"med": 7.891}}, "otra": None}}


def test_a_measurement_keeps_throughput_percentiles_stages_and_cpu():
    extra = measurement_of(k6_summary(), 2, {"gestor": 0.97, "api": 1.5}, {"carga": 3.0})
    assert extra["replicas"] == 2
    assert (extra["rps"], extra["peticiones"]) == (123.46, 3704)
    assert extra["rps_correctas"] == 92.59
    assert (extra["p50_ms"], extra["p95_ms"], extra["p99_ms"]) == (10.12, 20.5, 31.0)
    assert extra["gestor_p50_ms"] == 7.89
    assert "otra_p50_ms" not in extra
    assert (extra["cpu_gestor_nucleos"], extra["cpu_api_nucleos"]) == (0.97, 1.5)
    assert "cache_hit" not in extra
    assert (extra["respuestas_2xx"], extra["respuestas_5xx"]) == (3700, 4)
    assert "respuestas_4xx" not in extra


def test_cache_results_are_kept_only_when_the_route_answered_with_x_cache():
    extra = measurement_of(k6_summary({"hit": 0, "miss": 40}), 1, {}, {})
    assert (extra["cache_hit"], extra["cache_miss"]) == (0, 40)


def test_every_measured_service_runs_with_its_own_image():
    override = yaml.safe_load(COMPOSE_FILES[1].read_text(encoding="utf-8"))["services"]
    for app in APPS.values():
        for service in (app.api, app.web):
            assert override[service]["image"].startswith("carga-"), service
    assert override["engine"]["image"].startswith("carga-")


def test_the_k6_script_reads_what_the_benchmark_passes():
    script = (K6_SCRIPT_DIRECTORY / K6_SCRIPT).read_text(encoding="utf-8")
    for variable in ("BASE_URL", "SCENARIO", "STAGES", "VUS", "DURATION"):
        assert f"__ENV.{variable}" in script
