"""API REST del minigestor.

Expone lo justo para que el frontend funcione:

| Método | Ruta | Para qué |
|---|---|---|
| `GET` | `/health` | comprobar que el servidor responde |
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

`app_from_environment` es la fábrica que uvicorn llama con `--factory`; los tests usan
`create_app` con su propia configuración. Importar este módulo no abre ningún motor.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import query, sessions, tables, uploads
from api.service import Service
from api.settings import settings_from_environment
from config import EngineConfig

TITLE = "Minigestor de Base de Datos Multimodal"
VERSION = "0.1.0"


def app_from_environment() -> FastAPI:
    """La aplicación configurada con las variables de entorno.

    Raises:
        SettingsError: si falta una variable obligatoria.
    """
    settings = settings_from_environment()
    return create_app(settings.config, settings.cors_origins)


def create_app(config: EngineConfig, cors_origins: Sequence[str] = ()) -> FastAPI:
    """Construye la aplicación con una configuración explícita, para poder probarla aislada.

    Sin `cors_origins` solo admite peticiones del mismo origen.
    """
    service = Service(config)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        service.close()

    app = FastAPI(title=TITLE, version=VERSION, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.service = service

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": VERSION}

    for router in (tables.router, query.router, uploads.router, sessions.router):
        app.include_router(router)
    return app
