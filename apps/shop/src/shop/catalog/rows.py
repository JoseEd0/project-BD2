"""Las filas de cada tabla de la tienda, leídas de los CSV del dataset (#79) para la carga
masiva: un diccionario `{columna: valor}` por fila, sin tener el CSV entero en memoria.

Un campo vacío del CSV es `NULL` en el gestor: un producto sin precio, sin marca o sin foto
no recibe un valor inventado.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Any

from minigestor_client import Point

from .errors import CatalogError

Row = dict[str, Any]

PRODUCTS_CSV = "products.csv"
USERS_CSV = "users.csv"
INTERACTIONS_CSV = "interactions.csv"
STORES_CSV = "stores.csv"
INVENTORY_CSV = "inventory.csv"
CSV_ENCODING = "utf-8"
# Entre el título y la descripción, y entre las viñetas y los párrafos: un salto de párrafo
# corta el texto igual para el analizador y para quien lo lee.
PARAGRAPH_BREAK = "\n\n"
DATE_CHARACTERS = len("2003-05-26")
FIRST_PURCHASE_ID = 1
HISTORY_COLUMNS = ("usuario", "producto", "instante", "particion")
POINT_PATTERN = re.compile(r"POINT\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\)")


@dataclass(frozen=True, slots=True)
class DatasetPaths:
    """Dónde lee las fotos el gestor: la carpeta del dataset vista desde él.

    El gestor y la tienda pueden correr en contenedores distintos, cada uno con el dataset
    montado en otra ruta; la columna `imagen` guarda la ruta del gestor, que es quien abre
    las fotos para SIFT.

    Attributes:
        local: la carpeta del dataset en esta máquina, de donde se leen los CSV.
        engine: la misma carpeta vista desde el gestor.
    """

    local: Path
    engine: PurePath

    def engine_image(self, relative: str) -> str | None:
        return str(self.engine / relative) if relative else None


def product_rows(paths: DatasetPaths) -> Iterator[Row]:
    """`shop_productos`: la ficha, el texto que se indexa y la ruta de la foto en el gestor."""
    for record in _records(paths.local / PRODUCTS_CSV):
        title = record["title"]
        description = _paragraphs(record["features"], record["description"])
        yield {
            "id": record["product_id"],
            "titulo": _optional(title),
            "marca": _optional(record["brand"]),
            "departamento": _optional(record["department"]),
            "categoria": _optional(record["category"]),
            "ruta_categoria": _optional(record["category_path"]),
            "precio": _optional_float(record["price"]),
            "valoracion": _optional_float(record["average_rating"]),
            "valoraciones": _optional_int(record["rating_count"]),
            "descripcion": _optional(description),
            "texto": _optional(_paragraphs(title, description)),
            "imagen": paths.engine_image(record["image"]),
        }


def product_images(paths: DatasetPaths) -> Iterator[tuple[str, Path]]:
    """`(id, foto en esta máquina)` de cada producto con foto, en el orden del CSV."""
    for record in _records(paths.local / PRODUCTS_CSV):
        if record["image"]:
            yield record["product_id"], paths.local / record["image"]


def user_rows(dataset: Path) -> Iterator[Row]:
    """`shop_usuarios`: cada usuario con su número de compras."""
    for record in _records(dataset / USERS_CSV):
        yield {"id": record["user_id"], "compras": int(record["interactions"])}


def purchase_rows(dataset: Path) -> Iterator[Row]:
    """`shop_compras`, numeradas desde 1 en orden cronológico: el archivo secuencial queda
    ordenado por fecha y una fecha repetida no choca con la clave.

    Raises:
        CatalogError: si una compra es anterior a la que la precede en el CSV.
    """
    previous_instant = 0
    records = _records(dataset / INTERACTIONS_CSV)
    for purchase_id, record in enumerate(records, start=FIRST_PURCHASE_ID):
        instant = int(record["timestamp_ms"])
        if instant < previous_instant:
            raise CatalogError(
                f"{INTERACTIONS_CSV} no está en orden cronológico en la compra {purchase_id}"
            )
        previous_instant = instant
        yield {
            "id": purchase_id,
            "usuario": record["user_id"],
            "producto": record["product_id"],
            "valoracion": float(record["rating"]),
            "instante": instant,
            "fecha": record["purchased_at"][:DATE_CHARACTERS],
            "particion": record["split"],
        }


def history_rows(dataset: Path) -> Iterator[Row]:
    """`shop_historial`: las compras de `shop_compras` con lo que el recomendador busca por
    usuario o por producto, en el mismo orden.

    Raises:
        CatalogError: si una compra es anterior a la que la precede en el CSV.
    """
    for purchase in purchase_rows(dataset):
        yield {column: purchase[column] for column in HISTORY_COLUMNS}


def store_rows(dataset: Path) -> Iterator[Row]:
    """`shop_tiendas`, con su ubicación como `POINT`.

    Raises:
        CatalogError: si una ubicación no es un `POINT(lat, lon)`.
    """
    for record in _records(dataset / STORES_CSV):
        yield {
            "id": int(record["store_id"]),
            "nombre": record["name"],
            "tipo": record["kind"],
            "ubicacion": parse_point(record["location"]),
        }


def inventory_rows(dataset: Path) -> Iterator[Row]:
    """`shop_inventario`: unidades de un producto en una tienda."""
    for record in _records(dataset / INVENTORY_CSV):
        yield {
            "tienda": int(record["store_id"]),
            "producto": record["product_id"],
            "unidades": int(record["quantity"]),
        }


def parse_point(text: str) -> Point:
    """El `POINT(lat, lon)` de un CSV como `Point`.

    Raises:
        CatalogError: si el texto no tiene esa forma.
    """
    match = POINT_PATTERN.fullmatch(text.strip())
    if match is None:
        raise CatalogError(f"'{text}' no es un POINT(lat, lon)")
    return Point(float(match[1]), float(match[2]))


def _records(path: Path) -> Iterator[dict[str, str]]:
    with path.open(encoding=CSV_ENCODING, newline="") as handle:
        yield from csv.DictReader(handle)


def _paragraphs(*parts: str) -> str:
    return PARAGRAPH_BREAK.join(part for part in parts if part)


def _optional(text: str) -> str | None:
    return text or None


def _optional_float(text: str) -> float | None:
    return float(text) if text else None


def _optional_int(text: str) -> int | None:
    return int(text) if text else None
