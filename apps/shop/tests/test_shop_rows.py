"""Las filas de la carga masiva, leídas de un dataset diminuto con la forma del de #79."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest
from apps.shop.tests.tiny_dataset import (
    TINY_INVENTORY,
    TINY_PRODUCTS,
    TINY_PURCHASES,
    TINY_USERS,
    write_tiny_dataset,
)

from minigestor_client import Point
from shop.catalog.errors import CatalogError
from shop.catalog.rows import (
    DatasetPaths,
    inventory_rows,
    parse_point,
    product_images,
    product_rows,
    purchase_rows,
    store_rows,
    user_rows,
)
from shop.seed.manifest import ManifestError, expected_rows
from shop.settings import DATASET_DIRECTORY, SeedSettings

ENGINE_DATASET = PurePosixPath("/datos/amazon_musical_instruments")


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    return write_tiny_dataset(tmp_path / DATASET_DIRECTORY)


@pytest.fixture
def products(dataset: Path) -> dict[str, dict[str, object]]:
    rows = product_rows(DatasetPaths(dataset, ENGINE_DATASET))
    return {str(row["id"]): row for row in rows}


def test_every_product_becomes_a_row(products: dict[str, dict[str, object]]):
    assert list(products) == [str(product["product_id"]) for product in TINY_PRODUCTS]


def test_a_missing_price_brand_or_photo_is_null(products: dict[str, dict[str, object]]):
    assert products["B0000DRUMS"]["precio"] is None
    cable = products["B000CABLES"]
    assert (cable["marca"], cable["departamento"], cable["imagen"]) == (None, None, None)
    assert products["B000GUITAR"]["precio"] == pytest.approx(699.99)


def test_the_indexed_text_joins_title_features_and_description(
    products: dict[str, dict[str, object]],
):
    guitar = products["B000GUITAR"]
    assert guitar["descripcion"] == (
        "Maple neck | Two humbuckers\n\nA solid body guitar for rock and blues players."
    )
    assert guitar["texto"] == f"Electric Guitar with Maple Neck\n\n{guitar['descripcion']}"
    assert products["B000CABLES"]["texto"] == "Instrument Cable 10 ft\n\nBraided jacket"


def test_the_photo_path_is_the_one_the_engine_sees(
    dataset: Path, products: dict[str, dict[str, object]]
):
    image = str(products["B000GUITAR"]["imagen"])
    assert image.startswith(f"{ENGINE_DATASET}/images/")
    local = dataset / PurePosixPath(image).relative_to(ENGINE_DATASET)
    assert local.is_file()


def test_product_images_skip_the_products_without_photo(dataset: Path):
    images = list(product_images(DatasetPaths(dataset, ENGINE_DATASET)))
    with_photo = [str(item["product_id"]) for item in TINY_PRODUCTS if item["image_seed"]]
    assert [product for product, _ in images] == with_photo
    assert images[0][1] == images[3][1]
    assert all(path.is_file() for _, path in images)


def test_purchases_are_numbered_in_chronological_order(dataset: Path):
    purchases = list(purchase_rows(dataset))
    assert [row["id"] for row in purchases] == list(range(1, len(TINY_PURCHASES) + 1))
    assert purchases[0] == {
        "id": 1,
        "usuario": "USER-A",
        "producto": "B000GUITAR",
        "valoracion": 5.0,
        "instante": 1_600_000_000_000,
        "fecha": "2020-09-13",
        "particion": "train",
    }


def test_purchases_out_of_order_are_rejected(dataset: Path):
    path = dataset / "interactions.csv"
    header, *lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join([header, *reversed(lines)]) + "\n", encoding="utf-8")
    with pytest.raises(CatalogError, match="orden cronológico"):
        list(purchase_rows(dataset))


def test_users_stores_and_inventory(dataset: Path):
    assert [(row["id"], row["compras"]) for row in user_rows(dataset)] == list(TINY_USERS)
    stores = list(store_rows(dataset))
    assert stores[0] == {
        "id": 101,
        "nombre": "Centro",
        "tipo": "tienda",
        "ubicacion": Point(-12.0464, -77.0428),
    }
    inventory = inventory_rows(dataset)
    assert [(row["tienda"], row["producto"], row["unidades"]) for row in inventory] == list(
        TINY_INVENTORY
    )


@pytest.mark.parametrize("text", ["POINT(-12.1)", "(-12.1, -77.0)", "POINT(a, b)", ""])
def test_a_location_that_is_not_a_point_is_rejected(text: str):
    with pytest.raises(CatalogError):
        parse_point(text)


def test_expected_rows_come_from_the_manifest(dataset: Path):
    counts = expected_rows(dataset)
    assert (counts.products, counts.users, counts.purchases, counts.stores, counts.inventory) == (
        len(TINY_PRODUCTS),
        len(TINY_USERS),
        len(TINY_PURCHASES),
        3,
        len(TINY_INVENTORY),
    )


def test_a_dataset_without_manifest_is_rejected(dataset: Path):
    (dataset / "manifest.json").unlink()
    with pytest.raises(ManifestError, match=r"python -m shop\.dataset"):
        expected_rows(dataset)


def test_seed_paths_default_to_the_local_dataset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SHOP_ENGINE_URL", "http://gestor")
    monkeypatch.setenv("SHOP_ENGINE_KEY", "clave")
    monkeypatch.setenv("SHOP_MODELS_DIR", str(tmp_path / "modelos"))
    monkeypatch.setenv("SHOP_DATA_DIR", str(tmp_path / "datos"))
    settings = SeedSettings()  # type: ignore[call-arg]
    assert settings.dataset == tmp_path / "datos" / DATASET_DIRECTORY
    assert settings.engine_dataset == (tmp_path / "datos").resolve() / DATASET_DIRECTORY
    assert settings.embeddings == settings.dataset / "embeddings"
    monkeypatch.setenv("SHOP_ENGINE_DATA_DIR", "/data/datasets")
    assert SeedSettings().engine_dataset == PurePosixPath(  # type: ignore[call-arg]
        "/data/datasets/amazon_musical_instruments"
    )
