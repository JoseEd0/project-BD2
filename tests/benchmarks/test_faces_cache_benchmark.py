"""La medida de la caché de rastros: la secuencia que se desliza, el rebote en los bordes, los
aciertos por segundo y el resumen de una velocidad."""

import io

import pytest
from apps.faces.tests.faces_support import selfie
from benchmarks.common import Report
from benchmarks.faces_cache_benchmark import (
    SPEED,
    TIMELINE,
    _bounced,
    add_speed,
    hit_rate_per_second,
    sliding_frames,
)
from benchmarks.faces_stream_benchmark import FRAME_SIZE, STAGES, WITH_CACHE, WITHOUT_CACHE, Run
from PIL import Image


def test_the_face_bounces_between_the_edges():
    assert [_bounced(step * 40, 100) for step in range(7)] == [0, 40, 80, 80, 40, 0, 40]
    assert _bounced(500, 0) == 0


def test_a_still_face_gives_identical_frames_and_a_moving_one_does_not():
    portrait = selfie()
    still = sliding_frames(portrait, 3, 0)
    moving = sliding_frames(portrait, 3, 32)
    assert len(set(still)) == 1
    assert len(set(moving)) == 3
    with Image.open(io.BytesIO(moving[0])) as first:
        assert (first.format, first.size) == ("JPEG", FRAME_SIZE)


def test_hits_are_counted_per_whole_second_of_video():
    cached = [0, 1, 1, 1, 1, 0, 1, 1, 1]
    assert hit_rate_per_second(cached, fps=4) == [0.75, 0.75]


def run_with(cached: list[bool], latency: float) -> Run:
    run = Run()
    for frame, hit in enumerate(cached, start=1):
        timings = dict.fromkeys(STAGES, 1.0) | {"total": latency}
        face = {"status": "known", "cached": hit}
        run.add(latency, {"frame": frame, "faces": [face], "dropped": 0, "timings": timings})
    return run


def test_a_speed_reports_its_hit_rate_and_the_milliseconds_saved():
    report = Report("prueba", {})
    runs = {
        WITH_CACHE: run_with([False, True, True, True], 10.0),
        WITHOUT_CACHE: run_with([False] * 4, 25.0),
    }
    add_speed(report, 16, runs, {WITH_CACHE: (1.0, None), WITHOUT_CACHE: (1.0, None)}, fps=2)
    found = {(item.technique, item.operation): item for item in report.measurements}
    with_cache = found[(WITH_CACHE, SPEED)]
    assert with_cache.dataset_size == 16
    assert with_cache.extra["tasa_de_aciertos"] == 0.75
    assert with_cache.extra["ahorro_p50_ms"] == pytest.approx(15.0)
    assert found[(WITHOUT_CACHE, SPEED)].extra["tasa_de_aciertos"] == 0.0
    assert found[("16 px por cuadro", TIMELINE)].extra["tasa_de_aciertos"] == [0.5, 1.0]
