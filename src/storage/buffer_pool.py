"""Buffer pool: las páginas en memoria de todos los archivos del motor, con un solo presupuesto.

Un gestor de bases de datos tiene un único administrador de buffers. La memoria es una: un
archivo muy consultado puede ocupar casi todo el presupuesto mientras otro sin uso no ocupa
nada. Con una caché por archivo, el gasto crecía con el número de archivos abiertos —cada
tabla, cada índice, cada run de un ordenamiento— sin que nada lo acotara.

Las páginas se reemplazan por LRU sobre el conjunto de todos los archivos. Una página
modificada se escribe en su archivo antes de salir de memoria.
"""

from __future__ import annotations

import threading
import weakref
from collections import OrderedDict
from typing import Protocol

from config import EngineConfig

Frame = tuple[int, int]


class PageOwner(Protocol):
    """El archivo al que pertenece una página: sabe escribirla en su sitio."""

    def write_through(self, page_id: int, page: bytearray) -> None: ...


class BufferPool:
    """Páginas residentes de muchos archivos, como mucho `capacity` en total.

    Cada archivo se registra y recibe un número; una página se identifica por el par
    `(archivo, página)`. `guard` protege el conjunto: varias sesiones leen a la vez.
    """

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._frames: OrderedDict[Frame, bytearray] = OrderedDict()
        self._dirty: set[Frame] = set()
        self._owners: dict[int, PageOwner] = {}
        self._next_owner = 0
        self.guard = threading.RLock()

    @classmethod
    def shared(cls, config: EngineConfig) -> BufferPool:
        """El pool de esa configuración, el mismo para todos sus archivos.

        Vive mientras algún archivo lo use: cuando se cierran todos, desaparece.
        """
        with _SHARED_GUARD:
            pool = _SHARED.get(config)
            if pool is None:
                pool = cls(config.buffer_pool_pages)
                _SHARED[config] = pool
            return pool

    @property
    def resident_pages(self) -> int:
        return len(self._frames)

    def register(self, owner: PageOwner) -> int:
        with self.guard:
            self._next_owner += 1
            self._owners[self._next_owner] = owner
            return self._next_owner

    def unregister(self, owner: int) -> None:
        """Olvida las páginas del archivo, también las modificadas: quien llama ya las
        escribió o decidió que no hacen falta."""
        with self.guard:
            for frame in [frame for frame in self._frames if frame[0] == owner]:
                del self._frames[frame]
                self._dirty.discard(frame)
            del self._owners[owner]

    def get(self, owner: int, page_id: int) -> bytearray | None:
        with self.guard:
            page = self._frames.get((owner, page_id))
            if page is not None:
                self._frames.move_to_end((owner, page_id))
            return page

    def put_clean(self, owner: int, page_id: int, page: bytearray) -> None:
        """Una página recién leída del archivo: igual a la de disco."""
        with self.guard:
            self._place((owner, page_id), page)

    def put_dirty(self, owner: int, page_id: int, page: bytearray) -> None:
        """Una página modificada: tendrá que escribirse antes de salir de memoria."""
        with self.guard:
            self._dirty.add((owner, page_id))
            self._place((owner, page_id), page)

    def dirty_pages(self, owner: int) -> list[tuple[int, bytearray]]:
        """Páginas modificadas del archivo, en orden de página."""
        with self.guard:
            pages = sorted(page_id for frame_owner, page_id in self._dirty if frame_owner == owner)
            return [(page_id, self._frames[(owner, page_id)]) for page_id in pages]

    def mark_clean(self, owner: int, pages: list[int]) -> None:
        """Las páginas ya están en disco tal como están en memoria."""
        with self.guard:
            self._dirty.difference_update((owner, page_id) for page_id in pages)

    def _place(self, frame: Frame, page: bytearray) -> None:
        self._frames[frame] = page
        self._frames.move_to_end(frame)
        self._evict_if_needed()

    def _evict_if_needed(self) -> None:
        while len(self._frames) > self._capacity:
            frame, page = self._frames.popitem(last=False)
            if frame in self._dirty:
                self._dirty.discard(frame)
                self._owners[frame[0]].write_through(frame[1], page)


_SHARED: weakref.WeakValueDictionary[EngineConfig, BufferPool] = weakref.WeakValueDictionary()
_SHARED_GUARD = threading.Lock()
