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
- `shop_historial`: las mismas compras en un heap con B+ no agrupado sobre `usuario` y sobre
  `producto`, porque el archivo secuencial no admite índices secundarios: lo que compró un
  usuario y quién compró un producto, sin recorrer 511 836 filas.
- `shop_compras_conjuntas`: heap con B+ no agrupado sobre `producto`; cuántos clientes
  compraron cada par de productos, materializado por la carga con el `GROUP BY` del gestor
  (`shop.seed.copurchases`).
- `shop_materializaciones`: con qué partición, soporte y pares por producto se calculó cada
  tabla materializada.

Las columnas con muchas filas por clave (`producto` del historial y de los pares) llevan B+ y
no hash: el hash extendible parte una cubeta por el primer bit que separa dos claves, y con
cientos de filas de una misma clave en la cubeta duplica el directorio una y otra vez (400
claves repetidas 300 veces lo llevan a 2^18 entradas; con los pares del dataset entero llegó
a 4 GB y la carga no terminaba). La clave compuesta del B+, `(valor, dirección)`, no repite.

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
HISTORY = "shop_historial"
COPURCHASES = "shop_compras_conjuntas"
MATERIALIZATIONS = "shop_materializaciones"

PRODUCT_ID_LENGTH = 10
USER_ID_LENGTH = 40
BRAND_LENGTH = 128
DEPARTMENT_LENGTH = 64
CATEGORY_LENGTH = 64
CATEGORY_PATH_LENGTH = 192
SPLIT_LENGTH = 5
STORE_NAME_LENGTH = 64
STORE_KIND_LENGTH = 32
TABLE_NAME_LENGTH = 32
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
HISTORY_USER_INDEX = f"{HISTORY}_usuario"
HISTORY_PRODUCT_INDEX = f"{HISTORY}_producto"
COPURCHASES_INDEX = f"{COPURCHASES}_producto"

COPURCHASES_TABLE = (
    f"CREATE TABLE IF NOT EXISTS {COPURCHASES} ("
    f"producto VARCHAR({PRODUCT_ID_LENGTH}), otro VARCHAR({PRODUCT_ID_LENGTH}), "
    f"particion VARCHAR({SPLIT_LENGTH}), veces INT)"
)
COPURCHASES_INDEX_STATEMENT = (
    f"CREATE INDEX IF NOT EXISTS {COPURCHASES_INDEX} ON {COPURCHASES} USING BTREE (producto)"
)

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
    f"CREATE TABLE IF NOT EXISTS {HISTORY} ("
    f"usuario VARCHAR({USER_ID_LENGTH}), producto VARCHAR({PRODUCT_ID_LENGTH}), "
    f"instante BIGINT, particion VARCHAR({SPLIT_LENGTH}))",
    f"CREATE INDEX IF NOT EXISTS {HISTORY_USER_INDEX} ON {HISTORY} USING BTREE (usuario)",
    f"CREATE INDEX IF NOT EXISTS {HISTORY_PRODUCT_INDEX} ON {HISTORY} USING BTREE (producto)",
    COPURCHASES_TABLE,
    COPURCHASES_INDEX_STATEMENT,
    f"CREATE TABLE IF NOT EXISTS {MATERIALIZATIONS} ("
    f"tabla VARCHAR({TABLE_NAME_LENGTH}) PRIMARY KEY, particion VARCHAR({SPLIT_LENGTH}), "
    "soporte INT, por_producto INT, filas INT)",
)

SIFT_INDEX = (
    f"CREATE INDEX IF NOT EXISTS {SIFT_INDEX_NAME} ON {PRODUCTS} USING HNSW (imagen) "
    f"WITH (metric = '{VECTOR_METRIC}')"
)
