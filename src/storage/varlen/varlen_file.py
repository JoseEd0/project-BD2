"""Archivo de valores de longitud variable sobre páginas ranuradas.

Un valor que cabe en una página es un solo fragmento. Uno mayor se parte en una cadena: el
primer fragmento lleva lo que sobra de dividir el valor en páginas completas, y cada uno de
los siguientes llena una página entera. La dirección de un valor es la de su primer
fragmento. Ver `README.md` para el formato y sus invariantes.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import TracebackType
from typing import Any

from config import EngineConfig
from storage.layout import CorruptFileError, FileHeader, chained_links
from storage.pager import HEADER_PAGE_ID, Pager
from storage.record_id import RECORD_ID_SIZE, RecordId
from storage.types import StorageError

from .free_space_map import FreeSpaceMap
from .slotted_page import FragmentKind, SlottedPage, max_fragment_bytes

VARLEN_MAGIC = b"VLEN"
VARLEN_VERSION = 1
# Tamaño de página con que se creó, número de valores y bytes que suman.
HEADER_FIELDS = "IQQ"
LINK_SIZE = RECORD_ID_SIZE
# La cabecera nunca guarda fragmentos: su dirección sirve de fin de cadena.
NO_FRAGMENT = RecordId(page_id=HEADER_PAGE_ID, slot=0)
FIRST_FRAGMENT_KINDS = frozenset({FragmentKind.WHOLE, FragmentKind.HEAD})


class VariableLengthFormatError(StorageError):
    """El archivo no es de valores de longitud variable o fue creado con otro formato."""


VARLEN_HEADER = FileHeader(
    VARLEN_MAGIC,
    VARLEN_VERSION,
    HEADER_FIELDS,
    VariableLengthFormatError,
    "un archivo de valores de longitud variable",
)


class ValueNotFoundError(StorageError):
    """La dirección pedida no es la de un valor vigente."""


class VariableLengthFile:
    """Valores de bytes de cualquier longitud, cada uno con su dirección.

    Complejidad, con F los fragmentos del valor (1 si cabe en una página): `get` lee F
    páginas de datos; `put` escribe F páginas de datos y consulta el mapa de espacio libre,
    que lee como mucho una página por fragmento; `delete` y `replace` leen y escriben las F
    páginas del valor. Ninguna operación recorre el archivo salvo `scan`.

    Raises:
        VariableLengthFormatError: si el archivo existe pero no corresponde a este formato.
    """

    def __init__(self, path: Path, config: EngineConfig) -> None:
        self._page_size = config.page_size
        self._inline_limit = max_fragment_bytes(config.page_size)
        self._chunk_size = self._inline_limit - LINK_SIZE
        self._pager = Pager(path, config)
        try:
            if self._pager.page_count == 0:
                self._create_header()
            stored_page_size, count, total_bytes = VARLEN_HEADER.read(self._pager)
            self._validate(int(stored_page_size))
            self._space = FreeSpaceMap(self._pager, self._inline_limit)
        except BaseException:
            self._pager.close()
            raise
        self._count: int = int(count)
        self._total_bytes: int = int(total_bytes)

    @property
    def value_count(self) -> int:
        return self._count

    @property
    def page_count(self) -> int:
        return self._pager.page_count

    @property
    def inline_limit(self) -> int:
        """El valor más largo que ocupa un solo fragmento."""
        return self._inline_limit

    @property
    def chunk_size(self) -> int:
        """Bytes de un valor que lleva cada fragmento de página completa de una cadena."""
        return self._chunk_size

    def put(self, value: bytes) -> RecordId:
        """Guarda el valor y devuelve su dirección, la de su primer fragmento."""
        kind, payload = self._write_continuation(value)
        address = self._place(kind, payload)
        self._count += 1
        self._total_bytes += len(value)
        return address

    def get(self, address: RecordId) -> bytes:
        """El valor guardado en esa dirección.

        Raises:
            ValueNotFoundError: si la dirección no es la de un valor vigente.
        """
        kind, payload = self._first_fragment(address)
        return self._assemble(kind, payload)

    def replace(self, address: RecordId, value: bytes) -> RecordId:
        """Cambia el valor y devuelve su dirección: la misma si el primer fragmento nuevo cabe
        en la página del anterior; si no, otra, y la anterior deja de valer.

        Raises:
            ValueNotFoundError: si la dirección no es la de un valor vigente.
        """
        kind, payload = self._first_fragment(address)
        previous_length = self._release_continuation(kind, payload)
        new_kind, new_payload = self._write_continuation(value)
        self._total_bytes += len(value) - previous_length
        page = self._load(address.page_id)
        if page.rewrite_capacity(address.slot) >= len(new_payload):
            page.rewrite(address.slot, new_kind, new_payload)
            self._store(address.page_id, page)
            return address
        self._free(address)
        return self._place(new_kind, new_payload)

    def delete(self, address: RecordId) -> None:
        """Borra el valor y libera el espacio de todos sus fragmentos.

        Raises:
            ValueNotFoundError: si la dirección no es la de un valor vigente.
        """
        kind, payload = self._first_fragment(address)
        length = self._release_continuation(kind, payload)
        self._free(address)
        self._count -= 1
        self._total_bytes -= length

    def scan(self) -> Iterator[tuple[RecordId, bytes]]:
        """Cada valor vigente con su dirección, en el orden físico de su primer fragmento.

        Los fragmentos siguientes de un valor se leen al llegar a él: en memoria no hay más
        que una página y el valor que se entrega.
        """
        for page_id in self._space.data_pages():
            for slot, kind, payload in self._load(page_id).fragments():
                if kind in FIRST_FRAGMENT_KINDS:
                    yield RecordId(page_id=page_id, slot=slot), self._assemble(kind, payload)

    def describe(self) -> dict[str, Any]:
        """Forma física del archivo, para inspeccionarla."""
        return {
            "kind": "varlen",
            "pages": self.page_count,
            "data_pages": self._space.data_page_count,
            "map_pages": self._space.map_page_count,
            "values": self._count,
            "value_bytes": self._total_bytes,
            "inline_limit": self._inline_limit,
            "chunk_size": self._chunk_size,
        }

    def flush(self) -> None:
        self._write_header()
        self._pager.flush()

    def close(self) -> None:
        self._write_header()
        self._pager.close()

    def __enter__(self) -> VariableLengthFile:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _write_continuation(self, value: bytes) -> tuple[FragmentKind, bytes]:
        """Escribe los fragmentos que siguen al primero y devuelve el primero, ya enlazado.

        Van del último al segundo, para que cada uno sepa la dirección del siguiente. El
        primero lleva el resto de dividir el valor en fragmentos completos: es el que se
        comparte página con otros valores, y así `replace` lo puede dejar en su sitio.
        """
        if len(value) <= self._inline_limit:
            return FragmentKind.WHOLE, value
        head_length = len(value) % self._chunk_size or self._chunk_size
        link = NO_FRAGMENT
        for start in reversed(range(head_length, len(value), self._chunk_size)):
            chunk = value[start : start + self._chunk_size]
            link = self._place(FragmentKind.PART, link.pack() + chunk)
        return FragmentKind.HEAD, link.pack() + value[:head_length]

    def _assemble(self, kind: FragmentKind, payload: bytes) -> bytes:
        if kind is FragmentKind.WHOLE:
            return payload
        link, chunk = _split_link(payload)
        return b"".join([chunk, *(part for _, part in self._continuation(link))])

    def _release_continuation(self, kind: FragmentKind, payload: bytes) -> int:
        """Libera los fragmentos que siguen al primero y devuelve la longitud del valor."""
        if kind is FragmentKind.WHOLE:
            return len(payload)
        link, chunk = _split_link(payload)
        length = len(chunk)
        for address, part in self._continuation(link):
            self._free(address)
            length += len(part)
        return length

    def _continuation(self, link: RecordId) -> Iterator[tuple[RecordId, bytes]]:
        """Los fragmentos que siguen al primero, con su dirección. Cada uno está en una
        página distinta, así que la cadena no puede tener más eslabones que páginas el
        archivo."""
        return chained_links(
            link, self._load_part, NO_FRAGMENT, self._pager.page_count, self._pager.path.name
        )

    def _load_part(self, address: RecordId) -> tuple[RecordId, bytes]:
        try:
            kind, payload = self._fragment_at(address)
        except ValueNotFoundError as error:
            raise CorruptFileError(
                f"{self._pager.path.name}: una cadena apunta a {address}, que está vacía"
            ) from error
        if kind is not FragmentKind.PART:
            raise CorruptFileError(
                f"{self._pager.path.name}: una cadena apunta a {address}, que no la continúa"
            )
        return _split_link(payload)

    def _first_fragment(self, address: RecordId) -> tuple[FragmentKind, bytes]:
        kind, payload = self._fragment_at(address)
        if kind not in FIRST_FRAGMENT_KINDS:
            raise ValueNotFoundError(f"{address} no es el comienzo de un valor")
        return kind, payload

    def _fragment_at(self, address: RecordId) -> tuple[FragmentKind, bytes]:
        if not self._space.is_data_page(address.page_id):
            raise ValueNotFoundError(f"la página {address.page_id} no guarda valores")
        try:
            return self._load(address.page_id).fragment(address.slot)
        except StorageError as error:
            raise ValueNotFoundError(f"no hay un valor en {address}") from error

    def _place(self, kind: FragmentKind, payload: bytes) -> RecordId:
        page_id, page = self._page_with_room(len(payload))
        slot = page.insert(kind, payload)
        self._store(page_id, page)
        return RecordId(page_id=page_id, slot=slot)

    def _page_with_room(self, size: int) -> tuple[int, SlottedPage]:
        page_id = self._space.find(size)
        if page_id is None:
            return self._space.allocate(), SlottedPage.create(self._page_size)
        return page_id, self._load(page_id)

    def _free(self, address: RecordId) -> None:
        page = self._load(address.page_id)
        page.free(address.slot)
        self._store(address.page_id, page)

    def _load(self, page_id: int) -> SlottedPage:
        return SlottedPage.from_bytes(self._pager.read(page_id))

    def _store(self, page_id: int, page: SlottedPage) -> None:
        self._pager.write(page_id, page.to_bytes())
        self._space.record(page_id, page.insertable_bytes())

    def _create_header(self) -> None:
        self._pager.allocate()
        self._count = 0
        self._total_bytes = 0
        self._write_header()

    def _write_header(self) -> None:
        """La cabecera se escribe al volcar o cerrar el archivo, no en cada operación: su
        contenido vive en memoria y solo hace falta en disco cuando lo hacen las páginas."""
        VARLEN_HEADER.write(self._pager, self._page_size, self._count, self._total_bytes)

    def _validate(self, stored_page_size: int) -> None:
        """El tamaño de página fija qué páginas son de mapa y cuáles de datos: leer el
        archivo con otro confundiría unas con otras."""
        if stored_page_size != self._page_size:
            raise VariableLengthFormatError(
                f"el archivo usa páginas de {stored_page_size} bytes y se pidieron "
                f"{self._page_size}"
            )


def _split_link(payload: bytes) -> tuple[RecordId, bytes]:
    return RecordId.unpack(payload[:LINK_SIZE]), payload[LINK_SIZE:]
