# API REST

Capa delgada sobre el motor: traduce HTTP a sentencias y devuelve JSON. **No contiene
lógica de base de datos**; si algo hay que decidir sobre datos, lo decide el motor.

## Arrancar

```bash
.venv/bin/pip install -e .
cp .env.example .env
.venv/bin/uvicorn --factory api.main:app_from_environment --env-file .env --reload --port 8000
```

Documentación interactiva generada por FastAPI en `http://localhost:8000/docs`.

| Variable de entorno | Para qué | Por defecto |
|---|---|---|
| `MINIGESTOR_DATA_DIR` | dónde viven los archivos del gestor; vacía es un error, no el directorio actual | `data` |
| `MINIGESTOR_IMPORT_DIRS` | otros directorios de los que `FROM FILE` puede leer, separados por comas | ninguno |
| `MINIGESTOR_CORS_ORIGINS` | orígenes desde los que llama el frontend, separados por comas | **obligatoria** |
| `MINIGESTOR_LOCK_TIMEOUT` | segundos que una transacción espera un bloqueo; un número finito | `5` |

Están recogidas en [`.env.example`](../../.env.example), en la raíz del repositorio.

Ninguna ruta ni puerto está escrito en el código.

## Organización

| Módulo | Qué hace |
|---|---|
| [`main.py`](main.py) | `create_app`: arma la aplicación; uvicorn la llama con `--factory` |
| [`settings.py`](settings.py) | configuración leída de las variables de entorno |
| [`service.py`](service.py) | estado compartido: motor, bloqueos y sesiones |
| [`routes/`](routes/) | un router por dominio: tablas, consultas, subidas y sesiones |
| [`responses.py`](responses.py) | del resultado del motor al JSON de la respuesta |
| [`errors.py`](errors.py) | la forma única de toda respuesta de error |
| [`schemas.py`](schemas.py) | forma de cada mensaje que entra y sale |

## Rutas

| Método | Ruta | Para qué |
|---|---|---|
| `GET` | `/health` | comprobar que el servidor responde |
| `GET` | `/tables` | panel de archivos: tablas, columnas, tipos, índices y número de filas |
| `POST` | `/query` | ejecuta un script y devuelve filas, mensajes y plan |
| `POST` | `/tables/upload` | crea una tabla a partir de un CSV subido |
| `POST` | `/files/upload` | solo guarda el CSV; devuelve su ruta y su cabecera para usarla en `CREATE TABLE ... FROM FILE` |
| `GET` | `/tables/{name}/structure` | estructura física: páginas del archivo, niveles del B+, profundidad global y cubetas del hash, MBR de cada nodo del R-Tree, archivo de valores TEXT |
| `GET` | `/tables/{name}/points` | panel de mapa: los puntos de una columna `POINT` |
| `DELETE` | `/tables` | borra todas las tablas y sus archivos (dejar la BD vacía) |
| `POST` | `/sessions/{id}/close` | cierra una sesión y aborta lo que tuviera abierto; es `POST` para que el navegador lo envíe con `sendBeacon` al cerrar la pestaña |

### Sesiones y transacciones

`POST /query` acepta un `session_id` opcional:

- **sin él**, la sentencia va en autocommit, y un `BEGIN` se rechaza con `400`: no habría
  dónde guardar la transacción hasta la siguiente petición;
- **con él**, el servidor recuerda la sesión entre peticiones, así que `BEGIN … COMMIT`
  funciona a través de varias llamadas. Eso es lo que permite demostrar transacciones desde
  la interfaz.

Dos peticiones con el mismo `session_id` se atienden una tras otra. Una sesión que pasa
`EngineConfig.session_idle_seconds` (15 minutos) sin usarse se cierra y aborta su
transacción: una pestaña cerrada no deja tablas bloqueadas. Si cerrar una de ellas falla,
las demás se cierran igual. `POST /sessions/{id}/close` responde enseguida aunque la sesión
tenga una petición en curso: el cierre lo hace la última que termine, para que ninguna
abra una transacción en una sesión que el servidor ya olvidó. Una tabla que otro cliente
borra mientras `/tables` las lista simplemente no aparece. `/tables/{name}/structure` y
`/tables/{name}/points` aceptan el mismo `session_id` y leen con bloqueo compartido, así que
esperan a que termine una transacción que esté escribiendo en la tabla. `/tables` no
bloquea: su número de filas es un contador y puede incluir cambios aún no confirmados.

