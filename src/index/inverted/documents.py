"""La tabla de documentos: un registro fijo por `doc_id`, en el orden en que llegaron.

El `doc_id` es la posición del registro, así que llegar a un documento es una división:
página `1 + doc_id // capacidad`, ranura `doc_id % capacidad`. La marca de borrado es el
estado de la ranura (`SlotState.DELETED`), el mismo que usa el archivo secuencial para
sus lápidas: no cuesta un byte más. Un `doc_id` no se reutiliza: el documento borrado
deja su lápida, y uno nuevo recibe el siguiente.

El ranking necesita la longitud o la norma de cada documento que puntúa, que pueden ser
la mitad del corpus. Leerlas registro a registro costaría una página por documento; por
eso la tabla las entrega también como columnas en memoria (`DocumentColumns`), que se
cargan la primera vez que se piden, como las *norms* de Lucene, y que la tabla mantiene
al día cuando llega o se borra un documento.
"""

from __future__ import annotations

import struct
from array import array
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from config import EngineConfig
from storage.layout import RecordAppender
from storage.page import RecordPage, SlotState, slot_capacity
from storage.pager import Pager
from storage.record_id import BITS_PER_BYTE, SLOT_FIELD, RecordId

from .format import (
    DOCUMENT_RECORD,
    DOCUMENTS_HEADER,
    FIRST_DATA_PAGE,
    DocumentEntry,
    DocumentNotFoundError,
)

LENGTH_TYPECODE = "I"
NORM_TYPECODE = "d"
RECORD_KEY_TYPECODE = "Q"
SLOT_BITS = BITS_PER_BYTE * struct.calcsize(SLOT_FIELD)
SLOT_MASK = (1 << SLOT_BITS) - 1


def record_key(record_id: RecordId) -> int:
    """La dirección como un entero que se ordena igual que ella: página y luego ranura."""
    return record_id.page_id << SLOT_BITS | record_id.slot


@dataclass(frozen=True, slots=True)
class DocumentColumns:
    """La longitud, la norma lnc y la dirección de la fila de cada documento, indexadas
    por `doc_id`, y los `doc_id` borrados. Ocupan 20 bytes por documento más uno por cada
    borrado."""

    lengths: array[int]
    norms: array[float]
    record_keys: array[int]
    deleted: set[int]

    def record_id(self, doc_id: int) -> RecordId:
        key = self.record_keys[doc_id]
        return RecordId(key >> SLOT_BITS, key & SLOT_MASK)

    def append(self, entry: DocumentEntry) -> None:
        self.lengths.append(entry.length)
        self.norms.append(entry.norm)
        self.record_keys.append(record_key(entry.record_id))


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
        self._records.append(_encode(record_id, length, norm))
        self.total_length += length
        return doc_id

    def close(self) -> None:
        self._records.finish()
        DOCUMENTS_HEADER.write(self._pager, self.document_count, 0, self.total_length)
        self._pager.close()

    def discard(self) -> None:
        """Cierra sin escribir nada más: la construcción falló y el archivo se borrará."""
        self._pager.discard()


