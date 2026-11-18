"""La medida del pipeline de rostros: percentiles por etapa, cobertura y fotos ampliadas."""

import argparse
import os
import shutil
from pathlib import Path

import cv2
import numpy as np
import pytest
from benchmarks.common import Report
from benchmarks.faces_benchmark import (
    COVERAGE,
    DECODE_STAGE,
    DETECT_STAGE,
    DETECTOR,
    EMBED_STAGE,
    LATENCY,
    LFW_SIDE,
    TOTAL,
    ImageTiming,
    add_coverage,
    add_latencies,
    enlarged,
)

from benchmarks import faces_benchmark
from faces.models import MODELS_DIRECTORY_VARIABLE

FIXTURES = Path(__file__).parents[2] / "apps" / "faces" / "tests" / "fixtures"
PORTRAIT = FIXTURES / "collins-1998.jpg"
TIMINGS = [
    ImageTiming(decode=1.0, detect=2.0, embed=10.0, faces=1),
    ImageTiming(decode=1.0, detect=4.0, embed=None, faces=0),
    ImageTiming(decode=3.0, detect=2.0, embed=12.0, faces=3),
]


def measurement(report: Report, technique: str, operation: str):
    return next(
        item
        for item in report.measurements
        if item.technique == technique and item.operation == operation
    )


def test_each_stage_reports_its_median_and_95th_percentile():
    report = Report("rostros", {})

    add_latencies(report, LFW_SIDE, TIMINGS)

    embed = measurement(report, EMBED_STAGE, LATENCY)
    assert embed.dataset_size == LFW_SIDE
    assert embed.milliseconds == pytest.approx(11.0)
    assert embed.extra["p95_ms"] == pytest.approx(11.9)
    assert embed.extra["fotos"] == 2
    assert measurement(report, DECODE_STAGE, LATENCY).extra["fotos"] == 3
    assert measurement(report, DETECT_STAGE, LATENCY).milliseconds == pytest.approx(2.0)
    assert measurement(report, TOTAL, LATENCY).milliseconds == pytest.approx(13.0)


def test_coverage_counts_the_photos_with_a_face_and_with_several():
    report = Report("rostros", {})

    add_coverage(report, TIMINGS)

    coverage = measurement(report, DETECTOR, COVERAGE)
    assert coverage.dataset_size == len(TIMINGS)
    assert (coverage.extra["con_rostro"], coverage.extra["sin_rostro"]) == (2, 1)
    assert coverage.extra["con_varios"] == 1
    assert coverage.extra["cobertura"] == pytest.approx(2 / 3, abs=1e-5)


def test_an_enlarged_photo_is_a_jpeg_with_the_requested_longer_side():
    image = cv2.imdecode(np.frombuffer(enlarged(PORTRAIT, 900), np.uint8), cv2.IMREAD_COLOR)

    assert max(image.shape[:2]) == 900


def test_the_whole_experiment_runs_on_a_tiny_gallery(tmp_path: Path):
    models = os.environ.get(MODELS_DIRECTORY_VARIABLE)
    if not models:
        pytest.skip(f"sin {MODELS_DIRECTORY_VARIABLE}, no hay modelos con que medir")
    person = tmp_path / "lfw" / "Eileen_Collins"
    person.mkdir(parents=True)
    shutil.copy(PORTRAIT, person / "Eileen_Collins_0001.jpg")
    cv2.imwrite(str(person / "Eileen_Collins_0002.jpg"), np.full((250, 250, 3), 128, np.uint8))
    arguments = argparse.Namespace(
        lfw=tmp_path / "lfw", models=Path(models), sides=[500], sample=2, warmup=1, seed=1, threads=1
    )

    report = faces_benchmark.run(arguments)

    assert measurement(report, DETECTOR, COVERAGE).extra["con_rostro"] == 1
    assert {item.dataset_size for item in report.measurements if item.operation == LATENCY} == {
        LFW_SIDE,
        500,
    }
