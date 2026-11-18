"""Las tiendas de Lima y su inventario.

Las tiendas son lugares reales de OpenStreetMap, los mismos de la Parte 2
(`demos.descargar_lugares`): las de los rubros donde en Lima se venden instrumentos,
audio y sus accesorios. OpenStreetMap no distingue una tienda de música en el extracto de
Geofabrik, así que se eligen tiendas por departamentos, centros comerciales, tiendas de
cómputo y de video, y tiendas generales.

**El inventario es lo único sintético del dataset.** Cada tienda tiene en stock una muestra
de productos sin reemplazo, con probabilidad proporcional a sus compras de entrenamiento
más uno (los más comprados están en más tiendas, y ninguno queda con probabilidad cero), y
una cantidad uniforme. Cada tienda usa su propio generador, sembrado con la semilla y su
id: la misma semilla da el mismo inventario, y quitar una tienda no cambia el de las otras.
Las compras de validación y prueba no entran en los pesos, para que el inventario no le
cuente nada de ellas al recomendador.
"""

from __future__ import annotations

import csv
import heapq
import math
import random
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from shop.dataset.errors import PlacesFormatError

PLACE_COLUMNS = ("id", "nombre", "rubro", "ubicacion")
LOCATION_PATTERN = re.compile(r"POINT\((-?\d+(?:\.\d+)?), (-?\d+(?:\.\d+)?)\)")
COORDINATE_DECIMALS = 6
# Rubros tal como los escribe `demos.descargar_lugares` (en castellano, o la etiqueta de
# OpenStreetMap si no la traduce).
STORE_KINDS = frozenset(
    {
        "tienda por departamentos",
        "centro comercial",
        "tienda de cómputo",
        "video_shop",
        "tienda",
    }
)


@dataclass(frozen=True, slots=True)
class Store:
    """Una tienda: su id de OpenStreetMap, su nombre, su rubro y su ubicación."""

    store_id: int
    name: str
    kind: str
    latitude: float
    longitude: float

    @property
    def location(self) -> str:
        """La ubicación como la carga el gestor en una columna `POINT`."""
        return (
            f"POINT({self.latitude:.{COORDINATE_DECIMALS}f}, "
            f"{self.longitude:.{COORDINATE_DECIMALS}f})"
        )


@dataclass(frozen=True, slots=True)
class InventorySettings:
    """`products_per_store` productos distintos por tienda, de 1 a `max_quantity` unidades."""

    seed: int
    products_per_store: int
    max_quantity: int


@dataclass(frozen=True, slots=True)
class StockEntry:
    store_id: int
    product_id: str
    quantity: int


def read_places(path: Path) -> Iterator[Store]:
    """Los lugares del CSV de `demos.descargar_lugares` (`id, nombre, rubro, ubicacion`).

    Raises:
        PlacesFormatError: si faltan columnas o una ubicación no es `POINT(lat, lon)`.
    """
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or not set(PLACE_COLUMNS) <= set(reader.fieldnames):
            raise PlacesFormatError(f"{path} no tiene las columnas {', '.join(PLACE_COLUMNS)}")
        for row in reader:
            yield _store_of(row)


def select_stores(places: Iterable[Store], kinds: frozenset[str]) -> list[Store]:
    """Las tiendas con nombre de los rubros `kinds`, ordenadas por id y sin repetir id."""
    chosen: dict[int, Store] = {}
    for place in places:
        if place.kind in kinds and place.name.strip():
            chosen.setdefault(place.store_id, place)
    return [chosen[identifier] for identifier in sorted(chosen)]


def generate_inventory(
    stores: Sequence[Store], popularity: Mapping[str, int], settings: InventorySettings
) -> Iterator[StockEntry]:
    """El stock de cada tienda, por tienda y producto.

    `popularity` trae las compras de entrenamiento de cada producto del catálogo, también
    los que tienen cero.
    """
    products = sorted(popularity)
    sample_size = min(settings.products_per_store, len(products))
    for store in stores:
        generator = random.Random(f"{settings.seed}:{store.store_id}")
        stocked = _weighted_sample(generator, products, popularity, sample_size)
        for product_id in sorted(stocked):
            quantity = generator.randint(1, settings.max_quantity)
            yield StockEntry(store.store_id, product_id, quantity)


def _weighted_sample(
    generator: random.Random, products: list[str], popularity: Mapping[str, int], size: int
) -> list[str]:
    """Muestra sin reemplazo con probabilidad proporcional a `popularidad + 1`
    (Efraimidis y Spirakis, 2006): cada producto recibe la clave `log(u) / peso` y se
    quedan las `size` mayores."""
    keyed = (
        (math.log(1.0 - generator.random()) / (popularity[product] + 1), product)
        for product in products
    )
    return [product for _, product in heapq.nlargest(size, keyed)]


def _store_of(row: Mapping[str, str]) -> Store:
    match = LOCATION_PATTERN.fullmatch(row["ubicacion"].strip())
    if match is None:
        raise PlacesFormatError(f"ubicación inesperada {row['ubicacion']!r}")
    try:
        identifier = int(row["id"])
    except ValueError as error:
        raise PlacesFormatError(f"id inesperado {row['id']!r}") from error
    return Store(
        store_id=identifier,
        name=" ".join(row["nombre"].split()),
        kind=row["rubro"],
        latitude=float(match.group(1)),
        longitude=float(match.group(2)),
    )
