"""Los bloques de SPIMI en disco y su mezcla k-vías.

Un bloque son dos flujos de bytes temporales:

* `.terms`: por cada término, en orden creciente de sus bytes, una cabecera
  `(longitud, df, frecuencia total)` seguida del término;
* `.postings`: las listas de esos términos, en el mismo orden, como pares `(doc_id, tf)`.

Separarlos es lo que deja mezclar sin cargar listas: la mezcla lee las cabeceras de todos
los bloques que comparten un término —y para saber que el grupo terminó tiene que leer
la siguiente— antes de copiar una sola lista, y cada flujo de postings se recorre una
vez, en orden, con una página en memoria.
"""

from __future__ import annotations

import struct
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from itertools import chain, groupby
from pathlib import Path
from typing import NamedTuple, Protocol

from config import EngineConfig
from external.runs import merge_sorted_streams
from storage.pager import Pager
from storage.stream import ByteStreamReader, ByteStreamWriter

from .format import POSTING_BYTES

BLOCK_TERM_HEADER = struct.Struct("<HIQ")
TERMS_SUFFIX = ".terms"
POSTINGS_SUFFIX = ".postings"
FIRST_STREAM_PAGE = 0


class TermSink(Protocol):
    """Lo que recibe los términos en orden: un bloque nuevo o el índice final."""

    def add_term(
        self,
        term: bytes,
        document_frequency: int,
        total_frequency: int,
        postings: Iterable[bytes],
    ) -> None:
        """Un término con su lista, que llega en trozos de bytes ya codificados."""


class BlockTerm(NamedTuple):
    term: bytes
    document_frequency: int
    total_frequency: int


@dataclass(frozen=True, slots=True)
class Block:
    """Un bloque escrito: sus dos archivos y lo que miden sus flujos."""

    terms_path: Path
    postings_path: Path
    term_count: int
    terms_size: int
    postings_size: int

    def remove(self) -> None:
        self.terms_path.unlink(missing_ok=True)
        self.postings_path.unlink(missing_ok=True)


class BlockWriter:
    """Escribe un bloque; es un `TermSink`."""

    def __init__(self, base: Path, config: EngineConfig) -> None:
        self._terms_path = base.with_name(base.name + TERMS_SUFFIX)
        self._postings_path = base.with_name(base.name + POSTINGS_SUFFIX)
        self._terms_pager = _fresh_pager(self._terms_path, config)
        self._postings_pager = _fresh_pager(self._postings_path, config)
        self._terms = ByteStreamWriter(self._terms_pager)
        self._postings = ByteStreamWriter(self._postings_pager)
        self._term_count = 0

    def add_term(
        self,
        term: bytes,
        document_frequency: int,
        total_frequency: int,
        postings: Iterable[bytes],
    ) -> None:
        self._terms.write(
            BLOCK_TERM_HEADER.pack(len(term), document_frequency, total_frequency) + term
        )
        for chunk in postings:
            self._postings.write(chunk)
        self._term_count += 1

    def close(self) -> Block:
        """Termina los dos flujos y devuelve el bloque."""
        for stream, pager in (
            (self._terms, self._terms_pager),
            (self._postings, self._postings_pager),
        ):
            stream.finish()
            pager.close()
        return Block(
            self._terms_path,
            self._postings_path,
            self._term_count,
            self._terms.size,
            self._postings.size,
        )


    def discard(self) -> None:
        """Cierra y borra el bloque a medio escribir."""
        for pager in (self._terms_pager, self._postings_pager):
            pager.discard()
        self._terms_path.unlink(missing_ok=True)
        self._postings_path.unlink(missing_ok=True)


class BlockReader:
    """Recorre un bloque: sus términos en orden y, de cada uno, su lista."""

    def __init__(self, block: Block, config: EngineConfig) -> None:
        self._block = block
        self._terms_pager = Pager(block.terms_path, config)
        self._postings_pager = Pager(block.postings_path, config)
        self._terms = ByteStreamReader(
            self._terms_pager, FIRST_STREAM_PAGE, block.terms_size
        ).cursor()
        self._postings = ByteStreamReader(
            self._postings_pager, FIRST_STREAM_PAGE, block.postings_size
        ).cursor()

    def terms(self) -> Iterator[BlockTerm]:
        for _ in range(self._block.term_count):
            length, document_frequency, total_frequency = BLOCK_TERM_HEADER.unpack(
                self._terms.read(BLOCK_TERM_HEADER.size)
            )
            yield BlockTerm(self._terms.read(length), document_frequency, total_frequency)

    def postings(self, document_frequency: int) -> Iterator[bytes]:
        """La siguiente lista del bloque, en trozos de una página como mucho."""
        return self._postings.chunks(document_frequency * POSTING_BYTES)

    def close(self) -> None:
        self._terms_pager.close()
        self._postings_pager.close()


def merge_blocks(blocks: Sequence[Block], config: EngineConfig, sink: TermSink) -> None:
    """Mezcla k-vías de los bloques hacia `sink`, término a término.

    Las listas de un mismo término se concatenan en el orden de `blocks`. Como cada bloque
    tiene `doc_id` mayores que los del anterior, la lista concatenada sigue ordenada por
    `doc_id` sin comparar uno solo. Memoria: una página de cada flujo de cada bloque.
    """
    readers = [BlockReader(block, config) for block in blocks]
    try:
        merged = merge_sorted_streams([reader.terms() for reader in readers], _term_of)
        for term, group in groupby(merged, key=_merged_term_of):
            members = list(group)
            sink.add_term(
                term,
                sum(entry.document_frequency for _, entry in members),
                sum(entry.total_frequency for _, entry in members),
                chain.from_iterable(
                    readers[position].postings(entry.document_frequency)
                    for position, entry in members
                ),
            )
    finally:
        for reader in readers:
            reader.close()


def _term_of(entry: BlockTerm) -> bytes:
    return entry.term


def _merged_term_of(item: tuple[int, BlockTerm]) -> bytes:
    return item[1].term


def _fresh_pager(path: Path, config: EngineConfig) -> Pager:
    """Un pager sobre un archivo vacío: uno viejo con el mismo nombre dejaría sus páginas
    delante del flujo."""
    path.unlink(missing_ok=True)
    return Pager(path, config)
