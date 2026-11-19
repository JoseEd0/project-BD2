"""La caché de respuestas del recomendador, en Redis.

Cada respuesta se guarda con una clave hecha de la ruta, los parámetros, la versión de los
pesos y la **generación** de cada producto o usuario del que depende. Invalidar es sumar 1 a
una generación (`INCR`): las claves viejas dejan de pedirse y caducan solas con su TTL. A
diferencia de borrar claves, no hay carrera: una respuesta que se estaba calculando cuando
llegó la compra se guarda con la generación anterior, y nadie vuelve a leerla.

La caché es opcional. Sin `SHOP_REDIS_URL` no se usa; si Redis no responde, se responde sin
ella, se avisa una vez en el log y no se vuelve a probar hasta pasado `retry_seconds`, para
no pagar el plazo de Redis en cada petición mientras está caído.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError

KEY_PREFIX = "shop"
PRODUCT_ENTITY = "producto"
USER_ENTITY = "usuario"
# Una generación tiene que vivir más que las respuestas que separa: si caducara antes y
# volviera a 0, una respuesta vieja guardada con la generación 0 podría servirse otra vez.
GENERATION_TTL_FACTOR = 2
DIGEST_CHARACTERS = 16
NO_RETRIES = 0

logger = logging.getLogger(__name__)

Clock = Callable[[], float]


class CacheStatus(Enum):
    """Lo que dice la cabecera `X-Cache` de una respuesta."""

    HIT = "hit"
    MISS = "miss"
    BYPASS = "bypass"


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """Lo que identifica una respuesta: su ruta, de qué productos y usuarios depende, y sus
    parámetros."""

    route: str
    entities: tuple[tuple[str, str], ...]
    parameters: dict[str, object]


@dataclass(frozen=True, slots=True)
class CacheLookup:
    """El resultado de buscar: la respuesta guardada, si la hay, y la clave con que se
    guarda la que se calcule ahora (`None` si no hay caché)."""

    status: CacheStatus
    payload: bytes | None
    key: str | None


class ResponseCache:
    """Respuestas guardadas en `client` durante `ttl_seconds`, bajo la versión `version`
    (la de los pesos y la configuración del recomendador)."""

    def __init__(
        self,
        client: Redis | None,
        ttl_seconds: int,
        version: str,
        retry_seconds: float,
        clock: Clock = time.monotonic,
    ) -> None:
        self._client = client
        self._ttl_seconds = ttl_seconds
        self._version = version
        self._retry_seconds = retry_seconds
        self._clock = clock
        self._unavailable_until: float | None = None

    async def lookup(self, entry: CacheEntry) -> CacheLookup:
        """La respuesta de `entry` si está guardada; si Redis no está, sin caché."""
        client = self._available()
        if client is None:
            return CacheLookup(CacheStatus.BYPASS, None, None)
        try:
            generations = await client.mget([_generation_key(*entity) for entity in entry.entities])
            key = self._key(entry, generations)
            payload = await client.get(key)
        except RedisError as error:
            self._fail(error)
            return CacheLookup(CacheStatus.BYPASS, None, None)
        self._recovered()
        if payload is None:
            return CacheLookup(CacheStatus.MISS, None, key)
        stored = payload.encode() if isinstance(payload, str) else payload
        return CacheLookup(CacheStatus.HIT, stored, key)

    async def store(self, lookup: CacheLookup, payload: bytes) -> None:
        """Guarda `payload` con la clave de `lookup`; sin clave, no hace nada."""
        client = self._available()
        if client is None or lookup.key is None:
            return
        try:
            await client.set(lookup.key, payload, ex=self._ttl_seconds)
        except RedisError as error:
            self._fail(error)

    async def invalidate(self, products: Iterable[str], users: Iterable[str]) -> None:
        """Deja sin efecto las respuestas que dependen de `products` o de `users`."""
        client = self._available()
        if client is None:
            return
        entities = [(PRODUCT_ENTITY, product) for product in products]
        entities.extend((USER_ENTITY, user) for user in users)
        lifetime = self._ttl_seconds * GENERATION_TTL_FACTOR
        try:
            async with client.pipeline(transaction=False) as pipeline:
                for entity in entities:
                    pipeline.incr(_generation_key(*entity))
                    pipeline.expire(_generation_key(*entity), lifetime)
                await pipeline.execute()
        except RedisError as error:
            self._fail(error)

    def _key(self, entry: CacheEntry, generations: Iterable[object]) -> str:
        dependencies = ":".join(
            f"{kind}={identifier}@{_generation(value)}"
            for (kind, identifier), value in zip(entry.entities, generations, strict=True)
        )
        encoded = json.dumps(entry.parameters, sort_keys=True, default=str).encode()
        digest = hashlib.sha256(encoded).hexdigest()[:DIGEST_CHARACTERS]
        return f"{KEY_PREFIX}:{self._version}:{entry.route}:{dependencies}:{digest}"

    def _available(self) -> Redis | None:
        if self._client is None:
            return None
        if self._unavailable_until is not None and self._clock() < self._unavailable_until:
            return None
        return self._client

    def _fail(self, error: RedisError) -> None:
        if self._unavailable_until is None:
            logger.warning(
                "la caché de Redis no responde (%s): se responde sin ella y se reintenta "
                "cada %.0f s",
                error,
                self._retry_seconds,
            )
        self._unavailable_until = self._clock() + self._retry_seconds

    def _recovered(self) -> None:
        if self._unavailable_until is not None:
            logger.info("la caché de Redis volvió a responder")
            self._unavailable_until = None


def redis_client(url: str, timeout_seconds: float) -> Redis:
    """Un cliente de Redis que falla enseguida: sin reintentos y con `timeout_seconds` para
    conectar y para cada operación."""
    return Redis.from_url(
        url,
        socket_timeout=timeout_seconds,
        socket_connect_timeout=timeout_seconds,
        retry=Retry(NoBackoff(), NO_RETRIES),
    )


def _generation_key(kind: str, identifier: str) -> str:
    return f"{KEY_PREFIX}:generacion:{kind}:{identifier}"


def _generation(value: object) -> int:
    if isinstance(value, bytes | str):
        return int(value)
    return 0
