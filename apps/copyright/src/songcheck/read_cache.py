"""Lecturas del catálogo guardadas en memoria del proceso.

El catálogo no cambia después de la carga, pero `GET /catalog` cuenta las tablas y
`GET /songs` sin búsqueda las ordena enteras: uno o dos segundos del gestor por petición, y
varias páginas abiertas a la vez lo saturaban. Cada lectura se guarda por su clave: poco
tiempo mientras el catálogo todavía se carga (para ver el avance), mucho cuando ya está
indexado. Las peticiones que llegan a la vez por la misma clave esperan una sola consulta.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

Value = TypeVar("Value")


@dataclass(frozen=True, slots=True)
class CachePolicy:
    """Cuánto vive una lectura: `ready_seconds` con el catálogo completo e indexado,
    `loading_seconds` mientras se carga."""

    ready_seconds: float
    loading_seconds: float


@dataclass(slots=True)
class _Entry(Generic[Value]):
    value: Value
    expires_at: float


@dataclass
class ReadCache(Generic[Value]):
    """Un valor por clave, con vencimiento y una sola carga a la vez por clave."""

    policy: CachePolicy
    clock: Callable[[], float] = time.monotonic
    _entries: dict[Hashable, _Entry[Value]] = field(default_factory=dict)
    _locks: dict[Hashable, asyncio.Lock] = field(default_factory=dict)

    async def get(
        self, key: Hashable, load: Callable[[], Awaitable[Value]], ready: Callable[[Value], bool]
    ) -> Value:
        """El valor de `key`: el guardado si no venció o el que da `load`, que se guarda
        `policy.ready_seconds` si `ready(valor)` y `policy.loading_seconds` si no.

        Complejidad: una llamada a `load` por clave y vencimiento, aunque lleguen muchas a la vez.
        """
        cached = self._fresh(key)
        if cached is not None:
            return cached.value
        async with self._locks.setdefault(key, asyncio.Lock()):
            cached = self._fresh(key)
            if cached is not None:
                return cached.value
            value = await load()
            lifetime = self.policy.ready_seconds if ready(value) else self.policy.loading_seconds
            self._entries[key] = _Entry(value, self.clock() + lifetime)
            return value

    def _fresh(self, key: Hashable) -> _Entry[Value] | None:
        entry = self._entries.get(key)
        return entry if entry is not None and entry.expires_at > self.clock() else None
