"""La caché de respuestas del recomendador, en Redis.

Cada respuesta se guarda con una clave hecha de la ruta, los parámetros, la versión de los
pesos y la **generación** de cada producto o usuario del que depende. Invalidar es sumar 1 a
una generación (`INCR`): las claves viejas dejan de pedirse y caducan solas con su TTL. A
diferencia de borrar claves, no hay carrera: una respuesta que se estaba calculando cuando
llegó la compra se guarda con la generación anterior, y nadie vuelve a leerla.

La caché es opcional y la maneja `appkit.OptionalRedis`: sin `SHOP_REDIS_URL` no se usa; si
Redis no responde, se responde sin ella, se avisa una vez y no se vuelve a probar hasta su
ventana de reintento, y `/health` lo muestra como degradado.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from redis.exceptions import RedisError

from appkit import OptionalRedis

KEY_PREFIX = "shop"
PRODUCT_ENTITY = "producto"
USER_ENTITY = "usuario"
# Una generación tiene que vivir más que las respuestas que separa: si caducara antes y
# volviera a 0, una respuesta vieja guardada con la generación 0 podría servirse otra vez.
GENERATION_TTL_FACTOR = 2
DIGEST_CHARACTERS = 16


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


BYPASS = CacheLookup(CacheStatus.BYPASS, None, None)


class ResponseCache:
    """Respuestas guardadas en `redis` durante `ttl_seconds`, bajo la versión `version` (la
    de los pesos y la configuración del recomendador)."""

    def __init__(self, redis: OptionalRedis, ttl_seconds: int, version: str) -> None:
        self._redis = redis
        self._ttl_seconds = ttl_seconds
        self._version = version

    async def lookup(self, entry: CacheEntry) -> CacheLookup:
        """La respuesta de `entry` si está guardada; si Redis no está, sin caché."""
        client = self._redis.client()
        if client is None:
            return BYPASS
        try:
            generations = await client.mget([_generation_key(*entity) for entity in entry.entities])
            key = self._key(entry, generations)
            payload = await client.get(key)
        except RedisError as error:
            self._redis.failed(error)
            return BYPASS
        self._redis.succeeded()
        if payload is None:
            return CacheLookup(CacheStatus.MISS, None, key)
        stored = payload.encode() if isinstance(payload, str) else payload
        return CacheLookup(CacheStatus.HIT, stored, key)

    async def store(self, lookup: CacheLookup, payload: bytes) -> None:
        """Guarda `payload` con la clave de `lookup`; sin clave, no hace nada."""
        client = self._redis.client()
        if client is None or lookup.key is None:
            return
        try:
            await client.set(lookup.key, payload, ex=self._ttl_seconds)
        except RedisError as error:
            self._redis.failed(error)

    async def invalidate(self, products: Iterable[str], users: Iterable[str]) -> None:
        """Deja sin efecto las respuestas que dependen de `products` o de `users`."""
        client = self._redis.client()
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
            self._redis.failed(error)

    def _key(self, entry: CacheEntry, generations: Iterable[object]) -> str:
        dependencies = ":".join(
            f"{kind}={identifier}@{_generation(value)}"
            for (kind, identifier), value in zip(entry.entities, generations, strict=True)
        )
        encoded = json.dumps(entry.parameters, sort_keys=True, default=str).encode()
        digest = hashlib.sha256(encoded).hexdigest()[:DIGEST_CHARACTERS]
        return f"{KEY_PREFIX}:{self._version}:{entry.route}:{dependencies}:{digest}"


def _generation_key(kind: str, identifier: str) -> str:
    return f"{KEY_PREFIX}:generacion:{kind}:{identifier}"


def _generation(value: object) -> int:
    if isinstance(value, bytes | str):
        return int(value)
    return 0
