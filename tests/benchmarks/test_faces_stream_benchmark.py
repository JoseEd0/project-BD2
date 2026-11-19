"""La medida del reconocimiento en vivo: el resumen de una pasada, los cuadros y una ejecución
completa contra el servicio servido por uvicorn en un puerto libre, con el gestor en el mismo
proceso y Redis de mentira."""

import argparse
import io
import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn
from apps.faces.tests.faces_support import selfie
from benchmarks.faces_stream_benchmark import (
    DISTINCT_FRAMES,
    FRAME_SIZE,
    REST_STAGE,
    STAGES,
    TAIL_FIELD,
    WITH_CACHE,
    WITHOUT_CACHE,
    Run,
    frames,
    run_benchmark,
)
from fakeredis import FakeAsyncRedis, FakeServer
from PIL import Image

from api.auth import Role, ServiceKey, digest_of
from api.main import create_app as create_engine_app
from config import EngineConfig
from faces.api import create_app
from faces.api.settings import FacesSettings
from faces.models import MODELS_DIRECTORY_VARIABLE
from faces.pose import Pose

SERVICE_KEY = "clave-del-benchmark-en-vivo-0123456789"
ADMIN_KEY = "clave-de-administracion-en-vivo-0123456789"
LOCALHOST = "127.0.0.1"
STARTUP_SECONDS = 30.0
POLL_SECONDS = 0.05


def result(frame: int, cached: bool, total: float) -> dict[str, object]:
    timings = dict.fromkeys(STAGES, 1.0) | {"wait": 0.0, "total": total}
    face = {"status": "known", "cached": cached}
    return {"frame": frame, "faces": [face], "dropped": 2, "timings": timings}


def test_a_run_summarizes_latency_stages_and_the_rest():
    run = Run()
    run.add(10.0, result(1, False, 6.0))
    run.add(20.0, result(2, True, 6.0))
    summary = run.summary()
    assert (summary["p50_ms"], summary["max_ms"]) == (15.0, 20.0)
    assert summary["detect_p50_ms"] == 1.0
    assert summary[f"{REST_STAGE}_p50_ms"] == 10.0
    assert summary[TAIL_FIELD] == pytest.approx(summary["p95_ms"] - summary["p50_ms"])
    assert (summary["rostros"], summary["de_la_cache"], summary["reconocidos"]) == (2, 1, 2)
    assert summary["descartados"] == 2


def test_frames_are_jpegs_of_the_frame_size_that_move():
    images = frames(selfie(), DISTINCT_FRAMES, seed=1)
    assert len(images) == DISTINCT_FRAMES
    assert len(set(images)) > 1
    with Image.open(io.BytesIO(images[0])) as first:
        assert (first.format, first.size) == ("JPEG", FRAME_SIZE)


@pytest.fixture
def served_faces(tmp_path: Path) -> Iterator[str]:
    models = os.environ.get(MODELS_DIRECTORY_VARIABLE)
    if not models:
        pytest.skip(f"sin {MODELS_DIRECTORY_VARIABLE}, no hay modelos con que medir")
    keys = (
        ServiceKey("faces", Role.WRITE, digest_of(SERVICE_KEY)),
        ServiceKey("faces-migraciones", Role.ADMIN, digest_of(ADMIN_KEY)),
    )
    engine = create_engine_app(EngineConfig(data_directory=tmp_path), service_keys=keys)
    settings = FacesSettings(
        engine_url="http://gestor",
        engine_key=SERVICE_KEY,
        migration_key=ADMIN_KEY,
        models_dir=Path(models),
        analysis_workers=1,
        enrollment_poses=(Pose.FRONT, Pose.FRONT),
    )
    redis = FakeServer()
    app = create_app(
        settings,
        engine_transport=httpx.ASGITransport(app=engine),
        redis=lambda: FakeAsyncRedis(server=redis),
    )
    server = uvicorn.Server(uvicorn.Config(app, host=LOCALHOST, port=0, log_level="warning"))
    thread = threading.Thread(target=server.run)
    thread.start()
    try:
        deadline = time.monotonic() + STARTUP_SECONDS
        while not server.started and time.monotonic() < deadline:
            time.sleep(POLL_SECONDS)
        assert server.started
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"{LOCALHOST}:{port}"
    finally:
        server.should_exit = True
        thread.join()
        engine.state.service.close()


def test_the_whole_experiment_runs_against_a_served_api(served_faces: str, tmp_path: Path):
    arguments = argparse.Namespace(
        api_url=f"http://{served_faces}",
        target=[f"directo=ws://{served_faces}/ws/recognize"],
        frames=4,
        warmup=1,
        fps=50.0,
        seed=1,
        compose_project=None,
        output=tmp_path,
    )
    report = run_benchmark(arguments)
    measured = {item.operation: item.extra for item in report.measurements}
    assert set(measured) == {WITH_CACHE, WITHOUT_CACHE}
    assert measured[WITH_CACHE]["reconocidos"] == 4
    assert measured[WITH_CACHE]["de_la_cache"] == 4
    assert measured[WITHOUT_CACHE]["de_la_cache"] == 0
    assert measured[WITHOUT_CACHE]["reconocidos"] == 4
    assert len(measured[WITH_CACHE]["latencias_ms"]) == 4
    again = run_benchmark(arguments)
    assert len(again.measurements) == len(report.measurements)
