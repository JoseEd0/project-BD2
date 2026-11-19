# `minigestor_client`: el cliente del gestor

Cliente tipado del API REST del gestor, como un driver de base de datos: es lo único con lo
que un servicio de `apps/` habla con la base. Tiene una versión síncrona (`Client`, para
guiones y trabajos de carga) y otra asíncrona (`AsyncClient`, para el API de cada app), con
los mismos métodos y el mismo comportamiento.

```python
from minigestor_client import AsyncClient, Point

async with AsyncClient("http://engine:8000", clave_del_servicio) as gestor:
    tiendas = await gestor.fetch_all(
        "SELECT id, nombre FROM tiendas WHERE distancia(ubicacion, $1) < $2 LIMIT $3",
        [Point(-12.0464, -77.0428), 5000, 10],
    )
```

La dirección y la clave las da quien crea el cliente: el cliente no lee variables de
entorno. En un servicio las pone `appkit` desde su configuración (ver
[`kit/README.md`](../kit/README.md)).

## Consultas

| Método | Devuelve |
|---|---|
| `query(sql, params)` | `QueryResult`: `columns`, `rows`, `total_rows`, `statements`, `plan`, `similarity`, `in_transaction`, `request_id`, `server_timing` |
| `fetch_all(sql, params)` | `list[dict]`, una fila por diccionario; `ResultTruncatedError` si el resultado pasa de `max_result_rows` |
| `fetch_one(sql, params)` | la primera fila o `None` |
| `scalar(sql, params)` | la primera columna de la primera fila o `None` |
| `execute(sql, params)` | las filas afectadas |
| `tables()` | `list[TableInfo]` con columnas e índices |
| `ready()` | `True` si el gestor está listo, `False` si responde `503` |

`params` son los valores de `$1`, `$2`, …, que el gestor nunca lee como SQL. Con parámetros,
`sql` es una sola sentencia.

| Python | Tipo en el gestor |
|---|---|
| `int`, `float`, `str`, `bool`, `None` | el literal del mismo tipo |
| lista o tupla de números, arreglo de numpy | `VECTOR` |
| `Point(lat, lon)` | `POINT` |

numpy no es una dependencia: cualquier objeto con `tolist()` se convierte con él. Un `NaN`,
un infinito o un objeto sin forma JSON lanzan `UnencodableValueError` antes de enviar nada.
En las filas que vuelven, un `POINT` es `{"lat", "lon"}` y un `VECTOR`, una lista.

### Transacciones

```python
with gestor.transaction() as tx:        # async with … en AsyncClient
    tx.execute("UPDATE stock SET unidades = unidades - $1 WHERE id = $2", [1, producto])
    tx.execute("INSERT INTO ventas VALUES ($1, $2)", [venta, producto])
```

Abre una sesión propia (`session_id`), envía `BEGIN TRANSACTION`, y al salir `COMMIT`; si el
bloque lanza, `ROLLBACK`. Al final cierra siempre la sesión, lo que también aborta una
transacción que el `ROLLBACK` no alcanzó. Una sentencia que falla dentro del bloque se deshace
sola y la transacción sigue (`tx.in_transaction` dice cómo quedó, por si un interbloqueo la
abortó).

## Carga masiva

```python
reporte = gestor.bulk_insert("fragmentos", ({"id": i, "emb": vector(i)} for i in ids))
reporte.rows_loaded, reporte.rows_rejected, reporte.rejected   # lotes que no entraron
```

Envía las filas a `POST /tables/{name}/rows` como NDJSON por trozos de unos 64 KiB, pidiendo
cada fila al iterable (síncrono o asíncrono) solo cuando hace falta: una carga de millones de
filas no está nunca en memoria. Devuelve el `LoadReport` del gestor: los lotes rechazados
traen la línea culpable, y tras una interrupción la carga se reanuda en la línea siguiente a
`last_line`. Usa el plazo `Timeouts.load_seconds`, porque el gestor responde al terminar,
tras construir los índices diferidos.

## Multimedia

| Método | Ruta | Para qué |
|---|---|---|
| `upload_media(archivo)` | `POST /media/queries` | sube la consulta; su `path` va en `SIMILAR_TO` |
| `similar_media(archivo, sql, params)` | la anterior y `POST /query` | sube y ejecuta `sql` con la ruta como `$1` y `params` desde `$2` |
| `segments(archivo, tabla, k=, window=, hop=)` | `POST /media/segments` | vecinos de cada ventana de un audio |
| `download(path)`, `thumbnail(path)` | `GET /media/file`, `/media/thumbnail` | el archivo o su miniatura JPEG |
| `stream_file(path, byte_range=)` | `GET /media/file` | el archivo por trozos, para reenviarlo; con `byte_range` (una cabecera `Range`), solo ese trozo, con su estado y sus cabeceras |

```python
fotos = gestor.similar_media(
    Path("consulta.jpg"),
    "SELECT id, SIMILARITY_SCORE() FROM fotos WHERE SIMILAR_TO($1, k = $2)",
    [10],
)
```

El archivo es un `Path` o un `MediaFile(nombre, bytes, tipo)`; el gestor reconoce imagen o
audio por la extensión del nombre.

