"""El SQL del API de la tienda: siempre con parámetros, nunca con el texto del usuario dentro."""

from __future__ import annotations

from shop.catalog.schema import CLIP, PRODUCTS, STORES

PRODUCT_COLUMNS = (
    "id, titulo, marca, departamento, categoria, ruta_categoria, precio, valoracion, "
    "valoraciones, imagen"
)
PRODUCT_BY_ID = f"SELECT {PRODUCT_COLUMNS}, descripcion FROM {PRODUCTS} WHERE id = $1"
TEXT_SEARCH = (
    f"SELECT {PRODUCT_COLUMNS}, SCORE() AS puntaje FROM {PRODUCTS} "
    "WHERE MATCH(texto, $1) USING BM25 ORDER BY puntaje DESC LIMIT $2"
)
CLIP_SEARCH = (
    f"SELECT id, SIMILARITY_SCORE() AS puntaje FROM {CLIP} "
    "WHERE SIMILAR_TO(embedding, $1, k = $2) USING HNSW"
)
NEAREST_STORES = (
    f"SELECT id, nombre, tipo, ubicacion, distancia(ubicacion, $1) AS metros FROM {STORES} "
    "ORDER BY metros LIMIT $2"
)
FIRST_PARAMETER = 1


def products_by_ids(count: int) -> str:
    """Las fichas de `count` productos, con un parámetro por id."""
    placeholders = ", ".join(f"${number}" for number in range(FIRST_PARAMETER, count + 1))
    return f"SELECT {PRODUCT_COLUMNS} FROM {PRODUCTS} WHERE id IN ({placeholders})"
