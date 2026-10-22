"""Acceso a disco por páginas, a través del buffer pool común.

Ninguna estructura del motor abre un archivo por su cuenta: todas piden páginas al
`Pager`, que es el único que hace `read`/`write` y el único que decide qué se queda en
memoria. Así "nada se carga entero en memoria" queda garantizado por construcción.

Convención: la página 0 de cada archivo pertenece a la estructura que lo usa y guarda su
metadata (cabeza de la lista de libres, raíz del árbol, profundidad global, …).
"""

from __future__ import annotations

import os
from pathlib import Path
from types import TracebackType

from config import EngineConfig

from .buffer_pool import BufferPool
from .types import StorageError

HEADER_PAGE_ID = 0


class PageNotFoundError(StorageError):
    """Se pidió una página que el archivo no contiene."""


class Pager:
    """Lee y escribe las páginas de tamaño fijo de un archivo, a través del buffer pool.

    `read` devuelve una copia: quien quiera modificar la página escribe de vuelta con
    `write`. Es una copia por acceso a cambio de que sea imposible corromper el buffer
    pool por descuido.

    Las páginas en memoria no son de este archivo sino del pool que comparten todos los
    de la misma configuración (ver `buffer_pool.py`).
    """

    def __init__(self, path: Path, config: EngineConfig) -> None:
        self._path = path
        self._page_size = config.page_size
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.touch()
        self._file = path.open("r+b")
        self._page_count = self._measure_page_count()
        self._pool = BufferPool.shared(config)
        self._owner = self._pool.register(self)
        self.reads = 0
        self.writes = 0

    @property
    def path(self) -> Path:
        return self._path

    @property
    def page_size(self) -> int:
        return self._page_size

    @property
    def page_count(self) -> int:
        return self._page_count

    def allocate(self) -> int:
        """Añade una página en blanco al final del archivo y devuelve su número."""
        with self._pool.guard:
            page_id = self._page_count
            self._page_count += 1
            self._pool.put_dirty(self._owner, page_id, bytearray(self._page_size))
            return page_id

    def read(self, page_id: int) -> bytes:
        """Copia de la página.

        Raises:
            PageNotFoundError: si la página no existe.
        """
        with self._pool.guard:
            return bytes(self._load(page_id))

    def write(self, page_id: int, data: bytes) -> None:
        """Reemplaza el contenido de la página.

        Raises:
            PageNotFoundError: si la página no existe.
            StorageError: si el bloque no mide exactamente una página.
        """
        if len(data) != self._page_size:
            raise StorageError(
                f"se intentó escribir {len(data)} bytes en una página de {self._page_size}"
            )
        with self._pool.guard:
            self._check_page(page_id)
            self._pool.put_dirty(self._owner, page_id, bytearray(data))

    def flush(self) -> None:
        """Vuelca a disco todas las páginas modificadas y espera a que el sistema operativo
        las tenga en el dispositivo (`fsync`): un COMMIT se apoya en esto."""
        with self._pool.guard:
            dirty = self._pool.dirty_pages(self._owner)
            for page_id, page in dirty:
                self.write_through(page_id, page)
            self._pool.mark_clean(self._owner, [page_id for page_id, _ in dirty])
            self._file.flush()
            os.fsync(self._file.fileno())

    def close(self) -> None:
        with self._pool.guard:
            if self._file.closed:
                return
            self.flush()
            self._pool.unregister(self._owner)
            self._file.close()

    def write_through(self, page_id: int, page: bytearray) -> None:
        """Escribe la página en su sitio del archivo. La llama el pool al desalojarla."""
        self._file.seek(page_id * self._page_size)
        self._file.write(page)
        self.writes += 1

    def __enter__(self) -> Pager:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _load(self, page_id: int) -> bytearray:
        self._check_page(page_id)
        cached = self._pool.get(self._owner, page_id)
        if cached is not None:
            return cached
        self._file.seek(page_id * self._page_size)
        raw = self._file.read(self._page_size)
        self.reads += 1
        page = bytearray(raw.ljust(self._page_size, b"\x00"))
        self._pool.put_clean(self._owner, page_id, page)
        return page

    def _check_page(self, page_id: int) -> None:
        if not 0 <= page_id < self._page_count:
            raise PageNotFoundError(f"la página {page_id} no existe en {self._path.name}")

    def _measure_page_count(self) -> int:
        size = self._path.stat().st_size
        return -(-size // self._page_size)