Una consulta devuelve como mucho `EngineConfig.max_result_rows` filas (10 000); `total_rows`
dice cuántas tenía el resultado.

### Scripts de varias sentencias

El cuerpo puede traer varias sentencias separadas por `;`. Se ejecutan **en orden**; si una
falla, las anteriores ya quedaron aplicadas, igual que en cualquier gestor fuera de una
transacción. La respuesta trae un resumen por sentencia y, en el nivel superior, **las filas
y el plan de la última consulta**, que es lo que el editor enseña.

```jsonc
// POST /query
{ "sql": "INSERT INTO alumnos VALUES (1, 'Ana'); SELECT * FROM alumnos;",
  "session_id": "ui-a1b2c3" }

// 200 OK
{
  "statements": [
    { "sql": "Insert", "message": "1 fila(s) insertada(s) en 'alumnos'",
      "affected_rows": 1, "elapsed_ms": 0.31, "returned_rows": 0 },
    { "sql": "Select", "message": "", "affected_rows": 1,
      "elapsed_ms": 0.11, "returned_rows": 1 }
  ],
  "columns": ["id", "nombre"],
  "rows": [[1, "Ana"]],
  "plan": { "operation": "Projection", "detail": "id, nombre", "children": [ ... ] },
  "message": "",
  "affected_rows": 1,
  "elapsed_ms": 0.42,
  "in_transaction": false
}
```

### Consultas espaciales y el panel de mapa

Un punto viaja como `{"lat": -12.0464, "lon": -77.0428}`. Cuando la consulta toca una
columna `POINT`, la respuesta de `/query` añade `spatial`: de qué tabla y columna salen los
puntos y las **figuras** de la consulta, que el mapa dibuja encima.

```jsonc
// SELECT * FROM tiendas WHERE distancia(ubicacion, POINT(-12.0464, -77.0428)) < 5000
"spatial": {
  "table": "tiendas",
  "column": "ubicacion",
  "overlays": [
    { "kind": "radius", "center": { "lat": -12.0464, "lon": -77.0428 },
      "radius": 5000.0, "metric": "haversine", "unit": "m", "vertices": [] }
  ]
}
```

| `kind` | De dónde sale | Qué trae |
|---|---|---|
| `radius` | `distancia(col, punto) < r` en el `WHERE` | centro, radio, métrica y unidad |
| `nearest` | `ORDER BY distancia(col, punto)` | el punto de referencia |
| `polygon` | `intersecta(col, POLYGON(…))` en el `WHERE` | los vértices |

Las figuras describen **la consulta, no el plan**: salen igual con índice que sin él.

`GET /tables/{name}/points?column=ubicacion&limit=5000` devuelve los puntos de la tabla
para pintarlos de fondo. Con más filas que `limit` entrega una muestra tomada a intervalos
regulares —no las primeras—, para que no se concentre en una zona. El máximo es
`EngineConfig.map_points`.

### Búsqueda de texto

Cuando el `WHERE` tiene un `MATCH`, la respuesta de `/query` añade `text`: dónde se buscó,
con qué ranking y con qué **términos ya analizados** —plegados, sin stopwords y reducidos a
su raíz—, que la página «Texto» del frontend resalta en los resultados. `k1` y `b` son los
que usó BM25, los del `WITH` o los de la configuración; con TF-IDF van en `null`.

```jsonc
// SELECT *, SCORE() FROM catalogo WHERE MATCH(texto, 'auriculares inalámbricos')
"text": {
  "table": "catalogo",
  "column": "texto",
  "query": "auriculares inalámbricos",
  "method": "BM25",
  "terms": ["auricular", "inalambr"],
  "k1": 1.2,
  "b": 0.75
}
```

