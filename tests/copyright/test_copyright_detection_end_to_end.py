"""`POST /detections` contra el gestor real, en el mismo proceso, con el catálogo de muestra.

El catálogo son los barridos de `songcheck.sample` (cada pista suena distinta en cada
instante), cargados con el trabajo de carga; las subidas son tramos de esas mismas pistas
recortados y vueltos a escribir en otros formatos, frecuencias y canales.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import httpx
import numpy as np
import pytest
import soundfile
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.main import create_app
from appkit import load_settings
from config import DEFAULT_PAGE_SIZE, EngineConfig
from minigestor_client import AsyncClient
from songcheck.api import build_app
from songcheck.catalog import dataset_directory, read_catalog
from songcheck.detection_api import DETECTIONS_PATH
from songcheck.sample import write_sample
from songcheck.seed import load_catalog
from songcheck.settings import ENV_PREFIX, CopyrightSettings

ASGI_URL = "http://gestor"
VOCABULARY = 64
SAMPLE_SONGS = 4
SAMPLE_SECONDS = 30.0
COPIED_SONG = 2
EXCERPT_START = 5.0
EXCERPT_END = 25.0
CD_RATE = 44_100
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
SILENCE_SECONDS = 12.0
NOISE_AMPLITUDE = 0.3
NOISE_SEED = 76
EXPECTED_STAGES = (
    "probe",
    "loudness",
    "segments",
    "decode",
    "extraction",
    "search",
    "voting",
    "songs",
)


@pytest.fixture
def media(tmp_path: Path) -> Path:
    directory = tmp_path / "datasets"
    directory.mkdir()
    return directory


@pytest.fixture
def engine_app(config: EngineConfig, media: Path) -> Iterator[FastAPI]:
    app = create_app(
        replace(
            config,
            page_size=DEFAULT_PAGE_SIZE,
            import_directories=(media,),
            feature_workers=1,
            vocabulary_size=VOCABULARY,
        )
    )
    yield app
    app.state.service.close()


@pytest.fixture
def tracks(engine_app: FastAPI, media: Path) -> dict[int, Path]:
    songs = read_catalog(write_sample(media, SAMPLE_SONGS, SAMPLE_SECONDS))
    root = dataset_directory(media)
    asyncio.run(seed(engine_app, root))
    return {song.track_id: root / song.path for song in songs}


async def seed(app: FastAPI, root: Path) -> None:
    songs = read_catalog(root / "catalog.csv")
    async with AsyncClient(ASGI_URL, transport=httpx.ASGITransport(app=app)) as engine:
        await load_catalog(engine, engine, songs, str(root))


def service_for(
    engine_app: FastAPI, monkeypatch: pytest.MonkeyPatch, **variables: str
) -> Iterator[TestClient]:
    monkeypatch.setenv(f"{ENV_PREFIX}ENGINE_URL", ASGI_URL)
    monkeypatch.setenv(f"{ENV_PREFIX}ENGINE_KEY", "sin-claves-en-el-gestor-de-prueba")
    for name, value in variables.items():
        monkeypatch.setenv(f"{ENV_PREFIX}{name}", value)
    app = build_app(load_settings(CopyrightSettings), httpx.ASGITransport(app=engine_app))
    with TestClient(app) as client:
        yield client


@pytest.fixture
def service(
    engine_app: FastAPI, tracks: dict[int, Path], monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    yield from service_for(engine_app, monkeypatch)


def excerpt(path: Path, start: float, end: float) -> tuple[np.ndarray, int]:
    samples, rate = soundfile.read(path, dtype="float32")
    return samples[round(start * rate) : round(end * rate)], rate


def encoded(samples: np.ndarray, rate: int, audio_format: str) -> bytes:
    buffer = io.BytesIO()
    soundfile.write(buffer, samples, rate, format=audio_format)
    return buffer.getvalue()


def resampled_stereo(samples: np.ndarray, rate: int, target: int) -> np.ndarray:
    times = np.arange(round(len(samples) * target / rate)) / target
    mono = np.interp(times, np.arange(len(samples)) / rate, samples).astype(np.float32)
    return np.stack([mono, 0.5 * mono], axis=1)


def detect(service: TestClient, content: bytes, name: str = "subida.wav") -> httpx.Response:
    return service.post(DETECTIONS_PATH, files={"file": (name, content)})


def copied_excerpt(tracks: dict[int, Path], audio_format: str = "WAV") -> bytes:
    samples, rate = excerpt(tracks[COPIED_SONG], EXCERPT_START, EXCERPT_END)
    return encoded(samples, rate, audio_format)


def test_a_copied_excerpt_is_detected_with_its_offset_and_spans(
    service: TestClient, tracks: dict[int, Path]
):
    response = detect(service, copied_excerpt(tracks))
    assert response.status_code == 200, response.text
    detection = response.json()
    assert detection["detected"]
    assert (detection["windows"], detection["silent_windows"]) == (7, 0)
    best = detection["matches"][0]
    assert best["song"] == {"id": COPIED_SONG, "title": "Melodía 2", "artist": "Muestra"}
    assert best["offset_seconds"] == -EXCERPT_START
    assert best["upload"] == {"start_seconds": 0.0, "end_seconds": 20.0}
    assert best["original"] == {"start_seconds": 5.0, "end_seconds": 25.0}
    assert best["windows"] == 7
    assert best["coverage"] == 1.0
    assert best["above_threshold"]
    assert best["score"] == pytest.approx(best["mean_similarity"])


def test_the_engine_resamples_and_mixes_down_a_stereo_upload(
    service: TestClient, tracks: dict[int, Path]
):
    samples, rate = excerpt(tracks[COPIED_SONG], EXCERPT_START, EXCERPT_END)
    content = encoded(resampled_stereo(samples, rate, CD_RATE), CD_RATE, "WAV")
    detection = detect(service, content).json()
    assert (detection["sample_rate"], detection["channels"]) == (CD_RATE, 2)
    best = detection["matches"][0]
    assert (best["song"]["id"], best["offset_seconds"], best["above_threshold"]) == (
        COPIED_SONG,
        -EXCERPT_START,
        True,
    )


@pytest.mark.parametrize("audio_format", ["FLAC", "OGG", "MP3"])
def test_each_format_is_recognised_by_its_content_not_its_name(
    service: TestClient, tracks: dict[int, Path], audio_format: str
):
    detection = detect(service, copied_excerpt(tracks, audio_format), name="cancion.wav").json()
    assert detection["format"] == audio_format
    assert detection["matches"][0]["song"]["id"] == COPIED_SONG


def test_two_spliced_songs_are_two_matches(service: TestClient, tracks: dict[int, Path]):
    first, rate = excerpt(tracks[1], 0.0, 15.0)
    second, _ = excerpt(tracks[3], 10.0, 25.0)
    detection = detect(service, encoded(np.concatenate([first, second]), rate, "WAV")).json()
    found = {
        (match["song"]["id"], match["offset_seconds"])
        for match in detection["matches"]
        if match["above_threshold"]
    }
    assert found == {(1, 0.0), (3, 5.0)}


def test_an_upload_shorter_than_a_window_is_one_window(
    service: TestClient, tracks: dict[int, Path]
):
    samples, rate = excerpt(tracks[COPIED_SONG], 10.0, 13.0)
    detection = detect(service, encoded(samples, rate, "WAV")).json()
    assert detection["windows"] == 1
    assert detection["matches"][0]["song"]["id"] == COPIED_SONG


def test_silence_has_no_matches_and_does_not_reach_the_engine(service: TestClient):
    silence = np.zeros(round(SILENCE_SECONDS * CD_RATE), dtype=np.float32)
    response = detect(service, encoded(silence, CD_RATE, "WAV"))
    assert response.status_code == 200
    detection = response.json()
    assert (detection["detected"], detection["matches"]) == (False, [])
    assert detection["windows"] == detection["silent_windows"] == 3
    assert detection["engine_timings"] is None


def test_unrelated_noise_is_not_detected(service: TestClient):
    noise = NOISE_AMPLITUDE * np.random.default_rng(NOISE_SEED).standard_normal(
        round(SAMPLE_SECONDS * CD_RATE)
    )
    detection = detect(service, encoded(noise.astype(np.float32), CD_RATE, "WAV")).json()
    assert not detection["detected"]


def test_a_file_that_is_not_audio_is_a_415_whatever_its_name(service: TestClient):
    response = detect(service, PNG_SIGNATURE + bytes(2048), name="cancion.mp3")
    assert (response.status_code, response.json()["kind"]) == (415, "UnsupportedAudioError")


def test_a_corrupt_audio_is_a_400(service: TestClient, tracks: dict[int, Path]):
    damaged = b"RIFF\x00\x00\x00\x00WAVEjunk" + bytes(4096)
    response = detect(service, damaged)
    assert (response.status_code, response.json()["kind"]) == (400, "CorruptAudioError")
    assert detect(service, b"").json()["kind"] == "CorruptAudioError"


def test_audio_shorter_than_a_second_is_a_400(service: TestClient, tracks: dict[int, Path]):
    samples, rate = excerpt(tracks[COPIED_SONG], 0.0, 0.5)
    response = detect(service, encoded(samples, rate, "WAV"))
    assert (response.status_code, response.json()["kind"]) == (400, "AudioTooShortError")


def test_too_heavy_is_a_413(
    engine_app: FastAPI, tracks: dict[int, Path], monkeypatch: pytest.MonkeyPatch
):
    content = copied_excerpt(tracks)
    for service in service_for(engine_app, monkeypatch, MAX_UPLOAD_BYTES=str(len(content) - 1)):
        response = detect(service, content)
    assert (response.status_code, response.json()["kind"]) == (413, "UploadTooLargeError")


def test_too_long_is_a_413(
    engine_app: FastAPI, tracks: dict[int, Path], monkeypatch: pytest.MonkeyPatch
):
    for service in service_for(
        engine_app, monkeypatch, MAX_UPLOAD_SECONDS=str(EXCERPT_END - EXCERPT_START - 1)
    ):
        response = detect(service, copied_excerpt(tracks))
    assert (response.status_code, response.json()["kind"]) == (413, "AudioTooLongError")


def test_every_stage_is_in_the_server_timing(service: TestClient, tracks: dict[int, Path]):
    response = detect(service, copied_excerpt(tracks))
    stages = [part.split(";")[0] for part in response.headers["Server-Timing"].split(", ")]
    assert [stage for stage in stages if stage in EXPECTED_STAGES] == list(EXPECTED_STAGES)
    timings = response.json()["engine_timings"]
    assert set(timings) == {"decode_ms", "extraction_ms", "search_ms"}
