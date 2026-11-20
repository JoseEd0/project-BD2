# `appkit`: lo común de cada servicio

Todo servicio de la Parte 5 arranca, se configura, registra, responde errores y se deja
sondear igual. Eso vive aquí y no copiado en cada app:

| Módulo | Qué da |
|---|---|
| [`settings.py`](src/appkit/settings.py) | `ServiceSettings`: configuración del entorno con el prefijo del servicio; `load_settings` falla al arrancar nombrando cada variable que falta o no vale |
| [`logs.py`](src/appkit/logs.py) | logs JSON con la forma de los del gestor y una línea de acceso por petición |
| [`tracing.py`](src/appkit/tracing.py) | middleware de `X-Request-ID` y `Server-Timing`; `timed` y `record_stage` para medir etapas |
| [`errors.py`](src/appkit/errors.py) | `ServiceError` y sus subclases; todo error responde `{error, kind, request_id}` |
| [`engine.py`](src/appkit/engine.py) | el `AsyncClient` del gestor armado con la configuración; la dependencia `Engine` |
| [`health.py`](src/appkit/health.py) | `/health` y `/ready`, que pregunta al gestor y guarda la respuesta unos segundos |
| [`cache.py`](src/appkit/cache.py) | `OptionalRedis`: la caché en Redis de la que el servicio puede prescindir (*fail-open*) |
| [`metrics.py`](src/appkit/metrics.py) | `GET /metrics` para Prometheus: peticiones y latencia por ruta, búsquedas en la caché y CPU |
| [`migrations.py`](src/appkit/migrations.py) | migraciones repetibles al arrancar |
| [`service.py`](src/appkit/service.py) | `create_service_app`: la aplicación FastAPI con todo lo anterior puesto |
| [`download.py`](src/appkit/download.py) | descargas verificadas de modelos y datasets (ver [`apps/README.md`](../README.md)) |
| [`ranges.py`](src/appkit/ranges.py) | un archivo remoto leído por rangos HTTP: de un `.zip` de gigabytes, solo su índice y los miembros pedidos |

## Un servicio en veinte líneas

```python
# apps/shop/src/shop/api/main.py
from fastapi import APIRouter, FastAPI
from pydantic_settings import SettingsConfigDict

from appkit import (Engine, ResourceNotFoundError, ServiceSettings, configure_logging,
                    create_service_app, load_settings, timed)

VERSION = "0.1.0"
SCHEMA = (
    "CREATE TABLE IF NOT EXISTS shop_productos (id INT PRIMARY KEY, titulo TEXT, emb VECTOR(384))",
    "CREATE INDEX IF NOT EXISTS shop_productos_emb ON shop_productos USING HNSW (emb)",
)


class ShopSettings(ServiceSettings):
    model_config = SettingsConfigDict(env_prefix="SHOP_", frozen=True, extra="ignore")


router = APIRouter()


@router.get("/productos/{producto}")
async def producto(producto: int, gestor: Engine) -> dict[str, object]:
    with timed("gestor"):
        fila = await gestor.fetch_one("SELECT * FROM shop_productos WHERE id = $1", [producto])
    if fila is None:
        raise ResourceNotFoundError(f"no hay un producto {producto}")
    return fila


def app_from_environment() -> FastAPI:
    settings = load_settings(ShopSettings)
    configure_logging(settings.log_level)
    app = create_service_app(settings, name="shop", version=VERSION, migrations=SCHEMA)
    app.include_router(router)
    return app
```

```bash
uvicorn --factory shop.api.main:app_from_environment --host 0.0.0.0 --port 8000
```

## Configuración

Cada servicio hereda de `ServiceSettings` y fija su `env_prefix`; `load_settings` se niega
a leer una clase sin prefijo, para que dos servicios no compartan variables por descuido.

| Variable (con el prefijo, p. ej. `SHOP_`) | Para qué | Por defecto |
|---|---|---|
| `ENGINE_URL` | dirección del API del gestor, p. ej. `http://engine:8000` | **obligatoria** |
| `ENGINE_KEY` | clave del servicio en el gestor (`Authorization: Bearer`) | **obligatoria** |
| `MIGRATION_KEY` | clave con que corren las migraciones; ver abajo | la de `ENGINE_KEY` |
| `LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR` o `CRITICAL`, en cualquier caja | `INFO` |
| `ENGINE_CONNECT_TIMEOUT_SECONDS` | plazo para conectar con el gestor | `5` |
| `ENGINE_READ_TIMEOUT_SECONDS` | plazo de una respuesta (más que el `MINIGESTOR_STATEMENT_TIMEOUT` del gestor) | `45` |
| `ENGINE_LOAD_TIMEOUT_SECONDS` | plazo de una carga masiva | `600` |
| `ENGINE_RETRY_ATTEMPTS` | intentos de una lectura sin respuesta | `3` |
| `ENGINE_MAX_CONNECTIONS` | conexiones a la vez con el gestor por réplica | `20` |
| `READY_CACHE_SECONDS` | cuánto vale la última respuesta del gestor a `/ready` | `2` |
| `READY_TIMEOUT_SECONDS` | plazo de esa pregunta | `2` |

