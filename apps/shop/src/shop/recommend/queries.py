"""El SQL del recomendador: una consulta del gestor por señal, la de los pares materializados
y la de registrar una compra. Todo valor viaja como parámetro."""

from __future__ import annotations

from shop.catalog.queries import FIRST_PARAMETER, placeholders
from shop.catalog.schema import (
    CLIP,
    COPURCHASES,
    HISTORY,
    INVENTORY,
    MATERIALIZATIONS,
    PRODUCTS,
    PURCHASES,
    STORES,
    USERS,
)

PRODUCT_TO_RECOMMEND_FROM = f"SELECT id, titulo, imagen FROM {PRODUCTS} WHERE id = $1"
USER_BY_ID = f"SELECT id FROM {USERS} WHERE id = $1"
USER_HISTORY = (
    f"SELECT producto, instante FROM {HISTORY} WHERE usuario = $1 AND particion = $2"
)

COPURCHASES_OF_PRODUCT = (
    f"SELECT otro, veces FROM {COPURCHASES} WHERE producto = $1 AND particion = $2 "
    "ORDER BY veces DESC, otro LIMIT $3"
)
CLIP_EMBEDDING = f"SELECT embedding FROM {CLIP} WHERE id = $1"
SIFT_SEARCH = (
    f"SELECT id, SIMILARITY_SCORE() AS puntaje FROM {PRODUCTS} "
    "WHERE SIMILAR_TO(imagen, $1, k = $2) USING HNSW"
)
TEXT_NEIGHBOURS = (
    f"SELECT id, SCORE() AS puntaje FROM {PRODUCTS} "
    "WHERE MATCH(texto, $1) USING BM25 ORDER BY puntaje DESC LIMIT $2"
)

LAST_PURCHASE_FROM = f"SELECT MAX(id) FROM {PURCHASES} WHERE id >= $1"
INSERT_PURCHASE = (
    f"INSERT INTO {PURCHASES} (id, usuario, producto, valoracion, instante, fecha, particion) "
    "VALUES ($1, $2, $3, NULL, $4, $5, $6)"
)
INSERT_HISTORY = (
    f"INSERT INTO {HISTORY} (usuario, producto, instante, particion) VALUES ($1, $2, $3, $4)"
)
COUNT_USER_PURCHASE = f"UPDATE {USERS} SET compras = compras + 1 WHERE id = $1"
PRODUCT_EXISTS = f"SELECT id FROM {PRODUCTS} WHERE id = $1"
PARTNERS_OF_PRODUCT = f"SELECT otro FROM {COPURCHASES} WHERE producto = $1 AND particion = $2"
PAIR_COLUMNS = 4

MATERIALIZATION_OF = f"SELECT particion, soporte, filas FROM {MATERIALIZATIONS} WHERE tabla = $1"
RECORD_MATERIALIZATION = f"INSERT INTO {MATERIALIZATIONS} VALUES ($1, $2, $3, $4)"
FORGET_MATERIALIZATION = f"DELETE FROM {MATERIALIZATIONS} WHERE tabla = $1"
DROP_COPURCHASES = f"DROP TABLE IF EXISTS {COPURCHASES}"

# Los parámetros fijos van primero y la lista de ids al final: así el número de ids no mueve
# a los demás.
LIST_AFTER_TWO = 3


def copurchases_of_products(count: int) -> str:
    """Compras conjuntas de varios productos a la vez (las de un usuario), sumadas por
    candidato: `$1` partición, `$2` límite, `$3…` los productos."""
    return (
        f"SELECT otro, SUM(veces) AS clientes, COUNT(*) AS compras FROM {COPURCHASES} "
        f"WHERE producto IN ({placeholders(count, LIST_AFTER_TWO)}) AND particion = $1 "
        "GROUP BY otro ORDER BY clientes DESC, otro LIMIT $2"
    )


def clip_embeddings(count: int) -> str:
    return f"SELECT id, embedding FROM {CLIP} WHERE id IN ({placeholders(count)})"


