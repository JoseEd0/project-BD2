"""El índice invertido abierto: sus segmentos, el bloque en memoria, la tabla de documentos
y el mapa de direcciones.

Es lo que consume el ranking: el df y la lista de cada término de la consulta, y de cada
documento su longitud y su norma. No sabe de tablas ni de SQL; un documento es un texto
con la dirección de su fila.

Después de construirse se mantiene por **segmentos**, como Lucene (ver `README.md`):

* insertar analiza el texto, apunta el documento y deja sus postings en un bloque SPIMI
  en memoria, que `flush` escribe como un segmento más;
* borrar deja la lápida del documento en la tabla de documentos, en `O(1)` páginas: N y
  la longitud media cambian al instante, y el df sigue contándolo hasta la próxima fusión;
* `flush` fusiona con la mezcla de la construcción: los segmentos pendientes entre sí
  cuando son demasiados, y todo en uno —sin los postings de los borrados— cuando lo nuevo
  o lo borrado pasa de una fracción del índice. Nunca a mitad de una sentencia.
"""

from __future__ import annotations

import heapq
from collections.abc import Callable, Iterable, Iterator
from contextlib import ExitStack
from dataclasses import replace
from itertools import groupby
from pathlib import Path
from types import TracebackType
from typing import Any, NamedTuple

from config import EngineConfig
from external.runs import merge_sorted_streams
from storage.record_id import RecordId
from text import Analyzer

from .blocks import MergePasses, PurgingSink, TermSink, merge_sources
from .document_map import DocumentMap
from .documents import DocumentColumns, DocumentTable
from .format import (
    POSTING_BYTES,
    DocumentEntry,
    InvertedFormatError,
    InvertedIndexExistsError,
    InvertedIndexNotFoundError,
    LexiconEntry,
    build_directory,
    built_files,
    document_map_path,
    documents_path,
    encode_language,
    manifest_path,
    max_term_bytes,
    segment_base,
    segment_files,
)
from .postings import EMPTY_POSTING_LIST, PostingList
from .segments import (
    Manifest,
    Segment,
    SegmentView,
    files_of_segments,
    read_manifest,
    remove_segment,
    write_manifest,
    write_segment,
)
from .spimi import Document, SpimiBlock, SpimiBuilder
from .weights import lnc_norm

DESCRIBE_TOP_TERMS = 10
# Un segmento que sale del bloque en memoria es un bloque escrito sin mezcla.
FLUSHED_BLOCKS = 1
FLUSHED_MERGE_PASSES = 0

SinkWrapper = Callable[[TermSink], TermSink]


class TermEntry(NamedTuple):
    """Un término del índice entero: su df y su frecuencia total sumados entre segmentos
    —con los documentos borrados que aún no quitó una fusión— y su entrada en cada
    segmento que lo tiene, como `(posición del segmento, entrada)`.

    Las posiciones valen hasta el siguiente `flush` o inserción que escriba un segmento.
    """

    term: str
    document_frequency: int
    total_frequency: int
    locations: tuple[tuple[int, LexiconEntry], ...]


