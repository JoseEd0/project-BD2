"""El catálogo de juguete del despliegue: el trabajo de carga lo da por completo sin red."""

from __future__ import annotations

from pathlib import Path

import soundfile

from songcheck.catalog import read_catalog
from songcheck.fma.prepare import FmaSources, prepare_catalog
from songcheck.sample import write_sample

UNREACHABLE = FmaSources(
    audio_url="http://127.0.0.1:9/a.zip", metadata_url="http://127.0.0.1:9/m.zip"
)
THREADS = 1


def test_the_sample_is_a_complete_catalog_that_needs_no_network(tmp_path: Path):
    path = write_sample(tmp_path, songs=3, seconds=6.0)
    songs = read_catalog(path)
    assert [(song.track_id, song.duration_seconds) for song in songs] == [
        (1, 6.0),
        (2, 6.0),
        (3, 6.0),
    ]
    report = prepare_catalog(tmp_path, UNREACHABLE, THREADS)
    assert (report.songs, report.fetched, report.already_intact) == (3, 0, 3)


def test_the_sample_audio_lasts_what_the_catalog_says(tmp_path: Path):
    (song, *_) = read_catalog(write_sample(tmp_path, songs=1, seconds=6.0))
    info = soundfile.info(str(tmp_path / "fma_small" / song.path))
    assert info.frames / info.samplerate == song.duration_seconds