def products_to_recommend_from(count: int) -> str:
    return f"SELECT id, titulo, imagen FROM {PRODUCTS} WHERE id IN ({placeholders(count)})"


def stock_nearby(count: int) -> str:
    """Dónde hay stock de los candidatos cerca: `$1` el punto, `$2` el radio en metros,
    `$3…` los productos.

    El inventario va primero en el `JOIN`: el planificador elige el camino de acceso de cada
    tabla por su condición, así que el inventario entra por su hash (`producto IN`) y las
    tiendas por el R-Tree (`distancia < radio`). Con las tiendas primero, el inventario se
    recorrería entero (209 000 filas, 2,2 s medidos) para quedarse con unas pocas.
    """
    return (
        f"SELECT i.producto AS producto, t.nombre AS tienda, distancia(t.ubicacion, $1) AS metros "
        f"FROM {INVENTORY} i JOIN {STORES} t ON i.tienda = t.id "
        f"WHERE i.producto IN ({placeholders(count, LIST_AFTER_TWO)}) "
        "AND distancia(t.ubicacion, $1) < $2 ORDER BY metros"
    )


def copurchase_pairs(count: int) -> str:
    """Los pares de `count` productos con los demás que compraron sus mismos clientes, con
    cuántos clientes, desde `$2` clientes: `$1` partición, `$3…` los productos.

    Es el autojoin del historial con el `GROUP BY` por hash externo del gestor. El historial
    de la izquierda entra por su B+ (`producto IN`); el de la derecha se recorre entero.
    """
    return (
        "SELECT a.producto AS producto, b.producto AS otro, COUNT(*) AS veces "
        f"FROM {HISTORY} a JOIN {HISTORY} b ON a.usuario = b.usuario "
        f"WHERE a.producto IN ({placeholders(count, LIST_AFTER_TWO)}) "
        "AND a.particion = $1 AND b.particion = $1 AND b.producto <> a.producto "
        "GROUP BY a.producto, b.producto HAVING COUNT(*) >= $2"
    )


def copurchases_on_the_fly(table: str) -> str:
    """Las compras conjuntas de un producto calculadas en el momento sobre `table` (las
    compras o el historial), sin la tabla materializada: `$1` producto, `$2` partición,
    `$3` límite. Es la alternativa que la materialización evita (ver README)."""
    return (
        f"SELECT b.producto AS otro, COUNT(*) AS veces FROM {table} a "
        f"JOIN {table} b ON a.usuario = b.usuario "
        "WHERE a.producto = $1 AND a.particion = $2 AND b.particion = $2 "
        "AND b.producto <> $1 GROUP BY b.producto ORDER BY veces DESC, otro LIMIT $3"
    )


def increment_pairs_from(count: int) -> str:
    """`$1` el producto comprado, `$2` la partición, `$3…` los que ya formaban par con él."""
    return (
        f"UPDATE {COPURCHASES} SET veces = veces + 1 WHERE producto = $1 AND particion = $2 "
        f"AND otro IN ({placeholders(count, LIST_AFTER_TWO)})"
    )


def increment_pairs_to(count: int) -> str:
    """El par simétrico de `increment_pairs_from`, con los mismos parámetros."""
    return (
        f"UPDATE {COPURCHASES} SET veces = veces + 1 "
        f"WHERE producto IN ({placeholders(count, LIST_AFTER_TWO)}) AND otro = $1 "
        "AND particion = $2"
    )


def insert_pairs(count: int) -> str:
    """`count` pares nuevos, cuatro parámetros por fila: producto, otro, partición, veces."""
    rows = ", ".join(
        f"({placeholders(PAIR_COLUMNS, first)})"
        for first in range(FIRST_PARAMETER, FIRST_PARAMETER + count * PAIR_COLUMNS, PAIR_COLUMNS)
    )
    return f"INSERT INTO {COPURCHASES} (producto, otro, particion, veces) VALUES {rows}"
