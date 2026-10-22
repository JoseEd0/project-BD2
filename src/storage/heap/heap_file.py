"""Heap file paginado con lista de páginas con espacio libre.

Los registros se guardan en la primera ranura disponible, sin ningún orden. Lo único que
el archivo mantiene es una lista enlazada de las páginas que tienen huecos, de modo que
una inserción posterior a un borrado reutiliza el espacio en lugar de hacer crecer el
archivo. Ver `README.md` para el diseño y las complejidades.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from types import TracebackType
from typing import Any

from config import EngineConfig
from storage.layout import FileHeader
from storage.page import NO_PAGE, RecordPage, slot_capacity
from storage.pager import HEADER_PAGE_ID, Pager
from storage.record_id import RecordId
from storage.types import StorageError

HEAP_MAGIC = b"HEAP"
HEAP_VERSION = 1
# Tamaño de registro, cabeza de la lista de páginas con hueco y número de registros.
HEADER_FIELDS = "IiQ"


class HeapFormatError(StorageError):
    """El archivo no es un heap file válido o fue creado con otro formato."""


HEAP_HEADER = FileHeader(HEAP_MAGIC, HEAP_VERSION, HEADER_FIELDS, HeapFormatError, "un heap file")


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
        self._record_size = record_size
        self._capacity = slot_capacity(config.page_size, record_size)
        self._pager = Pager(path, config)
        if self._pager.page_count == 0:
            self._create_header()
        stored_size, free_head, count = HEAP_HEADER.read(self._pager)
        self._validate(int(stored_size))
        self._free_head: int = int(free_head)
        self._count: int = int(count)

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
            self._unlink_free_head(page)
        self._store(page_id, page)
        self._count += 1
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

    def read_many(self, record_ids: Iterable[RecordId]) -> Iterator[bytes]:
        """Registros de varias direcciones, en el orden en que se piden.

        Dos direcciones seguidas de la misma página comparten una sola lectura: quien las
        pida ordenadas por página lee cada página una vez, por muchas filas que tenga.

        Raises:
            RecordNotFoundError: si alguna ranura está libre o borrada.
        """
        loaded = NO_PAGE
        page: RecordPage | None = None
        for record_id in record_ids:
            if page is None or record_id.page_id != loaded:
                page = self._load_data_page(record_id.page_id)
                loaded = record_id.page_id
            try:
                yield page.read(record_id.slot)
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

    def scan(self) -> Iterator[tuple[RecordId, bytes]]:
        """Recorre todos los registros vigentes en orden físico."""
        for page_id in range(HEADER_PAGE_ID + 1, self._pager.page_count):
            page = self._load(page_id)
            for slot in page.live_slots():
                yield RecordId(page_id=page_id, slot=slot), page.read(slot)

    def describe(self) -> dict[str, Any]:
        """Forma física del archivo, para inspeccionarla."""
        return {
            "kind": "heap",
            "pages": self.page_count,
            "slots_per_page": self.slots_per_page,
            "records": self.record_count,
        }

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
        self._link_free_page(page_id, page)
        self._store(page_id, page)
        return page_id

    def _link_free_page(self, page_id: int, page: RecordPage) -> None:
        """Pone a la cabeza de la lista una página que acaba de ganar una ranura libre."""
        page.next_page = self._free_head
        self._free_head = page_id

    def _unlink_free_head(self, page: RecordPage) -> None:
        """Saca de la lista la página que se acaba de llenar.

        Es siempre la cabeza: `insert` no toma espacio de ninguna otra.
        """
        self._free_head = page.next_page
        page.next_page = NO_PAGE

    def _require_live(self, page: RecordPage, record_id: RecordId) -> None:
        try:
            page.read(record_id.slot)
        except StorageError as error:
            raise RecordNotFoundError(f"no hay registro en {record_id}") from error

    def _load_data_page(self, page_id: int) -> RecordPage:
        if not HEADER_PAGE_ID < page_id < self._pager.page_count:
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

    def _write_header(self) -> None:
        """La cabecera se escribe al volcar o cerrar el archivo, no en cada operación: su
        contenido vive en memoria y solo hace falta en disco cuando lo hacen las páginas."""
        HEAP_HEADER.write(self._pager, self._record_size, self._free_head, self._count)

    def _validate(self, stored_size: int) -> None:
        if stored_size != self._record_size:
            raise HeapFormatError(
                f"el archivo guarda registros de {stored_size} bytes y se pidieron "
                f"{self._record_size}"
            )
