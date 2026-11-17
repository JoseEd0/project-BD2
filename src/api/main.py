"""API REST del minigestor.

Expone lo justo para que el frontend funcione:

| Método | Ruta | Para qué |
|---|---|---|
| `GET` | `/health` | vivo: el proceso responde (sin clave) |
| `GET` | `/ready` | listo: el catálogo responde y no se está apagando (sin clave) |
| `GET` | `/tables` | panel de archivos: tablas, columnas e índices |
| `GET` | `/tables/{name}/structure` | forma física de la tabla y de sus índices |
| `GET` | `/tables/{name}/points` | panel de mapa: puntos de una columna POINT |
| `DELETE` | `/tables` | borra todas las tablas |
| `POST` | `/query` | panel de consultas: ejecuta SQL y devuelve filas, plan y figuras |
| `POST` | `/files/upload` | guarda un CSV para cargarlo después con `FROM FILE` |
| `POST` | `/tables/upload` | crea una tabla a partir de un CSV |
| `POST` | `/sessions/{id}/close` | cierra una sesión y aborta lo que tuviera abierto |

Las sentencias sin `session_id` van en autocommit. Enviando un `session_id` estable, el
cliente puede usar `BEGIN … COMMIT` a través de varias peticiones, que es lo que permite
demostrar transacciones desde la interfaz.

Cada ruta, salvo las de salud, pide una clave `Bearer` con el rol suficiente (`api.auth`).
Cada respuesta lleva `X-Request-ID` y `Server-Timing`, y cada petición deja una línea JSON
en el log (`api.logs`).

`app_from_environment` es la fábrica que uvicorn llama con `--factory`; los tests usan
`create_app` con su propia configuración. Importar este módulo no abre ningún motor.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.auth import Authenticator, Role, ServiceKey, requires
from api.errors import (
    SERVICE_UNAVAILABLE,
    server_failure,
    service_unavailable,
    validation_failed,
)
from api.limits import BodySizeLimit, body_limit_for
from api.logs import HEALTH_PATH, READY_PATH, configure_logging, log_request
from api.routes import media, query, sessions, tables, uploads
from api.service import Service, ServiceStoppingError
from api.settings import settings_from_environment
from api.tracing import (
    REQUEST_ID_HEADER,
    SERVER_TIMING_HEADER,
    RequestTrace,
    Stage,
    request_id_from,
    tracing,
)
from config import EngineConfig
from query.deadline import StatementCancelledError
from query.plan import MILLISECONDS_PER_SECOND

TITLE = "Minigestor de Base de Datos Multimodal"
VERSION = "0.1.0"
EXPOSED_HEADERS = [REQUEST_ID_HEADER, SERVER_TIMING_HEADER]


def app_from_environment() -> FastAPI:
    """La aplicación configurada con las variables de entorno.

    Raises:
        SettingsError: si falta una variable obligatoria.
    """
    settings = settings_from_environment()
    configure_logging(settings.log_level)
    return create_app(settings.config, settings.cors_origins, settings.service_keys)


def create_app(
    config: EngineConfig,
    cors_origins: Sequence[str] = (),
    service_keys: Sequence[ServiceKey] = (),
) -> FastAPI:
    """Construye la aplicación con una configuración explícita, para poder probarla aislada.

    Sin `cors_origins` solo admite peticiones del mismo origen; sin `service_keys`, el API
    queda abierto y lo avisa en el log al arrancar.
    """
    service = Service(config)
    authenticator = Authenticator(service_keys)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if authenticator.is_open:
            logging.getLogger(__name__).warning(
                "sin claves de servicio configuradas: el API está abierto a cualquiera "
                "(solo para desarrollo)"
            )
        yield
        service.close()

    app = FastAPI(title=TITLE, version=VERSION, lifespan=lifespan)
    app.state.service = service
    app.state.authenticator = authenticator
    _add_middleware(app, config, cors_origins)
    app.add_exception_handler(RequestValidationError, validation_failed)
    for stopping in (ServiceStoppingError, StatementCancelledError):
        app.add_exception_handler(stopping, service_unavailable)

    @app.get(HEALTH_PATH)
    def health() -> dict[str, str]:
        return {"status": "ok", "version": VERSION}

    @app.get(READY_PATH)
    def ready() -> JSONResponse:
        if service.is_ready():
            return JSONResponse({"status": "ready"})
        return JSONResponse({"status": "unavailable"}, status_code=SERVICE_UNAVAILABLE)

    for router in (tables.router, query.router, uploads.router, sessions.router, media.router):
        app.include_router(router, dependencies=[requires(Role.READ)])
    return app


def _add_middleware(app: FastAPI, config: EngineConfig, cors_origins: Sequence[str]) -> None:
    """Starlette envuelve con el último middleware que se añade. El de CORS tiene que ser
    ese: una respuesta sin sus cabeceras, aunque sea un 500, el navegador la descarta y el
    frontend solo ve un fallo de red. Justo dentro va la traza, para que también los `413`
    y los `500` lleven su `X-Request-ID` y su línea de log.

    El límite del cuerpo va el más adentro: un middleware `http` de Starlette lee el cuerpo
    dentro de un grupo de tareas, que envolvería su `HTTPException` en un `ExceptionGroup`
    y FastAPI lo tomaría por un cuerpo ilegible."""
    app.add_middleware(BodySizeLimit, limit_for=lambda path: body_limit_for(path, config))

    @app.middleware("http")
    async def answer_unexpected_failures(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        try:
            return await call_next(request)
        except Exception as error:
            return server_failure(error)

    @app.middleware("http")
    async def trace_requests(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.perf_counter()
        trace = RequestTrace(request_id_from(request.headers.get(REQUEST_ID_HEADER)))
        with tracing(trace):
            response = await call_next(request)
            trace.record(Stage.TOTAL, (time.perf_counter() - started) * MILLISECONDS_PER_SECOND)
            response.headers[REQUEST_ID_HEADER] = trace.request_id
            response.headers[SERVER_TIMING_HEADER] = trace.server_timing()
            log_request(request.method, request.url.path, response.status_code, trace)
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=EXPOSED_HEADERS,
    )
