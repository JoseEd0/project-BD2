"""El índice invertido abierto: léxico, listas de postings y tabla de documentos.

Es lo que consume el ranking: el df y la lista de cada término de la consulta, y de cada
documento su longitud y su norma. No sabe de tablas ni de SQL; un documento es un texto
con la dirección de su fila.
"""

from __future__ import annotations

import heapq
from collections.abc import Iterable, Iterator
from contextlib import ExitStack
from pathlib import Path
from types import TracebackType
from typing import Any

from config import EngineConfig
from text import Analyzer

from .documents import DocumentTable
from .format import (
    POSTING_BYTES,
    DocumentEntry,
    InvertedFormatError,
    InvertedIndexNotFoundError,
    LexiconEntry,
    documents_path,
    files_of,
    lexicon_path,
    postings_path,
    terms_path,
)
from .lexicon import Lexicon
from .postings import EMPTY_POSTING_LIST, PostingList, PostingsFile
from .spimi import Document, SpimiBuilder

DESCRIBE_TOP_TERMS = 10


class InvertedIndex:
    """Índice invertido en disco, construido con SPIMI.

    Complejidad, con `V` términos, `N` documentos y una lista de `df` postings:

    * buscar un término: `O(log V)` comparaciones en memoria y una página del léxico;
    * leer su lista: `⌈8·df / página⌉ + 1` páginas consecutivas, decodificadas en C;
    * un documento por `doc_id`: una página, `O(1)`;
    * memoria al abrir: el primer término de cada página del léxico.

    Raises:
        InvertedIndexNotFoundError: si falta alguno de los archivos del índice.
        InvertedFormatError: si los archivos no son de este formato o de un mismo índice.
    """

    def __init__(self, path: Path, config: EngineConfig) -> None:
        missing = [file for file in files_of(path) if not file.is_file()]
        if missing:
            raise InvertedIndexNotFoundError(f"no hay un índice invertido en {missing[0]}")
        self._config = config
        with ExitStack() as opened:
            self._lexicon = Lexicon(lexicon_path(path), terms_path(path), config)
            opened.callback(self._lexicon.close)
            self._postings = PostingsFile(postings_path(path), config)
            opened.callback(self._postings.close)
            self._documents = DocumentTable(documents_path(path), config)
            opened.callback(self._documents.close)
            if self._postings.posting_count != self._lexicon.posting_count:
                raise InvertedFormatError(
                    f"{path.name}: el léxico cuenta {self._lexicon.posting_count} postings y "
                    f"el archivo de postings {self._postings.posting_count}"
                )
            opened.pop_all()

    @classmethod
    def build(
        cls,
        path: Path,
        documents: Iterable[Document],
        analyzer: Analyzer,
        config: EngineConfig,
    ) -> InvertedIndex:
        """Construye el índice de los documentos `(dirección de la fila, texto)` con SPIMI
        y lo abre. El `doc_id` de cada documento es su posición en `documents`.

        Raises:
            InvertedIndexExistsError: si ya hay un índice en `path`.
            TermTooLongError: si el analizador produce un término de más de
                `max_term_bytes` bytes.
        """
        SpimiBuilder(path, analyzer, config).build(documents)
        return cls(path, config)

    @staticmethod
    def files_of(path: Path) -> list[Path]:
        """Los archivos que ocupa un índice guardado en `path`: léxico, términos,
        postings y documentos."""
        return files_of(path)

    @property
    def language(self) -> str:
        """El idioma del analizador con el que se construyó; la consulta usa el mismo."""
        return self._lexicon.language

    @property
    def document_count(self) -> int:
        return self._documents.document_count

    @property
    def total_length(self) -> int:
        """Suma de las longitudes, en términos, de todos los documentos."""
        return self._documents.total_length

    @property
    def average_length(self) -> float:
        """Longitud media de un documento, la `avgdl` de BM25; 0 si no hay documentos."""
        if self.document_count == 0:
            return 0.0
        return self.total_length / self.document_count

    @property
    def vocabulary_size(self) -> int:
        return self._lexicon.term_count

    @property
    def posting_count(self) -> int:
        return self._lexicon.posting_count

    @property
    def block_count(self) -> int:
        """Bloques que escribió SPIMI al construir el índice."""
        return self._lexicon.block_count

    @property
    def merge_passes(self) -> int:
        """Pasadas de mezcla de la construcción; 0 si todo cupo en un bloque."""
        return self._lexicon.merge_passes

    def lookup(self, term: str) -> LexiconEntry | None:
        """La entrada del léxico de un término ya analizado, o `None` si no aparece."""
        return self._lexicon.find(term)

    def document_frequency(self, term: str) -> int:
        """En cuántos documentos aparece el término; 0 si en ninguno."""
        entry = self._lexicon.find(term)
        return 0 if entry is None else entry.document_frequency

    def postings(self, term: str) -> PostingList:
        """La lista del término en orden de `doc_id`; vacía si no aparece."""
        entry = self._lexicon.find(term)
        if entry is None:
            return EMPTY_POSTING_LIST
        return self.postings_of(entry)

    def postings_of(self, entry: LexiconEntry) -> PostingList:
        """La lista de una entrada que ya se buscó, sin volver al léxico."""
        return self._postings.read(entry.postings_offset, entry.document_frequency)

    def document(self, doc_id: int) -> DocumentEntry:
        """Raises:
        DocumentNotFoundError: si no hay documento con ese `doc_id`."""
        return self._documents.entry(doc_id)

    def documents(self) -> Iterator[DocumentEntry]:
        """Todos los documentos en orden de `doc_id`."""
        return self._documents.entries()

    def terms(self) -> Iterator[LexiconEntry]:
        """Todo el léxico en orden de término."""
        return self._lexicon.entries()

    def describe(self, top_terms: int = DESCRIBE_TOP_TERMS) -> dict[str, Any]:
        """Forma del índice: vocabulario, bloques, tamaños y los términos con más df.

        Los términos más frecuentes salen de recorrer el léxico entero: es una herramienta
        de inspección, `O(V)`, no algo que use una consulta. A igual df, el primero en
        orden alfabético.
        """
        page_size = self._config.page_size
        top = heapq.nlargest(top_terms, self.terms(), key=lambda entry: entry.document_frequency)
        return {
            "kind": "inverted",
            "language": self.language,
            "documents": self.document_count,
            "total_length": self.total_length,
            "average_length": self.average_length,
            "vocabulary": self.vocabulary_size,
            "postings": self.posting_count,
            "blocks": self.block_count,
            "merge_passes": self.merge_passes,
            "lexicon_pages": self._lexicon.record_page_count,
            "term_bytes": self._lexicon.term_bytes,
            "postings_bytes": self.posting_count * POSTING_BYTES,
            "file_bytes": {
                "lexicon": self._lexicon.page_count * page_size,
                "terms": self._lexicon.terms_page_count * page_size,
                "postings": self._postings.page_count * page_size,
                "documents": self._documents.page_count * page_size,
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

    def flush(self) -> None:
        self._lexicon.flush()
        self._postings.flush()
        self._documents.flush()

    def close(self) -> None:
        self._lexicon.close()
        self._postings.close()
        self._documents.close()

    def __enter__(self) -> InvertedIndex:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
