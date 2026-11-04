"""SPIMI (*Single-Pass In-Memory Indexing*, Heinz y Zobel, 2003): el índice invertido de
una colección que no cabe en memoria, en una pasada sobre los documentos.

1. **Bloques.** Cada documento se analiza y sus términos van directo a un diccionario
   `término → lista de postings`. A diferencia de BSBI, no hay un término→id global ni
   pares que ordenar: la lista de un término crece en su sitio. Cuando el diccionario
   llega a `spimi_block_bytes`, se ordenan sus términos, se escribe como un bloque y se
   vacía.
2. **Mezcla.** Los bloques se mezclan de a `merge_fan_in` (k-vías); si hay más, por
   pasadas, como el ordenamiento externo. La última pasada escribe el primer segmento
   del índice: el léxico y las listas definitivas.

El mismo bloque en memoria recibe después los documentos que llegan al índice ya
construido, hasta que un `flush` lo escribe como un segmento más (ver `index.py`).

Ver `README.md` para el formato de cada archivo y la estimación de memoria del bloque.
"""

from __future__ import annotations

from array import array
from collections.abc import Callable, Iterable, Iterator, Mapping
from functools import partial
from pathlib import Path

from config import EngineConfig
from storage.page import slot_capacity
from storage.record_id import RecordId
from text import Analyzer

from .blocks import Block, MergePasses, TermSink, merge_sources
from .documents import DocumentTableWriter
from .format import (
    FIRST_GENERATION,
    LEXICON_RECORD,
    POSTING_FIELDS,
    POSTING_TYPECODE,
    InvertedIndexExistsError,
    LexiconEntry,
    TermTooLongError,
    build_directory,
    built_files,
    documents_path,
    encode_language,
    encode_postings,
    max_term_bytes,
    segment_base,
)
from .postings import PostingList
from .segments import write_segment
from .weights import lnc_norm

# Estimación del bloque en memoria, medida con `tracemalloc` sobre las fichas de ESCI:
# cada término nuevo cuesta su entrada en el diccionario, su cadena y un `array` vacío;
# cada posting, sus dos enteros de 4 bytes más lo que el `array` reserva de sobra al
# crecer. En lo medido, la estimación queda por encima de la memoria real: el bloque no
# pasa del presupuesto.
TERM_ENTRY_BYTES = 160
POSTING_ENTRY_BYTES = 10
# Una lista que todavía está en memoria no tiene posición en ningún archivo de postings.
IN_MEMORY_OFFSET = 0

Document = tuple[RecordId, str]


class SpimiBlock:
    """El diccionario en memoria de un bloque: `término → array('I')` con los postings
    intercalados `doc_id, tf, doc_id, tf, …`, en orden de llegada y por tanto de `doc_id`.

    `estimated_bytes` es una cuenta determinista —`TERM_ENTRY_BYTES` más los caracteres de
    cada término, y `POSTING_ENTRY_BYTES` por posting—, la misma en cualquier máquina, para
    que los tests puedan forzar un número exacto de bloques.

    También se lee como un segmento (`find`, `read`, `entries`): es el bloque de los
    documentos que llegaron al índice después de construirlo y aún no se escribieron.
    """

    def __init__(self, max_term_bytes: int) -> None:
        self._postings: dict[str, array[int]] = {}
        self._max_term_bytes = max_term_bytes
        self.estimated_bytes = 0
        self.document_count = 0
        self.posting_count = 0

    def __bool__(self) -> bool:
        return bool(self._postings)

    @property
    def term_count(self) -> int:
        return len(self._postings)

    def add(self, doc_id: int, frequencies: Mapping[str, int]) -> None:
        """Añade los postings de un documento, en `O(términos del documento)`.

        Raises:
            TermTooLongError: si un término mide más de `max_term_bytes` en UTF-8.
        """
        block = self._postings
        for term, frequency in frequencies.items():
            postings = block.get(term)
            if postings is None:
                postings = block[term] = array(POSTING_TYPECODE)
                self._admit_term(term)
            postings.append(doc_id)
            postings.append(frequency)
        self.estimated_bytes += len(frequencies) * POSTING_ENTRY_BYTES
        self.posting_count += len(frequencies)
        self.document_count += 1

    def find(self, term: str) -> LexiconEntry | None:
        """El término con su df y su frecuencia total, `O(df)`; `None` si no está."""
        postings = self._postings.get(term)
        if postings is None:
            return None
        return LexiconEntry(
            term,
            len(postings) // POSTING_FIELDS,
            sum(postings[1::POSTING_FIELDS]),
            IN_MEMORY_OFFSET,
        )

    def read(self, entry: LexiconEntry) -> PostingList:
        """Una copia de la lista del término, que el bloque puede seguir haciendo crecer."""
        return PostingList.interleaved(self._postings[entry.term])

    def entries(self) -> Iterator[LexiconEntry]:
        """Los términos en orden, como un léxico."""
        for term in sorted(self._postings):
            entry = self.find(term)
            if entry is not None:
                yield entry

    def drain_into(self, sink: TermSink) -> None:
        """Entrega los términos en orden a `sink` y suelta cada lista según la entrega: el
        bloque queda vacío y no se vuelve a usar, porque un diccionario no devuelve la
        memoria de su tabla al vaciarse."""
        for term in sorted(self._postings):
            postings = self._postings.pop(term)
            sink.add_term(
                term.encode(),
                len(postings) // POSTING_FIELDS,
                sum(postings[1::POSTING_FIELDS]),
                (encode_postings(postings),),
            )

    def _admit_term(self, term: str) -> None:
        width = len(term.encode())
        if width > self._max_term_bytes:
            raise TermTooLongError(
                f"el término {term[:20]!r}… mide {width} bytes y el léxico admite "
                f"{self._max_term_bytes}"
            )
        self.estimated_bytes += TERM_ENTRY_BYTES + len(term)


