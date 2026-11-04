"""SPIMI (*Single-Pass In-Memory Indexing*, Heinz y Zobel, 2003): el índice invertido de
una colección que no cabe en memoria, en una pasada sobre los documentos.

1. **Bloques.** Cada documento se analiza y sus términos van directo a un diccionario
   `término → lista de postings`. A diferencia de BSBI, no hay un término→id global ni
   pares que ordenar: la lista de un término crece en su sitio. Cuando el diccionario
   llega a `spimi_block_bytes`, se ordenan sus términos, se escribe como un bloque y se
   vacía.
2. **Mezcla.** Los bloques se mezclan de a `merge_fan_in` (k-vías); si hay más, por
   pasadas, como el ordenamiento externo. La última pasada escribe el léxico y las listas
   definitivas.

Ver `README.md` para el formato de cada archivo y la estimación de memoria del bloque.
"""

from __future__ import annotations

from array import array
from collections.abc import Callable, Iterable, Mapping
from functools import partial
from pathlib import Path

from config import EngineConfig
from external.runs import remove_if_empty
from storage.page import slot_capacity
from storage.record_id import RecordId
from text import Analyzer

from .blocks import Block, BlockWriter, TermSink, merge_blocks
from .documents import DocumentTableWriter
from .format import (
    LEXICON_RECORD,
    POSTING_FIELDS,
    POSTING_TYPECODE,
    InvertedIndexExistsError,
    TermTooLongError,
    build_directory,
    documents_path,
    encode_language,
    encode_postings,
    files_of,
    lexicon_path,
    max_term_bytes,
    postings_path,
    terms_path,
)
from .lexicon import LexiconWriter
from .postings import PostingsWriter
from .weights import lnc_norm

# Estimación del bloque en memoria, medida con `tracemalloc` sobre las fichas de ESCI:
# cada término nuevo cuesta su entrada en el diccionario, su cadena y un `array` vacío;
# cada posting, sus dos enteros de 4 bytes más lo que el `array` reserva de sobra al
# crecer. En lo medido, la estimación queda por encima de la memoria real: el bloque no
# pasa del presupuesto.
TERM_ENTRY_BYTES = 160
POSTING_ENTRY_BYTES = 10
BLOCK_PREFIX = "block"

Document = tuple[RecordId, str]


class SpimiBlock:
    """El diccionario en memoria de un bloque: `término → array('I')` con los postings
    intercalados `doc_id, tf, doc_id, tf, …`, en orden de llegada y por tanto de `doc_id`.

    `estimated_bytes` es una cuenta determinista —`TERM_ENTRY_BYTES` más los caracteres de
    cada término, y `POSTING_ENTRY_BYTES` por posting—, la misma en cualquier máquina, para
    que los tests puedan forzar un número exacto de bloques.
    """

    def __init__(self, max_term_bytes: int) -> None:
        self._postings: dict[str, array[int]] = {}
        self._max_term_bytes = max_term_bytes
        self.estimated_bytes = 0

    def __bool__(self) -> bool:
        return bool(self._postings)

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


class IndexSink:
    """El destino de la última mezcla: el léxico y el archivo de postings definitivos."""

    def __init__(self, path: Path, config: EngineConfig) -> None:
        self._lexicon = LexiconWriter(lexicon_path(path), terms_path(path), config)
        self._postings = PostingsWriter(postings_path(path), config)

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


class SpimiBuilder:
    """Construye un índice invertido en `path` con memoria acotada.

    Complejidad, con `T` postings, `V` términos, `B` bloques y abanico `k`:

    * pasada sobre los documentos: `O(T)` inserciones en el diccionario, más ordenar los
      términos de cada bloque, `O(V_b log V_b)`;
    * mezcla: `⌈log_k B⌉` pasadas, cada una lee y escribe todos los postings una vez, y
      `O(log k)` comparaciones por término de cada bloque;
    * memoria: el bloque (`spimi_block_bytes`) en la primera fase, y una página por flujo
      de cada bloque abierto en la mezcla, dentro del buffer pool.

    Si todo cabe en un bloque, se escribe directamente como índice y no hay mezcla.
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
        self._directory = build_directory(path)
        self._blocks: list[Block] = []
        self._created = 0
        self.block_count = 0
        self.merge_passes = 0

    def build(self, documents: Iterable[Document]) -> None:
        """Indexa los documentos `(dirección de la fila, texto)`; el `doc_id` de cada uno es
        su posición en `documents`.

        Raises:
            InvertedIndexExistsError: si ya hay un índice en la ruta.
            TermTooLongError: si el analizador produce un término más largo de lo admitido.
        """
        existing = [path for path in files_of(self._path) if path.exists()]
        if existing:
            raise InvertedIndexExistsError(f"ya hay un índice invertido en {existing[0]}")
        try:
            last_block = self._index_documents(documents)
            self._write_index(last_block)
        except BaseException:
            for path in files_of(self._path):
                path.unlink(missing_ok=True)
            raise
        finally:
            for block in self._blocks:
                block.remove()
            remove_if_empty(self._directory)

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
            self._write_final(last_block.drain_into)
            return
        if last_block:
            self._spill(last_block)
        blocks = list(self._blocks)
        while len(blocks) > self._config.merge_fan_in:
            blocks = self._merge_pass(blocks)
            self.merge_passes += 1
        self.merge_passes += 1
        self._write_final(partial(merge_blocks, blocks, self._config))

    def _spill(self, block: SpimiBlock) -> None:
        self._write_block(block.drain_into)
        self.block_count += 1

    def _merge_pass(self, blocks: list[Block]) -> list[Block]:
        """Una pasada: cada grupo de `merge_fan_in` bloques consecutivos pasa a ser uno, de
        modo que el orden de los `doc_id` entre bloques se conserva."""
        fan_in = self._config.merge_fan_in
        merged: list[Block] = []
        for start in range(0, len(blocks), fan_in):
            group = blocks[start : start + fan_in]
            merged.append(self._write_block(partial(merge_blocks, group, self._config)))
            for block in group:
                block.remove()
                self._blocks.remove(block)
        return merged

    def _write_block(self, fill: Callable[[TermSink], None]) -> Block:
        writer = BlockWriter(self._new_block_base(), self._config)
        try:
            fill(writer)
        except BaseException:
            writer.discard()
            raise
        block = writer.close()
        self._blocks.append(block)
        return block

    def _write_final(self, fill: Callable[[TermSink], None]) -> None:
        sink = IndexSink(self._path, self._config)
        try:
            fill(sink)
        except BaseException:
            sink.discard()
            raise
        sink.close(self._language, self.block_count, self.merge_passes)

    def _new_block_base(self) -> Path:
        """Nombre nunca reutilizado: reciclarlo pisaría un bloque que aún se está leyendo."""
        self._directory.mkdir(parents=True, exist_ok=True)
        base = self._directory / f"{BLOCK_PREFIX}{self._created}"
        self._created += 1
        return base
