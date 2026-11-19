"""Las tablas de la tienda en el gestor y sus índices, como migraciones repetibles.

Cada tabla usa la organización y los índices que su consulta necesita:

- `shop_productos`: heap con B+ sobre `id`, invertido en inglés sobre `texto` y HNSW de
  SIFT sobre `imagen`: la ficha por id, la búsqueda de texto y el parecido visual con la
  bolsa de palabras de la Parte 4.
- `shop_clip`: heap con B+ sobre `id` y HNSW (coseno) sobre `embedding`: el parecido
  visual con CLIP y la búsqueda de fotos por texto.
- `shop_usuarios`: heap con B+ sobre `id`.
- `shop_compras`: archivo secuencial por `id`, la posición de la compra en orden
  cronológico, para recorrer y cortar las compras por fecha.
- `shop_tiendas`: heap con B+ sobre `id` y R-Tree sobre `ubicacion`: tiendas cercanas.
- `shop_inventario`: heap con hash extendible sobre `producto`: dónde hay un producto.

Los productos no son un B+ agrupado: el gestor solo pone índices secundarios sobre un heap,
y esta tabla necesita tres.

Los índices que el gestor construye de una vez —el invertido, el R-Tree y los HNSW de
`VECTOR`— se crean aquí, con la tabla vacía: la carga masiva los construye al final con
todas las filas (#69). El HNSW de SIFT no puede: entrena su vocabulario con las fotos de la
tabla, así que la carga lo crea después (`SIFT_INDEX`).
"""

from __future__ import annotations

PRODUCTS = "shop_productos"
CLIP = "shop_clip"
USERS = "shop_usuarios"
PURCHASES = "shop_compras"
STORES = "shop_tiendas"
INVENTORY = "shop_inventario"

PRODUCT_ID_LENGTH = 10
USER_ID_LENGTH = 40
BRAND_LENGTH = 128
DEPARTMENT_LENGTH = 64
CATEGORY_LENGTH = 64
CATEGORY_PATH_LENGTH = 192
SPLIT_LENGTH = 5
STORE_NAME_LENGTH = 64
STORE_KIND_LENGTH = 32
CLIP_DIMENSION = 512
# El texto de Amazon está en inglés: con el stemmer y las stopwords en inglés, «strings» y
# «string» son el mismo término y «the» no cuenta.
TEXT_LANGUAGE = "en"
VECTOR_METRIC = "cosine"

TEXT_INDEX = f"{PRODUCTS}_texto"
CLIP_INDEX = f"{CLIP}_embedding"
STORES_INDEX = f"{STORES}_ubicacion"
INVENTORY_INDEX = f"{INVENTORY}_producto"
SIFT_INDEX_NAME = f"{PRODUCTS}_imagen"

SCHEMA: tuple[str, ...] = (
    f"CREATE TABLE IF NOT EXISTS {PRODUCTS} ("
    f"id VARCHAR({PRODUCT_ID_LENGTH}) PRIMARY KEY, titulo TEXT, marca VARCHAR({BRAND_LENGTH}), "
    f"departamento VARCHAR({DEPARTMENT_LENGTH}), categoria VARCHAR({CATEGORY_LENGTH}), "
    f"ruta_categoria VARCHAR({CATEGORY_PATH_LENGTH}), precio DOUBLE, valoracion FLOAT, "
    "valoraciones INT, descripcion TEXT, texto TEXT, imagen TEXT)",
    f"CREATE INDEX IF NOT EXISTS {TEXT_INDEX} ON {PRODUCTS} USING INVERTED (texto) "
    f"WITH (idioma = '{TEXT_LANGUAGE}')",
    f"CREATE TABLE IF NOT EXISTS {CLIP} ("
    f"id VARCHAR({PRODUCT_ID_LENGTH}) PRIMARY KEY, embedding VECTOR({CLIP_DIMENSION}))",
    f"CREATE INDEX IF NOT EXISTS {CLIP_INDEX} ON {CLIP} USING HNSW (embedding) "
    f"WITH (metric = '{VECTOR_METRIC}')",
    f"CREATE TABLE IF NOT EXISTS {USERS} (id VARCHAR({USER_ID_LENGTH}) PRIMARY KEY, compras INT)",
    f"CREATE TABLE IF NOT EXISTS {PURCHASES} ("
    f"id INT PRIMARY KEY INDEX SEQ, usuario VARCHAR({USER_ID_LENGTH}), "
    f"producto VARCHAR({PRODUCT_ID_LENGTH}), valoracion FLOAT, instante BIGINT, fecha DATE, "
    f"particion VARCHAR({SPLIT_LENGTH}))",
    f"CREATE TABLE IF NOT EXISTS {STORES} ("
    f"id BIGINT PRIMARY KEY, nombre VARCHAR({STORE_NAME_LENGTH}), "
    f"tipo VARCHAR({STORE_KIND_LENGTH}), ubicacion POINT)",
    f"CREATE INDEX IF NOT EXISTS {STORES_INDEX} ON {STORES} USING RTREE (ubicacion)",
    f"CREATE TABLE IF NOT EXISTS {INVENTORY} ("
    f"tienda BIGINT, producto VARCHAR({PRODUCT_ID_LENGTH}), unidades INT)",
    f"CREATE INDEX IF NOT EXISTS {INVENTORY_INDEX} ON {INVENTORY} USING HASH (producto)",
)

SIFT_INDEX = (
    f"CREATE INDEX IF NOT EXISTS {SIFT_INDEX_NAME} ON {PRODUCTS} USING HNSW (imagen) "
    f"WITH (metric = '{VECTOR_METRIC}')"
)
