"""De la dirección de una fila al `doc_id` de su documento, para borrarlo.

Al borrar una fila, la tabla le da al índice la dirección (`RecordId`), no el `doc_id`.
El mapa vive en el hash extendible de la Parte 1, con la dirección como clave y el
`doc_id` en 4 bytes como valor: no ocupa memoria, no hay que reconstruirlo al abrir el
índice y cada consulta lee una página del directorio y una cubeta. Guarda solo los
documentos vivos: un borrado quita la entrada, porque la ranura del heap se reutiliza y
la misma dirección puede ser después de otra fila.
"""

from __future__ import annotations

import struct
from collections.abc import Iterable
from pathlib import Path

from config import EngineConfig
from index.hash import ExtendibleHashIndex
from index.keys import RecordIdKeyCodec
from storage.record_id import RecordId

from .format import DocumentAlreadyIndexedError

DOC_ID = struct.Struct("<I")


class DocumentMap:
    """`RecordId → doc_id` de los documentos vivos, en un hash extendible.

    Complejidad: buscar, añadir y quitar cuestan `O(1)` páginas.
    """

    def __init__(self, path: Path, config: EngineConfig) -> None:
        self._entries = ExtendibleHashIndex(path, RecordIdKeyCodec(), DOC_ID.size, config)

    files_of = staticmethod(ExtendibleHashIndex.files_of)

    @property
    def entry_count(self) -> int:
        return self._entries.entry_count

    @property
    def page_count(self) -> int:
        return self._entries.page_count

    def load(self, documents: Iterable[tuple[RecordId, int]]) -> None:
        """Llena un mapa vacío con documentos de direcciones distintas, como los que deja
        SPIMI al construir: sin la búsqueda que hace `add` antes de cada inserción."""
        for record_id, doc_id in documents:
            self._entries.insert(record_id, DOC_ID.pack(doc_id))

    def add(self, record_id: RecordId, doc_id: int) -> None:
        """Raises:
        DocumentAlreadyIndexedError: si la dirección ya es de un documento vivo."""
        if self._entries.search(record_id):
            raise DocumentAlreadyIndexedError(
                f"la fila {record_id} ya tiene un documento en el índice"
            )
        self._entries.insert(record_id, DOC_ID.pack(doc_id))

    def pop(self, record_id: RecordId) -> int | None:
        """Quita la dirección del mapa y devuelve su `doc_id`, o `None` si no estaba."""
        found = self._entries.search(record_id)
        if not found:
            return None
        self._entries.delete_entry(record_id, found[0])
        (doc_id,) = DOC_ID.unpack(found[0])
        return int(doc_id)

    def flush(self) -> None:
        self._entries.flush()

    def close(self) -> None:
        self._entries.close()