## Errores

Toda excepción del cliente deriva de `MinigestorError` y lleva el `request_id` de la
petición, para buscarla en los logs del gestor. Las respuestas de error siguen la forma
`{"detail": {"error", "kind", "line", "column", "in_transaction"}}` del gestor:

| Excepción | Cuándo |
|---|---|
| `TransportError` | sin respuesta tras los intentos permitidos (`attempts`) |
| `AuthenticationError` | `401`: falta la clave o no es de nadie |
| `PermissionDeniedError` | `403`: el rol no alcanza (`read` < `write` < `admin`) |
| `ServiceUnavailableError` | `503`: el gestor se apaga |
| `ServerError` | otro `5xx` |
| `SqlSyntaxError` | SQL ilegible; `line`, `column` y `cursor` (la línea con un `^`) |
| `NotFoundError` | `404`, o tabla o índice que no existen |
| `DefinitionMismatchError` | un `CREATE … IF NOT EXISTS` encontró otra definición |
| `ConflictError` | clave repetida, objeto que ya existe, interbloqueo, espera de bloqueo agotada |
| `StatementTimeoutError` | la sentencia pasó del plazo del gestor |
| `DomainError` | cualquier otro error de lo pedido (`400`, `413`, `422`); base de los cinco anteriores |

## Reintentos

Una operación se repite solo si **no llegó respuesta** (conexión rechazada o cortada, plazo
agotado) y si **no cambia nada en el gestor**. Un error que el gestor respondió nunca se
reintenta.

- `query` decide por el SQL (`is_read_only`): se reintenta un script cuyas sentencias son
  todas `SELECT`, `EXPLAIN` o `EXPLAIN ANALYZE SELECT`. No es un parser: quita comentarios y
  literales y mira el verbo de cada sentencia; ante la duda, escribe. Quien llama puede
  decidirlo con `idempotent=True` (una migración `IF NOT EXISTS`) o `idempotent=False`.
- Se reintentan las lecturas de tablas y archivos, la subida de una consulta multimedia, la
  búsqueda por segmentos y el cierre de una sesión.
- Nunca se reintentan `BEGIN`, `COMMIT`, `ROLLBACK`, una escritura ni una carga masiva (su
  iterable ya se consumió). `ready()` pregunta una sola vez: una sonda informa del estado de
  ahora.

`RetryPolicy(attempts, base_delay_seconds, max_delay_seconds)` fija cuántos intentos (3) y
cuánto esperar: entre 0 y `min(max, base · 2ⁿ⁻¹)` al azar («full jitter»), para que las
réplicas que fallaron a la vez no reintenten a la vez. Todos los intentos llevan el mismo
`X-Request-ID`.

## Trazas, plazos y conexiones

- **`X-Request-ID`**: cada petición lleva el de `current_request_id()`, la variable de
  contexto que fija el middleware de `appkit` para la petición que atiende el servicio (o
  `request_id_scope(id)` a mano); fuera de una petición, uno nuevo. El que devolvió el
  gestor queda en `result.request_id` y en cada error.
- **`Server-Timing`**: `result.server_timing` trae los milisegundos de cada etapa del gestor
  (`parse`, `plan`, `exec`, `txn`, `serialize`, `total`).
- **`Timeouts`**: conectar (5 s), recibir y enviar (45 s, más que los 30 s con que el gestor
  corta una sentencia, para que llegue su `StatementTimeoutError`), conseguir una conexión
  del pool (5 s) y la carga masiva (600 s).
- **`PoolLimits`**: conexiones abiertas a la vez (20) y guardadas para reutilizar (10). El
  cliente es uno por proceso y se cierra al apagar (`close()` / `aclose()` o `with`).

## Contrato

El contrato del gestor está versionado en [`openapi/engine.json`](openapi/engine.json) y lo
regenera `uv run python -m tools.export_openapi`; la CI falla si cambió sin versionarse.
[`tests/test_client_openapi.py`](tests/test_client_openapi.py) comprueba que cada ruta que
llama el cliente y cada campo que lee o envía siguen en ese contrato: un cambio del API
rompe la CI del cliente y no una app en producción. Cada modelo de `models.py` nombra en
`ENGINE_SCHEMA` el esquema del que lee sus campos.

## Tests

```bash
uv run pytest apps/client -q     # sin red: SQL, valores, errores, Server-Timing, contrato
uv run pytest tests/client -q    # contra el API real del gestor, en el mismo proceso
```

Los de [`tests/client/`](../../tests/client/) viven en el árbol del gestor porque construyen
su aplicación (`api.main.create_app`), y ningún archivo de `apps/` puede importar el motor.
No hay respuestas simuladas: el cliente asíncrono habla con la aplicación por
`httpx.ASGITransport`, y el síncrono con uvicorn en un hilo, en un puerto libre, para que
el envío por trozos de la carga masiva, el pool y una conexión rechazada sean de verdad
(el transporte de prueba de Starlette es privado y junta el cuerpo entero antes de
entregarlo). Los reintentos se prueban con un transporte que rechaza las primeras
conexiones y después entrega a la red real.
