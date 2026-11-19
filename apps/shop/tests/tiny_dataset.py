"""Un dataset de la tienda diminuto, con la forma del de #79 (`python -m shop.dataset`):
cinco productos, tres usuarios, trece compras, tres tiendas y su inventario, con fotos
sintéticas. En entrenamiento, la guitarra y el cable se compran juntos dos veces; el pack
de iniciación solo se compra en validación y prueba, así que no tiene compras conjuntas de
entrenamiento. Lo usan los tests y el humo del compose de la CI, que lo genera con

    uv run --no-sync python -m apps.shop.tests.tiny_dataset data/datasets/amazon_musical_instruments
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

TINY_PRODUCTS = (
    {
        "product_id": "B000GUITAR",
        "title": "Electric Guitar with Maple Neck",
        "brand": "Fender",
        "department": "Guitars",
        "category": "Electric Guitars",
        "category_path": "Musical Instruments > Guitars > Electric Guitars",
        "price": "699.99",
        "average_rating": "4.7",
        "rating_count": "120",
        "features": "Maple neck | Two humbuckers",
        "description": "A solid body guitar for rock and blues players.",
        "image_seed": 1,
    },
    {
        "product_id": "B0000DRUMS",
        "title": "Drum Sticks 5A",
        "brand": "Vic Firth",
        "department": "Drums & Percussion",
        "category": "Drumsticks",
        "category_path": "Musical Instruments > Drums & Percussion > Drumsticks",
        "price": "",
        "average_rating": "4.9",
        "rating_count": "800",
        "features": "",
        "description": "Hickory drum sticks with wood tips.",
        "image_seed": 2,
    },
    {
        "product_id": "B000CABLES",
        "title": "Instrument Cable 10 ft",
        "brand": "",
        "department": "",
        "category": "",
        "category_path": "",
        "price": "12.5",
        "average_rating": "4.2",
        "rating_count": "33",
        "features": "Braided jacket",
        "description": "",
        "image_seed": None,
    },
    {
        "product_id": "B000STRING",
        "title": "Acoustic Guitar Strings",
        "brand": "Martin",
        "department": "Instrument Accessories",
        "category": "Strings",
        "category_path": "Musical Instruments > Instrument Accessories > Strings",
        "price": "9.99",
        "average_rating": "4.6",
        "rating_count": "2100",
        "features": "Phosphor bronze",
        "description": "Light gauge strings for acoustic guitars.",
        "image_seed": 3,
    },
    {
        "product_id": "B000COPYGT",
        "title": "Electric Guitar Starter Pack",
        "brand": "Fender",
        "department": "Guitars",
        "category": "Electric Guitars",
        "category_path": "Musical Instruments > Guitars > Electric Guitars",
        "price": "249.0",
        "average_rating": "4.1",
        "rating_count": "15",
        "features": "Guitar, amp and cable",
        "description": "Everything to start playing.",
        "image_seed": 1,
    },
)
TINY_USERS = (("USER-A", 4), ("USER-B", 5), ("USER-C", 4))
TINY_PURCHASES = (
    ("USER-A", "B000GUITAR", 5.0, 1_600_000_000_000, "train"),
    ("USER-B", "B0000DRUMS", 4.0, 1_600_000_000_000, "train"),
    ("USER-B", "B000GUITAR", 5.0, 1_600_000_100_000, "train"),
    ("USER-A", "B000CABLES", 3.0, 1_600_000_200_000, "train"),
    ("USER-B", "B000CABLES", 4.0, 1_600_000_300_000, "train"),
    ("USER-C", "B000GUITAR", 4.0, 1_600_000_400_000, "train"),
    ("USER-C", "B000STRING", 5.0, 1_600_000_500_000, "train"),
    ("USER-A", "B000COPYGT", 4.0, 1_650_000_000_000, "valid"),
    ("USER-B", "B000STRING", 5.0, 1_650_000_100_000, "valid"),
    ("USER-C", "B0000DRUMS", 3.0, 1_650_000_200_000, "valid"),
    ("USER-B", "B000COPYGT", 5.0, 1_700_000_000_000, "test"),
    ("USER-A", "B000STRING", 4.0, 1_700_000_100_000, "test"),
    ("USER-C", "B000CABLES", 4.0, 1_700_000_200_000, "test"),
)
TINY_STORES = (
    (101, "Centro", "tienda", "POINT(-12.0464, -77.0428)"),
    (102, "Miraflores", "centro comercial", "POINT(-12.1211, -77.0297)"),
    (103, "Callao", "tienda de cómputo", "POINT(-12.0566, -77.1181)"),
)
TINY_INVENTORY = ((101, "B000GUITAR", 2), (101, "B000CABLES", 7), (102, "B000GUITAR", 1))
PRODUCT_FIELDS = (
    "product_id", "title", "brand", "department", "category", "category_path", "price",
    "average_rating", "rating_count", "features", "description", "image", "image_url",
)  # fmt: skip
PHOTO_SIDE = 192
PHOTO_SHAPES = 80
SHAPE_MIN_SIDE = 3
JPEG_QUALITY = 85


def shapes_photo(seed: int) -> bytes:
    """Una foto JPEG de figuras de colores al azar, con semilla: unos 170 puntos SIFT, así
    que cuatro fotos alcanzan para el vocabulario de 256 palabras por omisión del gestor."""
    generator = np.random.default_rng(seed)
    image = Image.new("RGB", (PHOTO_SIDE, PHOTO_SIDE), "white")
    draw = ImageDraw.Draw(image)
    for _ in range(PHOTO_SHAPES):
        left, top = (int(value) for value in generator.integers(0, PHOTO_SIDE, 2))
        side = int(generator.integers(SHAPE_MIN_SIDE, PHOTO_SIDE // 6))
        color = tuple(int(value) for value in generator.integers(0, 256, 3))
        shape = draw.ellipse if generator.random() < 0.5 else draw.rectangle
        shape((left, top, left + side, top + side), fill=color)
    output = io.BytesIO()
    image.save(output, "JPEG", quality=JPEG_QUALITY)
    return output.getvalue()


def write_tiny_dataset(dataset: Path) -> Path:
    """Un dataset de la tienda con la forma del de #79 y cinco productos: uno sin precio,
    uno sin foto ni marca, y dos que comparten la misma foto."""
    dataset.mkdir(parents=True, exist_ok=True)
    products = [_product_record(dataset, product) for product in TINY_PRODUCTS]
    counts = {
        "products.csv": _write_csv(dataset / "products.csv", PRODUCT_FIELDS, products),
        "users.csv": _write_csv(
            dataset / "users.csv",
            ("user_id", "interactions"),
            [{"user_id": user, "interactions": count} for user, count in TINY_USERS],
        ),
        "interactions.csv": _write_csv(
            dataset / "interactions.csv",
            ("user_id", "product_id", "rating", "timestamp_ms", "purchased_at", "split"),
            [_purchase_record(*purchase) for purchase in TINY_PURCHASES],
        ),
        "stores.csv": _write_csv(
            dataset / "stores.csv",
            ("store_id", "name", "kind", "location"),
            [
                {"store_id": store, "name": name, "kind": kind, "location": location}
                for store, name, kind, location in TINY_STORES
            ],
        ),
        "inventory.csv": _write_csv(
            dataset / "inventory.csv",
            ("store_id", "product_id", "quantity"),
            [
                {"store_id": store, "product_id": product, "quantity": quantity}
                for store, product, quantity in TINY_INVENTORY
            ],
        ),
    }
    manifest = {"tables": {name: {"rows": rows} for name, rows in counts.items()}}
    (dataset / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return dataset


def _product_record(dataset: Path, product: dict[str, object]) -> dict[str, object]:
    record = {key: value for key, value in product.items() if key != "image_seed"}
    seed = product["image_seed"]
    record["image"] = "" if seed is None else _save_photo(dataset, shapes_photo(int(str(seed))))
    record["image_url"] = ""
    return record


def _save_photo(dataset: Path, data: bytes) -> str:
    name = hashlib.sha256(data).hexdigest()
    relative = f"images/{name[:2]}/{name}.jpg"
    (dataset / relative).parent.mkdir(parents=True, exist_ok=True)
    (dataset / relative).write_bytes(data)
    return relative


def _purchase_record(
    user: str, product: str, rating: float, instant: int, split: str
) -> dict[str, object]:
    moment = datetime.fromtimestamp(instant / 1000, tz=UTC).isoformat(timespec="milliseconds")
    return {
        "user_id": user,
        "product_id": product,
        "rating": rating,
        "timestamp_ms": instant,
        "purchased_at": moment,
        "split": split,
    }


def _write_csv(path: Path, fields: Sequence[str], records: list[dict[str, object]]) -> int:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), lineterminator="\n")
        writer.writeheader()
        writer.writerows(records)
    return len(records)


if __name__ == "__main__":
    print(write_tiny_dataset(Path(sys.argv[1])))
