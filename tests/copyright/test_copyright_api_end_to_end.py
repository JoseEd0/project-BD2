"""El API del servicio de copyright sobre un catálogo cargado en el gestor real."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.multimedia.media_support import chirp, write_audio

from api.main import create_app
from appkit import load_settings
from config import DEFAULT_PAGE_SIZE, EngineConfig
from minigestor_client import AsyncClient
from songcheck.api import build_app
from songcheck.catalog import CatalogSong
from songcheck.seed import load_catalog
from songcheck.settings import ENV_PREFIX, CopyrightSettings

ASGI_URL = "http://gestor"
VOCABULARY = 8
SONGS = (
    (2, "Dancing in the Rain", "Ava Zandieh", "Folk", 10.0),
    (5, "Night Drive", "The NSJ Crew", "Hip-Hop", 6.0),
    (140, "Rain Dance", "Kosta T", "Folk", 6.0),
)


@pytest.fixture
def engine_app(config: EngineConfig, tmp_path: Path) -> Iterator[FastAPI]:
    app = create_app(
        replace(
            config,
            page_size=DEFAULT_PAGE_SIZE,
            import_directories=(tmp_path,),
            feature_workers=1,
            vocabulary_size=VOCABULARY,
        )
    )
    yield app
    app.state.service.close()


async def seed(app: FastAPI, songs: list[CatalogSong], root: str) -> None:
    async with AsyncClient(ASGI_URL, transport=httpx.ASGITransport(app=app)) as engine:
        await load_catalog(engine, engine, songs, root)


@pytest.fixture
def service(
    engine_app: FastAPI, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    songs = [
        CatalogSong(track, title, artist, genre, seconds, f"{track}.wav", 0, 0)
        for track, title, artist, genre, seconds in SONGS
    ]
    for song in songs:
        write_audio(tmp_path / song.path, chirp(song.duration_seconds))
    asyncio.run(seed(engine_app, songs, str(tmp_path)))
    monkeypatch.setenv(f"{ENV_PREFIX}ENGINE_URL", ASGI_URL)
    monkeypatch.setenv(f"{ENV_PREFIX}ENGINE_KEY", "sin-claves-en-el-gestor-de-prueba")
    settings = load_settings(CopyrightSettings)
    app = build_app(settings, httpx.ASGITransport(app=engine_app))
    with TestClient(app) as client:
        yield client


def test_the_catalog_summary_counts_songs_segments_and_genres(service: TestClient):
    summary = service.get("/catalog").json()
    assert summary == {
        "songs": 3,
        "segments": 3 + 1 + 1,
        "window_seconds": 5.0,
        "hop_seconds": 2.5,
        "segments_indexed": True,
        "genres": [{"genre": "Folk", "songs": 2}, {"genre": "Hip-Hop", "songs": 1}],
    }


def test_songs_are_listed_by_id_and_filtered_by_genre(service: TestClient):
    listed = service.get("/songs", params={"limit": 2}).json()["songs"]
    assert [song["id"] for song in listed] == [2, 5]
    assert listed[0]["score"] is None
    folk = service.get("/songs", params={"genre": "Folk", "offset": 1}).json()["songs"]
    assert [song["id"] for song in folk] == [140]


def test_songs_are_searched_by_title_and_artist(service: TestClient):
    stemmed = service.get("/songs", params={"query": "dance"}).json()["songs"]
    assert sorted(song["id"] for song in stemmed) == [2, 140]
    assert all(song["score"] > 0 for song in stemmed)
    by_artist = service.get("/songs", params={"query": "nsj"}).json()["songs"]
    assert [song["title"] for song in by_artist] == ["Night Drive"]


def test_a_song_comes_with_its_indexed_segments(service: TestClient):
    song = service.get("/songs/2").json()
    assert (song["title"], song["duration_seconds"]) == ("Dancing in the Rain", 10.0)
    assert song["segments"] == [
        {"start_seconds": 0.0, "end_seconds": 5.0},
        {"start_seconds": 2.5, "end_seconds": 7.5},
        {"start_seconds": 5.0, "end_seconds": 10.0},
    ]


def test_the_original_audio_is_streamed_whole_or_by_range(service: TestClient, tmp_path: Path):
    original = (tmp_path / "2.wav").read_bytes()
    whole = service.get("/songs/2/audio")
    assert (whole.status_code, whole.content) == (200, original)
    assert whole.headers["accept-ranges"] == "bytes"
    assert whole.headers["content-type"].startswith("audio/")
    tail = service.get("/songs/2/audio", headers={"Range": "bytes=100-"})
    assert (tail.status_code, tail.content) == (206, original[100:])
    assert tail.headers["content-range"] == f"bytes 100-{len(original) - 1}/{len(original)}"
    missing = service.get("/songs/404/audio")
    assert (missing.status_code, missing.json()["kind"]) == (404, "ResourceNotFoundError")


def test_an_unknown_song_is_a_404_and_a_bad_page_a_422(service: TestClient):
    missing = service.get("/songs/404")
    assert (missing.status_code, missing.json()["kind"]) == (404, "ResourceNotFoundError")
    assert service.get("/songs", params={"limit": 1000}).status_code == 422


def test_the_service_reports_health_and_readiness(service: TestClient):
    assert service.get("/health").json()["status"] == "ok"
    assert service.get("/ready").json() == {"status": "ready"}
