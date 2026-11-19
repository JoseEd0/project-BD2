"""La aplicación FastAPI de un servicio con todo lo común ya puesto.

    def app_from_environment() -> FastAPI:
        settings = load_settings(ShopSettings)
        configure_logging(settings.log_level)
        app = create_service_app(settings, name="shop", version=VERSION, migrations=SCHEMA)
        app.include_router(routes.router)
        return app

Al arrancar abre el cliente del gestor y aplica las migraciones (si fallan, el servicio no
arranca); al apagarse cierra el pool de conexiones. Las rutas reciben el cliente con la
dependencia `appkit.Engine`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import httpx
from fastapi import FastAPI

from appkit.engine import engine_client
from appkit.errors import install_error_handlers
from appkit.health import Degradable, ReadinessProbe, add_probes
from appkit.migrations import check_repeatable, migrate
from appkit.settings import ServiceSettings
from appkit.tracing import RequestTracingMiddleware
from minigestor_client import AsyncClient, new_request_id, request_id_scope

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]
STARTUP_REQUEST_PREFIX = "arranque-"


def create_service_app(
    settings: ServiceSettings,
    *,
    name: str,
    version: str,
    migrations: Sequence[str] = (),
    lifespan: Lifespan | None = None,
    engine_transport: httpx.AsyncBaseTransport | None = None,
    degradables: Sequence[Degradable] = (),
) -> FastAPI:
    """La aplicación del servicio `name`, con middleware de trazas, errores uniformes,
    `/health`, `/ready` y el ciclo de vida del cliente del gestor.

    `migrations` son las sentencias del esquema (ver `appkit.migrations`), con la clave de
    migraciones si está configurada. `lifespan` es el arranque y apagado propios del servicio:
    corre con el cliente ya abierto y las migraciones aplicadas, y termina antes de cerrarlo.
    `engine_transport` reemplaza la red hacia el gestor, para los tests. `degradables` son
    las piezas opcionales del servicio (su caché, por ejemplo), que las sondas muestran como
    degradadas cuando faltan.

    Raises:
        NonRepeatableMigrationError: si alguna migración no es repetible, antes de arrancar.
    """
    check_repeatable(migrations)

    @asynccontextmanager
    async def service_lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with engine_client(settings, settings.engine_key, engine_transport) as engine:
            with request_id_scope(STARTUP_REQUEST_PREFIX + new_request_id()):
                await _migrate(settings, migrations, engine, engine_transport)
            app.state.engine = engine
            app.state.readiness = ReadinessProbe(
                engine, settings.ready_cache_seconds, settings.ready_timeout_seconds
            )
            if lifespan is None:
                yield
                return
            async with lifespan(app):
                yield

    app = FastAPI(title=name, version=version, lifespan=service_lifespan)
    app.add_middleware(RequestTracingMiddleware, service=name)
    install_error_handlers(app)
    add_probes(app, version, lambda: app.state.readiness, degradables)
    return app


async def _migrate(
    settings: ServiceSettings,
    migrations: Sequence[str],
    engine: AsyncClient,
    transport: httpx.AsyncBaseTransport | None,
) -> None:
    if not migrations:
        return
    if settings.migration_key is None:
        await migrate(engine, migrations)
        return
    async with engine_client(settings, settings.migration_key, transport) as migrator:
        await migrate(migrator, migrations)
