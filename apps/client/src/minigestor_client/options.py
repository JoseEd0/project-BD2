"""Plazos, tamaño del pool de conexiones y reintentos del cliente."""

from __future__ import annotations

import random
from dataclasses import dataclass

import httpx

DEFAULT_CONNECT_SECONDS = 5.0
# El gestor corta una sentencia a los 30 s (`MINIGESTOR_STATEMENT_TIMEOUT`): esperar algo
# más deja llegar su `StatementTimeoutError`, que explica qué pasó, en vez de cortar antes.
DEFAULT_READ_SECONDS = 45.0
DEFAULT_WRITE_SECONDS = 45.0
DEFAULT_POOL_SECONDS = 5.0
# Una carga masiva solo responde al terminar, tras construir los índices diferidos; mientras
# escribe un lote, el gestor deja de leer y el envío espera.
DEFAULT_LOAD_SECONDS = 600.0
DEFAULT_MAX_CONNECTIONS = 20
DEFAULT_MAX_KEEPALIVE_CONNECTIONS = 10
DEFAULT_ATTEMPTS = 3
DEFAULT_BASE_DELAY_SECONDS = 0.1
DEFAULT_MAX_DELAY_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class Timeouts:
    """Segundos de cada espera: abrir la conexión, recibir, enviar, conseguir una conexión del
    pool y, en la carga masiva, enviar las filas y esperar el resumen."""

    connect_seconds: float = DEFAULT_CONNECT_SECONDS
    read_seconds: float = DEFAULT_READ_SECONDS
    write_seconds: float = DEFAULT_WRITE_SECONDS
    pool_seconds: float = DEFAULT_POOL_SECONDS
    load_seconds: float = DEFAULT_LOAD_SECONDS

    def for_requests(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self.connect_seconds,
            read=self.read_seconds,
            write=self.write_seconds,
            pool=self.pool_seconds,
        )

    def for_loads(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self.connect_seconds,
            read=self.load_seconds,
            write=self.load_seconds,
            pool=self.pool_seconds,
        )


@dataclass(frozen=True, slots=True)
class PoolLimits:
    """Conexiones abiertas a la vez con el gestor y cuántas se guardan para reutilizar."""

    max_connections: int = DEFAULT_MAX_CONNECTIONS
    max_keepalive_connections: int = DEFAULT_MAX_KEEPALIVE_CONNECTIONS

    def as_httpx(self) -> httpx.Limits:
        return httpx.Limits(
            max_connections=self.max_connections,
            max_keepalive_connections=self.max_keepalive_connections,
        )


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Cuántas veces se intenta una operación idempotente que no recibió respuesta.

    `attempts` cuenta el primer intento: con 1 no se reintenta nada. La espera antes del
    reintento `n` es un valor al azar entre 0 y `min(max_delay, base_delay · 2ⁿ⁻¹)`
    («full jitter»): con el tope que crece se espera más cuanto más dura la caída, y el azar
    evita que las réplicas que fallaron a la vez reintenten a la vez.

    Raises:
        ValueError: si `attempts` es menor que 1 o una espera es negativa.
    """

    attempts: int = DEFAULT_ATTEMPTS
    base_delay_seconds: float = DEFAULT_BASE_DELAY_SECONDS
    max_delay_seconds: float = DEFAULT_MAX_DELAY_SECONDS

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError(f"attempts tiene que ser al menos 1, no {self.attempts}")
        if self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("las esperas entre reintentos no pueden ser negativas")

    def delay_before(self, retry: int) -> float:
        """Segundos que esperar antes del reintento número `retry` (el primero es 1)."""
        ceiling = min(self.max_delay_seconds, self.base_delay_seconds * 2 ** (retry - 1))
        return random.uniform(0, ceiling)


NO_RETRIES = RetryPolicy(attempts=1)
