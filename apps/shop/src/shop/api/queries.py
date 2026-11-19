"""El SQL del API de la tienda: siempre con parámetros, nunca con el texto del usuario dentro."""

from __future__ import annotations

from shop.catalog.queries import PRODUCT_COLUMNS
from shop.catalog.schema import PRODUCTS, STORES

PRODUCT_BY_ID = f"SELECT {PRODUCT_COLUMNS}, descripcion FROM {PRODUCTS} WHERE id = $1"
TEXT_SEARCH = (
    f"SELECT {PRODUCT_COLUMNS}, SCORE() AS puntaje FROM {PRODUCTS} "
    "WHERE MATCH(texto, $1) USING BM25 ORDER BY puntaje DESC LIMIT $2"
)
NEAREST_STORES = (
    f"SELECT id, nombre, tipo, ubicacion, distancia(ubicacion, $1) AS metros FROM {STORES} "
    "ORDER BY metros LIMIT $2"
)
