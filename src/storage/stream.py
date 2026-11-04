"""Flujos de bytes sobre páginas: datos de longitud variable guardados de corrido.

Una lista de postings o un bloque de SPIMI no son registros de tamaño fijo, sino una tira
de bytes que puede medir menos de una página o cientos. Aquí se escriben seguidos, sin
huecos ni cabecera por página, en páginas consecutivas a partir de una dada: el byte `b`
del flujo está en la página `first_page + b // page_size`, en la posición
`b % page_size`. Leer un tramo es leer las páginas que lo cubren, y recorrer el flujo de
principio a fin es leer cada página una sola vez.

El flujo no guarda su longitud: la última página va rellenada con ceros, así que quien lo
escribe anota `size` en su propia cabecera.
"""

from __future__ import annotations

from collections.abc import Iterator

from .pager import Pager
from .types import StorageError

NO_PAGE_LOADED = -1


class StreamBoundsError(StorageError):
    """Se pidió leer más allá del final del flujo."""


class ByteStreamWriter:
    """Escribe un flujo de bytes en páginas nuevas al final del archivo.

    El flujo empieza en la página que sigue a la última del archivo al crearse el
    escritor (`first_page`). Mientras se escribe, nadie más puede reservar páginas en ese
    archivo: las del flujo tienen que quedar consecutivas.
    """

    def __init__(self, pager: Pager) -> None:
        self._pager = pager
        self._page = bytearray()
        self.first_page = pager.page_count
        self.size = 0

    def write(self, data: bytes) -> None:
        """Añade los bytes al final del flujo, en `O(len(data))`."""
        page_size = self._pager.page_size
        view = memoryview(data)
        while view:
            room = page_size - len(self._page)
            self._page += view[:room]
            view = view[room:]
            if len(self._page) == page_size:
                self._write_page()
        self.size += len(data)

    def finish(self) -> None:
        """Escribe la última página, rellenada con ceros."""
        if self._page:
            self._page.extend(bytes(self._pager.page_size - len(self._page)))
            self._write_page()

    def _write_page(self) -> None:
        self._pager.write(self._pager.allocate(), bytes(self._page))
        self._page = bytearray()


class ByteStreamReader:
    """Lee un flujo de `size` bytes que empieza en la página `first_page`."""

    def __init__(self, pager: Pager, first_page: int, size: int) -> None:
        self._pager = pager
        self._first_page = first_page
        self._size = size

    @property
    def size(self) -> int:
        return self._size

    @property
    def page_size(self) -> int:
        return self._pager.page_size

    def read(self, position: int, length: int) -> bytes:
        """Los `length` bytes que empiezan en `position`, leyendo solo las páginas que los
        cubren: `⌈length / page_size⌉ + 1` como mucho.

        Raises:
            StreamBoundsError: si el tramo se sale del flujo.
        """
        self.check_bounds(position, length)
        if length == 0:
            return b""
        page_size = self._pager.page_size
        first, last = position // page_size, (position + length - 1) // page_size
        covered = b"".join(
            self._pager.read(self._first_page + page) for page in range(first, last + 1)
        )
        start = position - first * page_size
        return covered[start : start + length]

    def cursor(self) -> ByteStreamCursor:
        """Un lector secuencial desde el principio del flujo."""
        return ByteStreamCursor(self)

    def page(self, index: int) -> bytes:
        """La página `index` del flujo, contada desde su primera."""
        return self._pager.read(self._first_page + index)

    def check_bounds(self, position: int, length: int) -> None:
        """Raises:
            StreamBoundsError: si el tramo de `length` bytes desde `position` se sale."""
        if position < 0 or length < 0 or position + length > self._size:
            raise StreamBoundsError(
                f"{self._pager.path.name}: se pidieron {length} bytes desde {position} en un "
                f"flujo de {self._size}"
            )


class ByteStreamCursor:
    """Recorre un flujo de principio a fin, con su página en curso en memoria.

    Las lecturas pequeñas y seguidas —la cabecera de un término, luego el término— salen
    de la misma página sin volver a pedirla al pager.
    """

    def __init__(self, stream: ByteStreamReader) -> None:
        self._stream = stream
        self._position = 0
        self._page_index = NO_PAGE_LOADED
        self._page = b""

    @property
    def position(self) -> int:
        return self._position

    def read(self, length: int) -> bytes:
        """Los siguientes `length` bytes.

        Raises:
            StreamBoundsError: si el flujo se acaba antes.
        """
        return b"".join(self.chunks(length))

    def chunks(self, length: int) -> Iterator[bytes]:
        """Los siguientes `length` bytes en trozos de una página como mucho, para copiar un
        tramo largo sin tenerlo entero en memoria.

        Raises:
            StreamBoundsError: si el flujo se acaba antes.
        """
        self._stream.check_bounds(self._position, length)
        page_size = self._stream.page_size
        remaining = length
        while remaining > 0:
            page_index, offset = divmod(self._position, page_size)
            piece = self._load(page_index)[offset : offset + remaining]
            self._position += len(piece)
            remaining -= len(piece)
            yield piece

    def _load(self, page_index: int) -> bytes:
        if page_index != self._page_index:
            self._page = self._stream.page(page_index)
            self._page_index = page_index
        return self._page
