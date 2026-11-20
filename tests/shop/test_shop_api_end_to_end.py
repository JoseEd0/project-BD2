"""El API de la tienda contra el gestor real con el catálogo diminuto ya cargado: ficha y su
foto, búsqueda de texto, fotos por texto y por foto con CLIP, filtros de departamento y
precio, tiendas cercanas y con stock, departamentos, usuarios y sus compras."""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from tests.shop.conftest import ENGINE_KEY, ENGINE_URL, FakeImageEncoder

from shop.api import create_app
from shop.api.photos import _photo_path
from shop.catalog.rows import DatasetPaths, product_images
from shop.clip import load_image, pixel_values
from shop.clip.encoder import Embeddings
from shop.settings import SeedSettings, ShopSettings

MAX_RESULTS = 3
QUERY_PRODUCT = "B000STRING"
CENTRO = {"lat": -12.05, "lon": -77.04}
PHOTO_MAX_BYTES = 64 * 1024
PHOTO_MAX_PIXELS = 400 * 400


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
def text_encoder(photos: dict[str, Path]) -> PhotoTextEncoder:
    pixels = pixel_values(load_image(photos[QUERY_PRODUCT]))[np.newaxis]
    return PhotoTextEncoder(FakeImageEncoder().embed_pixels(pixels)[0])


@pytest.fixture
def photos(seed_settings: SeedSettings) -> dict[str, Path]:
    return dict(product_images(DatasetPaths(seed_settings.dataset, Path("/"))))


