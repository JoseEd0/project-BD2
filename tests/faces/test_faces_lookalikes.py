"""«¿A qué famoso te pareces?» de punta a punta: la carga de un LFW de juguete hecho con las
fotos de `apps/faces/tests/fixtures/`, y el API con los modelos reales contra el gestor real."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from apps.faces.tests.faces_support import (
    OTHER_PORTRAIT,
    SINGLE_PORTRAIT,
    crew_member,
    jpeg,
    portrait_image,
    solid,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from tests.faces.conftest import ADMIN_KEY, ENGINE_URL, WRITE_KEY, ServiceFactory

from faces.api.identification import NO_FACE_MESSAGE
from faces.api.lookalikes import FOUND_MESSAGE, NOT_LOADED_MESSAGE
from faces.api.settings import FacesSeedSettings
from faces.famous import lfw_images_directory
from faces.seed import seed

Rows = Callable[[str], list[dict[str, Any]]]
COLLINS = "Eileen Collins"
NO_FACE_GRAY = 128
NO_FACE_SIDE = 250


def lfw_photos() -> dict[str, list[Image.Image]]:
    return {
        "Eileen_Collins": [portrait_image(SINGLE_PORTRAIT), portrait_image(OTHER_PORTRAIT)],
        "Crew_Other": [crew_member("other")],
        "Crew_Another": [crew_member("another")],
        "No_Face": [solid(NO_FACE_SIDE, NO_FACE_SIDE, NO_FACE_GRAY)],
    }


@pytest.fixture
def datasets(tmp_path: Path) -> Path:
    root = tmp_path / "datasets"
    for person, images in lfw_photos().items():
        folder = lfw_images_directory(root) / person
        folder.mkdir(parents=True)
        for number, picture in enumerate(images, start=1):
            (folder / f"{person}_{number:04d}.jpg").write_bytes(jpeg(picture))
    return root


def run_seed(engine_app: FastAPI, models_dir: Path, datasets: Path) -> int:
    settings = FacesSeedSettings(
        engine_url=ENGINE_URL,
        engine_key=WRITE_KEY,
        migration_key=ADMIN_KEY,
        models_dir=models_dir,
        data_dir=datasets,
        seed_workers=1,
    )
    return asyncio.run(seed(settings, httpx.ASGITransport(app=engine_app)))


def lookalikes(service: TestClient, photo: bytes) -> dict[str, Any]:
    response = service.post("/lookalikes", files={"image": ("yo.jpg", photo, "image/jpeg")})
    assert response.status_code == 200, response.text
    return dict(response.json())


def test_the_seed_loads_one_row_per_person_with_a_face_once(
    engine_app: FastAPI, models_dir: Path, datasets: Path, engine_rows: Rows
):
    assert run_seed(engine_app, models_dir, datasets) == 3
    assert run_seed(engine_app, models_dir, datasets) == 0

    rows = engine_rows("SELECT nombre, fotos FROM face_famosos")
    assert sorted((row["nombre"], row["fotos"]) for row in rows) == [
        ("Crew Another", 1),
        ("Crew Other", 1),
        (COLLINS, 2),
    ]


def test_a_photo_finds_the_closest_public_figures_and_their_photos(
    engine_app: FastAPI, models_dir: Path, datasets: Path, start_service: ServiceFactory
):
    run_seed(engine_app, models_dir, datasets)

    with start_service(data_dir=datasets, lookalike_count=2) as service:
        found = lookalikes(service, jpeg(crew_member("collins")))
        first = found["people"][0]
        photo = service.get(first["photo_path"])
        unknown = service.get("/lookalikes/999/photo")

    assert found["message"] == FOUND_MESSAGE
    assert len(found["people"]) == 2
    assert len({person["id"] for person in found["people"]}) == 2
    assert (first["name"], first["photos"]) == (COLLINS, 2)
    assert found["people"][0]["similarity"] >= found["people"][1]["similarity"]
    assert 0 <= first["percent"] <= 100
    assert (photo.status_code, photo.headers["content-type"]) == (200, "image/jpeg")
    assert unknown.status_code == 404


def test_without_lfw_loaded_it_says_how_to_load_it(start_service: ServiceFactory):
    with start_service() as service:
        found = lookalikes(service, jpeg(crew_member("collins")))
        photo = service.get("/lookalikes/0/photo")

    assert found == {"people": [], "message": NOT_LOADED_MESSAGE}
    assert photo.status_code == 404


def test_a_photo_without_a_face_finds_nobody(service: TestClient):
    found = lookalikes(service, jpeg(solid(NO_FACE_SIDE, NO_FACE_SIDE, NO_FACE_GRAY)))

    assert found == {"people": [], "message": NO_FACE_MESSAGE}