class DocumentTable:
    """La tabla de documentos de un índice abierto: se lee, se le añaden documentos y se
    marcan borrados, todo en `O(1)` páginas.

    `slot_count` cuenta los `doc_id` repartidos, borrados incluidos; `document_count`, los
    vivos, que son la N del ranking, y `total_length` suma solo las longitudes de los vivos.
    Los contadores se guardan en la cabecera con `flush`.

    Raises:
        InvertedFormatError: si el archivo no es una tabla de documentos de este formato.
    """

    def __init__(self, path: Path, config: EngineConfig) -> None:
        self._pager = Pager(path, config)
        self._capacity = slot_capacity(config.page_size, DOCUMENT_RECORD.size)
        slot_count, deleted_count, total_length = DOCUMENTS_HEADER.read(self._pager)
        self.slot_count: int = slot_count
        self.deleted_count: int = deleted_count
        self.total_length: int = total_length
        self._columns: DocumentColumns | None = None

    @property
    def document_count(self) -> int:
        return self.slot_count - self.deleted_count

    @property
    def page_count(self) -> int:
        return self._pager.page_count

    def entry(self, doc_id: int) -> DocumentEntry:
        """El documento `doc_id`, leyendo una sola página: `O(1)`.

        Raises:
            DocumentNotFoundError: si no hay documento con ese `doc_id`.
        """
        page_index, slot = self._locate(doc_id)
        page = self._page(page_index)
        return _decode(page.record_at(slot), page.state_of(slot))

    def entries(self) -> Iterator[DocumentEntry]:
        """Todos los documentos en orden de `doc_id`, borrados incluidos, página a página."""
        page_count = -(-self.slot_count // self._capacity)
        for page_index in range(page_count):
            page = self._page(page_index)
            for slot in range(page.used_slots):
                yield _decode(page.record_at(slot), page.state_of(slot))

    def columns(self) -> DocumentColumns:
        """Longitudes, normas, direcciones y borrados de todos los documentos. La primera
        llamada recorre la tabla, `O(N)`; las siguientes devuelven lo ya cargado, `O(1)`."""
        if self._columns is None:
            self._columns = self._load_columns()
        return self._columns

    def append(self, record_id: RecordId, length: int, norm: float) -> int:
        """Añade un documento al final y devuelve su `doc_id`: una página."""
        doc_id = self.slot_count
        page_index, slot = divmod(doc_id, self._capacity)
        page_id = FIRST_DATA_PAGE + page_index
        if page_id == self._pager.page_count:
            self._pager.allocate()
            page = RecordPage.create(self._pager.page_size, DOCUMENT_RECORD.size)
        else:
            page = self._page(page_index)
        page.write(slot, _encode(record_id, length, norm))
        self._pager.write(page_id, page.to_bytes())
        self.slot_count += 1
        self.total_length += length
        if self._columns is not None:
            self._columns.append(DocumentEntry(record_id, length, norm, deleted=False))
        return doc_id

    def mark_deleted(self, doc_id: int) -> DocumentEntry:
        """Deja la lápida del documento y lo descuenta de N y de la longitud total: una
        página. Devuelve el documento tal como estaba.

        Raises:
            DocumentNotFoundError: si no hay documento vivo con ese `doc_id`.
        """
        page_index, slot = self._locate(doc_id)
        page = self._page(page_index)
        entry = _decode(page.record_at(slot), page.state_of(slot))
        if entry.deleted:
            raise DocumentNotFoundError(f"el documento {doc_id} ya estaba borrado")
        page.tombstone(slot)
        self._pager.write(FIRST_DATA_PAGE + page_index, page.to_bytes())
        self.deleted_count += 1
        self.total_length -= entry.length
        if self._columns is not None:
            self._columns.deleted.add(doc_id)
        return entry

    def flush(self) -> None:
        self._write_header()
        self._pager.flush()

    def close(self) -> None:
        self._write_header()
        self._pager.close()

    def _write_header(self) -> None:
        DOCUMENTS_HEADER.write(self._pager, self.slot_count, self.deleted_count, self.total_length)

    def _locate(self, doc_id: int) -> tuple[int, int]:
        if not 0 <= doc_id < self.slot_count:
            raise DocumentNotFoundError(
                f"no hay documento {doc_id}: el índice repartió {self.slot_count}"
            )
        return divmod(doc_id, self._capacity)

    def _load_columns(self) -> DocumentColumns:
        columns = DocumentColumns(
            array(LENGTH_TYPECODE), array(NORM_TYPECODE), array(RECORD_KEY_TYPECODE), set()
        )
        for doc_id, entry in enumerate(self.entries()):
            columns.append(entry)
            if entry.deleted:
                columns.deleted.add(doc_id)
        return columns

    def _page(self, page_index: int) -> RecordPage:
        raw = self._pager.read(FIRST_DATA_PAGE + page_index)
        return RecordPage.from_bytes(raw, DOCUMENT_RECORD.size)


def _encode(record_id: RecordId, length: int, norm: float) -> bytes:
    return DOCUMENT_RECORD.pack(record_id.page_id, record_id.slot, length, norm)


def _decode(raw: bytes, state: SlotState) -> DocumentEntry:
    page_id, slot, length, norm = DOCUMENT_RECORD.unpack(raw)
    return DocumentEntry(RecordId(page_id, slot), length, norm, state is SlotState.DELETED)
