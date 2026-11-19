"""El API de la tienda contra el gestor real con el catálogo diminuto ya cargado: ficha,
búsqueda de texto, fotos por texto con CLIP y tiendas cercanas."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.shop.conftest import ENGINE_KEY, ENGINE_URL, FakeImageEncoder

from shop.api import create_app
from shop.catalog.rows import DatasetPaths, product_images
from shop.clip import load_image, pixel_values
from shop.clip.encoder import Embeddings
from shop.settings import SeedSettings, ShopSettings

MAX_RESULTS = 3
QUERY_PRODUCT = "B000STRING"


class PhotoTextEncoder:
    """Una torre de texto de mentira: cualquier texto «describe» la foto de un producto, así
    que ese producto es el primero de la búsqueda de fotos por texto."""

    def __init__(self, vector: Embeddings) -> None:
        self.vector = vector
        self.texts: list[str] = []

    def embed(self, texts: list[str]) -> Embeddings:
        self.texts.extend(texts)
        return np.stack([self.vector for _ in texts])


@pytest.fixture
def text_encoder(seed_settings: SeedSettings) -> PhotoTextEncoder:
    photos = dict(product_images(DatasetPaths(seed_settings.dataset, Path("/"))))
    pixels = pixel_values(load_image(photos[QUERY_PRODUCT]))[np.newaxis]
    return PhotoTextEncoder(FakeImageEncoder().embed_pixels(pixels)[0])


@pytest.fixture
def shop(
    seeded: None, engine_app: FastAPI, tmp_path: Path, text_encoder: PhotoTextEncoder
) -> Iterator[TestClient]:
    settings = ShopSettings(
        engine_url=ENGINE_URL,  # type: ignore[arg-type]
        engine_key=ENGINE_KEY,  # type: ignore[arg-type]
        models_dir=tmp_path / "modelos",
        max_search_limit=MAX_RESULTS,
        ready_cache_seconds=0,
    )
    app = create_app(
        settings,
        text_encoder=text_encoder,
        engine_transport=httpx.ASGITransport(app=engine_app),
    )
    with TestClient(app) as client:
        yield client


def test_the_service_is_ready(shop: TestClient):
    assert shop.get("/ready").json() == {"status": "ready"}


def test_a_product_by_id(shop: TestClient):
    guitar = shop.get("/productos/B000GUITAR").json()
    assert guitar["titulo"] == "Electric Guitar with Maple Neck"
    assert guitar["precio"] == pytest.approx(699.99)
    assert guitar["descripcion"].startswith("Maple neck")
    drums = shop.get("/productos/B0000DRUMS").json()
    assert drums["precio"] is None


def test_a_missing_product_is_404(shop: TestClient):
    missing = shop.get("/productos/NOPE")
    assert (missing.status_code, missing.json()["kind"]) == (404, "ResourceNotFoundError")


def test_text_search_ranks_by_bm25(shop: TestClient):
    found = shop.get("/busqueda/texto", params={"q": "electric guitars"}).json()
    ids = [item["producto"]["id"] for item in found]
    assert set(ids[:2]) == {"B000GUITAR", "B000COPYGT"}
    assert "B000STRING" in ids
    scores = [item["puntaje"] for item in found]
    assert scores == sorted(scores, reverse=True)


def test_search_text_is_a_parameter_not_sql(shop: TestClient):
    found = shop.get("/busqueda/texto", params={"q": "guitar'; drop table shop_productos; --"})
    assert found.status_code == 200, found.json()
    assert shop.get("/productos/B000GUITAR").status_code == 200


def test_a_malformed_boolean_query_is_the_callers_error(shop: TestClient):
    malformed = shop.get("/busqueda/texto", params={"q": "(guitar AND"})
    assert (malformed.status_code, malformed.json()["kind"]) == (400, "BooleanQueryError")


def test_photos_are_found_with_words(shop: TestClient, text_encoder: PhotoTextEncoder):
    found = shop.get("/busqueda/imagenes", params={"q": "guitar strings", "limite": 2}).json()
    assert text_encoder.texts == ["guitar strings"]
    assert len(found) == 2
    assert found[0]["producto"]["id"] == QUERY_PRODUCT
    assert found[0]["puntaje"] == pytest.approx(1.0, abs=1e-4)


def test_nearest_stores_come_with_their_distance(shop: TestClient):
    stores = shop.get("/tiendas/cercanas", params={"lat": -12.05, "lon": -77.04}).json()
    assert [store["id"] for store in stores] == [101, 102, 103]
    assert stores[0]["metros"] < stores[1]["metros"] < stores[2]["metros"]
    assert stores[0]["ubicacion"] == {"lat": -12.0464, "lon": -77.0428}


def test_limits_are_validated(shop: TestClient):
    too_many = shop.get("/busqueda/texto", params={"q": "guitar", "limite": MAX_RESULTS + 1})
    assert (too_many.status_code, too_many.json()["kind"]) == (400, "BadRequestError")
    assert shop.get("/busqueda/texto", params={"q": ""}).status_code == 422
    assert shop.get("/tiendas/cercanas", params={"lat": 95, "lon": 0}).status_code == 422
    one = shop.get("/tiendas/cercanas", params={"lat": -12.05, "lon": -77.04, "limite": 1})
    assert len(one.json()) == 1
