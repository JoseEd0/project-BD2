"""La consulta multimedia por el cliente: subir el archivo y buscarlo con `SIMILAR_TO`, los
segmentos de un audio y las descargas, con imágenes y audios generados en el test."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi import FastAPI
from tests.client.engine_server import serving
from tests.multimedia.media_support import (
    SAMPLE_RATE,
    TEXTURE_SIDE,
    chirp,
    tone,
    write_audio,
    write_image,
)

from api.main import create_app
from config import DEFAULT_PAGE_SIZE, EngineConfig
from minigestor_client import Client, DomainError, MediaFile

VOCABULARY = 8
# Ruido desenfocado a escalas distintas: cada foto da otras palabras visuales.
PHOTO_BLURS = (1.0, 2.0, 4.0, 7.0, 11.0)
QUERY_PHOTO = 3
K = 3
SONGS = 2
SONG_SECONDS = 3.0
WINDOW = 1.0
HOP = 1.0
JPEG_START = b"\xff\xd8"
STREAM_CHUNK = 1_000


def photo(directory: Path, number: int) -> Path:
    noise = np.random.default_rng(number).integers(0, 256, (TEXTURE_SIDE, TEXTURE_SIDE))
    blurred = cv2.GaussianBlur(noise.astype(np.uint8), (0, 0), PHOTO_BLURS[number])
    pixels = cv2.normalize(blurred, None, 0, 255, cv2.NORM_MINMAX)
    return write_image(directory / f"f{number}.png", pixels)


def song(directory: Path, number: int) -> Path:
    samples = np.roll(chirp(SONG_SECONDS), number * SAMPLE_RATE // 3)
    return write_audio(directory / f"cancion{number}.wav", samples + 0.1 * tone(SONG_SECONDS))


@pytest.fixture
def media(tmp_path: Path) -> Path:
    directory = tmp_path / "medios"
    directory.mkdir()
    return directory


@pytest.fixture
def media_client(engine_config: EngineConfig, media: Path) -> Iterator[Client]:
    config = replace(
        engine_config,
        page_size=DEFAULT_PAGE_SIZE,
        import_directories=(*engine_config.import_directories, media),
        feature_workers=1,
        min_segment_seconds=0.5,
    )
    app: FastAPI = create_app(config)
    with serving(app) as url, Client(url) as client:
        yield client


@pytest.fixture
def photos(media_client: Client, media: Path) -> list[Path]:
    paths = [photo(media, number) for number in range(len(PHOTO_BLURS))]
    media_client.execute("CREATE TABLE fotos (id INT PRIMARY KEY, ruta VARCHAR(200))")
    for number, path in enumerate(paths):
        media_client.execute("INSERT INTO fotos VALUES ($1, $2)", [number, str(path)])
    media_client.execute(
        f"CREATE INDEX ON fotos USING HNSW (ruta) WITH (vocabulary = {VOCABULARY})"
    )
    return paths


def test_an_uploaded_photo_is_searched_with_similar_to(media_client: Client, photos: list[Path]):
    result = media_client.similar_media(
        photos[QUERY_PHOTO],
        "SELECT id, SIMILARITY_SCORE() FROM fotos WHERE SIMILAR_TO($1, k = $2)",
        [K],
    )
    assert len(result.rows) == K
    assert result.rows[0][0] == QUERY_PHOTO
    assert result.similarity is not None
    assert (result.similarity.table, result.similarity.k, result.similarity.media) == (
        "fotos",
        K,
        "image",
    )


def test_a_file_in_memory_is_uploaded_with_its_name(media_client: Client, photos: list[Path]):
    uploaded = media_client.upload_media(
        MediaFile("mi foto.png", photos[0].read_bytes(), "image/png")
    )
    assert (uploaded.name, uploaded.media) == ("mi foto.png", "image")
    assert media_client.download(uploaded.path) == photos[0].read_bytes()


def test_a_file_is_downloaded_whole_or_streamed(media_client: Client, photos: list[Path]):
    original = photos[1].read_bytes()
    assert media_client.download(str(photos[1])) == original
    with media_client.stream_file(str(photos[1]), STREAM_CHUNK) as chunks:
        received = list(chunks)
    assert b"".join(received) == original
    assert len(received) > 1


def test_a_thumbnail_is_a_jpeg(media_client: Client, photos: list[Path]):
    assert media_client.thumbnail(str(photos[0])).startswith(JPEG_START)


def test_a_file_outside_the_allowed_directories_is_refused(
    media_client: Client, tmp_path_factory: pytest.TempPathFactory
):
    elsewhere = tmp_path_factory.mktemp("fuera")
    outside = write_image(elsewhere / "fuera.png", np.zeros((8, 8), dtype=np.uint8))
    with pytest.raises(DomainError):
        media_client.download(str(outside))


def test_each_window_of_an_audio_gets_its_neighbours(media_client: Client, media: Path):
    songs = [song(media, number) for number in range(SONGS)]
    media_client.execute("CREATE TABLE tramos (id INT PRIMARY KEY, ruta VARCHAR(250))")
    for number, path in enumerate(songs):
        for second in range(int(SONG_SECONDS)):
            fragment = f"{path}#t={second},{second + 1}"
            media_client.execute(
                "INSERT INTO tramos VALUES ($1, $2)", [number * 10 + second, fragment]
            )
    media_client.execute(
        f"CREATE INDEX i ON tramos USING HNSW (ruta) WITH (vocabulary = {VOCABULARY})"
    )

    found = media_client.segments(songs[1], "tramos", k=K, window=WINDOW, hop=HOP)

    assert (found.index, found.k, found.window, found.hop) == ("i", K, WINDOW, HOP)
    assert [(segment.start, segment.end) for segment in found.segments] == [
        (0.0, 1.0),
        (1.0, 2.0),
        (2.0, 3.0),
    ]
    first = found.segments[0].neighbours[0]
    assert first.row == {"id": 10, "ruta": f"{songs[1]}#t=0,1"}
    assert first.path == str(songs[1])
    assert first.fragment is not None
    assert (first.fragment.start, first.fragment.end) == (0.0, 1.0)
    assert found.timings.decode_ms >= 0
    assert "total" in found.server_timing