La búsqueda va entera por `/query`: no hay una ruta aparte que repita lo que el motor ya
hace con SQL.

### Carga de CSV

`POST /tables/upload` recibe un formulario *multipart*:

| Campo | Qué es |
|---|---|
| `file` | el CSV, con cabecera; los tipos se deducen leyendo el archivo entero. Una celda `POINT(lat, lon)` hace de su columna una columna `POINT` |
| `name` | nombre de la tabla; solo se admite un identificador |
| `organization` | `heap`, `sequential` o `clustered_btree` |
| `key_column` | columna clave: obligatoria en `sequential` y `clustered_btree`; opcional en `heap`, donde recibe un índice hash |
| `session_id` | sesión, igual que en `/query` |

La sentencia se construye como objeto, **no concatenando texto SQL**: un nombre de tabla
malicioso no puede inyectar nada. La carga es atómica: si una fila falla —una clave
repetida, una columna clave que no está en la cabecera— la tabla no queda creada.

Una subida que anuncia en `Content-Length` más de `max_upload_bytes` (con un margen para
el envoltorio *multipart*) se rechaza con `413` antes de recibir el cuerpo. Si no lo
anuncia, el archivo se copia por bloques, sin tenerlo entero en memoria, y se corta con el
mismo `413` en cuanto pasa del límite; cada subida escribe en su propio temporal, así que
dos con el mismo nombre a la vez no se mezclan. En `/tables/upload` el CSV vive en un directorio propio de la
petición y se borra al terminar, salga bien o no: los datos ya están en la tabla. En
`/files/upload` se guarda en `uploads/` y reemplaza al anterior del mismo nombre. Las dos
rutas son síncronas a propósito: crear la tabla puede esperar un bloqueo, y esa espera
dentro del event loop congelaría todo el servidor.

`CREATE TABLE ... FROM FILE` solo lee archivos del directorio de datos o de los
`MINIGESTOR_IMPORT_DIRS`; cualquier otra ruta da `ForbiddenFileError`. Sin ese límite,
quien pudiera enviar una consulta leería cualquier CSV del servidor.

### Errores

Los errores de SQL devuelven `400` con la posición cuando el parser la conoce, que es lo
que el editor del frontend necesita para señalar dónde está el problema:

```jsonc
{ "detail": { "error": "se esperaba una sentencia, se encontró 'FROM'",
              "kind": "SqlSyntaxError", "line": 1, "column": 1,
              "in_transaction": false } }
```

`in_transaction` dice cómo quedó la sesión tras el error: un interbloqueo o una espera
agotada abortan la transacción, y el cliente tiene que saberlo para no seguir creyéndola
abierta. Es `null` cuando el error no pasó por una sesión.

Solo las excepciones **de dominio** —errores del SQL, del catálogo, del almacenamiento, de
la carga de archivos o de bloqueos— se devuelven como `400`: son culpa de lo que se pidió.
Todas derivan de una misma clase, `DomainError` ([`src/errors.py`](../errors.py)), así que
un módulo nuevo no tiene que anotarse en ninguna lista para que sus errores lleguen bien.
Un cuerpo que no cumple el esquema de la ruta es un `422`, con la misma forma de `detail`.
Un archivo más grande que el límite es un `413`. Cualquier otra excepción es un fallo del
servidor y llega como `500`, con la misma forma, la clase de la excepción en `kind` y la
traza en el log; convertirla en un `400` escondería el bug detrás de un «error del
usuario». El middleware de CORS envuelve a todos los demás, así que también los `413` y
`500` llevan sus cabeceras: sin ellas el navegador descartaría la respuesta y el frontend
solo vería un fallo de red.

## Tests

```bash
.venv/bin/python -m pytest tests/api -q
```

Cubren las cuatro rutas, la estructura que consume el panel de archivos, el plan devuelto
en un `SELECT`, los errores con posición y el ciclo completo de una transacción a través de
varias peticiones (incluido el rollback al cerrar la sesión).
