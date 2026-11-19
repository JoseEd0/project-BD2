"""Apoyo de los tests de la tienda: una red falsa que cuenta pedidos, imágenes diminutas y
las imágenes sintéticas con que se prueba CLIP.

Ningún test sale a la red: las fotos y los archivos publicados se sirven desde memoria.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from shop.dataset.errors import HttpStatusError

DISC_CANVAS = (300, 200)
DISC_BOX = (60, 20, 240, 180)


class FakeTransport:
    """Responde a cada URL con lo que se le programó: bytes, o un estado de error. Una
    lista se consume en orden, para simular un fallo que luego se arregla."""

    def __init__(self) -> None:
        self.responses: dict[str, list[bytes | int]] = {}
        self.requests: list[str] = []

    def serve(self, url: str, *responses: bytes | int) -> None:
        self.responses[url] = list(responses)

    @contextmanager
    def open(self, url: str) -> Iterator[io.BytesIO]:
        self.requests.append(url)
        queue = self.responses.get(url, [404])
        response = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(response, int):
            raise HttpStatusError(url, response)
        yield io.BytesIO(response)


def image_bytes(
    size: tuple[int, int], color: tuple[int, int, int], image_format: str = "PNG"
) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, image_format)
    return output.getvalue()


def write_jsonl(path: Path, records: list[dict[str, object]]) -> Path:
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return path


def pattern_image(width: int, height: int) -> Image.Image:
    """Una imagen determinista con gradientes y una trama: cada píxel distinto, para que un
    corte o una interpolación equivocados cambien los valores."""
    y, x = np.mgrid[0:height, 0:width]
    channels = (
        x * 255 // max(width - 1, 1),
        y * 255 // max(height - 1, 1),
        (x * 7 + y * 13) % 256,
    )
    return Image.fromarray(np.stack(channels, axis=-1).astype(np.uint8), "RGB")


def disc(color: str) -> Image.Image:
    """Un disco de `color` sobre blanco, que CLIP reconoce por su color."""
    image = Image.new("RGB", DISC_CANVAS, "white")
    ImageDraw.Draw(image).ellipse(DISC_BOX, fill=color)
    return image


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
TINY_USERS = (("USER-A", 3), ("USER-B", 2))
TINY_PURCHASES = (
    ("USER-A", "B000GUITAR", 5.0, 1_600_000_000_000, "train"),
    ("USER-B", "B0000DRUMS", 4.0, 1_600_000_000_000, "train"),
    ("USER-A", "B000CABLES", 3.0, 1_600_000_500_000, "valid"),
    ("USER-B", "B000STRING", 5.0, 1_700_000_000_000, "test"),
    ("USER-A", "B000STRING", 4.0, 1_700_000_100_000, "test"),
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
NOISE_SIDE = 96


def noise_photo(seed: int) -> bytes:
    """Una foto JPEG de ruido con semilla: textura de sobra para que SIFT encuentre puntos."""
    generator = np.random.default_rng(seed)
    pixels = generator.integers(0, 256, (NOISE_SIDE, NOISE_SIDE, 3), dtype=np.uint8)
    output = io.BytesIO()
    Image.fromarray(pixels, "RGB").save(output, "JPEG")
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
    record["image"] = "" if seed is None else _save_photo(dataset, noise_photo(int(str(seed))))
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
