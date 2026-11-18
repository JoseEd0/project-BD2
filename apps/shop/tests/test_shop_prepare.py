"""El comando completo sobre un 5-core diminuto: tablas coherentes y reanudación."""

import csv
import json
from collections import Counter
from pathlib import Path

import pytest
from apps.shop.tests.shop_support import FakeTransport, image_bytes, write_jsonl

from appkit.download import RemoteFile
from shop.dataset.images import ImageSettings
from shop.dataset.prepare import (
    INTERACTIONS_CSV,
    INVENTORY_CSV,
    MANIFEST_NAME,
    PRODUCTS_CSV,
    STORES_CSV,
    USERS_CSV,
    MissingMetadataError,
    PrepareSettings,
    SourceFetcher,
    prepare,
)
from shop.dataset.sources import METADATA, SPLITS, Split, file_name_of
from shop.dataset.stores import STORE_KINDS, InventorySettings

USERS = [f"U{index}" for index in range(5)]
PRODUCTS = [f"B{index}" for index in range(5)]
OUTSIDE_THE_CORE = "B9"
NO_IMAGE = "B4"
PLACES = """id,nombre,rubro,ubicacion
2,Saga Falabella,tienda por departamentos,"POINT(-12.091000, -77.023000)"
1,Jockey Plaza,centro comercial,"POINT(-12.085000, -76.977000)"
3,Interbank,banco,"POINT(-12.104020, -77.052737)"
"""


def url_of(product: str) -> str:
    return f"https://cdn/{product}.jpg"


def split_files(users: list[str]) -> dict[Split, str]:
    """Cada usuario compra los cinco productos, uno por día, en un orden propio."""
    rows: dict[Split, list[str]] = {split: [] for split in Split}
    for offset, user in enumerate(users):
        order = PRODUCTS[offset:] + PRODUCTS[:offset]
        splits = [Split.TRAIN] * 3 + [Split.VALID, Split.TEST]
        for day, (product, split) in enumerate(zip(order, splits, strict=True)):
            rows[split].append(f"{user},{product},4.0,{(day + 1) * 86_400_000 + offset}")
    header = "user_id,parent_asin,rating,timestamp\n"
    return {split: header + "\n".join(lines) + "\n" for split, lines in rows.items()}


def main_image(product: str) -> list[dict[str, str]]:
    return [] if product == NO_IMAGE else [{"variant": "MAIN", "large": url_of(product)}]


