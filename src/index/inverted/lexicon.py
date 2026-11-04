"""El léxico: los términos en orden, con su df, su frecuencia total y dónde está su lista.

Son dos archivos. En el **léxico**, páginas de registros fijos ordenados por los bytes
UTF-8 del término (el mismo orden que el de los puntos de código); cada registro dice
dónde está su término en el **archivo de términos**, que los guarda uno detrás de otro sin
relleno. Los términos de una página del léxico quedan contiguos ahí, así que se leen de un
solo tramo. Detrás de los registros, el léxico guarda el primer término de cada página
(las *fronteras*), que se cargan al abrir: una entrada por página.

Buscar un término es una búsqueda binaria en memoria sobre las fronteras y otra dentro de
la única página que puede contenerlo.
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from config import EngineConfig
from storage.layout import RecordAppender
from storage.page import RecordPage, slot_capacity
from storage.pager import Pager
from storage.stream import ByteStreamReader, ByteStreamWriter

from .format import (
    FIRST_DATA_PAGE,
    LANGUAGE_PADDING,
    LEXICON_HEADER,
    LEXICON_RECORD,
    TERMS_HEADER,
    InvertedFormatError,
    LexiconEntry,
)

FENCE_LENGTH = struct.Struct("<H")


class LexiconWriter:
    """Escribe los términos en orden creciente; las fronteras y las cabeceras, al cerrar."""

    def __init__(self, lexicon_path: Path, terms_path: Path, config: EngineConfig) -> None:
        self._pager = Pager(lexicon_path, config)
        self._pager.allocate()
        self._terms_pager = Pager(terms_path, config)
        self._terms_pager.allocate()
        self._records = RecordAppender(self._pager, LEXICON_RECORD.size)
        self._terms = ByteStreamWriter(self._terms_pager)
        self._fences: list[bytes] = []

    def append(
        self, term: bytes, document_frequency: int, total_frequency: int, postings_offset: int
    ) -> None:
        if self._records.record_count % self._records.page_capacity == 0:
            self._fences.append(term)
        self._records.append(
            LEXICON_RECORD.pack(
                self._terms.size, len(term), document_frequency, total_frequency, postings_offset
            )
        )
        self._terms.write(term)

    def close(
        self, language: bytes, posting_count: int, block_count: int, merge_passes: int
    ) -> None:
        """Escribe la última página, las fronteras y las cabeceras, y cierra los archivos.

        `language` es el código de idioma ya codificado con `encode_language`.
        """
        self._records.finish()
        fences = ByteStreamWriter(self._pager)
        for fence in self._fences:
            fences.write(FENCE_LENGTH.pack(len(fence)) + fence)
        fences.finish()
        self._terms.finish()
        LEXICON_HEADER.write(
            self._pager,
            language,
            self._records.record_count,
            self._terms.size,
            posting_count,
            fences.size,
            block_count,
            merge_passes,
        )
        TERMS_HEADER.write(self._terms_pager, self._terms.size)
        self._pager.close()
        self._terms_pager.close()

    def discard(self) -> None:
        """Cierra sin escribir nada más: la construcción falló y los archivos se borrarán."""
        self._pager.discard()
        self._terms_pager.discard()


@dataclass(frozen=True, slots=True)
class _LexiconPage:
    """Una página del léxico con el tramo del archivo de términos que le corresponde."""

    records: RecordPage
    terms: bytes
    first_term_offset: int

    def term(self, slot: int) -> bytes:
        term_offset, term_length, *_ = LEXICON_RECORD.unpack(self.records.record_at(slot))
        start = term_offset - self.first_term_offset
        return self.terms[start : start + term_length]

    def entry(self, slot: int) -> LexiconEntry:
        _, _, document_frequency, total_frequency, postings_offset = LEXICON_RECORD.unpack(
            self.records.record_at(slot)
        )
        return LexiconEntry(
            self.term(slot).decode(), document_frequency, total_frequency, postings_offset
        )


class Lexicon:
    """Busca términos en el léxico.

    Raises:
        InvertedFormatError: si los archivos no son un léxico y un archivo de términos de
            este formato, o no son del mismo índice.
    """

    def __init__(self, lexicon_path: Path, terms_path: Path, config: EngineConfig) -> None:
        self._pager = Pager(lexicon_path, config)
        self._terms_pager = Pager(terms_path, config)
        language, term_count, term_bytes, posting_count, fence_bytes, blocks, passes = (
            LEXICON_HEADER.read(self._pager)
        )
        (stored_term_bytes,) = TERMS_HEADER.read(self._terms_pager)
        if stored_term_bytes != term_bytes:
            raise InvertedFormatError(
                f"{lexicon_path.name} cuenta {term_bytes} bytes de términos y "
                f"{terms_path.name} {stored_term_bytes}"
            )
        self.language: str = language.rstrip(LANGUAGE_PADDING).decode()
        self.term_count: int = term_count
        self.posting_count: int = posting_count
        self.block_count: int = blocks
        self.merge_passes: int = passes
        self._terms = ByteStreamReader(self._terms_pager, FIRST_DATA_PAGE, term_bytes)
        self._capacity = slot_capacity(config.page_size, LEXICON_RECORD.size)
        self.record_page_count: int = -(-term_count // self._capacity)
        self._fences = self._load_fences(fence_bytes)

    @property
    def page_count(self) -> int:
        return self._pager.page_count

    @property
    def terms_page_count(self) -> int:
        return self._terms_pager.page_count

    @property
    def term_bytes(self) -> int:
        return self._terms.size

    def find(self, term: str) -> LexiconEntry | None:
        """La entrada del término, o `None` si no está: una búsqueda binaria en memoria,
        una página del léxico y el tramo de sus términos, `O(log V)` comparaciones."""
        key = term.encode()
        page_index = _last_at_most(len(self._fences), key, self._fences.__getitem__)
        if page_index is None:
            return None
        page = self._page(page_index)
        slot = _last_at_most(page.records.used_slots, key, page.term)
        if slot is None or page.term(slot) != key:
            return None
        return page.entry(slot)

    def entries(self) -> Iterator[LexiconEntry]:
        """Todos los términos en orden, página a página."""
        for page_index in range(self.record_page_count):
            page = self._page(page_index)
            for slot in range(page.records.used_slots):
                yield page.entry(slot)

    def flush(self) -> None:
        self._pager.flush()
        self._terms_pager.flush()

    def close(self) -> None:
        self._pager.close()
        self._terms_pager.close()

    def _page(self, page_index: int) -> _LexiconPage:
        records = RecordPage.from_bytes(
            self._pager.read(FIRST_DATA_PAGE + page_index), LEXICON_RECORD.size
        )
        first_offset, *_ = LEXICON_RECORD.unpack(records.record_at(0))
        last_offset, last_length, *_ = LEXICON_RECORD.unpack(
            records.record_at(records.used_slots - 1)
        )
        terms = self._terms.read(first_offset, last_offset + last_length - first_offset)
        return _LexiconPage(records, terms, first_offset)

    def _load_fences(self, fence_bytes: int) -> list[bytes]:
        first_page = FIRST_DATA_PAGE + self.record_page_count
        cursor = ByteStreamReader(self._pager, first_page, fence_bytes).cursor()
        fences = []
        for _ in range(self.record_page_count):
            (length,) = FENCE_LENGTH.unpack(cursor.read(FENCE_LENGTH.size))
            fences.append(cursor.read(length))
        return fences


def _last_at_most(count: int, key: bytes, key_at: Callable[[int], bytes]) -> int | None:
    """La última posición de una secuencia ordenada de `count` claves cuya clave no pasa de
    `key`, o `None` si todas pasan."""
    low, high = 0, count
    while low < high:
        middle = (low + high) // 2
        if key_at(middle) <= key:
            low = middle + 1
        else:
            high = middle
    return low - 1 if low > 0 else None
