"""`POST /media/segments`: un audio subido, cortado en ventanas, contra una tabla de tramos;
y el audio de un tramo servido sin su fragmento."""

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from tests.multimedia.media_support import (
    SAMPLE_RATE,
    TEXTURE_SIDE,
    chirp,
    textured_pixels,
    tone,
    write_audio,
    write_image,
)

from api.main import create_app
from config import DEFAULT_PAGE_SIZE, EngineConfig

SONGS = 3
SONG_SECONDS = 4.0
FRAGMENT_SECONDS = 1.0
WINDOW = 1.0
HOP = 0.5
K = 3
VOCABULARY = 8
SEGMENTS_PATH = "/media/segments"
# Siete ventanas por k = 3 caben; por k = 4, no.
MAX_ROWS = 25


@pytest.fixture
def media(tmp_path: Path) -> Path:
    directory = tmp_path / "medios"
    directory.mkdir()
    for number in range(SONGS):
        samples = np.roll(chirp(SONG_SECONDS), number * SAMPLE_RATE // 3)
        write_audio(directory / f"cancion{number}.wav", samples + 0.1 * tone(SONG_SECONDS))
    return directory


@pytest.fixture
def media_config(config: EngineConfig, media: Path) -> EngineConfig:
    return replace(
        config,
        page_size=DEFAULT_PAGE_SIZE,
        import_directories=(*config.import_directories, media),
        feature_workers=1,
        min_segment_seconds=0.5,
        max_segment_audio_seconds=8.0,
        max_segments=10,
        max_result_rows=MAX_ROWS,
    )


@pytest.fixture
def client(media_config: EngineConfig, media: Path):
    with TestClient(create_app(media_config)) as instance:
        run(instance, "CREATE TABLE tramos (id INT PRIMARY KEY, ruta VARCHAR(250), cancion INT)")
        rows = [
            f"({number * 10 + second}, '{media / f'cancion{number}.wav'}#t={second},{second + 1}', {number})"
            for number in range(SONGS)
            for second in range(int(SONG_SECONDS))
        ]
        run(instance, f"INSERT INTO tramos VALUES {', '.join(rows)}")
        run(
            instance,
            "CREATE INDEX i ON tramos USING HNSW (ruta) "
            f"WITH (METRIC = euclidean, vocabulary = {VOCABULARY})",
        )
        yield instance


def run(client: TestClient, sql: str) -> dict:
    response = client.post("/query", json={"sql": sql})
    assert response.status_code == 200, response.json()
    return response.json()


def segments(client: TestClient, audio: Path, **form: object) -> dict:
    fields = {"table": "tramos", "k": K, "window": WINDOW, "hop": HOP, **form}
    return client.post(
        SEGMENTS_PATH,
        data={name: str(value) for name, value in fields.items()},
        files={"file": (audio.name, audio.read_bytes(), "audio/wav")},
    ).json()


def failure(client: TestClient, audio: Path, **form: object) -> tuple[int, str]:
    fields = {"table": "tramos", "k": K, "window": WINDOW, "hop": HOP, **form}
    response = client.post(
        SEGMENTS_PATH,
        data={name: str(value) for name, value in fields.items()},
        files={"file": (audio.name, audio.read_bytes(), "application/octet-stream")},
    )
    return response.status_code, response.json()["detail"]["error"]


def test_every_window_gets_what_its_fragment_gets_with_similar_to(client, media):
    song = media / "cancion1.wav"

    found = segments(client, song)

    assert found["index"] == "i"
    assert found["duration"] == pytest.approx(SONG_SECONDS)
    assert [(item["start"], item["end"]) for item in found["segments"]] == [
        (start * HOP, start * HOP + WINDOW) for start in range(7)
    ]
    for segment in found["segments"]:
        query = f"{song}#t={segment['start']:g},{segment['end']:g}"
        alone = run(
            client,
            f"SELECT id, SIMILARITY_SCORE() FROM tramos WHERE SIMILAR_TO('{query}', k = {K})",
        )["rows"]
        assert [item["row"]["id"] for item in segment["neighbours"]] == [row[0] for row in alone]
        assert [item["score"] for item in segment["neighbours"]] == pytest.approx(
            [row[1] for row in alone], rel=1e-6
        )


def test_a_neighbour_carries_its_row_its_file_and_its_fragment(client, media):
    song = media / "cancion2.wav"

    first = segments(client, song)["segments"][0]["neighbours"][0]

    assert first["row"] == {"id": 20, "ruta": f"{song}#t=0,1", "cancion": 2}
    assert first["path"] == str(song)
    assert first["fragment"] == {"start": 0.0, "end": 1.0}
    assert first["score"] == pytest.approx(1.0)


def test_the_windows_default_to_the_configuration(media_config, media):
    config = replace(media_config, segment_window_seconds=2.0, segment_hop_seconds=2.0)
    with TestClient(create_app(config)) as client:
        run(client, "CREATE TABLE tramos (id INT PRIMARY KEY, ruta VARCHAR(250))")
        run(client, f"INSERT INTO tramos VALUES (1, '{media / 'cancion0.wav'}#t=0,2')")
        run(client, f"CREATE INDEX i ON tramos USING HNSW (ruta) WITH (vocabulary = {VOCABULARY})")
        audio = media / "cancion0.wav"

        response = client.post(
            SEGMENTS_PATH,
            data={"table": "tramos"},
            files={"file": (audio.name, audio.read_bytes(), "audio/wav")},
        ).json()

    assert (response["window"], response["hop"], response["k"]) == (2.0, 2.0, 5)
    assert len(response["segments"]) == 2


def test_an_audio_shorter_than_a_window_is_one_window(client, tmp_path):
    short = write_audio(tmp_path / "corta.wav", chirp(0.75))

    found = segments(client, short, window=2.0)

    assert [(item["start"], item["end"]) for item in found["segments"]] == [(0.0, 0.75)]


@pytest.mark.parametrize(
    ("seconds", "form", "message"),
    [
        (0.25, {}, "menos que la ventana más corta"),
        (9.0, {}, "admite hasta"),
        (4.0, {"hop": 0.25}, "pasan del máximo"),
        (4.0, {"window": 0.25}, "al menos"),
        (4.0, {"k": K + 1}, "la respuesta admite"),
        (4.0, {"table": "no_existe"}, "no_existe"),
    ],
)
def test_requests_over_the_limits_are_refused(client, tmp_path, seconds, form, message):
    audio = write_audio(tmp_path / "subida.wav", chirp(seconds))

    status, error = failure(client, audio, **form)

    assert status == 400
    assert message in error


def test_an_image_is_refused(client, tmp_path):
    image = write_image(tmp_path / "foto.png", textured_pixels(TEXTURE_SIDE, TEXTURE_SIDE))

    status, error = failure(client, image)

    assert status == 400
    assert "es una imagen" in error


def test_a_table_without_a_similarity_index_is_refused(client, tmp_path):
    run(client, "CREATE TABLE vacia (id INT PRIMARY KEY, ruta VARCHAR(250))")
    audio = write_audio(tmp_path / "subida.wav", chirp(2.0))

    status, error = failure(client, audio, table="vacia")

    assert status == 400
    assert "no tiene un índice HNSW ni IVF" in error


def test_the_upload_does_not_outlive_the_request(client, media_config, media):
    segments(client, media / "cancion0.wav")

    leftovers = media_config.data_directory / "uploads" / "segmentos"
    assert not any(leftovers.iterdir())


def test_a_fragment_path_is_served_as_its_whole_file(client, media):
    song = media / "cancion0.wav"

    response = client.get("/media/file", params={"path": f"{song}#t=1,2"})

    assert response.status_code == 200
    assert response.content == song.read_bytes()


def test_a_malformed_fragment_in_a_served_path_is_refused(client, media):
    response = client.get("/media/file", params={"path": f"{media / 'cancion0.wav'}#t=uno"})

    assert response.status_code == 400