class SpimiBuilder:
    """Construye en `path` la tabla de documentos y el primer segmento de un índice
    invertido, con memoria acotada.

    Complejidad, con `T` postings, `V` términos, `B` bloques y abanico `k`:

    * pasada sobre los documentos: `O(T)` inserciones en el diccionario, más ordenar los
      términos de cada bloque, `O(V_b log V_b)`;
    * mezcla: `⌈log_k B⌉` pasadas, cada una lee y escribe todos los postings una vez, y
      `O(log k)` comparaciones por término de cada bloque;
    * memoria: el bloque (`spimi_block_bytes`) en la primera fase, y una página por flujo
      de cada bloque abierto en la mezcla, dentro del buffer pool.

    Si todo cabe en un bloque, se escribe directamente como segmento y no hay mezcla.
    Los bloques viven en un directorio temporal junto al índice, que se borra siempre,
    también si la construcción falla; en ese caso tampoco quedan los archivos del índice.

    Raises:
        PageOverflowError: al crearse, si un registro del léxico con un término de
            `max_term_bytes` no cabe en una página.
    """

    def __init__(self, path: Path, analyzer: Analyzer, config: EngineConfig) -> None:
        self._path = path
        self._analyzer = analyzer
        self._language = encode_language(analyzer.language)
        self._config = config
        self._max_term_bytes = max_term_bytes(config)
        slot_capacity(config.page_size, LEXICON_RECORD.size)
        self._passes = MergePasses(build_directory(path), config)
        self._blocks: list[Block] = []
        self.block_count = 0
        self.merge_passes = 0

    def build(self, documents: Iterable[Document]) -> None:
        """Indexa los documentos `(dirección de la fila, texto)`; el `doc_id` de cada uno es
        su posición en `documents`.

        Raises:
            InvertedIndexExistsError: si ya hay un índice en la ruta.
            TermTooLongError: si el analizador produce un término más largo de lo admitido.
        """
        existing = [path for path in built_files(self._path) if path.exists()]
        if existing:
            raise InvertedIndexExistsError(f"ya hay un índice invertido en {existing[0]}")
        try:
            last_block = self._index_documents(documents)
            self._write_index(last_block)
        except BaseException:
            for path in built_files(self._path):
                path.unlink(missing_ok=True)
            raise
        finally:
            self._passes.remove_all()

    def _index_documents(self, documents: Iterable[Document]) -> SpimiBlock:
        """La pasada única: analiza, apunta el documento y llena bloques. Devuelve el último
        bloque, aún en memoria."""
        block = SpimiBlock(self._max_term_bytes)
        table = DocumentTableWriter(documents_path(self._path), self._config)
        try:
            for record_id, text in documents:
                analyzed = self._analyzer.analyze(text)
                norm = lnc_norm(analyzed.frequencies.values())
                block.add(table.append(record_id, analyzed.length, norm), analyzed.frequencies)
                if block.estimated_bytes >= self._config.spimi_block_bytes:
                    self._spill(block)
                    block = SpimiBlock(self._max_term_bytes)
        except BaseException:
            table.discard()
            raise
        table.close()
        return block

    def _write_index(self, last_block: SpimiBlock) -> None:
        if not self._blocks:
            if last_block:
                self.block_count = 1
            self._write_segment(last_block.drain_into)
            return
        if last_block:
            self._spill(last_block)
        sources = self._passes.reduce(self._blocks)
        self.merge_passes = self._passes.passes + 1
        self._write_segment(partial(merge_sources, sources, self._config))

    def _spill(self, block: SpimiBlock) -> None:
        self._blocks.append(self._passes.write_block(block.drain_into))
        self.block_count += 1

    def _write_segment(self, fill: Callable[[TermSink], None]) -> None:
        write_segment(
            segment_base(self._path, FIRST_GENERATION),
            fill,
            self._language,
            self.block_count,
            self.merge_passes,
            self._config,
        )
