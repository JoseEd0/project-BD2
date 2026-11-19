"""El SQL del API de la tienda: siempre con parámetros, nunca con el texto del usuario dentro."""

from __future__ import annotations

from shop.catalog.queries import PRODUCT_COLUMNS, placeholders
from shop.catalog.schema import INVENTORY, PRODUCTS, STORES, USERS

PRODUCT_BY_ID = f"SELECT {PRODUCT_COLUMNS}, descripcion FROM {PRODUCTS} WHERE id = $1"
PRODUCT_PHOTO = f"SELECT imagen FROM {PRODUCTS} WHERE id = $1"
NEAREST_STORES = (
    f"SELECT id, nombre, tipo, ubicacion, distancia(ubicacion, $1) AS metros FROM {STORES} "
    "ORDER BY metros LIMIT $2"
)
# El inventario va primero en el JOIN, como en la señal de cercanía: entra por su hash
# (`producto = $1`) y no se recorre entero.
STORES_WITH_STOCK = (
    "SELECT t.id AS id, t.nombre AS nombre, t.tipo AS tipo, t.ubicacion AS ubicacion, "
    "i.unidades AS unidades, distancia(t.ubicacion, $2) AS metros "
    f"FROM {INVENTORY} i JOIN {STORES} t ON i.tienda = t.id "
    "WHERE i.producto = $1 ORDER BY metros LIMIT $3"
)
DEPARTMENTS = (
    f"SELECT departamento, COUNT(*) AS productos FROM {PRODUCTS} "
    "WHERE departamento IS NOT NULL GROUP BY departamento HAVING COUNT(*) >= $1 "
    "ORDER BY productos DESC, departamento"
)
FEATURED_USERS = f"SELECT id, compras FROM {USERS} ORDER BY compras DESC, id LIMIT $1"
# Los parámetros fijos de la búsqueda de texto: `$1` la consulta y `$2` el límite.
TEXT_SEARCH_FIXED_PARAMETERS = 2


def text_search(conditions: str) -> str:
    """BM25 sobre el invertido en inglés, con las `conditions` del filtro (desde `$3`)."""
    filtered = f" AND {conditions}" if conditions else ""
    return (
        f"SELECT {PRODUCT_COLUMNS}, SCORE() AS puntaje FROM {PRODUCTS} "
        f"WHERE MATCH(texto, $1){filtered} USING BM25 ORDER BY puntaje DESC LIMIT $2"
    )


def filtered_products_by_ids(count: int, conditions: str) -> str:
    """Las fichas de `count` productos (`$1…$count`) que cumplen `conditions`, cuyos
    parámetros siguen a los ids."""
    filtered = f" AND {conditions}" if conditions else ""
    return (
        f"SELECT {PRODUCT_COLUMNS} FROM {PRODUCTS} "
        f"WHERE id IN ({placeholders(count)}){filtered}"
    )
