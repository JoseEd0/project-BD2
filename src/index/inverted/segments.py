"""Los segmentos del índice y el manifiesto que dice cuáles hay.

Un **segmento** es un léxico, sus términos y sus listas de postings: el formato de siempre
(ver `README.md`) para un tramo de `doc_id`. La construcción escribe el primero; cada
`flush` con documentos nuevos escribe otro, y una fusión reemplaza varios por uno. Como
los `doc_id` se reparten en orden, cada segmento tiene `doc_id` mayores que los del
anterior: la lista de un término en el índice entero es la concatenación de sus listas
en el orden del manifiesto.

El **manifiesto** enumera los segmentos vivos por su generación. No es una estructura
paginada sino un descriptor de pocos bytes, que se escribe aparte y reemplaza al anterior
con un `rename`: es el punto en que un `flush` o una fusión pasan a existir, como el
`segments_N` de Lucene. Leerlo no necesita la configuración del motor, y por eso
`files_of` puede enumerar los segmentos para `DROP INDEX`.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from config import EngineConfig
from storage.stream import ByteStreamCursor

from .blocks import BlockTerm, TermSink, TermStream
from .format import (
    FIRST_GENERATION,
    GENERATION,
    MANIFEST_HEADER,
    POSTING_BYTES,
    InvertedFormatError,
    LexiconEntry,
    lexicon_path,
    postings_path,
    replacement_path,
    segment_base,
    segment_files,
    terms_path,
)
from .lexicon import Lexicon, LexiconWriter
from .postings import PostingList, PostingsFile, PostingsWriter


class SegmentView(Protocol):
    """Lo que el índice lee de un segmento, en disco o el bloque aún en memoria."""

    @property
    def term_count(self) -> int: ...

    @property
    def posting_count(self) -> int: ...

    def find(self, term: str) -> LexiconEntry | None: ...

    def read(self, entry: LexiconEntry) -> PostingList: ...

    def entries(self) -> Iterator[LexiconEntry]: ...


class SegmentWriter:
    """Escribe un segmento: el destino de la última mezcla o del bloque en memoria."""

    def __init__(self, base: Path, config: EngineConfig) -> None:
        self._lexicon = LexiconWriter(lexicon_path(base), terms_path(base), config)
        self._postings = PostingsWriter(postings_path(base), config)

    def add_term(
        self,
        term: bytes,
        document_frequency: int,
        total_frequency: int,
        postings: Iterable[bytes],
    ) -> None:
        offset = self._postings.append(postings, document_frequency)
        self._lexicon.append(term, document_frequency, total_frequency, offset)

    def close(self, language: bytes, block_count: int, merge_passes: int) -> None:
        self._lexicon.close(language, self._postings.posting_count, block_count, merge_passes)
        self._postings.close()

    def discard(self) -> None:
        self._lexicon.discard()
        self._postings.discard()


def write_segment(
    base: Path,
    fill: Callable[[TermSink], None],
    language: bytes,
    block_count: int,
    merge_passes: int,
    config: EngineConfig,
) -> None:
    """Escribe en `base` el segmento de los términos que `fill` entregue en orden. Si falla,
    no deja ningún archivo.

    `language` es el código ya codificado; `block_count` y `merge_passes` describen cómo
    se obtuvo y quedan en la cabecera del léxico.
    """
    _remove(segment_files(base))
    writer = SegmentWriter(base, config)
    try:
        fill(writer)
    except BaseException:
        writer.discard()
        _remove(segment_files(base))
        raise
    writer.close(language, block_count, merge_passes)


class Segment:
    """Un segmento abierto: buscar un término, leer su lista y recorrerlo para fusionarlo.

    Raises:
        InvertedFormatError: si los archivos no son un segmento de este formato o no son
            del mismo segmento.
    """

    def __init__(self, path: Path, generation: int, config: EngineConfig) -> None:
        base = segment_base(path, generation)
        self.generation = generation
        self._lexicon = Lexicon(lexicon_path(base), terms_path(base), config)
        try:
            self._postings = PostingsFile(postings_path(base), config)
        except BaseException:
            self._lexicon.close()
            raise
        if self._postings.posting_count != self._lexicon.posting_count:
            self.close()
            raise InvertedFormatError(
                f"{base.name}: el léxico cuenta {self._lexicon.posting_count} postings y "
                f"el archivo de postings {self._postings.posting_count}"
            )

    @property
    def language(self) -> str:
        return self._lexicon.language

    @property
    def term_count(self) -> int:
        return self._lexicon.term_count

    @property
    def posting_count(self) -> int:
        return self._lexicon.posting_count

    @property
    def block_count(self) -> int:
        """Bloques de SPIMI, o segmentos fusionados, de los que salió."""
        return self._lexicon.block_count

    @property
    def merge_passes(self) -> int:
        return self._lexicon.merge_passes

    @property
    def lexicon_pages(self) -> int:
        return self._lexicon.record_page_count

    @property
    def term_bytes(self) -> int:
        return self._lexicon.term_bytes

    def find(self, term: str) -> LexiconEntry | None:
        return self._lexicon.find(term)

    def read(self, entry: LexiconEntry) -> PostingList:
        return self._postings.read(entry.postings_offset, entry.document_frequency)

    def entries(self) -> Iterator[LexiconEntry]:
        return self._lexicon.entries()

    def postings_cursor(self) -> ByteStreamCursor:
        """Todas las listas en el orden del léxico, de una pasada."""
        return self._postings.cursor()

    def open(self, config: EngineConfig) -> TermStream:
        """El segmento como fuente de la mezcla: el léxico en orden y las listas detrás,
        de una pasada cada uno."""
        return _SegmentStream(self)

    def file_bytes(self, page_size: int) -> dict[str, int]:
        return {
            "lexicon": self._lexicon.page_count * page_size,
            "terms": self._lexicon.terms_page_count * page_size,
            "postings": self._postings.page_count * page_size,
        }

    def flush(self) -> None:
        self._lexicon.flush()
        self._postings.flush()

    def close(self) -> None:
        self._lexicon.close()
        self._postings.close()


class _SegmentStream:
    """Un segmento recorrido como `TermStream`. Los archivos son del segmento, que sigue
    abierto: cerrar el recorrido no los cierra."""

    def __init__(self, segment: Segment) -> None:
        self._segment = segment
        self._postings = segment.postings_cursor()

    def terms(self) -> Iterator[BlockTerm]:
        for entry in self._segment.entries():
            yield BlockTerm(entry.term.encode(), entry.document_frequency, entry.total_frequency)

    def postings(self, document_frequency: int) -> Iterator[bytes]:
        return self._postings.chunks(document_frequency * POSTING_BYTES)

    def close(self) -> None:
        """Nada que soltar: ver la clase."""


@dataclass(frozen=True, slots=True)
class Manifest:
    """Los segmentos vivos, en orden de `doc_id`, y lo que el mantenimiento lleva contado.

    Attributes:
        generations: la generación de cada segmento; la del primero es la del principal.
        next_generation: la que recibirá el próximo segmento; nunca se reutiliza.
        purged_count: documentos borrados cuyos postings ya quitó una fusión completa.
        merge_count: fusiones completas hechas desde la construcción.
    """

    generations: tuple[int, ...]
    next_generation: int
    purged_count: int = 0
    merge_count: int = 0

    @classmethod
    def of_build(cls) -> Manifest:
        """El de un índice recién construido: solo el segmento que escribió SPIMI."""
        return cls((FIRST_GENERATION,), FIRST_GENERATION + 1)

    def with_segments(self, generations: Iterable[int], next_generation: int) -> Manifest:
        return replace(self, generations=tuple(generations), next_generation=next_generation)


def read_manifest(path: Path) -> Manifest:
    """Raises:
    InvertedFormatError: si el archivo no es un manifiesto de este formato."""
    raw = path.read_bytes()
    next_generation, purged, merges, count = MANIFEST_HEADER.decode(raw, path.name)
    start = MANIFEST_HEADER.size
    expected = start + count * GENERATION.size
    if len(raw) != expected:
        raise InvertedFormatError(
            f"{path.name} enumera {count} segmentos y mide {len(raw)} bytes, no {expected}"
        )
    generations = tuple(
        generation for (generation,) in GENERATION.iter_unpack(raw[start:expected])
    )
    return Manifest(generations, next_generation, purged, merges)


def write_manifest(path: Path, manifest: Manifest) -> None:
    """Escribe el manifiesto aparte, lo lleva al disco y reemplaza al anterior de una vez."""
    raw = MANIFEST_HEADER.encode(
        manifest.next_generation,
        manifest.purged_count,
        manifest.merge_count,
        len(manifest.generations),
    ) + b"".join(GENERATION.pack(generation) for generation in manifest.generations)
    staged = replacement_path(path)
    with staged.open("wb") as file:
        file.write(raw)
        file.flush()
        os.fsync(file.fileno())
    staged.replace(path)


def remove_segment(path: Path, generation: int) -> None:
    _remove(segment_files(segment_base(path, generation)))


def files_of_segments(path: Path, generations: Iterable[int]) -> list[Path]:
    """Los archivos de los segmentos de esas generaciones."""
    return [
        file
        for generation in generations
        for file in segment_files(segment_base(path, generation))
    ]


def _remove(files: Iterable[Path]) -> None:
    for file in files:
        file.unlink(missing_ok=True)

