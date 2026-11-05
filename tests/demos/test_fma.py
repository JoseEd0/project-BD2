"""Las pistas del Free Music Archive sin tocar la red: `tracks.csv` y el `.zip` de audio se
arman aquí con el formato que publica el FMA y el `.zip` se lee por rangos desde memoria."""

import zipfile
from collections import Counter
from pathlib import Path

import pytest
from benchmarks.remote import RemoteFile
from tests.benchmarks.remote_support import MemorySource, zip_with_members

from demos import descargar_fma as fma

GENRES = ("Rock", "Hip-Hop", "Folk", "Electronic")
TRACKS_PER_GENRE = 10
SEED = 9

HEADER = [
    ["", "artist", "set", "set", "track", "track"],
    ["", "name", "split", "subset", "genre_top", "title"],
    ["track_id", "", "", "", "", ""],
]


def tracks_rows() -> list[list[str]]:
    """Diez pistas `small` por género, más una de `medium` que no entra."""
    rows = [list(row) for row in HEADER]
    track = 1
    for genre in GENRES:
        for number in range(TRACKS_PER_GENRE):
            rows.append([str(track), f"Artista {track % 7}", "training", "small", genre, f" {genre}  {number} "])
            track += 1
    rows.append([str(track), "Otro", "training", "medium", "Rock", "No entra"])
    return rows


def test_tracks_csv_gives_the_small_tracks_with_genre_and_artist():
    tracks = list(fma.read_tracks(iter(tracks_rows())))
    assert len(tracks) == len(GENRES) * TRACKS_PER_GENRE
    first = tracks[0]
    assert (first.path, first.title, first.category, first.group) == (
        "pistas/000/000001.mp3",
        "Rock 0",
        "Rock",
        "Artista 1",
    )


def test_a_tracks_csv_without_a_needed_column_is_rejected():
    rows = tracks_rows()
    rows[1][4] = "genres"
    with pytest.raises(fma.TracksError, match="genre_top"):
        list(fma.read_tracks(iter(rows)))


def test_track_paths_follow_the_folders_of_the_zip():
    assert fma.track_path(2) == "000/000002.mp3"
    assert fma.track_path(155066) == "155/155066.mp3"


def test_the_chosen_tracks_are_balanced_by_genre_and_reproducible():
    tracks = list(fma.read_tracks(iter(tracks_rows())))
    chosen = fma.choose_tracks(tracks, 8, SEED)
    assert Counter(item.category for item in chosen) == dict.fromkeys(GENRES, 2)
    assert fma.choose_tracks(reversed(tracks), 8, SEED) == chosen


def test_only_the_chosen_tracks_are_read_from_the_remote_zip_one_request_each(tmp_path: Path):
    members = {"fma_small/README.txt": b"FMA"}
    for track in (2, 5, 140, 155066):
        members[f"fma_small/{fma.track_path(track)}"] = f"ID3 pista {track}".encode() * 50
    content = zip_with_members(members)
    index_only = MemorySource(content)
    zipfile.ZipFile(RemoteFile(index_only)).close()
    source = MemorySource(content)
    wanted = ["000/000005.mp3", "155/155066.mp3"]
    assert fma.extract_tracks(RemoteFile(source), wanted, tmp_path) == 2
    for relative in wanted:
        assert (tmp_path / relative).read_bytes() == members[f"fma_small/{relative}"]
    assert not (tmp_path / "000" / "000002.mp3").exists()
    assert source.fetches == index_only.fetches + len(wanted)


def test_a_track_path_outside_the_zip_layout_is_rejected(tmp_path: Path):
    source = MemorySource(zip_with_members({"fma_small/000/000002.mp3": b"ID3"}))
    with pytest.raises(fma.TracksError):
        fma.extract_tracks(RemoteFile(source), ["../../fuera.mp3"], tmp_path)


def test_tracks_without_audio_or_missing_from_the_zip_are_left_out():
    tracks = list(fma.read_tracks(iter(tracks_rows())))[:3]
    sizes = {
        fma.track_path(1): fma.MIN_TRACK_BYTES,
        fma.track_path(2): fma.MIN_TRACK_BYTES - 1,
    }
    assert fma.playable_tracks(tracks, sizes) == tracks[:1]


def test_the_sizes_come_from_the_index_of_the_remote_zip(monkeypatch: pytest.MonkeyPatch):
    members = {"fma_small/README.txt": b"FMA", "fma_small/000/000002.mp3": b"ID3" * 10}
    source = MemorySource(zip_with_members(members))
    monkeypatch.setattr(fma, "HttpRangeSource", lambda url: source)
    assert fma.audio_sizes("https://datos.example/fma_small.zip") == {
        "README.txt": 3,
        "000/000002.mp3": 30,
    }
