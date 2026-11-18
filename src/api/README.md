# API REST

Capa delgada sobre el motor: traduce HTTP a sentencias y devuelve JSON. **No contiene
lógica de base de datos**; si algo hay que decidir sobre datos, lo decide el motor.

## Arrancar

```bash
uv sync
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
| `MINIGESTOR_STATEMENT_TIMEOUT` | segundos que puede durar cada sentencia | `30` |
| `MINIGESTOR_MAX_REQUEST_BYTES` | bytes del cuerpo de una petición que no sube un archivo | `8388608` (8 MiB) |
| `MINIGESTOR_API_KEYS` | claves de los servicios, `servicio:rol:sha256` separadas por comas | ninguna: API abierto |
| `MINIGESTOR_LOG_LEVEL` | nivel de los logs JSON | `INFO` |

Están recogidas en [`.env.example`](../../.env.example), en la raíz del repositorio.

Ninguna ruta ni puerto está escrito en el código.

## Organización

| Módulo | Qué hace |
|---|---|
| [`main.py`](main.py) | `create_app`: arma la aplicación y sus middlewares; uvicorn la llama con `--factory` |
| [`settings.py`](settings.py) | configuración leída de las variables de entorno, claves incluidas |
| [`service.py`](service.py) | estado compartido: motor, bloqueos, sesiones, plazos y apagado |
| [`auth.py`](auth.py) | claves por servicio, roles y el rol que pide cada sentencia |
| [`tracing.py`](tracing.py) | `X-Request-ID` y las etapas de `Server-Timing` de la petición en curso |
| [`logs.py`](logs.py) | logs en JSON, una línea por petición |
| [`limits.py`](limits.py) | tamaño máximo del cuerpo, anunciado o enviado por trozos |
| [`routes/`](routes/) | un router por dominio: tablas, consultas, subidas y sesiones |
| [`responses.py`](responses.py) | del resultado del motor al JSON de la respuesta |
| [`errors.py`](errors.py) | la forma única de toda respuesta de error |
| [`schemas.py`](schemas.py) | forma de cada mensaje que entra y sale |

## Rutas

| Método | Ruta | Para qué |
|---|---|---|
| `GET` | `/health` | vivo: el proceso responde (sin clave; es el healthcheck de Docker) |
| `GET` | `/ready` | listo: el catálogo responde y no se está apagando; `503` si no (sin clave) |
| `GET` | `/tables` | panel de archivos: tablas, columnas, tipos, índices y número de filas |
| `POST` | `/query` | ejecuta un script y devuelve filas, mensajes y plan |
| `POST` | `/tables/upload` | crea una tabla a partir de un CSV subido |
| `POST` | `/files/upload` | solo guarda el CSV; devuelve su ruta y su cabecera para usarla en `CREATE TABLE ... FROM FILE` |
| `GET` | `/tables/{name}/structure` | estructura física: páginas del archivo, niveles del B+, profundidad global y cubetas del hash, MBR de cada nodo del R-Tree, archivo de valores TEXT |
| `GET` | `/tables/{name}/points` | panel de mapa: los puntos de una columna `POINT` |
| `DELETE` | `/tables` | borra todas las tablas y sus archivos (dejar la BD vacía) |
| `POST` | `/media/queries` | guarda la imagen o el audio de una consulta y devuelve la ruta que va en `SIMILAR_TO` |
| `POST` | `/media/segments` | corta un audio subido en ventanas y devuelve las `k` filas más parecidas a cada una |
| `GET` | `/media/thumbnail?path=…` | miniatura en JPEG de una imagen, con caché en disco |
| `GET` | `/media/file?path=…` | el archivo tal cual; con `Range`, solo el trozo pedido (`206`), para el reproductor de audio; un `#t=` al final de la ruta se ignora |
| `POST` | `/sessions/{id}/close` | cierra una sesión y aborta lo que tuviera abierto; es `POST` para que el navegador lo envíe con `sendBeacon` al cerrar la pestaña |

### Sesiones y transacciones

`POST /query` acepta un `session_id` opcional:

- **sin él**, la sentencia va en autocommit, y un `BEGIN` se rechaza con `400`: no habría
  dónde guardar la transacción hasta la siguiente petición;
- **con él**, el servidor recuerda la sesión entre peticiones, así que `BEGIN … COMMIT`
  funciona a través de varias llamadas. Eso es lo que permite demostrar transacciones desde
  la interfaz.

Dos peticiones con el mismo `session_id` (del mismo servicio, ver *Claves por servicio y
roles*) se atienden una tras otra. Una sesión que pasa
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

### Parámetros

`POST /query` acepta además `params`, una lista JSON con los valores de `$1`, `$2`, … (por
omisión, vacía). Es como una aplicación envía lo que escribe su usuario sin concatenarlo
al SQL: el valor nunca se lee como SQL, así que `'; DROP TABLE x; --` se guarda o se busca
como un texto más.

```jsonc
// POST /query
{ "sql": "SELECT id, nombre FROM tiendas WHERE distancia(ubicacion, $1) < $2 LIMIT $3",
  "params": [{ "lat": -12.0464, "lon": -77.0428 }, 5000, 10] }
```

| JSON | Valor en el motor |
|---|---|
| número, texto, `true`/`false`, `null` | el literal del mismo tipo |
| lista de números, `[0.1, 0.2]` | un `VECTOR`, igual que el literal `[0.1, 0.2]` |
| `{"lat": …, "lon": …}` | un `POINT`, la misma forma con que vuelve en `rows` |

- Cada valor se comprueba contra la columna o el argumento con que se usa, **antes de leer
  filas** y con los errores de siempre (`ExpressionError` si `id = $1` recibe un texto,
  `TextSearchError` si `MATCH(texto, $1)` recibe un número, `InvalidValueError` si un `INSERT`
  no cabe en su columna).
- **Con `params` no vacío, `sql` es una sola sentencia**: en un script no se sabría si `$1`
  de la segunda sentencia es el mismo valor que el de la primera. Una transacción se arma
  con varias peticiones y el mismo `session_id`.
- Un `$n` sin valor es `400` con `kind` `SqlMissingParameterError`, `line` y `column`; un
  valor que ningún `$n` usa (sobra o se saltó un número), `SqlParameterError`; un valor de
  ningún tipo del dialecto (un objeto que no es un punto, una lista con un texto, un
  `NaN`), `ParameterValueError`.
- `EXPLAIN` muestra el valor enlazado y no `$1`, como el plan personalizado de PostgreSQL:
  el planificador eligió el camino de acceso con ese valor.