Con una variable que falta o no vale, el servicio no arranca:

```
appkit.settings.SettingsError: configuración de ShopSettings inválida: falta SHOP_ENGINE_URL;
SHOP_LOG_LEVEL: Input should be 'DEBUG', 'INFO', 'WARNING', 'ERROR' or 'CRITICAL'
```

Las claves son `SecretStr`: no salen en un `repr` ni en un log.

## Trazas y logs

`RequestTracingMiddleware` (lo pone `create_service_app`) toma el `X-Request-ID` de la
petición —el que generó la interfaz con el kit web o el nginx de la app— si es válido, o
genera uno; lo deja en la variable de contexto de `minigestor_client`, así **cada llamada al
gestor durante la petición lleva el mismo id**, y lo devuelve en la respuesta. Una petición
se sigue de punta a punta buscando su id en `docker compose logs`.

`Server-Timing` lleva las etapas que el servicio mide y el total:

```python
with timed("embedding"):
    vector = modelo.embed(texto)
record_stage("gestor", resultado.server_timing["total"])
```

`configure_logging(nivel)` manda todos los logs del proceso, uvicorn incluido, en JSON a la
salida estándar, con los mismos campos que el gestor (`src/api/logs.py`: `time`, `level`,
`logger`, `message`, `request_id` y, en la línea de acceso, `method`, `path`, `status`,
`service`, `duration_ms`). En el gestor `service` es quien llamó; aquí, el servicio que
atendió. Las sondas que responden bien van en `DEBUG`. El formateador repite el del gestor
porque `apps/` no puede importar `src/`.

## Errores

Toda respuesta de error es `{"error": "…", "kind": "…", "request_id": "…"}` con su estado:

- **del servicio**: `raise ResourceNotFoundError("…")` (404), `BadRequestError` (400),
  `ResourceConflictError` (409), o una subclase propia de `ServiceError` con su `status`;
- **de validación** de FastAPI: `422`, `RequestValidationError`; una ruta que no existe,
  `404` `HTTPError`;
- **del gestor**, si la ruta no los atiende:

  | Error del cliente | Estado | `kind` |
  |---|---|---|
  | sin respuesta, `503` u otro `5xx` | `503` | `EngineUnavailableError` |
  | `401`/`403` (clave mal configurada) | `500` | `EngineAccessError` |
  | `SqlSyntaxError` (el SQL es del servicio) | `500` | `ServiceError` |
  | `StatementTimeoutError` | `504` | el del gestor |
  | `NotFoundError` / `ConflictError` | `404` / `409` | el del gestor |
  | otro `DomainError` | `400` | el del gestor |

  En los `5xx` el mensaje del gestor queda en el log y no se muestra a quien llamó;

- **cualquier otra excepción**: `500` `ServiceError`, con la traza en el log y su
  `X-Request-ID` en la respuesta.

## Salud

- `GET /health`: `{"status": "ok", "version": "…"}` mientras el proceso vive. No toca el
  gestor: es el healthcheck de Docker.
- `GET /ready`: `{"status": "ready"}` si el gestor responde que está listo; si no, `503` con
  `{"status": "unavailable", "reason": "…"}`. Pregunta una vez, sin reintentos, con plazo
  `READY_TIMEOUT_SECONDS`, y guarda la respuesta `READY_CACHE_SECONDS`: las sondas de todas
  las réplicas no se vuelven una consulta al gestor cada una.

Son los mismos nombres que usa el gestor y que consulta el kit web (`apps/web-kit`).

Si una pieza opcional del servicio falta —las que recibe `create_service_app(...,
degradables=[...])`, como su caché—, las dos respuestas lo dicen sin cambiar su estado, porque
el servicio sigue atendiendo: `{"status": "ok", "version": "…", "degraded": {"redis": "sin
caché: Redis no responde (…)"}}`.

## Métricas

`GET /metrics` responde en el formato de texto de Prometheus, sin clave y fuera del contrato
OpenAPI. El nginx de la app no la reenvía (`/api/metrics` es un `404`): la lee Prometheus por
la red de la app, réplica por réplica (perfil `observability`, ver
[`deploy/observability/`](../../deploy/observability/README.md)). Son los mismos nombres que
las del gestor:

