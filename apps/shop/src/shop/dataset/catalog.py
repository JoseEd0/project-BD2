"""Las fichas de producto de Amazon Reviews 2023, de una línea JSON a un `Product` plano.

Las fichas traen de todo: precios nulos o escritos como texto (`"from 23.99"`), productos
sin imágenes, categorías vacías o con promociones colgadas del árbol («Prime Card Bonus»)
y descripciones con los mismos párrafos repetidos. Aquí se aplanan a columnas de texto y
números que el gestor carga sin más, sin inventar nada: lo que falta queda vacío.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Set
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CATEGORY_ROOT = "Musical Instruments"
CATEGORY_SEPARATOR = " > "
FEATURE_SEPARATOR = " | "
MAIN_IMAGE_VARIANT = "MAIN"
# Lado mayor aproximado de cada variante de imagen de Amazon, medido sobre la categoría:
# `large` (`._AC_.jpg`) ronda los 500 px y unos 28 kB; `hi_res` (`._AC_SL1500_.jpg`), los
# 1 500 px y unos 170 kB. Se baja la menor que cubre el lado pedido.
LARGE_VARIANT_SIDE = 500
SMALL_FIRST_VARIANTS = ("large", "hi_res")
LARGE_FIRST_VARIANTS = ("hi_res", "large")
ALLOWED_URL_SCHEMES = ("https://", "http://")
PLAIN_PRICE = re.compile(r"\$?\s*(\d+(?:,\d{3})*(?:\.\d+)?)")


@dataclass(frozen=True, slots=True)
class Product:
    """Un producto con sus columnas ya aplanadas; `None` es un dato que la ficha no trae."""

    product_id: str
    title: str
    brand: str
    department: str
    category: str
    category_path: str
    price: float | None
    average_rating: float | None
    rating_count: int
    features: str
    description: str
    image_url: str | None


def parse_product(record: Mapping[str, Any], max_side: int) -> Product:
    """El producto de una línea de `meta_<categoría>.jsonl`.

    `max_side` es el lado mayor al que se reducirá la imagen: decide qué variante bajar.

    Raises:
        KeyError: si la línea no trae `parent_asin`.
    """
    categories = _strings(record.get("categories"))
    return Product(
        product_id=str(record["parent_asin"]),
        title=normalized(record.get("title")),
        brand=brand_of(record),
        department=department_of(categories),
        category=leaf_category_of(categories),
        category_path=CATEGORY_SEPARATOR.join(categories),
        price=price_of(record.get("price")),
        average_rating=_float_or_none(record.get("average_rating")),
        rating_count=_int_or_zero(record.get("rating_number")),
        features=FEATURE_SEPARATOR.join(unique_paragraphs(_strings(record.get("features")))),
        description=" ".join(unique_paragraphs(_strings(record.get("description")))),
        image_url=image_url_of(record.get("images"), max_side),
    )


def read_products(path: Path, wanted: Set[str], max_side: int) -> list[Product]:
    """Los productos de `wanted` que aparecen en el `.jsonl`, ordenados por id. Si una
    ficha se repite, manda la primera. Lee línea a línea: el archivo pesa cientos de MB."""
    chosen: dict[str, Product] = {}
    with path.open(encoding="utf-8") as lines:
        for line in lines:
            record = json.loads(line)
            identifier = record.get("parent_asin")
            if identifier not in wanted or identifier in chosen:
                continue
            chosen[identifier] = parse_product(record, max_side)
    return [chosen[identifier] for identifier in sorted(chosen)]


def normalized(value: object) -> str:
    """El texto con los espacios, tabuladores y saltos de línea reducidos a un espacio."""
    return " ".join(value.split()) if isinstance(value, str) else ""


def unique_paragraphs(paragraphs: Iterable[str]) -> list[str]:
    """Los párrafos no vacíos, normalizados y sin repetir, en su orden de aparición."""
    seen: dict[str, None] = {}
    for paragraph in paragraphs:
        text = normalized(paragraph)
        if text:
            seen.setdefault(text)
    return list(seen)


def brand_of(record: Mapping[str, Any]) -> str:
    """La marca: el campo `store` y, si no viene, `details.Brand`."""
    store = normalized(record.get("store"))
    if store:
        return store
    details = record.get("details")
    return normalized(details.get("Brand")) if isinstance(details, Mapping) else ""


def department_of(categories: list[str]) -> str:
    """El primer nivel bajo la raíz de la categoría («Guitars», «Drums & Percussion»).

    Algunas fichas empiezan directamente en el segundo nivel, sin la raíz: entonces el
    departamento es su primer elemento.
    """
    if not categories:
        return ""
    if categories[0] != CATEGORY_ROOT:
        return categories[0]
    return categories[1] if len(categories) > 1 else ""


def leaf_category_of(categories: list[str]) -> str:
    """La categoría más específica; vacía si la ficha solo trae la raíz o nada."""
    if not categories or categories == [CATEGORY_ROOT]:
        return ""
    return categories[-1]


def price_of(value: object) -> float | None:
    """El precio en dólares; `None` si falta o si es un texto que no es solo un número.

    Un texto como `"from 23.99"` es una cota, no un precio, y se descarta.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value) if value > 0 else None
    if not isinstance(value, str):
        return None
    match = PLAIN_PRICE.fullmatch(value.strip())
    return float(match.group(1).replace(",", "")) if match else None


def image_url_of(images: object, max_side: int) -> str | None:
    """La URL de la foto principal (variante `MAIN`, o la primera si ninguna lo es) en la
    menor variante que cubre `max_side`. Solo se aceptan URL `http(s)`."""
    if not isinstance(images, list):
        return None
    candidates = [image for image in images if isinstance(image, Mapping)]
    if not candidates:
        return None
    main = next(
        (image for image in candidates if image.get("variant") == MAIN_IMAGE_VARIANT),
        candidates[0],
    )
    variants = SMALL_FIRST_VARIANTS if max_side <= LARGE_VARIANT_SIDE else LARGE_FIRST_VARIANTS
    for variant in variants:
        url = main.get(variant)
        if isinstance(url, str) and url.startswith(ALLOWED_URL_SCHEMES):
            return url
    return None


def _strings(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    return [text for text in (normalized(value) for value in values) if text]


def _float_or_none(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _int_or_zero(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return int(value)
