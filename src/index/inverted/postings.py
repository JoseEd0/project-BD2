"""El archivo de postings: las listas de todos los términos, una detrás de otra.

La lista de un término es contigua y está ordenada por `doc_id`, así que leerla es leer
las páginas consecutivas que la cubren. Su posición se cuenta en postings desde el
principio del flujo; el léxico la guarda junto al número de postings de la lista.
"""

from __future__ import annotations

from array import array
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from config import EngineConfig
from storage.pager import Pager
from storage.stream import ByteStreamReader, ByteStreamWriter

from .format import (
    FIRST_DATA_PAGE,
    POSTING_BYTES,
    POSTING_FIELDS,
    POSTINGS_HEADER,
    InvertedFormatError,
    decode_postings,
)


@dataclass(frozen=True, slots=True)
class PostingList:
    """La lista de un término: `doc_ids` en orden creciente y el `tf` de cada uno en la
    misma posición de `term_frequencies`. Son `array('I')`, sin un objeto por posting."""

    doc_ids: array[int]
    term_frequencies: array[int]

    @classmethod
    def decode(cls, raw: bytes) -> PostingList:
        values = decode_postings(raw)
        return cls(values[0::POSTING_FIELDS], values[1::POSTING_FIELDS])

    def __len__(self) -> int:
        return len(self.doc_ids)


EMPTY_POSTING_LIST = PostingList.decode(b"")


class PostingsWriter:
    """Escribe las listas una tras otra, en el orden del léxico."""

    def __init__(self, path: Path, config: EngineConfig) -> None:
        self._pager = Pager(path, config)
        self._pager.allocate()
        self._stream = ByteStreamWriter(self._pager)
        self.posting_count = 0

    def append(self, postings: Iterable[bytes], count: int) -> int:
        """Añade la lista de `count` postings, que llega en trozos, y devuelve su posición.

        Raises:
            InvertedFormatError: si los trozos no suman `count` postings.
        """
        offset = self.posting_count
        for chunk in postings:
            self._stream.write(chunk)
        self.posting_count += count
        if self._stream.size != self.posting_count * POSTING_BYTES:
            raise InvertedFormatError(
                f"una lista de {count} postings llegó con otro tamaño: el flujo mide "
                f"{self._stream.size} bytes"
            )
        return offset

    def close(self) -> None:
        self._stream.finish()
        POSTINGS_HEADER.write(self._pager, self.posting_count)
        self._pager.close()

    def discard(self) -> None:
        """Cierra sin escribir nada más: la construcción falló y el archivo se borrará."""
        self._pager.discard()


class PostingsFile:
    """Lee listas del archivo de postings.

    Raises:
        InvertedFormatError: si el archivo no es un archivo de postings de este formato.
    """

    def __init__(self, path: Path, config: EngineConfig) -> None:
        self._pager = Pager(path, config)
        (posting_count,) = POSTINGS_HEADER.read(self._pager)
        self.posting_count: int = posting_count
        self._stream = ByteStreamReader(
            self._pager, FIRST_DATA_PAGE, self.posting_count * POSTING_BYTES
        )

    @property
    def page_count(self) -> int:
        return self._pager.page_count

    def read(self, offset: int, count: int) -> PostingList:
        """La lista de `count` postings que empieza en `offset`: lee `⌈8·count / página⌉ + 1`
        páginas como mucho."""
        return PostingList.decode(self._stream.read(offset * POSTING_BYTES, count * POSTING_BYTES))

    def flush(self) -> None:
        self._pager.flush()

    def close(self) -> None:
        self._pager.close()
