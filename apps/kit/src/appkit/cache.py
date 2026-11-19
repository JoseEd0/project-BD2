"""Un Redis del que el servicio puede prescindir.

La caché acelera, pero el servicio responde sin ella (*fail-open*): si Redis no contesta,
quien la usa sigue por el camino lento. El primer fallo se avisa una vez en el log y Redis no
se vuelve a probar hasta `retry_seconds` después, para no pagar su plazo en cada petición
mientras está caído; `/health` y `/ready` lo muestran como degradado mientras tanto.

    cache = OptionalRedis(redis_factory(url, timeout_seconds=0.1), retry_seconds=5)
    client = cache.client()
    if client is not None:
        try:
            value = await client.get(key)
        except RedisError as error:
            cache.failed(error)
        else:
            cache.succeeded()
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError

CACHE_COMPONENT = "redis"
NO_RETRIES = 0

RedisFactory = Callable[[], Redis]
Clock = Callable[[], float]

logger = logging.getLogger(__name__)


def redis_factory(url: str, timeout_seconds: float) -> RedisFactory:
    """Crea clientes de `url` que fallan enseguida: sin reintentos y con `timeout_seconds`
    para conectar y para cada operación. Un plazo largo haría esperar cada petición
    mientras Redis no responde, hasta que el primer fallo abra la ventana de reintento."""

    def connect() -> Redis:
        return Redis.from_url(
            url,
            socket_timeout=timeout_seconds,
            socket_connect_timeout=timeout_seconds,
            retry=Retry(NoBackoff(), NO_RETRIES),
        )

    return connect


class OptionalRedis:
    """El cliente de Redis de una réplica y si conviene usarlo ahora.

    Sin `connect` el servicio corre sin caché, y eso no es una degradación: es la
    configuración. El cliente se abre en `connected()`, dentro del ciclo de vida del
    servicio, porque queda atado al bucle de eventos que lo usa.
    """

    name = CACHE_COMPONENT

    def __init__(
        self,
        connect: RedisFactory | None,
        retry_seconds: float,
        clock: Clock = time.monotonic,
    ) -> None:
        self._connect = connect
        self._retry_seconds = retry_seconds
        self._clock = clock
        self._client: Redis | None = None
        self._unavailable_until: float | None = None
        self._last_error: str | None = None

    @property
    def configured(self) -> bool:
        return self._connect is not None

    @asynccontextmanager
    async def connected(self) -> AsyncIterator[OptionalRedis]:
        """Abre el cliente mientras dura el bloque y lo cierra al salir."""
        if self._connect is None:
            yield self
            return
        self._client = self._connect()
        try:
            yield self
        finally:
            client, self._client = self._client, None
            await client.aclose()

    def client(self) -> Redis | None:
        """El cliente, o `None` si no hay caché o si falló hace menos de `retry_seconds`."""
        if self._client is None:
            return None
        if self._unavailable_until is not None and self._clock() < self._unavailable_until:
            return None
        return self._client

    def failed(self, error: RedisError) -> None:
        """Anota que Redis no respondió; solo el primer fallo de una racha va al log."""
        if self._unavailable_until is None:
            logger.warning(
                "Redis no responde (%s): se sigue sin caché y se reintenta cada %.0f s",
                error,
                self._retry_seconds,
            )
        self._last_error = str(error) or type(error).__name__
        self._unavailable_until = self._clock() + self._retry_seconds

    def succeeded(self) -> None:
        """Anota que Redis respondió: si estaba caído, se avisa que volvió."""
        if self._unavailable_until is not None:
            logger.info("Redis volvió a responder: la caché vuelve a usarse")
            self._unavailable_until = None
            self._last_error = None

    def degradation(self) -> str | None:
        """Por qué el servicio corre sin su caché, o `None` si la tiene (o no la usa)."""
        if self._last_error is None:
            return None
        return f"sin caché: Redis no responde ({self._last_error})"
