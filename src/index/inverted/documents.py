"""La tabla de documentos: un registro fijo por `doc_id`, en el orden en que llegaron.

El `doc_id` es la posición del registro, así que llegar a un documento es una división:
página `1 + doc_id // capacidad`, ranura `doc_id % capacidad`. La marca de borrado es el
estado de la ranura (`SlotState.DELETED`), el mismo que usa el archivo secuencial para
sus lápidas: no cuesta un byte más.

El ranking necesita la longitud o la norma de cada documento que puntúa, que pueden ser
la mitad del corpus. Leerlas registro a registro costaría una página por documento; por
eso la tabla las entrega también como columnas en memoria (`DocumentColumns`), que se
cargan la primera vez que se piden, como las *norms* de Lucene.
"""

from __future__ import annotations

from array import array
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from config import EngineConfig
from storage.layout import RecordAppender
from storage.page import RecordPage, SlotState, slot_capacity
from storage.pager import Pager
from storage.record_id import RecordId

from .format import (
    DOCUMENT_RECORD,
    DOCUMENTS_HEADER,
    FIRST_DATA_PAGE,
    DocumentEntry,
    DocumentNotFoundError,
)

LENGTH_TYPECODE = "I"
NORM_TYPECODE = "d"


@dataclass(frozen=True, slots=True)
class DocumentColumns:
    """La longitud y la norma lnc de cada documento, indexadas por `doc_id`, y los
    `doc_id` borrados. Ocupan 12 bytes por documento más uno por cada borrado."""

    lengths: array[int]
    norms: array[float]
    deleted: frozenset[int]


class DocumentTableWriter:
    """Escribe la tabla de documentos de una vez, en orden de `doc_id`."""

    def __init__(self, path: Path, config: EngineConfig) -> None:
        self._pager = Pager(path, config)
        self._pager.allocate()
        self._records = RecordAppender(self._pager, DOCUMENT_RECORD.size)
        self.total_length = 0

    @property
    def document_count(self) -> int:
        return self._records.record_count

    def append(self, record_id: RecordId, length: int, norm: float) -> int:
        """Añade el siguiente documento y devuelve su `doc_id`."""
        doc_id = self._records.record_count
        self._records.append(DOCUMENT_RECORD.pack(record_id.page_id, record_id.slot, length, norm))
        self.total_length += length
        return doc_id

    def close(self) -> None:
        self._records.finish()
        DOCUMENTS_HEADER.write(self._pager, self.document_count, self.total_length)
        self._pager.close()

    def discard(self) -> None:
        """Cierra sin escribir nada más: la construcción falló y el archivo se borrará."""
        self._pager.discard()


class DocumentTable:
    """Lee la tabla de documentos.

    Raises:
        InvertedFormatError: si el archivo no es una tabla de documentos de este formato.
    """

    def __init__(self, path: Path, config: EngineConfig) -> None:
        self._pager = Pager(path, config)
        self._capacity = slot_capacity(config.page_size, DOCUMENT_RECORD.size)
        document_count, total_length = DOCUMENTS_HEADER.read(self._pager)
        self.document_count: int = document_count
        self.total_length: int = total_length
        self._columns: DocumentColumns | None = None

    @property
    def page_count(self) -> int:
        return self._pager.page_count

    def entry(self, doc_id: int) -> DocumentEntry:
        """El documento `doc_id`, leyendo una sola página: `O(1)`.

        Raises:
            DocumentNotFoundError: si no hay documento con ese `doc_id`.
        """
        if not 0 <= doc_id < self.document_count:
            raise DocumentNotFoundError(
                f"no hay documento {doc_id}: el índice tiene {self.document_count}"
            )
        page_index, slot = divmod(doc_id, self._capacity)
        page = self._page(page_index)
        return _decode(page.record_at(slot), page.state_of(slot))

    def entries(self) -> Iterator[DocumentEntry]:
        """Todos los documentos en orden de `doc_id`, borrados incluidos, página a página."""
        page_count = -(-self.document_count // self._capacity)
        for page_index in range(page_count):
            page = self._page(page_index)
            for slot in range(page.used_slots):
                yield _decode(page.record_at(slot), page.state_of(slot))

    def columns(self) -> DocumentColumns:
        """Longitudes, normas y borrados de todos los documentos. La primera llamada recorre
        la tabla, `O(N)`; las siguientes devuelven lo ya cargado, `O(1)`."""
        if self._columns is None:
            self._columns = self._load_columns()
        return self._columns

    def flush(self) -> None:
        self._pager.flush()

    def close(self) -> None:
        self._pager.close()

    def _load_columns(self) -> DocumentColumns:
        lengths: array[int] = array(LENGTH_TYPECODE)
        norms: array[float] = array(NORM_TYPECODE)
        deleted: set[int] = set()
        for doc_id, entry in enumerate(self.entries()):
            lengths.append(entry.length)
            norms.append(entry.norm)
            if entry.deleted:
                deleted.add(doc_id)
        return DocumentColumns(lengths, norms, frozenset(deleted))

    def _page(self, page_index: int) -> RecordPage:
        raw = self._pager.read(FIRST_DATA_PAGE + page_index)
        return RecordPage.from_bytes(raw, DOCUMENT_RECORD.size)


def _decode(raw: bytes, state: SlotState) -> DocumentEntry:
    page_id, slot, length, norm = DOCUMENT_RECORD.unpack(raw)
    return DocumentEntry(RecordId(page_id, slot), length, norm, state is SlotState.DELETED)