@pytest.fixture
def shop(
    seeded: None,
    engine_app: FastAPI,
    tmp_path: Path,
    datasets: Path,
    text_encoder: PhotoTextEncoder,
) -> Iterator[TestClient]:
    settings = ShopSettings(
        engine_url=ENGINE_URL,  # type: ignore[arg-type]
        engine_key=ENGINE_KEY,  # type: ignore[arg-type]
        models_dir=tmp_path / "modelos",
        max_search_limit=MAX_RESULTS,
        ready_cache_seconds=0,
        data_dir=datasets,
        photo_max_bytes=PHOTO_MAX_BYTES,
        photo_max_pixels=PHOTO_MAX_PIXELS,
        department_min_products=1,
    )
    app = create_app(
        settings,
        text_encoder=text_encoder,
        image_encoder=FakeImageEncoder(),
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


def test_a_misspelled_search_finds_the_corrected_words(shop: TestClient):
    found = shop.get("/busqueda/productos", params={"q": "electrc guitr"}).json()
    assert found["consulta"] == "electrc guitr"
    assert found["corregida"] == "electric guitar"
    assert {"B000GUITAR", "B000COPYGT"} <= {item["producto"]["id"] for item in found["resultados"]}


def test_an_exact_search_is_not_corrected(shop: TestClient):
    found = shop.get("/busqueda/productos", params={"q": "guitr", "exacta": True}).json()
    assert found == {"consulta": "guitr", "corregida": None, "resultados": []}


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


def found_ids(response: httpx.Response) -> list[str]:
    assert response.status_code == 200, response.json()
    return [item["producto"]["id"] for item in response.json()]


def jpeg_of(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, "JPEG")
    return output.getvalue()


def test_text_search_filters_by_department_and_price(shop: TestClient):
    guitars = {"q": "guitar", "departamento": "Guitars"}
    assert set(found_ids(shop.get("/busqueda/texto", params=guitars))) == {
        "B000GUITAR",
        "B000COPYGT",
    }
    cheap = {**guitars, "precio_max": 300}
    assert found_ids(shop.get("/busqueda/texto", params=cheap)) == ["B000COPYGT"]
    priced = {"q": "drum sticks guitar", "precio_min": 0}
    assert "B0000DRUMS" not in found_ids(shop.get("/busqueda/texto", params=priced))


def test_a_price_range_upside_down_is_the_callers_error(shop: TestClient):
    upside_down = shop.get(
        "/busqueda/texto", params={"q": "guitar", "precio_min": 100, "precio_max": 10}
    )
    assert (upside_down.status_code, upside_down.json()["kind"]) == (
        400,
        "InvalidPriceRangeError",
    )


def test_the_filter_text_is_a_parameter_not_sql(shop: TestClient):
    injected = {"q": "guitar", "departamento": "Guitars' OR '1' = '1"}
    assert found_ids(shop.get("/busqueda/texto", params=injected)) == []


def test_photos_found_with_words_honour_the_filter(shop: TestClient):
    strings_only = {"q": "guitar strings", "departamento": "Guitars"}
    found = found_ids(shop.get("/busqueda/imagenes", params=strings_only))
    assert QUERY_PRODUCT not in found
    assert set(found) <= {"B000GUITAR", "B000COPYGT"}


def test_an_uploaded_photo_finds_the_product_in_it(shop: TestClient, photos: dict[str, Path]):
    upload = {"foto": ("cuerdas.jpg", photos[QUERY_PRODUCT].read_bytes(), "image/jpeg")}
    found = shop.post("/busqueda/foto", files=upload, params={"limite": 2})
    assert found_ids(found)[0] == QUERY_PRODUCT
    assert found.json()[0]["puntaje"] == pytest.approx(1.0, abs=1e-4)
    assert "clip" in found.headers["Server-Timing"]


def test_an_uploaded_photo_in_another_format_is_read_too(shop: TestClient, photos: dict[str, Path]):
    png = io.BytesIO()
    with Image.open(photos[QUERY_PRODUCT]) as image:
        image.save(png, "PNG")
    upload = {"foto": ("cuerdas.png", png.getvalue(), "image/png")}
    assert found_ids(shop.post("/busqueda/foto", files=upload))[0] == QUERY_PRODUCT


def test_an_upload_that_is_not_a_photo_is_rejected(shop: TestClient):
    for content in (b"", b"no es una imagen"):
        rejected = shop.post("/busqueda/foto", files={"foto": ("x.jpg", content, "image/jpeg")})
        assert (rejected.status_code, rejected.json()["kind"]) == (400, "UnreadablePhotoError")


def test_a_photo_too_heavy_or_too_large_is_rejected(shop: TestClient):
    heavy = shop.post(
        "/busqueda/foto", files={"foto": ("x.jpg", b"x" * (PHOTO_MAX_BYTES + 1), "image/jpeg")}
    )
    assert (heavy.status_code, heavy.json()["kind"]) == (413, "UploadTooLargeError")
    wide = jpeg_of(Image.new("RGB", (PHOTO_MAX_PIXELS // 100 + 1, 100), "white"))
    large = shop.post("/busqueda/foto", files={"foto": ("x.jpg", wide, "image/jpeg")})
    assert (large.status_code, large.json()["kind"]) == (413, "TooManyPixelsError")


def test_a_product_photo_is_served_from_the_dataset(shop: TestClient, photos: dict[str, Path]):
    served = shop.get(f"/productos/{QUERY_PRODUCT}/foto")
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/jpeg"
    assert served.headers["cache-control"].startswith("public, max-age=")
    assert served.content == photos[QUERY_PRODUCT].read_bytes()


def test_a_product_without_photo_or_without_product_is_404(shop: TestClient):
    for product in ("B000CABLES", "NOPE"):
        missing = shop.get(f"/productos/{product}/foto")
        assert (missing.status_code, missing.json()["kind"]) == (
            404,
            "ProductPhotoNotFoundError",
        )


def test_a_stored_path_outside_the_photos_is_never_served(tmp_path: Path):
    outside = tmp_path / "secreto.jpg"
    outside.write_bytes(b"secreto")
    for stored in (str(outside), "../../secreto.jpg", "images/ab/../../secreto.jpg", None, 7):
        assert _photo_path(tmp_path, stored) is None


def test_the_stores_that_stock_a_product_come_nearest_first(shop: TestClient):
    stores = shop.get("/productos/B000GUITAR/tiendas", params=CENTRO).json()
    assert [(store["nombre"], store["unidades"]) for store in stores] == [
        ("Centro", 2),
        ("Miraflores", 1),
    ]
    assert stores[0]["metros"] < stores[1]["metros"]
    assert shop.get("/productos/B000STRING/tiendas", params=CENTRO).json() == []


def test_departments_with_enough_products_are_listed(shop: TestClient):
    listed = shop.get("/departamentos")
    assert listed.headers["cache-control"].startswith("public, max-age=")
    assert listed.json() == [
        {"departamento": "Guitars", "productos": 2},
        {"departamento": "Drums & Percussion", "productos": 1},
        {"departamento": "Instrument Accessories", "productos": 1},
    ]


def test_featured_customers_come_with_their_purchases(shop: TestClient):
    assert shop.get("/usuarios").json() == [
        {"id": "USER-B", "compras": 5},
        {"id": "USER-A", "compras": 4},
        {"id": "USER-C", "compras": 4},
    ]


def test_a_customers_purchases_come_newest_first(shop: TestClient):
    bought = shop.get("/usuarios/USER-A/compras").json()
    assert [product["id"] for product in bought] == ["B000CABLES", "B000GUITAR"]
    assert shop.get("/usuarios/NOPE/compras").status_code == 404