| Métrica | Etiquetas | Qué cuenta |
|---|---|---|
| `http_requests_total` | `method`, `route`, `status` | peticiones respondidas |
| `http_request_duration_seconds` | `method`, `route` | histograma del tiempo de respuesta, de 5 ms a 30 s |
| `cache_lookups_total` | `cache`, `result` | búsquedas en la caché: `hit`, `miss` o `bypass` (sin Redis ahora) |
| `process_cpu_seconds_total`, `process_resident_memory_bytes` | | CPU y memoria de la réplica |

`route` es la plantilla (`/productos/{product_id}`), no la URL, para que el número de series
no crezca con los ids; una ruta que no existe cuenta como `unmatched`. El middleware de trazas
cuenta cada petición al terminar, con un contador y un histograma en memoria: no hace E/S. Un
servicio cuenta su caché con `count_cache_lookup("recomendaciones", CACHE_HIT)`.

## Caché en Redis

`OptionalRedis` es el Redis de una réplica que el servicio puede perder sin dejar de
responder. `redis_factory(url, timeout_seconds)` crea clientes sin reintentos y con un plazo
corto: si Redis se cae, una petición no espera más que ese plazo. Quien la usa pide
`client()` —`None` si no hay caché o si falló hace poco—, atrapa `RedisError` y avisa con
`failed(error)` o `succeeded()`:

- el primer fallo de una racha va al log una sola vez (`WARNING`) y abre una ventana de
  `retry_seconds` en la que `client()` da `None`, para no pagar el plazo en cada petición;
- pasada la ventana se vuelve a probar, y al responder se avisa que volvió (`INFO`);
- mientras tanto, `degradation()` da el motivo que muestran `/health` y `/ready`.

Sin URL configurada el servicio corre sin caché y no está degradado: es su configuración. El
cliente se abre con `async with cache.connected():` en el `lifespan` del servicio, porque
queda atado al bucle de eventos que lo usa. Lo usa el reconocimiento en vivo de rostros
([`faces`](../faces/README.md)); los tests lo prueban con `fakeredis` y con un puerto donde
no escucha nadie.

## Migraciones

`create_service_app(..., migrations=SCHEMA)` aplica en orden, en cada arranque, la lista de
sentencias del servicio. Solo admite `CREATE TABLE IF NOT EXISTS` y
`CREATE INDEX IF NOT EXISTS` (otra cosa es `NonRepeatableMigrationError` antes de arrancar):
el gestor omite lo que ya existe igual y lanza `DefinitionMismatchError` si existe con otra
definición. Entonces el servicio **no arranca** (`MigrationError` con la posición, la causa y
la sentencia), en lugar de fallar después en la primera carga. Un cambio de esquema se hace
con una sentencia nueva, o borrando y recreando a mano lo que cambió.

**Claves.** El DDL pide rol `admin` en el gestor. Lo prudente es que el servicio atienda con
una clave `read` o `write` (`ENGINE_KEY`) y migre con otra `admin` (`MIGRATION_KEY`), que
solo se usa al arrancar y se cierra enseguida. Sin `MIGRATION_KEY`, las migraciones usan
`ENGINE_KEY`, que entonces necesita `admin`; si no lo tiene, el arranque falla diciendo que
falta la clave de migraciones. Las tablas de cada servicio llevan su prefijo (`cr_`,
`shop_`, `rag_`, `face_`).

## Arranque y apagado

El `lifespan` de `create_service_app` abre el cliente del gestor, aplica las migraciones,
corre el `lifespan` propio del servicio si se le pasa uno (con el cliente ya abierto) y, al
apagarse, cierra el del servicio y después el pool de conexiones. Las rutas reciben el
cliente con `gestor: Engine`.

## Tests

```bash
uv run pytest apps/kit -q          # contra un gestor de mentira: kit_fakes.FakeEngine
uv run pytest tests/kit -q         # el servicio de ejemplo contra el gestor real
```

[`tests/kit_example.py`](tests/kit_example.py) es un servicio mínimo hecho con el kit, que
solo existe para los tests: salud, preparación, errores, migraciones y el `X-Request-ID` que
llega al gestor. [`tests/kit/test_service_kit_end_to_end.py`](../../tests/kit/test_service_kit_end_to_end.py)
lo conecta al gestor real en el mismo proceso, con una clave `write` para atender y otra
`admin` para migrar, y comprueba que una petición deja el mismo id en el log de los dos.
