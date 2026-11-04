"""Piezas del formato de archivo que comparten todas las estructuras de disco.

Heap, archivo secuencial, B+, hash y R-Tree guardan en la página 0 una cabecera con firma y
versión, y los tres índices reutilizan las páginas que liberan. Cada estructura lo hacía a
su manera; aquí vive una sola vez:

* `FileHeader`: la página 0, con una firma que distingue un archivo de otro y una versión
  que distingue un formato de otro, antes de interpretar ninguna página;
* `FreePageList`: las páginas liberadas, enlazadas a través de ellas mismas;
* `chained_pages`: el recorrido de una cadena de páginas, que una página corrupta no puede
  convertir en un bucle sin fin;
* `RecordAppender`: registros de tamaño fijo añadidos uno tras otro en páginas nuevas al
  final del archivo, que es como se escriben los runs y las tablas que se construyen de una
  vez.
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Iterable, Iterator
from typing import Any, TypeVar

from .page import NO_PAGE, RecordPage
from .pager import HEADER_PAGE_ID, Pager
from .types import StorageError

Page = TypeVar("Page")
SIGNATURE_FORMAT = "<4sH"
FREE_LINK = struct.Struct("<i")


class CorruptFileError(StorageError):
    """Las páginas del archivo no forman la estructura que dicen formar."""


class FileHeader:
    """La cabecera de un tipo de archivo: firma, versión y los campos de la estructura.

    `fields` es el formato `struct` de esos campos, que la estructura escribe y lee en orden.
    `kind` nombra el tipo en los mensajes de error, con su artículo: «un árbol B+».
    """

    def __init__(
        self, magic: bytes, version: int, fields: str, error: type[StorageError], kind: str
    ) -> None:
        self._magic = magic
        self._version = version
        self._layout = struct.Struct(SIGNATURE_FORMAT + fields)
        self._error = error
        self._kind = kind

    @property
    def size(self) -> int:
        return self._layout.size

    def write(self, pager: Pager, *values: Any) -> None:
        """Raises:
            StorageError: el `error` del tipo, si la cabecera no cabe en una página."""
        if self._layout.size > pager.page_size:
            raise self._error(
                f"la cabecera de {self._kind} mide {self._layout.size} bytes y la página "
                f"{pager.page_size}"
            )
        pager.write(HEADER_PAGE_ID, self.encode(*values).ljust(pager.page_size, b"\x00"))

    def read(self, pager: Pager) -> tuple[Any, ...]:
        """Los campos de la estructura, tras comprobar la firma y la versión.

        Raises:
            StorageError: el `error` del tipo, si el archivo es de otro tipo o de otra versión.
        """
        return self.decode(pager.read(HEADER_PAGE_ID), pager.path.name)

    def encode(self, *values: Any) -> bytes:
        """La firma, la versión y los campos, en los `size` bytes que ocupan: para un
        archivo que no se lee por páginas."""
        return self._layout.pack(self._magic, self._version, *values)

    def decode(self, raw: bytes, file_name: str) -> tuple[Any, ...]:
        """Los campos de una cabecera leída de `file_name`, tras comprobar la firma y la
        versión.

        Raises:
            StorageError: el `error` del tipo, si los bytes no son una cabecera de este
                tipo y de esta versión.
        """
        if len(raw) < self._layout.size:
            raise self._error(f"{file_name} no es {self._kind}: mide {len(raw)} bytes")
        magic, version, *values = self._layout.unpack_from(raw, 0)
        if magic != self._magic:
            raise self._error(f"{file_name} no es {self._kind}")
        if version != self._version:
            raise self._error(
                f"{file_name} es {self._kind} de la versión {version} del formato, "
                f"y este motor lee la {self._version}"
            )
        return tuple(values)


class FreePageList:
    """Páginas que el archivo liberó, para reutilizarlas antes de hacerlo crecer.

    Cada página libre guarda al principio el número de la siguiente; la cabeza de la lista
    la guarda la cabecera del archivo. Una página liberada no vuelve a leerse como nodo:
    `allocate` la entrega para que se escriba entera.
    """

    def __init__(self, pager: Pager, head: int) -> None:
        self._pager = pager
        self._head = head

    @property
    def head(self) -> int:
        return self._head

    def allocate(self) -> int:
        """Una página libre si la hay; si no, una nueva al final del archivo."""
        if self._head == NO_PAGE:
            return self._pager.allocate()
        page_id = self._head
        (self._head,) = FREE_LINK.unpack_from(self._pager.read(page_id), 0)
        return page_id

    def release(self, page_id: int) -> None:
        raw = bytearray(self._pager.page_size)
        FREE_LINK.pack_into(raw, 0, self._head)
        self._pager.write(page_id, bytes(raw))
        self._head = page_id

    def pages(self) -> Iterator[int]:
        """Las páginas de la lista, de la cabeza a la cola.

        Raises:
            CorruptFileError: si la lista tiene un ciclo.
        """
        return (
            page_id
            for page_id, _ in chained_pages(
                self._head, self._read_link, self._pager.page_count, self._pager.path.name
            )
        )

    def _read_link(self, page_id: int) -> tuple[int, int]:
        (following,) = FREE_LINK.unpack_from(self._pager.read(page_id), 0)
        return following, following


def chained_pages(
    first: int,
    load: Callable[[int], tuple[int, Page]],
    page_count: int,
    file_name: str,
) -> Iterator[tuple[int, Page]]:
    """Las páginas de una cadena, empezando por `first`, con lo que `load` lee de cada una.

    `load` devuelve el número de la página siguiente y la página. Una cadena no puede tener
    más páginas que el archivo: si las tiene, alguna página corrupta apunta hacia atrás, y
    seguirla sería girar para siempre.

    Raises:
        CorruptFileError: si la cadena tiene un ciclo.
    """
    page_id = first
    visited = 0
    while page_id != NO_PAGE:
        visited += 1
        if visited > page_count:
            raise CorruptFileError(f"{file_name} tiene una cadena de páginas que vuelve sobre sí")
        following, page = load(page_id)
        yield page_id, page
        page_id = following


class RecordAppender:
    """Añade registros de tamaño fijo en páginas nuevas al final del archivo, llenando cada
    página antes de empezar la siguiente.

    Solo la página en curso vive en memoria; `finish` escribe la última aunque no esté
    llena. Mientras se añade, nadie más puede reservar páginas en ese archivo: las de los
    registros tienen que quedar consecutivas.
    """

    def __init__(self, pager: Pager, record_size: int) -> None:
        self._pager = pager
        self._record_size = record_size
        self._page = RecordPage.create(pager.page_size, record_size)
        self.record_count = 0

    @property
    def page_capacity(self) -> int:
        return self._page.capacity

    def append(self, record: bytes) -> None:
        if self._page.used_slots == self._page.capacity:
            self._write_page()
        self._page.insert_at(self._page.used_slots, record)
        self.record_count += 1

    def extend(self, records: Iterable[bytes]) -> None:
        for record in records:
            self.append(record)

    def finish(self) -> None:
        if self._page.used_slots > 0:
            self._write_page()

    def _write_page(self) -> None:
        self._pager.write(self._pager.allocate(), self._page.to_bytes())
        self._page = RecordPage.create(self._pager.page_size, self._record_size)
