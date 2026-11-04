"""Los bloques de SPIMI en disco y la mezcla k-vías de listas ordenadas por término.

Un bloque son dos flujos de bytes temporales:

* `.terms`: por cada término, en orden creciente de sus bytes, una cabecera
  `(longitud, df, frecuencia total)` seguida del término;
* `.postings`: las listas de esos términos, en el mismo orden, como pares `(doc_id, tf)`.

Separarlos es lo que deja mezclar sin cargar listas: la mezcla lee las cabeceras de todos
los bloques que comparten un término —y para saber que el grupo terminó tiene que leer
la siguiente— antes de copiar una sola lista, y cada flujo de postings se recorre una
vez, en orden, con una página en memoria.

La mezcla no sabe de dónde vienen los términos: de bloques al construir y de segmentos
del índice al mantenerlo (`TermSource`). Si son más que `merge_fan_in`, `MergePasses` los
reduce por pasadas con bloques intermedios, como el ordenamiento externo.
"""

from __future__ import annotations

import struct
from array import array
from collections.abc import Callable, Container, Iterable, Iterator, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from functools import partial
from itertools import chain, groupby
from pathlib import Path
from typing import NamedTuple, Protocol

from config import EngineConfig
from external.runs import merge_sorted_streams, remove_if_empty
from storage.pager import Pager
from storage.stream import ByteStreamReader, ByteStreamWriter

from .format import (
    POSTING_BYTES,
    POSTING_FIELDS,
    POSTING_TYPECODE,
    decode_postings,
    encode_postings,
)

BLOCK_TERM_HEADER = struct.Struct("<HIQ")
TERMS_SUFFIX = ".terms"
POSTINGS_SUFFIX = ".postings"
BLOCK_PREFIX = "block"
FIRST_STREAM_PAGE = 0


class TermSink(Protocol):
    """Lo que recibe los términos en orden: un bloque nuevo o un segmento del índice."""

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


class TermStream(Protocol):
    """Términos en orden creciente de sus bytes y, después de cada uno, su lista."""

    def terms(self) -> Iterator[BlockTerm]: ...

    def postings(self, document_frequency: int) -> Iterator[bytes]:
        """La lista del último término entregado por `terms`, en trozos."""

    def close(self) -> None: ...


class TermSource(Protocol):
    """Lo que la mezcla sabe recorrer: un bloque de SPIMI o un segmento del índice."""

    def open(self, config: EngineConfig) -> TermStream: ...


@dataclass(frozen=True, slots=True)
class Block:
    """Un bloque escrito: sus dos archivos y lo que miden sus flujos."""

    terms_path: Path
    postings_path: Path
    term_count: int
    terms_size: int
    postings_size: int

    def open(self, config: EngineConfig) -> TermStream:
        return BlockReader(self, config)

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


def merge_sources(sources: Sequence[TermSource], config: EngineConfig, sink: TermSink) -> None:
    """Mezcla k-vías de las fuentes hacia `sink`, término a término.

    Las listas de un mismo término se concatenan en el orden de `sources`. Como cada fuente
    tiene `doc_id` mayores que los de la anterior, la lista concatenada sigue ordenada por
    `doc_id` sin comparar uno solo. Memoria: una página de cada flujo de cada fuente.
    """
    with ExitStack() as opened:
        streams: list[TermStream] = []
        for source in sources:
            stream = source.open(config)
            opened.callback(stream.close)
            streams.append(stream)
        merged = merge_sorted_streams([stream.terms() for stream in streams], _term_of)
        for term, group in groupby(merged, key=_merged_term_of):
            members = list(group)
            sink.add_term(
                term,
                sum(entry.document_frequency for _, entry in members),
                sum(entry.total_frequency for _, entry in members),
                chain.from_iterable(
                    streams[position].postings(entry.document_frequency)
                    for position, entry in members
                ),
            )


class PurgingSink:
    """Un `TermSink` que quita de cada lista los postings de documentos borrados antes de
    pasarla a `sink`, con su df y su frecuencia total recontados; un término que se queda
    sin postings no llega. Es lo que hace la fusión de segmentos con las lápidas.

    Para recontar necesita la lista entera en memoria: la de un término, nunca el índice.
    """

    def __init__(self, sink: TermSink, deleted: Container[int]) -> None:
        self._sink = sink
        self._deleted = deleted

    def add_term(
        self,
        term: bytes,
        document_frequency: int,
        total_frequency: int,
        postings: Iterable[bytes],
    ) -> None:
        values = decode_postings(b"".join(postings))
        deleted = self._deleted
        kept = array(POSTING_TYPECODE)
        for doc_id, frequency in zip(
            values[0::POSTING_FIELDS], values[1::POSTING_FIELDS], strict=True
        ):
            if doc_id not in deleted:
                kept.append(doc_id)
                kept.append(frequency)
        if kept:
            self._sink.add_term(
                term,
                len(kept) // POSTING_FIELDS,
                sum(kept[1::POSTING_FIELDS]),
                (encode_postings(kept),),
            )


class MergePasses:
    """Bloques temporales en un directorio y la mezcla por pasadas con abanico acotado.

    `reduce` deja una lista de fuentes en `merge_fan_in` o menos: cada grupo de fuentes
    **consecutivas** pasa a ser un bloque, lo que conserva el orden de los `doc_id` entre
    fuentes. Solo borra los bloques que escribió él; las fuentes que recibe (los segmentos
    del índice) no son suyas. `remove_all` borra lo que quede, también tras un error.
    """

    def __init__(self, directory: Path, config: EngineConfig) -> None:
        self._directory = directory
        self._config = config
        self._owned: list[Block] = []
        self._created = 0
        self.passes = 0

    def write_block(self, fill: Callable[[TermSink], None]) -> Block:
        """Escribe un bloque temporal con lo que `fill` le entregue."""
        writer = BlockWriter(self._new_block_base(), self._config)
        try:
            fill(writer)
        except BaseException:
            writer.discard()
            raise
        block = writer.close()
        self._owned.append(block)
        return block

    def reduce(self, sources: Sequence[TermSource]) -> list[TermSource]:
        """Las fuentes que quedan tras las pasadas necesarias, en el mismo orden."""
        fan_in = self._config.merge_fan_in
        remaining = list(sources)
        while len(remaining) > fan_in:
            merged: list[TermSource] = []
            for start in range(0, len(remaining), fan_in):
                group = remaining[start : start + fan_in]
                merged.append(self.write_block(partial(merge_sources, group, self._config)))
                self._release(group)
            remaining = merged
            self.passes += 1
        return remaining

    def remove_all(self) -> None:
        for block in self._owned:
            block.remove()
        self._owned.clear()
        remove_if_empty(self._directory)

    def _release(self, group: Sequence[TermSource]) -> None:
        for block in [block for block in self._owned if block in group]:
            block.remove()
            self._owned.remove(block)

    def _new_block_base(self) -> Path:
        """Nombre nunca reutilizado: reciclarlo pisaría un bloque que aún se está leyendo."""
        self._directory.mkdir(parents=True, exist_ok=True)
        base = self._directory / f"{BLOCK_PREFIX}{self._created}"
        self._created += 1
        return base


def _term_of(entry: BlockTerm) -> bytes:
    return entry.term


def _merged_term_of(item: tuple[int, BlockTerm]) -> bytes:
    return item[1].term


def _fresh_pager(path: Path, config: EngineConfig) -> Pager:
    """Un pager sobre un archivo vacío: uno viejo con el mismo nombre dejaría sus páginas
    delante del flujo."""
    path.unlink(missing_ok=True)
    return Pager(path, config)
