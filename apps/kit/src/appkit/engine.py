"""El cliente del gestor de un servicio: armado con su configuración y entregado a las rutas."""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import Depends, Request
from pydantic import SecretStr

from appkit.settings import ServiceSettings
from minigestor_client import AsyncClient


class EngineNotStartedError(RuntimeError):
    """Una ruta pidió el cliente del gestor fuera del `lifespan` del servicio."""


def engine_client(
    settings: ServiceSettings,
    key: SecretStr,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncClient:
    """Un cliente asíncrono del gestor con los plazos, reintentos y pool de `settings`, que
    se identifica con `key`. `transport` reemplaza la red, para los tests."""
    return AsyncClient(
        settings.engine_base_url,
        key.get_secret_value(),
        timeouts=settings.timeouts(),
        pool=settings.pool(),
        retry=settings.retry(),
        transport=transport,
    )


def engine_of(request: Request) -> AsyncClient:
    """Dependencia de FastAPI: el cliente del gestor que abrió el servicio al arrancar.

    Raises:
        EngineNotStartedError: si el servicio no arrancó con `create_service_app`.
    """
    engine = getattr(request.app.state, "engine", None)
    if not isinstance(engine, AsyncClient):
        raise EngineNotStartedError("el servicio no abrió su cliente del gestor al arrancar")
    return engine


Engine = Annotated[AsyncClient, Depends(engine_of)]