def metadata_records(products: list[str]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = [
        {
            "parent_asin": product,
            "title": f"Product {product}",
            "categories": ["Musical Instruments", "Guitars"],
            "price": None if product == "B1" else 10.5,
            "images": main_image(product),
        }
        for product in products
    ]
    records.append({"parent_asin": OUTSIDE_THE_CORE, "title": "outside"})
    return records


class Sources:
    """Los archivos publicados, servidos desde disco y contando cuántas veces se piden."""

    def __init__(self, tmp_path: Path, products: list[str] = PRODUCTS) -> None:
        self.contents: dict[str, bytes] = {
            file_name_of(source): text.encode() for source, text in zip(
                SPLITS.values(), split_files(USERS).values(), strict=True
            )
        }
        metadata = write_jsonl(tmp_path / "meta.jsonl", metadata_records(products))
        self.contents[file_name_of(METADATA)] = metadata.read_bytes()
        self.fetched: Counter[str] = Counter()

    def fetcher(self) -> SourceFetcher:
        def fetch(source: RemoteFile, destination: Path) -> Path:
            if not destination.exists():
                self.fetched[destination.name] += 1
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(self.contents[destination.name])
            return destination

        return fetch


@pytest.fixture
def settings(tmp_path: Path) -> PrepareSettings:
    places = tmp_path / "lugares.csv"
    places.write_text(PLACES, encoding="utf-8")
    return PrepareSettings(
        output=tmp_path / "out",
        places=places,
        images=ImageSettings(
            max_side=32, jpeg_quality=85, workers=2, attempts=2, backoff_seconds=0.0
        ),
        inventory=InventorySettings(seed=1, products_per_store=3, max_quantity=4),
        store_kinds=STORE_KINDS,
    )


@pytest.fixture
def served(transport: FakeTransport) -> FakeTransport:
    transport.serve(url_of("B0"), image_bytes((64, 64), (200, 0, 0)))
    transport.serve(url_of("B1"), image_bytes((64, 64), (200, 0, 0)))
    transport.serve(url_of("B2"), image_bytes((64, 64), (0, 0, 200)))
    return transport


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def run(settings: PrepareSettings, sources: Sources, transport: FakeTransport) -> dict[str, object]:
    return prepare(settings, sources.fetcher(), transport, sleep=lambda _: None)


def test_the_tables_cover_exactly_the_core(
    tmp_path: Path, settings: PrepareSettings, served: FakeTransport
) -> None:
    run(settings, Sources(tmp_path), served)
    products = rows(settings.output / PRODUCTS_CSV)
    interactions = rows(settings.output / INTERACTIONS_CSV)
    assert [row["product_id"] for row in products] == PRODUCTS
    assert {row["product_id"] for row in interactions} == set(PRODUCTS)
    assert [row["user_id"] for row in rows(settings.output / USERS_CSV)] == USERS
    assert products[1]["price"] == ""
    assert products[0]["image"] == products[1]["image"] != ""
    assert products[3]["image"] == ""
    assert products[4]["image"] == products[4]["image_url"] == ""


def test_each_user_has_its_last_purchase_in_test(
    tmp_path: Path, settings: PrepareSettings, served: FakeTransport
) -> None:
    run(settings, Sources(tmp_path), served)
    interactions = rows(settings.output / INTERACTIONS_CSV)
    timestamps = [int(row["timestamp_ms"]) for row in interactions]
    assert timestamps == sorted(timestamps)
    for user in USERS:
        mine = [row["split"] for row in interactions if row["user_id"] == user]
        assert mine == ["train", "train", "train", "valid", "test"]


def test_stores_inventory_and_manifest(
    tmp_path: Path, settings: PrepareSettings, served: FakeTransport
) -> None:
    manifest = run(settings, Sources(tmp_path), served)
    stores = rows(settings.output / STORES_CSV)
    assert [row["store_id"] for row in stores] == ["1", "2"]
    inventory = rows(settings.output / INVENTORY_CSV)
    assert len(inventory) == 2 * 3
    assert {row["product_id"] for row in inventory} <= set(PRODUCTS)
    assert manifest["counts"] == {
        "products": 5,
        "users": 5,
        "interactions": {"train": 15, "valid": 5, "test": 5, "total": 25},
        "stores": 2,
        "inventory_rows": 6,
    }
    assert manifest["images"] == {
        "without_url": 1,
        "with_image": 3,
        "failed": 1,
        "deduplicated": 1,
        "files": 2,
        "bytes": manifest["images"]["bytes"],  # type: ignore[index]
    }
    written = json.loads((settings.output / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert written == manifest


def test_a_second_run_downloads_nothing_and_writes_the_same_bytes(
    tmp_path: Path, settings: PrepareSettings, served: FakeTransport
) -> None:
    sources = Sources(tmp_path)
    run(settings, sources, served)
    first = {path: path.read_bytes() for path in settings.output.rglob("*") if path.is_file()}
    served.requests.clear()
    run(settings, sources, served)
    second = {path: path.read_bytes() for path in settings.output.rglob("*") if path.is_file()}
    assert served.requests == []
    assert set(sources.fetched.values()) == {1}
    assert second == first


def test_a_core_product_without_metadata_stops_the_preparation(
    tmp_path: Path, settings: PrepareSettings, served: FakeTransport
) -> None:
    with pytest.raises(MissingMetadataError, match="1 productos"):
        run(settings, Sources(tmp_path, PRODUCTS[:-1]), served)
