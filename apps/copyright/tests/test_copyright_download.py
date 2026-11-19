"""La descarga del catálogo contra un FMA de juguete servido por HTTP en local: qué baja,
cómo se reanuda y cómo se verifica."""

from __future__ import annotations

from pathlib import Path

import pytest
from copyright_support import (
    AUDIO_PATH,
    SILENT_TRACK,
    TOY_TRACKS,
    TRACK_SECONDS,
    audio_members,
    serving,
    toy_audio,
    toy_fma,
    zip_of,
)

from songcheck.catalog import CATALOG_FILE, dataset_directory, read_catalog
from songcheck.errors import TracksError
from songcheck.fma.archive import track_path
from songcheck.fma.prepare import prepare_catalog
from songcheck.fma.tracks import read_tracks

THREADS = 2
PLAYABLE = (2, 5, 140, 155066)
GARBAGE_BYTES = 150_000


def track_file(data: Path, track_id: int) -> Path:
    return dataset_directory(data) / "pistas" / track_path(track_id)


def test_the_catalog_lists_the_playable_small_tracks_with_metadata(tmp_path: Path):
    with serving(toy_fma()) as server:
        report = prepare_catalog(tmp_path, server.sources(), THREADS)
    songs = read_catalog(report.catalog)
    assert [song.track_id for song in songs] == list(PLAYABLE)
    assert (report.songs, report.fetched, report.already_intact, report.unreadable) == (4, 4, 0, 0)
    first = songs[0]
    assert (first.title, first.artist, first.genre, first.path) == (
        "Food",
        "AWOL",
        "Hip-Hop",
        "pistas/000/000002.mp3",
    )
    assert first.duration_seconds == pytest.approx(TRACK_SECONDS)
    assert songs[-1].duration_seconds == pytest.approx(3.5)
    assert track_file(tmp_path, 2).read_bytes() == toy_audio()[2]
    assert not track_file(tmp_path, SILENT_TRACK).exists()


def test_a_second_run_does_not_touch_the_network(tmp_path: Path):
    with serving(toy_fma()) as server:
        prepare_catalog(tmp_path, server.sources(), THREADS)
        server.gets.clear()
        report = prepare_catalog(tmp_path, server.sources(), THREADS)
    assert sum(server.gets.values()) == 0
    assert (report.songs, report.fetched, report.already_intact) == (4, 0, 4)


def test_only_missing_or_damaged_tracks_are_fetched_again(tmp_path: Path):
    with serving(toy_fma()) as server:
        prepare_catalog(tmp_path, server.sources(), THREADS)
        track_file(tmp_path, 5).unlink()
        damaged = track_file(tmp_path, 140)
        damaged.write_bytes(damaged.read_bytes()[:-1] + b"\1")
        report = prepare_catalog(tmp_path, server.sources(), THREADS)
    assert (report.fetched, report.already_intact) == (2, 2)
    assert track_file(tmp_path, 140).read_bytes() == toy_audio()[140]


def test_an_interrupted_download_resumes_without_a_catalog(tmp_path: Path):
    with serving(toy_fma()) as server:
        prepare_catalog(tmp_path, server.sources(), THREADS)
        (dataset_directory(tmp_path) / CATALOG_FILE).unlink()
        track_file(tmp_path, 2).unlink()
        report = prepare_catalog(tmp_path, server.sources(), THREADS)
    assert (report.songs, report.fetched, report.already_intact) == (4, 1, 3)


def test_a_track_that_arrives_with_another_crc_is_rejected(tmp_path: Path):
    files = toy_fma()
    archive = files[AUDIO_PATH]
    target = toy_audio()[5]
    position = archive.index(target) + len(target) // 2
    files[AUDIO_PATH] = (
        archive[:position] + bytes([archive[position] ^ 0xFF]) + archive[position + 1 :]
    )
    with serving(files) as server, pytest.raises(TracksError, match="dañada"):
        prepare_catalog(tmp_path, server.sources(), THREADS)
    assert not (dataset_directory(tmp_path) / CATALOG_FILE).exists()


def test_tracks_csv_without_a_needed_column_is_rejected():
    rows = [["", "artist"], ["", "name"], ["track_id", ""]]
    with pytest.raises(TracksError, match="genre_top"):
        list(read_tracks(iter(rows)))


def test_tracks_missing_from_the_audio_zip_are_left_out(tmp_path: Path):
    files = toy_fma()
    files[AUDIO_PATH] = zip_of({"fma_small/README.txt": b"FMA"})
    with serving(files) as server:
        report = prepare_catalog(tmp_path, server.sources(), THREADS)
    assert report.songs == 0


def test_a_track_that_does_not_decode_is_left_out_of_the_catalog(tmp_path: Path):
    files = toy_fma()
    members = audio_members(TOY_TRACKS)
    members["fma_small/000/000005.mp3"] = b"\x01" * GARBAGE_BYTES
    files[AUDIO_PATH] = zip_of(members)
    with serving(files) as server:
        report = prepare_catalog(tmp_path, server.sources(), THREADS)
    assert (report.songs, report.unreadable) == (3, 1)
    assert 5 not in [song.track_id for song in read_catalog(report.catalog)]
