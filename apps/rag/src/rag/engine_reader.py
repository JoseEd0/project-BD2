"""Las lecturas del servicio en el gestor, reintentadas mientras su catálogo está ocupado.

El gestor es compartido: mientras otra app construye un índice (un `CREATE INDEX` sobre una
tabla grande retiene el catálogo), cualquier consulta que abra una tabla espera hasta el plazo
de bloqueo del gestor y falla con `EngineBusyError`. Es un fallo pasajero: se reintenta con
espera creciente y, si sigue, se responde un 503 que dice qué pasa y qué hacer, en vez del 400
genérico de un error del gestor.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Protocol, TypeVar

import anyio

from appkit import ServiceError
from minigestor_client import DomainError, Record

ENGINE_BUSY_KIND = "EngineBusyError"
BUSY_MESSAGE = (
    "la base de datos está ocupada construyendo índices de otra aplicación; vuelve a "
    "intentarlo en unos segundos"
)

logger = logging.getLogger(__name__)
Result = TypeVar("Result")
Sleep = Callable[[float], Awaitable[None]]


class EngineReader(Protocol):
    """Lo que el servicio lee del gestor: `minigestor_client.AsyncClient` lo cumple."""

    async def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[Record]: ...

    async def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> Record | None: ...


class EngineBusyError(ServiceError):
    """El catálogo del gestor siguió ocupado después de todos los reintentos."""

    status = HTTPStatus.SERVICE_UNAVAILABLE


@dataclass(frozen=True, slots=True)
class BusyRetry:
    """Cuántas veces se reintenta una lectura con el catálogo ocupado y la primera espera,
    que se duplica en cada reintento."""

    retries: int
    backoff_seconds: float


def is_engine_busy(error: DomainError) -> bool:
    return error.kind == ENGINE_BUSY_KIND


class BusyRetryingReader:
    """Un `EngineReader` que reintenta las lecturas de `engine` según `retry`.

    Raises (de cada lectura):
        EngineBusyError: si el catálogo sigue ocupado tras `retry.retries` reintentos.
        minigestor_client.MinigestorError: cualquier otro fallo del gestor, sin reintentar.
    """

    def __init__(self, engine: EngineReader, retry: BusyRetry, sleep: Sleep = anyio.sleep) -> None:
        self._engine = engine
        self._retry = retry
        self._sleep = sleep

    async def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[Record]:
        return await self._retrying(lambda: self._engine.fetch_all(sql, params))

    async def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> Record | None:
        return await self._retrying(lambda: self._engine.fetch_one(sql, params))

    async def _retrying(self, read: Callable[[], Awaitable[Result]]) -> Result:
        wait = self._retry.backoff_seconds
        for attempt in range(self._retry.retries):
            try:
                return await read()
            except DomainError as error:
                if not is_engine_busy(error):
                    raise
                logger.info("catálogo ocupado (intento %d); reintento en %.2f s", attempt + 1, wait)
            await self._sleep(wait)
            wait *= 2
        try:
            return await read()
        except DomainError as error:
            if is_engine_busy(error):
                raise EngineBusyError(BUSY_MESSAGE) from error
            raise
