"""Los archivos multimedia por el API: subir la consulta, miniaturas, audio con `Range` y la
galería que describe un `SIMILAR_TO`."""

from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from tests.multimedia.media_support import (
    TEXTURE_SIDE,
    textured_pixels,
    tone,
    write_audio,
    write_image,
)

from api.main import create_app
from config import EngineConfig

LARGE_SIDE = 600
SMALL_THUMBNAIL = 64
RANGE_BYTES = 99
VOCABULARY = 8
# Ruido desenfocado a escalas distintas: cada foto da otras palabras, así que la consulta se
# encuentra a sí misma primero.
PHOTO_BLURS = (1.0, 2.0, 4.0, 7.0, 11.0)


@pytest.fixture
def media(tmp_path: Path) -> Path:
    directory = tmp_path / "medios"
    directory.mkdir()
    return directory


@pytest.fixture
def media_config(config: EngineConfig, media: Path) -> EngineConfig:
    return replace(
        config,
        import_directories=(*config.import_directories, media),
        thumbnail_side=SMALL_THUMBNAIL,
        max_query_uploads=2,
        feature_workers=1,
    )


@pytest.fixture
def client(media_config: EngineConfig):
    with TestClient(create_app(media_config)) as instance:
        yield instance


def run(client: TestClient, sql: str) -> dict:
    response = client.post("/query", json={"sql": sql})
    assert response.status_code == 200, response.json()
    return response.json()


def photo(directory: Path, number: int) -> Path:
    noise = np.random.default_rng(number).integers(0, 256, (TEXTURE_SIDE, TEXTURE_SIDE))
    blurred = cv2.GaussianBlur(noise.astype(np.uint8), (0, 0), PHOTO_BLURS[number])
    return write_image(directory / f"f{number}.png", cv2.normalize(blurred, None, 0, 255, cv2.NORM_MINMAX))


def png_bytes(side: int = TEXTURE_SIDE, seed: int = 0) -> bytes:
    ok, encoded = cv2.imencode(".png", textured_pixels(side, side, seed=seed))
    assert ok
    return encoded.tobytes()


def test_an_uploaded_query_is_searched_with_similar_to(client: TestClient, media: Path):
    photos = [photo(media, number) for number in range(len(PHOTO_BLURS))]
    run(client, "CREATE TABLE fotos (id INT PRIMARY KEY, ruta VARCHAR(200))")
    run(client, "INSERT INTO fotos VALUES " + ", ".join(f"({n}, '{p}')" for n, p in enumerate(photos)))
    run(client, f"CREATE INDEX i ON fotos USING HNSW (ruta) WITH (vocabulary = {VOCABULARY})")

    uploaded = client.post(
        "/media/queries", files={"file": ("mi foto.png", photos[3].read_bytes(), "image/png")}
    ).json()
    result = run(
        client,
        f"SELECT id, SIMILARITY_SCORE() AS score FROM fotos "
        f"WHERE SIMILAR_TO('{uploaded['path']}', k = 3)",
    )

    assert uploaded["media"] == "image"
    assert uploaded["name"] == "mi foto.png"
    assert result["rows"][0][0] == 3
    assert result["similarity"] == {
        "table": "fotos",
        "column": "ruta",
        "index": "i",
        "method": "HNSW",
        "metric": "cosine",
        "k": 3,
        "query": uploaded["path"],
        "media": "image",
    }


def test_a_query_that_is_not_an_image_or_audio_is_refused(client: TestClient):
    response = client.post("/media/queries", files={"file": ("notas.txt", b"hola", "text/plain")})

    assert response.status_code == 400
    assert "no es una imagen ni un audio" in response.json()["detail"]["error"]


def test_a_query_larger_than_the_limit_is_refused(media_config: EngineConfig):
    small = replace(media_config, max_upload_bytes=1024)
    with TestClient(create_app(small)) as limited:
        response = limited.post(
            "/media/queries", files={"file": ("grande.png", png_bytes(), "image/png")}
        )

    assert response.status_code == 413


def test_only_the_newest_queries_are_kept(client: TestClient, media_config: EngineConfig):
    paths = [
        client.post("/media/queries", files={"file": ("q.png", png_bytes(seed=n), "image/png")}).json()["path"]
        for n in range(3)
    ]

    directory = media_config.data_directory / "uploads" / "consultas"
    assert len(list(directory.iterdir())) == media_config.max_query_uploads
    assert not Path(paths[0]).exists()
    assert Path(paths[-1]).exists()


def test_a_thumbnail_keeps_the_proportions_and_is_cached(
    client: TestClient, media: Path, media_config: EngineConfig
):
    pixels = textured_pixels(LARGE_SIDE // 2, LARGE_SIDE)
    path = write_image(media / "ancha.png", pixels)

    first = client.get("/media/thumbnail", params={"path": str(path)})
    second = client.get("/media/thumbnail", params={"path": str(path)})

    image = cv2.imdecode(np.frombuffer(first.content, np.uint8), cv2.IMREAD_COLOR)
    assert first.status_code == 200
    assert first.headers["content-type"] == "image/jpeg"
    assert image.shape[:2] == (SMALL_THUMBNAIL // 2, SMALL_THUMBNAIL)
    assert second.content == first.content
    assert len(list((media_config.data_directory / ".miniaturas").iterdir())) == 1


def test_audio_is_served_in_ranges(client: TestClient, media: Path):
    path = write_audio(media / "tono.wav", tone(1.0))

    whole = client.get("/media/file", params={"path": str(path)})
    part = client.get(
        "/media/file", params={"path": str(path)}, headers={"Range": f"bytes=0-{RANGE_BYTES}"}
    )

    assert whole.status_code == 200
    assert whole.headers["content-type"] == "audio/x-wav"
    assert part.status_code == 206
    assert part.content == path.read_bytes()[: RANGE_BYTES + 1]


def test_an_audio_has_no_thumbnail(client: TestClient, media: Path):
    path = write_audio(media / "tono.wav", tone(1.0))

    assert client.get("/media/thumbnail", params={"path": str(path)}).status_code == 400


@pytest.mark.parametrize("route", ["/media/file", "/media/thumbnail"])
def test_files_outside_the_allowed_directories_are_refused(
    client: TestClient, tmp_path_factory: pytest.TempPathFactory, route: str
):
    elsewhere = tmp_path_factory.mktemp("fuera")
    outside = write_image(elsewhere / "fuera.png", textured_pixels(TEXTURE_SIDE, TEXTURE_SIDE))

    for path in (str(outside), "../../../etc/passwd", "/etc/hosts"):
        assert client.get(route, params={"path": path}).status_code == 400


def test_a_file_that_is_not_media_is_refused(client: TestClient, media: Path):
    text = media / "notas.csv"
    text.write_text("a,b\n1,2\n")

    response = client.get("/media/file", params={"path": str(text)})

    assert response.status_code == 400
    assert "no es una imagen ni un audio" in response.json()["detail"]["error"]