La gramática completa está en [`sql/README.md`](../sql/README.md#parámetros-posicionales).

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
su raíz—, que la página «Texto» del frontend resalta en los resultados. De una consulta
booleana (`'funda AND iphone AND NOT silicona'`) solo van los términos positivos: ni los
negados ni los operadores se resaltan. `k1` y `b` son los que usó BM25, los del `WITH` o
los de la configuración; con TF-IDF van en `null`. Una consulta booleana mal formada es un
`400` de tipo `BooleanQueryError`.

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

### Archivos multimedia

La página «Multimedia» los usa para el resultado de un `SIMILAR_TO`:

- **`POST /media/queries`** recibe el archivo de la consulta (*multipart*, campo `file`),
  con el mismo límite de `max_upload_bytes` y `413` que los CSV. Solo acepta imágenes y
  audios por su extensión. Lo guarda en `uploads/consultas/` con un nombre nuevo, para que
  dos consultas `foto.jpg` no se pisen, y borra las más viejas al pasar de
  `max_query_uploads` (100). Devuelve `path`, la ruta para `SIMILAR_TO('…')`, `media`
  (`image` o `audio`) y el nombre original.
- **`GET /media/thumbnail`** reduce la imagen a `thumbnail_side` píxeles (256) y la guarda en
  `.miniaturas/` del directorio de datos, con el tamaño y la fecha del original en la clave:
  pedirla de nuevo no decodifica nada, y si el original cambia se rehace.
- **`GET /media/file`** sirve el archivo con `Range`, que es lo que necesita un `<audio>`
  para saltar a otro segundo. Una ruta con tramo (`pista.mp3#t=10,20`) sirve el archivo
  entero: el tramo lo pone el reproductor, que lo lee al final de la dirección.

Las dos lecturas pasan por el mismo control que `FROM FILE` y que el archivo de un
`SIMILAR_TO`: el archivo tiene que estar en el directorio de datos o en
`MINIGESTOR_IMPORT_DIRS`, y ser una imagen o un audio. Quien puede escribir
`SIMILAR_TO('ruta')` ya puede hacer que el motor lea esa ruta, así que servirla no abre
nada nuevo; un CSV o un archivo fuera de esos directorios da `400`.

#### Consulta por segmentos: `POST /media/segments`

Es la de la detección de copyright: ¿qué tramos del catálogo suenan como cada parte de esta
canción? Recibe el audio (*multipart*, campo `file`, con el límite de `max_upload_bytes`) y
en el formulario:

| Campo | Por omisión | Qué es |
|---|---|---|
| `table` | — | la tabla con la columna de rutas indexada con HNSW o IVF |
| `column` | la única indexada | la columna de las rutas, si hay varias |
| `k` | 5 | filas por ventana |
| `window` | `segment_window_seconds` (10 s) | segundos de cada ventana |
| `hop` | `segment_hop_seconds` (5 s) | segundos entre el inicio de una ventana y el de la siguiente |
| `session_id` | — | para leer dentro de una transacción de la sesión |

El audio se decodifica **una vez**, se corta en las ventanas `[i·hop, i·hop + window]` que
caben enteras y todas se buscan en **un solo lote** del índice. Cada ventana recibe las
mismas filas y puntajes que `SIMILAR_TO('audio#t=inicio,fin', k = k)`. La respuesta trae,
por ventana, `start`, `end` y sus `neighbours`: la fila entera (`row`), la ruta del archivo
sin fragmento (`path`), el tramo que pide la fila (`fragment`, `{start, end}` o `null`) y el
`score`; además la duración del audio, las columnas de la tabla, las distancias calculadas
y lo que tardó cada etapa (`decode_ms`, `extraction_ms`, `search_ms`). El archivo subido solo
vive mientras dura la petición, que lee con bloqueo compartido sobre la tabla y con rol
`read`.

Responde `400` si el archivo no es un audio, si dura más que `max_segment_audio_seconds`
(600 s) o menos que `min_segment_seconds` (1 s), si la ventana es más corta que eso, si
salen más de `max_segments` ventanas (256), si ventanas por `k` pasan de `max_result_rows`
o si la tabla no tiene un único índice de similitud sobre rutas. Un audio más corto que una
ventana es **una sola ventana**, del inicio al final. No hay forma SQL: el `FROM` del
dialecto solo nombra tablas, y la respuesta es una lista de ventanas con sus vecinos, no una
relación (ver [`query/segment_search.py`](../query/segment_search.py)).

Una consulta con `SIMILAR_TO` trae en `similarity` la tabla, la columna de las rutas, el
índice, el método, la métrica, `k`, la ruta de la consulta y si son imágenes o audios: con
eso la interfaz pinta la galería sin adivinar qué columna es un archivo.

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
Un archivo o un cuerpo más grande que el límite es un `413`; sin clave válida, un `401`;
con un rol que no alcanza, un `403`; con el servidor apagándose, un `503` (ver *El gestor
como servicio*). Cualquier otra excepción es un fallo del
servidor y llega como `500`, con la misma forma, la clase de la excepción en `kind` y la
traza en el log; convertirla en un `400` escondería el bug detrás de un «error del
usuario». El middleware de CORS envuelve a todos los demás, así que también los `413` y
`500` llevan sus cabeceras: sin ellas el navegador descartaría la respuesta y el frontend
solo vería un fallo de red.

## El gestor como servicio

En la Parte 5 el gestor es un contenedor aparte y las aplicaciones le hablan solo por HTTP.
Para eso el API sabe quién llama, deja rastro de cada petición, no deja que una consulta
tumbe a las demás y le dice al orquestador si está vivo y listo.

### Claves por servicio y roles

Cada servicio tiene su clave y la envía como `Authorization: Bearer <clave>`. El servidor
guarda solo su SHA-256 en `MINIGESTOR_API_KEYS`, junto al nombre del servicio y su rol:

```bash
clave=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
printf %s "$clave" | shasum -a 256          # el hash va en MINIGESTOR_API_KEYS
MINIGESTOR_API_KEYS=consola:admin:<sha256>,recomendador:read:<sha256>,ventas:write:<sha256>
```

| Rol | Qué permite |
|---|---|
| `read` | `SELECT`, `EXPLAIN`, `BEGIN`/`COMMIT`/`ROLLBACK`, las rutas `GET`, cerrar su sesión, subir el archivo de un `SIMILAR_TO` |
| `write` | además `INSERT`, `UPDATE` y `DELETE` |
| `admin` | además `CREATE` y `DROP`, cargar CSV (`/tables/upload`, `/files/upload`) y vaciar la base (`DELETE /tables`) |

El rol de `POST /query` sale del **AST ya parseado**, no del texto: el script entero tiene
que caber en el rol antes de que corra la primera sentencia, así que `INSERT …; DROP TABLE t`
con una clave `write` no inserta nada. Una sentencia que no se reconozca pide `admin`. Sin
clave o con una que no es de nadie la respuesta es `401` (con `WWW-Authenticate: Bearer`);
con un rol que no alcanza, `403`. Un hash rápido basta porque las claves son aleatorias y
largas, no contraseñas que se puedan adivinar con un diccionario. Sin
`MINIGESTOR_API_KEYS` el API queda **abierto**, para desarrollar, y lo avisa en el log al
arrancar. `/health`, `/ready` y `/docs` no piden clave.

**La consola** no lleva la clave en el bundle: nginx, en el contenedor `web`, añade
`Authorization: Bearer $CONSOLE_API_KEY` a cada petición que reenvía a `/api/`, y reemplaza
la que traiga el navegador. Así también la llevan las peticiones que el navegador hace solo
—el `<img>` de una miniatura, el `sendBeacon` que cierra la sesión—, que no pueden añadir
cabeceras. Con Docker se da en `MINIGESTOR_CONSOLE_KEY` (ver [`.env.example`](../../.env.example))
y su hash va en `MINIGESTOR_API_KEYS`, normalmente con rol `admin`. Quien llega a la interfaz
actúa con la clave de la consola: el puerto de `web` es la puerta de administración y se
publica solo donde deba. Con `npm run dev` la interfaz habla directo con el API, sin nginx,
así que en desarrollo se deja el API sin claves.

**Las sesiones son de quien las abre.** El servidor las guarda por el par (servicio que
llama, `session_id`): dos servicios que usen el mismo identificador tienen transacciones
distintas, y uno que adivine el `session_id` de otro no puede continuar su transacción ni
cerrarla con `POST /sessions/{id}/close`, que solo cierra las del propio servicio. Con el API
abierto todos son `anonymous` y comparten el espacio de sesiones, como antes.

### Trazas: `X-Request-ID`, logs y `Server-Timing`

- **`X-Request-ID`**: si la petición trae uno válido (hasta 128 caracteres entre letras,
  dígitos y `._:-`) se usa ese; si no, se genera uno. Vuelve en la respuesta, también en
  los errores, y va en cada línea de log escrita durante la petición.
- **Logs**: JSON a la salida estándar, con `logging` de la biblioteca estándar; los de
  uvicorn salen por el mismo camino. Una línea por petición:

  ```json
  {"time": "2026-11-17T15:04:05.123456+00:00", "level": "INFO", "logger": "api.access",
   "message": "POST /query 200", "request_id": "3f2a…", "method": "POST", "path": "/query",
   "status": 200, "service": "recomendador", "duration_ms": 4.81}
  ```

  `service` es `-` si la petición no llegó a autenticarse y `anonymous` con el API abierto.
  `/health` y `/ready` que responden bien se registran en `DEBUG`: el healthcheck de Docker
  llama cada 10 s y taparía las demás líneas. Si fallan, salen en `INFO` como cualquier otra.
- **`Server-Timing`**, el estándar que muestran las herramientas del navegador (pestaña
  *Network → Timing*), con las etapas medidas en milisegundos:

  | Métrica | Qué mide |
  |---|---|
  | `parse` | analizar el SQL |
  | `plan` | planificar (`QueryResult.planning_ms`, como el *Planning Time* de PostgreSQL) |
  | `exec` | ejecutar en el motor, sin la planificación |
  | `txn` | lo que la sentencia pasó fuera del motor: esperar bloqueos y escribir al confirmar |
  | `serialize` | convertir el resultado en la respuesta |
  | `total` | la petición entera, en el servidor |

  En un script, cada métrica suma las de todas sus sentencias. Las rutas que no ejecutan
  SQL solo llevan `total`.

### Límites

- **Tiempo por sentencia** (`MINIGESTOR_STATEMENT_TIMEOUT`, 30 s): una sentencia que se
  pasa se corta con `400` de tipo `StatementTimeoutError` y la sesión la deshace como
  cualquier otro error; dentro de una transacción, la transacción sigue abierta. Las demás
  peticiones siguen respondiendo mientras tanto: cada una corre en su hilo y la cortada
  suelta sus bloqueos al terminar. La cancelación es **cooperativa**: un hilo no se puede
  matar sin dejar páginas y bloqueos a medias, así que el ejecutor mira el plazo entre fila
  y fila ([`query/deadline.py`](../query/deadline.py)). La espera de un bloqueo tiene su
  propio plazo (`MINIGESTOR_LOCK_TIMEOUT`), y un `CREATE INDEX`, que no pasa filas por el
  ejecutor, no se corta.
- **Tamaño del cuerpo** (`MINIGESTOR_MAX_REQUEST_BYTES`, 8 MiB): `413` de tipo
  `RequestTooLargeError`. Si el cuerpo lo anuncia en `Content-Length` se rechaza sin
  leerlo; si llega por trozos, se cuenta y se corta en cuanto pasa. Las subidas de archivos
  tienen su propio límite, `max_upload_bytes` (ver *Carga de CSV*).
- **Tamaño del resultado**: viajan como mucho `max_result_rows` filas y `total_rows` dice
  cuántas había (ver *Sesiones y transacciones*).

### Salud y apagado ordenado

`/health` responde mientras el proceso vive; el `HEALTHCHECK` de Docker lo usa. `/ready`
responde `200` solo si el catálogo contesta y el servidor no se está apagando, y `503` si
no: es la que debe mirar quien reparte tráfico.

Al recibir `SIGTERM` (`docker stop`), uvicorn deja de aceptar conexiones y espera
`API_DRAIN_SECONDS` (5 s) a las que siguen abiertas. Después el cierre del servicio deja de
admitir sentencias (`503 ServiceStoppingError`), cancela las que están corriendo
(`503 StatementCancelledError`, deshechas), espera como mucho `shutdown_grace_seconds`
(5 s) a que salgan, cierra las sesiones abortando sus transacciones y cierra el motor, que
escribe en disco tablas e índices. `compose.yaml` le da 20 s antes de matar el proceso.

## Tests

```bash
.venv/bin/python -m pytest tests/api -q
```

Cubren las cuatro rutas, la estructura que consume el panel de archivos, el plan devuelto
en un `SELECT`, los errores con posición y el ciclo completo de una transacción a través de
varias peticiones (incluido el rollback al cerrar la sesión). `test_auth.py` prueba las
claves y los roles; `test_tracing.py`, el `X-Request-ID`, `Server-Timing` y la línea JSON
del log; `test_limits.py`, una consulta que pasa del tiempo máximo mientras otra en paralelo
responde, el cuerpo demasiado grande, `/ready` y el apagado que cancela una consulta en
curso sin perder lo confirmado.