class InvertedIndex:
    """Índice invertido en disco, construido con SPIMI y mantenido por segmentos.

    Complejidad, con `V` términos, `N` documentos, `S` segmentos y una lista de `df`
    postings:

    * buscar un término: `O(S log V)` comparaciones en memoria y una página del léxico de
      cada segmento;
    * leer su lista: `⌈8·df / página⌉ + 1` páginas consecutivas por segmento;
    * un documento por `doc_id`: una página, `O(1)`;
    * insertar: analizar el texto, más `O(1)` páginas de la tabla de documentos y del mapa;
    * borrar: `O(1)` páginas;
    * memoria al abrir: el primer término de cada página del léxico de cada segmento.

    `flush` y `close` dejan en disco todo lo insertado y borrado, y el manifiesto que dice
    qué segmentos valen. Lo que no se volcó se puede perder, como en el resto del motor,
    que vuelca al confirmar cada transacción.

    Raises:
        InvertedIndexNotFoundError: si falta alguno de los archivos del índice.
        InvertedFormatError: si los archivos no son de este formato o de un mismo índice.
    """

    def __init__(self, path: Path, config: EngineConfig) -> None:
        missing = [file for file in _fixed_files(path) if not file.is_file()]
        if missing:
            raise InvertedIndexNotFoundError(f"no hay un índice invertido en {missing[0]}")
        self._path = path
        self._config = config
        self._manifest = read_manifest(manifest_path(path))
        if not self._manifest.generations:
            raise InvertedFormatError(f"{manifest_path(path).name} no enumera ningún segmento")
        self._saved_manifest = self._manifest
        self._obsolete: list[int] = []
        self._segments: list[Segment] = []
        with ExitStack() as opened:
            self._documents = DocumentTable(documents_path(path), config)
            opened.callback(self._documents.close)
            self._addresses = DocumentMap(document_map_path(path), config)
            opened.callback(self._addresses.close)
            for generation in self._manifest.generations:
                segment = self._open_segment(generation)
                opened.callback(segment.close)
                self._segments.append(segment)
            opened.pop_all()
        self._analyzer = Analyzer(config, self.language)
        self._block = self._new_block()

    @classmethod
    def build(
        cls,
        path: Path,
        documents: Iterable[Document],
        analyzer: Analyzer,
        config: EngineConfig,
    ) -> InvertedIndex:
        """Construye el índice de los documentos `(dirección de la fila, texto)` con SPIMI
        y lo abre. El `doc_id` de cada documento es su posición en `documents`, y las
        direcciones tienen que ser distintas.

        Raises:
            InvertedIndexExistsError: si ya hay un índice en `path`.
            TermTooLongError: si el analizador produce un término de más de
                `max_term_bytes` bytes.
        """
        existing = [file for file in _every_file(path) if file.exists()]
        if existing:
            raise InvertedIndexExistsError(f"ya hay un índice invertido en {existing[0]}")
        SpimiBuilder(path, analyzer, config).build(documents)
        try:
            _map_addresses(path, config)
            write_manifest(manifest_path(path), Manifest.of_build())
        except BaseException:
            for file in _every_file(path):
                file.unlink(missing_ok=True)
            raise
        return cls(path, config)

    @staticmethod
    def files_of(path: Path) -> list[Path]:
        """Los archivos que ocupa un índice guardado en `path`: la tabla de documentos, el
        manifiesto, el mapa de direcciones y los de cada segmento que enumera el
        manifiesto."""
        files = _fixed_files(path)
        manifest = manifest_path(path)
        if manifest.is_file():
            files.extend(files_of_segments(path, read_manifest(manifest).generations))
        return files

    @property
    def language(self) -> str:
        """El idioma del analizador con el que se construyó; la consulta usa el mismo."""
        return self._segments[0].language

    @property
    def document_count(self) -> int:
        """Documentos vivos: la N del ranking."""
        return self._documents.document_count

    @property
    def deleted_count(self) -> int:
        return self._documents.deleted_count

    @property
    def total_length(self) -> int:
        """Suma de las longitudes, en términos, de los documentos vivos."""
        return self._documents.total_length

    @property
    def average_length(self) -> float:
        """Longitud media de un documento vivo, la `avgdl` de BM25; 0 si no hay ninguno."""
        if self.document_count == 0:
            return 0.0
        return self.total_length / self.document_count

    @property
    def vocabulary_size(self) -> int:
        """Términos distintos. Con un solo segmento y nada en memoria es `O(1)`; si no,
        recorre los léxicos, `O(V)`. Como el df, cuenta los términos de los documentos
        borrados hasta la próxima fusión."""
        if len(self._segments) == 1 and not self._block:
            return self._segments[0].term_count
        return sum(1 for _ in self.terms())

    @property
    def posting_count(self) -> int:
        """Postings de todos los segmentos y del bloque en memoria, con los de documentos
        borrados que aún no quitó una fusión."""
        return sum(view.posting_count for view in self._views())

    @property
    def block_count(self) -> int:
        """Bloques de SPIMI, o segmentos fusionados, de los que salió el segmento principal."""
        return self._segments[0].block_count

    @property
    def merge_passes(self) -> int:
        """Pasadas de mezcla con que se escribió el segmento principal; 0 si no hubo mezcla."""
        return self._segments[0].merge_passes

    @property
    def segment_count(self) -> int:
        """Segmentos en disco, el principal incluido."""
        return len(self._segments)

    @property
    def pending_document_count(self) -> int:
        """Documentos insertados que siguen en el bloque en memoria."""
        return self._block.document_count

    @property
    def merge_count(self) -> int:
        """Fusiones completas desde la construcción."""
        return self._manifest.merge_count

    def lookup(self, term: str) -> TermEntry | None:
        """El término ya analizado en todos los segmentos, o `None` si no aparece."""
        located = []
        for position, view in enumerate(self._views()):
            entry = view.find(term)
            if entry is not None:
                located.append((position, entry))
        return _term_entry(term, located) if located else None

    def document_frequency(self, term: str) -> int:
        """En cuántos documentos aparece el término, borrados que aún no quitó una fusión
        incluidos; 0 si en ninguno."""
        entry = self.lookup(term)
        return 0 if entry is None else entry.document_frequency

    def postings(self, term: str) -> PostingList:
        """La lista del término en orden de `doc_id`; vacía si no aparece."""
        entry = self.lookup(term)
        if entry is None:
            return EMPTY_POSTING_LIST
        return self.postings_of(entry)

    def postings_of(self, entry: TermEntry) -> PostingList:
        """La lista de una entrada que ya se buscó, sin volver a los léxicos: la de cada
        segmento, una tras otra, que es el orden de `doc_id`."""
        views = self._views()
        return PostingList.concatenate(
            [views[position].read(located) for position, located in entry.locations]
        )

    def document(self, doc_id: int) -> DocumentEntry:
        """Raises:
        DocumentNotFoundError: si no hay documento con ese `doc_id`."""
        return self._documents.entry(doc_id)

    def document_columns(self) -> DocumentColumns:
        """La longitud, la norma y la dirección de cada documento en memoria, y los
        borrados: lo que el ranking consulta por cada posting. Se carga la primera vez,
        `O(N)`, y después se mantiene al día."""
        return self._documents.columns()

    def documents(self) -> Iterator[DocumentEntry]:
        """Todos los documentos en orden de `doc_id`, borrados incluidos."""
        return self._documents.entries()

    def terms(self) -> Iterator[TermEntry]:
        """Todo el léxico en orden de término, con los segmentos mezclados k-vías."""
        views = self._views()
        merged = merge_sorted_streams([view.entries() for view in views], _entry_term)
        for term, group in groupby(merged, key=_located_term):
            yield _term_entry(term, list(group))

    def insert(self, record_id: RecordId, text: str) -> int:
        """Indexa el texto de la fila y devuelve su `doc_id`, el siguiente sin usar. Sus
        postings quedan en memoria hasta el `flush`, pero las búsquedas ya lo ven.

        Raises:
            DocumentAlreadyIndexedError: si la dirección ya es de un documento vivo.
        """
        analyzed = self._analyzer.analyze(text)
        doc_id = self._documents.slot_count
        self._addresses.add(record_id, doc_id)
        self._block.add(doc_id, analyzed.frequencies)
        self._documents.append(record_id, analyzed.length, lnc_norm(analyzed.frequencies.values()))
        if self._block.estimated_bytes >= self._config.spimi_block_bytes:
            self._write_block()
        return doc_id

    def delete(self, record_id: RecordId) -> bool:
        """Deja la lápida del documento de la fila, en `O(1)` páginas. Devuelve si había
        uno. Sus postings siguen en los segmentos, filtrados al buscar, hasta la próxima
        fusión completa."""
        doc_id = self._addresses.pop(record_id)
        if doc_id is None:
            return False
        self._documents.mark_deleted(doc_id)
        return True

    def flush(self) -> None:
        """Escribe el bloque en memoria como un segmento, fusiona si toca y deja todo en
        disco. Es el único momento en que se fusiona."""
        self._write_block()
        if self._merge_due():
            self._merge_all()
        elif self._compaction_due():
            self._compact_pending()
        self._persist()

    def merge(self) -> None:
        """Fusiona ya todos los segmentos y el bloque en memoria en uno, sin los postings
        de los borrados, y lo deja en disco. Después el df es exacto y el índice es el
        mismo que se obtendría construyéndolo de cero con los documentos vivos, salvo por
        los `doc_id`, que no cambian."""
        self._write_block()
        self._merge_all()
        self._persist()

    def describe(self, top_terms: int = DESCRIBE_TOP_TERMS) -> dict[str, Any]:
        """Forma del índice: documentos, segmentos, bloque en memoria, tamaños y los
        términos con más df.

        Los términos más frecuentes salen de recorrer el léxico entero: es una herramienta
        de inspección, `O(V)`, no algo que use una consulta. A igual df, el primero en
        orden alfabético.
        """
        page_size = self._config.page_size
        top = heapq.nlargest(top_terms, self.terms(), key=lambda entry: entry.document_frequency)
        on_disk = sum(segment.posting_count for segment in self._segments)
        return {
            "kind": "inverted",
            "language": self.language,
            "documents": self.document_count,
            "deleted": self.deleted_count,
            "deleted_with_postings": self._unpurged_count(),
            "total_length": self.total_length,
            "average_length": self.average_length,
            "vocabulary": self.vocabulary_size,
            "postings": self.posting_count,
            "blocks": self.block_count,
            "merge_passes": self.merge_passes,
            "merges": self.merge_count,
            "segments": [_describe_segment(segment) for segment in self._segments],
            "pending": {
                "documents": self._block.document_count,
                "terms": self._block.term_count,
                "postings": self._block.posting_count,
                "estimated_bytes": self._block.estimated_bytes,
            },
            "lexicon_pages": sum(segment.lexicon_pages for segment in self._segments),
            "term_bytes": sum(segment.term_bytes for segment in self._segments),
            "postings_bytes": on_disk * POSTING_BYTES,
            "file_bytes": {
                **_summed_file_bytes(self._segments, page_size),
                "documents": self._documents.page_count * page_size,
                "document_map": self._addresses.page_count * page_size,
            },
            "top_terms": [
                {
                    "term": entry.term,
                    "document_frequency": entry.document_frequency,
                    "total_frequency": entry.total_frequency,
                }
                for entry in top
            ],
        }

    def close(self) -> None:
        """`flush` y suelta los archivos, también si el `flush` falla."""
        try:
            self.flush()
        finally:
            for segment in self._segments:
                segment.close()
            self._documents.close()
            self._addresses.close()

    def __enter__(self) -> InvertedIndex:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _views(self) -> list[SegmentView]:
        """Los segmentos en orden de `doc_id`: los de disco y, al final, el bloque."""
        return [*self._segments, self._block]

    def _new_block(self) -> SpimiBlock:
        return SpimiBlock(max_term_bytes(self._config))

    def _open_segment(self, generation: int) -> Segment:
        base = segment_base(self._path, generation)
        missing = [file for file in segment_files(base) if not file.is_file()]
        if missing:
            raise InvertedIndexNotFoundError(f"falta {missing[0].name}, del segmento {generation}")
        return Segment(self._path, generation, self._config)

    def _write_block(self) -> None:
        """El bloque en memoria pasa a ser el último segmento. Un bloque cuyos documentos
        no tienen ningún término no escribe nada: esos documentos ya están en la tabla."""
        if not self._block:
            self._block = self._new_block()
            return
        generation = self._take_generation()
        write_segment(
            segment_base(self._path, generation),
            self._block.drain_into,
            encode_language(self.language),
            FLUSHED_BLOCKS,
            FLUSHED_MERGE_PASSES,
            self._config,
        )
        self._block = self._new_block()
        self._segments.append(self._open_segment(generation))
        self._record_segments()

    def _merge_due(self) -> bool:
        """Lo nuevo o lo borrado pasa de su fracción del índice."""
        main, pending = self._segments[0], self._segments[1:]
        pending_postings = sum(segment.posting_count for segment in pending)
        grown = bool(pending) and (
            pending_postings >= self._config.inverted_pending_fraction * main.posting_count
        )
        unpurged = self._unpurged_count()
        indexed = self.document_count + unpurged
        shrunk = unpurged > 0 and unpurged >= self._config.inverted_deleted_fraction * indexed
        return grown or shrunk

    def _compaction_due(self) -> bool:
        return len(self._segments) - 1 > self._config.inverted_max_segments

    def _merge_all(self) -> None:
        """Todo en un segmento, sin los postings de los borrados."""
        if self._unpurged_count() == 0:
            self._replace_segments(0, _unchanged)
        else:
            deleted = self._documents.columns().deleted
            self._replace_segments(0, lambda sink: PurgingSink(sink, deleted))
        self._manifest = replace(
            self._manifest,
            purged_count=self.deleted_count,
            merge_count=self._manifest.merge_count + 1,
        )

    def _compact_pending(self) -> None:
        """Los segmentos pendientes en uno, copiando las listas sin decodificarlas: los
        borrados se quitan en la fusión completa, que es la que lleva la cuenta."""
        self._replace_segments(1, _unchanged)

    def _replace_segments(self, start: int, wrap: SinkWrapper) -> None:
        """Fusiona los segmentos desde `start` en uno nuevo que ocupa su lugar, con la
        mezcla de la construcción. Los archivos viejos se borran al guardar el manifiesto,
        cuando ya no hacen falta."""
        merging = self._segments[start:]
        generation = self._take_generation()
        passes = MergePasses(build_directory(self._path), self._config)
        try:
            sources = passes.reduce(merging)
            write_segment(
                segment_base(self._path, generation),
                lambda sink: merge_sources(sources, self._config, wrap(sink)),
                encode_language(self.language),
                len(merging),
                passes.passes + 1,
                self._config,
            )
        finally:
            passes.remove_all()
        merged = self._open_segment(generation)
        for segment in merging:
            segment.close()
            self._obsolete.append(segment.generation)
        self._segments[start:] = [merged]
        self._record_segments()

    def _take_generation(self) -> int:
        generation = self._manifest.next_generation
        self._manifest = replace(self._manifest, next_generation=generation + 1)
        return generation

    def _record_segments(self) -> None:
        self._manifest = self._manifest.with_segments(
            [segment.generation for segment in self._segments], self._manifest.next_generation
        )

    def _unpurged_count(self) -> int:
        """Borrados cuyos postings siguen en algún segmento."""
        return self.deleted_count - self._manifest.purged_count

    def _persist(self) -> None:
        """La tabla de documentos y el mapa, después el manifiesto —que es lo que hace
        valer los segmentos nuevos— y solo entonces se borran los segmentos que reemplazó."""
        self._documents.flush()
        self._addresses.flush()
        if self._manifest != self._saved_manifest:
            write_manifest(manifest_path(self._path), self._manifest)
            self._saved_manifest = self._manifest
        for generation in self._obsolete:
            remove_segment(self._path, generation)
        self._obsolete.clear()


