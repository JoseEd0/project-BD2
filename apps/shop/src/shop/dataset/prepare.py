"""Baja y prepara el dataset de la tienda en una carpeta: catálogo, usuarios, compras con
su partición, fotos, tiendas de Lima, inventario y un manifiesto con conteos y SHA-256.

Todo es determinista: con los mismos archivos de origen y los mismos parámetros, dos
ejecuciones dejan los mismos bytes, y la segunda no va a la red (los originales verifican
su SHA-256 y las fotos, el suyo).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from appkit.download import RemoteFile, sha256_of
from shop.dataset.catalog import Product, read_products
from shop.dataset.errors import DatasetError
from shop.dataset.files import write_csv_atomically, write_text_atomically
from shop.dataset.images import (
    IMAGES_DIRECTORY,
    ImageFetcher,
    ImageReport,
    ImageSettings,
    image_report,
)
from shop.dataset.interactions import (
    Interaction,
    chronological,
    counts_by_user,
    read_split,
    train_popularity,
    verify_core,
    verify_leave_last_out,
)
from shop.dataset.sources import (
    CATEGORY,
    CORE_MINIMUM,
    HUB_REPOSITORY,
    HUB_REVISION,
    METADATA,
    SPLIT_SCHEME,
    SPLITS,
    Split,
    file_name_of,
)
from shop.dataset.stores import (
    InventorySettings,
    Store,
    generate_inventory,
    read_places,
    select_stores,
)
from shop.dataset.transport import Transport

RAW_DIRECTORY = "raw"
MANIFEST_NAME = "manifest.json"
PRODUCTS_CSV = "products.csv"
USERS_CSV = "users.csv"
INTERACTIONS_CSV = "interactions.csv"
STORES_CSV = "stores.csv"
INVENTORY_CSV = "inventory.csv"
PRODUCT_HEADER = (
    "product_id",
    "title",
    "brand",
    "department",
    "category",
    "category_path",
    "price",
    "average_rating",
    "rating_count",
    "features",
    "description",
    "image",
    "image_url",
)
USER_HEADER = ("user_id", "interactions")
INTERACTION_HEADER = ("user_id", "product_id", "rating", "timestamp_ms", "purchased_at", "split")
STORE_HEADER = ("store_id", "name", "kind", "location")
INVENTORY_HEADER = ("store_id", "product_id", "quantity")
JSON_INDENT = 2

logger = logging.getLogger(__name__)


class MissingMetadataError(DatasetError):
    """Un producto del 5-core no tiene ficha: el catálogo no cubriría todas las compras."""


@dataclass(frozen=True, slots=True)
class PrepareSettings:
    """Dónde se escribe, de dónde salen las tiendas y con qué parámetros."""

    output: Path
    places: Path
    images: ImageSettings
    inventory: InventorySettings
    store_kinds: frozenset[str]


SourceFetcher = Callable[[RemoteFile, Path], Path]


def prepare(
    settings: PrepareSettings,
    fetch_source: SourceFetcher,
    transport: Transport,
    sleep: Callable[[float], None],
) -> dict[str, object]:
    """Deja el dataset en `settings.output` y devuelve el manifiesto que escribió.

    `fetch_source(archivo, destino)` deja un archivo publicado en `destino`, verificado, y
    devuelve su ruta (`appkit.download.fetch_verified`); `transport` baja las fotos y
    `sleep` espera entre sus reintentos.

    Raises:
        DatasetError: si una descarga no verifica, el 5-core o la partición no son los
            esperados, falta la ficha de un producto o el CSV de lugares no es el de la
            Parte 2. Una foto que no se pudo bajar no es un error: queda sin foto.
    """
    raw = settings.output / RAW_DIRECTORY
    split_paths = {
        split: fetch_source(source, raw / file_name_of(source)) for split, source in SPLITS.items()
    }
    metadata_path = fetch_source(METADATA, raw / file_name_of(METADATA))
    interactions = _verified_interactions(split_paths)
    products = _catalog(metadata_path, interactions, settings.images.max_side)
    fetcher = ImageFetcher(settings.output, transport, settings.images, sleep)
    image_urls = {product.product_id: product.image_url for product in products}
    outcomes = fetcher.fetch(url for url in image_urls.values() if url is not None)
    images = {
        product_id: outcomes[url].image if url is not None else None
        for product_id, url in image_urls.items()
    }
    stores = select_stores(read_places(settings.places), settings.store_kinds)
    popularity = {product.product_id: 0 for product in products} | train_popularity(interactions)
    files = _write_tables(settings, products, images, interactions, stores, popularity)
    manifest = _manifest(
        settings, interactions, files, image_report(image_urls, outcomes, fetcher.store)
    )
    write_text_atomically(
        settings.output / MANIFEST_NAME, json.dumps(manifest, indent=JSON_INDENT) + "\n"
    )
    return manifest


def _verified_interactions(split_paths: Mapping[Split, Path]) -> list[Interaction]:
    interactions = [
        item for split, path in split_paths.items() for item in read_split(path, split)
    ]
    verify_core(interactions, CORE_MINIMUM)
    verify_leave_last_out(interactions)
    return chronological(interactions)


def _catalog(path: Path, interactions: Sequence[Interaction], max_side: int) -> list[Product]:
    wanted = frozenset(item.product_id for item in interactions)
    products = read_products(path, wanted, max_side)
    missing = len(wanted) - len(products)
    if missing:
        raise MissingMetadataError(f"{missing} productos del 5-core no tienen ficha")
    return products


def _write_tables(
    settings: PrepareSettings,
    products: Sequence[Product],
    images: Mapping[str, str | None],
    interactions: Sequence[Interaction],
    stores: Sequence[Store],
    popularity: Mapping[str, int],
) -> dict[str, int]:
    """Escribe los cinco CSV y devuelve las filas de cada uno."""
    output = settings.output
    inventory = generate_inventory(stores, popularity, settings.inventory)
    return {
        PRODUCTS_CSV: write_csv_atomically(
            output / PRODUCTS_CSV, PRODUCT_HEADER, _product_rows(products, images)
        ),
        USERS_CSV: write_csv_atomically(
            output / USERS_CSV, USER_HEADER, counts_by_user(interactions).items()
        ),
        INTERACTIONS_CSV: write_csv_atomically(
            output / INTERACTIONS_CSV, INTERACTION_HEADER, _interaction_rows(interactions)
        ),
        STORES_CSV: write_csv_atomically(
            output / STORES_CSV,
            STORE_HEADER,
            ((store.store_id, store.name, store.kind, store.location) for store in stores),
        ),
        INVENTORY_CSV: write_csv_atomically(
            output / INVENTORY_CSV,
            INVENTORY_HEADER,
            ((entry.store_id, entry.product_id, entry.quantity) for entry in inventory),
        ),
    }


def _product_rows(
    products: Iterable[Product], images: Mapping[str, str | None]
) -> Iterable[Sequence[object]]:
    for product in products:
        yield (
            product.product_id,
            product.title,
            product.brand,
            product.department,
            product.category,
            product.category_path,
            product.price,
            product.average_rating,
            product.rating_count,
            product.features,
            product.description,
            images[product.product_id],
            product.image_url,
        )


def _interaction_rows(interactions: Iterable[Interaction]) -> Iterable[Sequence[object]]:
    for item in interactions:
        yield (
            item.user_id,
            item.product_id,
            item.rating,
            item.timestamp_ms,
            item.purchased_at,
            item.split.value,
        )


def _manifest(
    settings: PrepareSettings,
    interactions: Sequence[Interaction],
    rows: Mapping[str, int],
    images: ImageReport,
) -> dict[str, object]:
    output = settings.output
    by_split = {split.value: 0 for split in Split}
    for item in interactions:
        by_split[item.split.value] += 1
    tables = {name: _file_entry(output / name, count) for name, count in rows.items()}
    return {
        "dataset": f"Amazon Reviews 2023, {CATEGORY}, 5-core {SPLIT_SCHEME}",
        "sources": {
            "repository": HUB_REPOSITORY,
            "revision": HUB_REVISION,
            "metadata": _source_entry(METADATA),
            **{split.value: _source_entry(source) for split, source in SPLITS.items()},
            "places": {
                "file": settings.places.name,
                "sha256": sha256_of(settings.places),
                "bytes": settings.places.stat().st_size,
            },
        },
        "parameters": {
            "core_minimum": CORE_MINIMUM,
            "split_scheme": SPLIT_SCHEME,
            "image_max_side": settings.images.max_side,
            "jpeg_quality": settings.images.jpeg_quality,
            "inventory_seed": settings.inventory.seed,
            "products_per_store": settings.inventory.products_per_store,
            "max_quantity": settings.inventory.max_quantity,
            "store_kinds": sorted(settings.store_kinds),
        },
        "counts": {
            "products": rows[PRODUCTS_CSV],
            "users": rows[USERS_CSV],
            "interactions": {**by_split, "total": len(interactions)},
            "stores": rows[STORES_CSV],
            "inventory_rows": rows[INVENTORY_CSV],
        },
        "images": asdict(images),
        "tables": tables,
        "disk_bytes": {
            RAW_DIRECTORY: _directory_bytes(output / RAW_DIRECTORY),
            IMAGES_DIRECTORY: _directory_bytes(output / IMAGES_DIRECTORY),
            "tables": sum((output / name).stat().st_size for name in rows),
        },
    }


def _source_entry(source: RemoteFile) -> dict[str, object]:
    return {"url": source.url, "sha256": source.sha256, "bytes": source.size}


def _file_entry(path: Path, rows: int) -> dict[str, object]:
    return {"rows": rows, "bytes": path.stat().st_size, "sha256": sha256_of(path)}


def _directory_bytes(directory: Path) -> int:
    return sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())
