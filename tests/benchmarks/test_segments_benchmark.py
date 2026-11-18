"""El experimento de segmentos, en pequeño: no mide nada, comprueba que sigue funcionando y
que los dos caminos de la consulta dan los mismos vecinos."""

from pathlib import Path

import numpy as np
from benchmarks.segments.names import (
    EXTRACTION,
    INDEX_BUILD,
    SEGMENT_QUERY,
    SEGMENT_STAGES,
    SEGMENTS,
    SEPARATE,
)
from benchmarks.segments_benchmark import Settings, run
from tests.multimedia.media_support import SAMPLE_RATE, chirp, tone, write_audio

TRACKS = 3
TRACK_SECONDS = 12.0
SONG_SECONDS = 20


def tracks(directory: Path) -> Path:
    folder = directory / "pistas" / "000"
    folder.mkdir(parents=True)
    for number in range(TRACKS):
        samples = np.roll(chirp(TRACK_SECONDS), number * SAMPLE_RATE) + 0.1 * tone(TRACK_SECONDS)
        write_audio(folder / f"00000{number}.mp3", samples)
    return folder.parent


def test_the_experiment_runs_and_both_paths_agree(tmp_path: Path):
    settings = Settings(
        tracks_directory=tracks(tmp_path),
        catalogue_sizes=(2, TRACKS),
        song_seconds=(SONG_SECONDS,),
        window=4.0,
        hop=2.0,
        k=2,
        repetitions=1,
        workers=1,
    )

    report = run(settings, tmp_path / "trabajo")

    measured = {(item.technique, item.operation, item.dataset_size) for item in report.measurements}
    operations = {operation for _, operation, _ in measured}
    assert operations == {INDEX_BUILD, EXTRACTION, SEGMENT_QUERY, SEGMENT_STAGES}
    batched = next(item for item in report.measurements if item.technique == SEGMENTS)
    separate = next(item for item in report.measurements if item.technique == SEPARATE)
    assert batched.extra["ventanas"] == separate.extra["ventanas"] == 9
    assert batched.extra["ventanas_con_los_mismos_vecinos"] == 9
    assert separate.extra["peticiones"] == 10