def _fixed_files(path: Path) -> list[Path]:
    """Los archivos del índice que no dependen de sus segmentos."""
    return [
        documents_path(path),
        manifest_path(path),
        *DocumentMap.files_of(document_map_path(path)),
    ]


def _every_file(path: Path) -> list[Path]:
    """Los archivos del índice, también los de una construcción que no llegó a escribir
    el manifiesto."""
    return list(dict.fromkeys([*InvertedIndex.files_of(path), *built_files(path)]))


def _map_addresses(path: Path, config: EngineConfig) -> None:
    """El mapa de direcciones de un índice recién construido, desde su tabla de documentos."""
    documents = DocumentTable(documents_path(path), config)
    addresses = DocumentMap(document_map_path(path), config)
    try:
        addresses.load(
            (entry.record_id, doc_id) for doc_id, entry in enumerate(documents.entries())
        )
    finally:
        addresses.close()
        documents.close()


def _term_entry(term: str, located: list[tuple[int, LexiconEntry]]) -> TermEntry:
    return TermEntry(
        term,
        sum(entry.document_frequency for _, entry in located),
        sum(entry.total_frequency for _, entry in located),
        tuple(located),
    )


def _entry_term(entry: LexiconEntry) -> str:
    """El orden de un léxico: comparar cadenas de Python es comparar puntos de código, que
    es el mismo orden que el de sus bytes UTF-8."""
    return entry.term


def _located_term(item: tuple[int, LexiconEntry]) -> str:
    return item[1].term


def _unchanged(sink: TermSink) -> TermSink:
    return sink


def _describe_segment(segment: Segment) -> dict[str, int]:
    return {
        "generation": segment.generation,
        "terms": segment.term_count,
        "postings": segment.posting_count,
        "blocks": segment.block_count,
        "merge_passes": segment.merge_passes,
    }


def _summed_file_bytes(segments: list[Segment], page_size: int) -> dict[str, int]:
    totals: dict[str, int] = {}
    for segment in segments:
        for name, size in segment.file_bytes(page_size).items():
            totals[name] = totals.get(name, 0) + size
    return totals
