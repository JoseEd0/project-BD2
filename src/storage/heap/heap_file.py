"""Heap file paginado con lista de páginas con espacio libre.

Los registros se guardan en la primera ranura disponible, sin ningún orden. Lo único que
el archivo mantiene es una lista enlazada de las páginas que tienen huecos, de modo que
una inserción posterior a un borrado reutiliza el espacio en lugar de hacer crecer el
archivo. Ver `README.md` para el diseño y las complejidades.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType

from config import EngineConfig
from storage.page import NO_PAGE, RecordPage, slot_capacity
from storage.pager import HEADER_PAGE_ID, Pager
from storage.record_id import RecordId
from storage.types import StorageError

HEADER_FORMAT = struct.Struct("<4sHIiQ")
HEAP_MAGIC = b"HEAP"
HEAP_VERSION = 1
IN_FREE_LIST = 1


class HeapFormatError(StorageError):
    """El archivo no es un heap file válido o fue creado con otro formato."""


class RecordNotFoundError(StorageError):
    """La dirección pedida no contiene un registro vigente."""


class HeapFile:
    """Archivo de registros sin orden, con reutilización de ranuras liberadas.

    Complejidad: inserción, lectura, borrado y actualización en O(1) accesos a página;
    el recorrido completo cuesta O(páginas).

    Raises:
        HeapFormatError: si el archivo existe pero no corresponde a este formato.
    """

    def __init__(self, path: Path, record_size: int, config: EngineConfig) -> None:
        self._pager = Pager(path, config)
        self._record_size = record_size
        self._capacity = slot_capacity(config.page_size, record_size)
        if self._pager.page_count == 0:
            self._create_header()
        self._magic, self._version, stored_size, self._free_head, self._count = self._read_header()
        self._validate(stored_size)

    @property
    def record_size(self) -> int:
        return self._record_size

    @property
    def record_count(self) -> int:
        return self._count

    @property
    def page_count(self) -> int:
        return self._pager.page_count

    @property
    def slots_per_page(self) -> int:
        return self._capacity

    def insert(self, record: bytes) -> RecordId:
        """Guarda el registro y devuelve su dirección física."""
        page_id = self._free_head if self._free_head != NO_PAGE else self._grow()
        page = self._load(page_id)
        slot = page.insert(record)
        if page.first_free_slot() is None:
            self._unlink_free_page(page_id, page)
        self._store(page_id, page)
        self._count += 1
        self._write_header()
        return RecordId(page_id=page_id, slot=slot)

    def read(self, record_id: RecordId) -> bytes:
        """Registro almacenado en esa dirección.

        Raises:
            RecordNotFoundError: si la ranura está libre o borrada.
        """
        page = self._load_data_page(record_id.page_id)
        try:
            return page.read(record_id.slot)
        except StorageError as error:
            raise RecordNotFoundError(f"no hay registro en {record_id}") from error

    def update(self, record_id: RecordId, record: bytes) -> None:
        """Reemplaza el registro en su sitio, sin cambiar su dirección.

        Raises:
            RecordNotFoundError: si la ranura está libre o borrada.
        """
        page = self._load_data_page(record_id.page_id)
        self._require_live(page, record_id)
        page.write(record_id.slot, record)
        self._store(record_id.page_id, page)

    def delete(self, record_id: RecordId) -> None:
        """Libera la ranura y devuelve la página a la lista de espacio libre.

        Raises:
            RecordNotFoundError: si la ranura ya estaba libre.
        """
        page = self._load_data_page(record_id.page_id)
        self._require_live(page, record_id)
        was_full = page.first_free_slot() is None
        page.free(record_id.slot)
        if was_full:
            self._link_free_page(record_id.page_id, page)
        self._store(record_id.page_id, page)
        self._count -= 1
        self._write_header()

    def scan(self) -> Iterator[tuple[RecordId, bytes]]:
        """Recorre todos los registros vigentes en orden físico."""
        for page_id in range(HEADER_PAGE_ID + 1, self._pager.page_count):
            page = self._load(page_id)
            for slot in page.live_slots():
                yield RecordId(page_id=page_id, slot=slot), page.read(slot)

    def flush(self) -> None:
        self._write_header()
        self._pager.flush()

    def close(self) -> None:
        self._write_header()
        self._pager.close()

    def __enter__(self) -> HeapFile:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _grow(self) -> int:
        page_id = self._pager.allocate()
        page = RecordPage.create(self._pager.page_size, self._record_size)
        if self._capacity > 1:
            self._link_free_page(page_id, page)
        self._store(page_id, page)
        return page_id

    def _link_free_page(self, page_id: int, page: RecordPage) -> None:
        if page.flags & IN_FREE_LIST:
            return
        page.next_page = self._free_head
        page.flags |= IN_FREE_LIST
        self._free_head = page_id

    def _unlink_free_page(self, page_id: int, page: RecordPage) -> None:
        if not page.flags & IN_FREE_LIST:
            return
        if self._free_head != page_id:
            raise StorageError("la página con espacio libre no está a la cabeza de la lista")
        self._free_head = page.next_page
        page.next_page = NO_PAGE
        page.flags &= ~IN_FREE_LIST

    def _require_live(self, page: RecordPage, record_id: RecordId) -> None:
        try:
            page.read(record_id.slot)
        except StorageError as error:
            raise RecordNotFoundError(f"no hay registro en {record_id}") from error

    def _load_data_page(self, page_id: int) -> RecordPage:
        if page_id <= HEADER_PAGE_ID:
            raise RecordNotFoundError(f"la página {page_id} no contiene datos")
        return self._load(page_id)

    def _load(self, page_id: int) -> RecordPage:
        return RecordPage.from_bytes(self._pager.read(page_id), self._record_size)

    def _store(self, page_id: int, page: RecordPage) -> None:
        self._pager.write(page_id, page.to_bytes())

    def _create_header(self) -> None:
        self._pager.allocate()
        self._free_head = NO_PAGE
        self._count = 0
        self._write_header()

    def _read_header(self) -> tuple[bytes, int, int, int, int]:
        raw = self._pager.read(HEADER_PAGE_ID)
        magic, version, record_size, free_head, count = HEADER_FORMAT.unpack_from(raw, 0)
        return magic, int(version), int(record_size), int(free_head), int(count)

    def _write_header(self) -> None:
        raw = bytearray(self._pager.page_size)
        HEADER_FORMAT.pack_into(
            raw, 0, HEAP_MAGIC, HEAP_VERSION, self._record_size, self._free_head, self._count
        )
        self._pager.write(HEADER_PAGE_ID, bytes(raw))

    def _validate(self, stored_size: int) -> None:
        if self._magic != HEAP_MAGIC:
            raise HeapFormatError(f"{self._pager.path.name} no es un heap file")
        if self._version != HEAP_VERSION:
            raise HeapFormatError(f"versión de heap file no soportada: {self._version}")
        if stored_size != self._record_size:
            raise HeapFormatError(
                f"el archivo guarda registros de {stored_size} bytes y se pidieron "
                f"{self._record_size}"
            )
