"""La medida del servicio de rostros: errores del conjunto abierto, resumen por etapa y una
ejecución completa sobre una galería de tres personas hecha con las fotos de prueba."""

import argparse
import os
from pathlib import Path

import httpx
import pytest
from apps.faces.tests.faces_support import (
    OTHER_PORTRAIT,
    SINGLE_PORTRAIT,
    crew_member,
    selfie,
)
from benchmarks.common import Report
from benchmarks.faces_service_benchmark import (
    ENROLLED,
    FALSE_DUPLICATES,
    IDENTIFY,
    MEASUREMENTS,
    OPEN_SET,
    REJECTIONS,
    SHOT,
    STRANGER,
    Timings,
    _best_score,
    _genuine_outcome,
    _open_set,
)

from benchmarks import faces_service_benchmark
from faces.models import MODELS_DIRECTORY_VARIABLE


def test_open_set_errors_at_a_threshold():
    genuine = [(True, 0.8), (True, 0.4), (False, 0.7), (False, None)]
    impostors = [0.2, 0.5, None, 0.39]

    errors = _open_set(genuine, impostors, 0.45)

    assert errors["fnir"] == pytest.approx(0.75)
    assert errors["fpir"] == pytest.approx(0.25)
    assert (errors["registradas"], errors["desconocidas"]) == (4, 4)
    assert _open_set([], [], 0.45)["fnir"] == 0.0


def test_the_main_face_decides_the_outcome():
    known = {
        "faces": [{"display_name": "Ana Paz", "score": 0.7}, {"display_name": None, "score": 0.1}]
    }
    assert _genuine_outcome(known, "Ana_Paz") == (True, 0.7)
    assert _genuine_outcome({"faces": []}, "Ana_Paz") == (False, None)
    assert _best_score(known) == 0.7
    assert _best_score({"faces": [{"score": None}]}) is None


def test_timings_summarize_each_stage():
    timings = Timings()
    for total, analysis in ((10.0, 6.0), (20.0, 8.0), (30.0, 10.0)):
        response = httpx.Response(200, headers={"Server-Timing": f"analisis;dur={analysis}"})
        timings.add(response, total)

    summary = timings.summary()

    assert summary["p50"] == pytest.approx(20.0)
    assert summary["analisis_p50_ms"] == pytest.approx(8.0)
    assert summary["galeria_p50_ms"] == 0.0
    assert summary["consultas"] == 3


def test_the_whole_experiment_runs_on_a_tiny_gallery(tmp_path: Path):
    models = os.environ.get(MODELS_DIRECTORY_VARIABLE)
    if not models:
        pytest.skip(f"sin {MODELS_DIRECTORY_VARIABLE}, no hay modelos con que medir")
    photos = {
        "Eileen_Collins": [selfie(SINGLE_PORTRAIT), selfie(OTHER_PORTRAIT)],
        "Otro_Tripulante": [crew_member("other"), crew_member("other")],
        "Una_Sola": [crew_member("another")],
    }
    for person, images in photos.items():
        (tmp_path / person).mkdir()
        for number, image in enumerate(images, start=1):
            image.save(tmp_path / person / f"{person}_{number:04d}.jpg")
    arguments = argparse.Namespace(
        lfw=tmp_path,
        models=Path(models),
        people=2,
        checkpoints=[1, 2],
        queries=2,
        thresholds=[0.363, 0.5],
        seed=1,
        threads=1,
    )

    report = faces_service_benchmark.run(arguments)

    def find(technique: str, operation: str, size: int) -> dict[str, object]:
        return next(
            item.extra
            for item in report.measurements
            if (item.technique, item.operation, item.dataset_size) == (technique, operation, size)
        )

    assert find("toma aceptada", SHOT, 2)["consultas"] == 2
    assert find(ENROLLED, IDENTIFY, 2)["consultas"] == 2
    assert find(STRANGER, IDENTIFY, 1)["consultas"] == 1
    assert find("umbral 0.363", OPEN_SET, 2)["fnir"] == 0.0
    assert find("LFW", REJECTIONS, 2)["rechazadas"] == 0
    assert find("umbral 0.5", FALSE_DUPLICATES, 2)["bloqueados"] == 0
    assert find("sharpness", MEASUREMENTS, 2)["p50"] > 0
    assert isinstance(report, Report)
