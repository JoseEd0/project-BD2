"""`/health` y `/ready` de un servicio, los mismos nombres que usa el gestor y que consulta
el kit web (`apps/web-kit`).

`/health` responde mientras el proceso vive: es el healthcheck de Docker. `/ready` dice si el
servicio puede atender, y para eso pregunta al gestor por su `/ready`; guarda la respuesta
`ready_cache_seconds` para que las sondas de todas las réplicas no se vuelvan una consulta al
gestor cada una.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Protocol

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from appkit.logs import HEALTH_PATH, READY_PATH
from minigestor_client import AsyncClient, MinigestorError

READY = "ready"
UNAVAILABLE = "unavailable"
ENGINE_NOT_READY = "el gestor responde que no está listo"
DEGRADED_FIELD = "degraded"


class Degradable(Protocol):
    """Una pieza de la que el servicio puede prescindir, como su caché: sin ella sigue
    atendiendo, pero las sondas lo dicen."""

    name: str

    def degradation(self) -> str | None:
        """Por qué el servicio corre sin esta pieza, o `None` si la tiene."""


@dataclass(frozen=True, slots=True)
class Readiness:
    """Si el servicio puede atender y, si no, por qué."""

    ready: bool
    reason: str | None = None


class ReadinessProbe:
    """Pregunta al gestor si está listo y recuerda la respuesta `cache_seconds`.

    Dos sondas a la vez esperan la misma pregunta en vez de hacer dos.
    """

    def __init__(
        self,
        engine: AsyncClient,
        cache_seconds: float,
        timeout_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._engine = engine
        self._cache_seconds = cache_seconds
        self._timeout_seconds = timeout_seconds
        self._clock = clock
        self._lock = asyncio.Lock()
        self._last: tuple[float, Readiness] | None = None

    async def check(self) -> Readiness:
        async with self._lock:
            if self._last is not None and self._clock() - self._last[0] < self._cache_seconds:
                return self._last[1]
            readiness = await self._ask()
            self._last = (self._clock(), readiness)
            return readiness

    async def _ask(self) -> Readiness:
        try:
            ready = await self._engine.ready(self._timeout_seconds)
        except MinigestorError as error:
            return Readiness(False, f"el gestor no responde: {error.message}")
        return Readiness(True) if ready else Readiness(False, ENGINE_NOT_READY)


def add_probes(
    app: FastAPI,
    version: str,
    readiness: Callable[[], ReadinessProbe],
    degradables: Sequence[Degradable] = (),
) -> None:
    """Las rutas `/health` y `/ready`; `readiness` da la sonda, que existe desde el arranque.
    Si alguna de `degradables` falta, las dos respuestas lo dicen en `degraded` sin cambiar
    su estado: el servicio sigue atendiendo."""

    @app.get(HEALTH_PATH, include_in_schema=False)
    def health() -> dict[str, Any]:
        return {"status": "ok", "version": version, **_degradations(degradables)}

    @app.get(READY_PATH, include_in_schema=False)
    async def ready() -> JSONResponse:
        state = await readiness().check()
        if state.ready:
            return JSONResponse({"status": READY, **_degradations(degradables)})
        return JSONResponse(
            {"status": UNAVAILABLE, "reason": state.reason},
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
        )


def _degradations(degradables: Sequence[Degradable]) -> dict[str, dict[str, str]]:
    reasons = {
        degradable.name: reason
        for degradable in degradables
        if (reason := degradable.degradation()) is not None
    }
    return {DEGRADED_FIELD: reasons} if reasons else {}
