"""Índice B+ no agrupado: las hojas guardan direcciones, no filas.

El archivo de datos vive aparte (normalmente un heap file) y el índice solo dice **dónde**
está cada fila. Como una columna no clave puede repetirse, la clave del árbol es el par
`(valor, dirección)`: así todas las claves del árbol siguen siendo únicas y las entradas de
un mismo valor quedan contiguas, que es lo que hace que buscarlas sea un recorrido por
rango. Es la misma técnica que usan los B+ de los gestores reales.

El coste que hay que tener presente es el **salto al heap**: cada resultado exige un acceso
extra a la página donde vive la fila.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import TracebackType

from config import EngineConfig
from index.keys import CompositeKeyCodec, Key, RecordIdKeyCodec, ScalarKeyCodec
from storage.record import RecordSerializer
from storage.record_id import RecordId

from .tree import BPlusTree

EMPTY_VALUE = b""
LOWEST_RECORD_ID = RecordId(page_id=0, slot=0)
HIGHEST_RECORD_ID = RecordId(page_id=0xFFFFFFFF, slot=0xFFFF)


class UnclusteredBPlusIndex:
    """Índice secundario que apunta a las filas de otro archivo."""

    def __init__(
        self,
        path: Path,
        serializer: RecordSerializer,
        key_field: str,
        config: EngineConfig,
    ) -> None:
        self._serializer = serializer
        self._key_position = serializer.schema.position_of(key_field)
        key_field_definition = serializer.schema.fields[self._key_position]
        codec = CompositeKeyCodec([ScalarKeyCodec(key_field_definition), RecordIdKeyCodec()])
        self._tree = BPlusTree(path, codec, len(EMPTY_VALUE), config)

    @property
    def entry_count(self) -> int:
        return self._tree.entry_count

    @property
    def height(self) -> int:
        return self._tree.height

    @property
    def page_count(self) -> int:
        return self._tree.page_count

    def insert(self, record: bytes, record_id: RecordId) -> None:
        self._tree.insert((self._key_of(record), record_id), EMPTY_VALUE)

    def search(self, value: Key) -> list[RecordId]:
        """Direcciones de todas las filas cuyo campo indexado vale `value`."""
        return [record_id for _, record_id in self._range(value, value)]

    def range_search(self, low: Key | None, high: Key | None) -> Iterator[tuple[Key, RecordId]]:
        """Pares `(valor, dirección)` con `low <= valor <= high`, en orden de valor."""
        return self._range(low, high)

    def scan(self) -> Iterator[tuple[Key, RecordId]]:
        return self._range(None, None)

    def delete(self, record: bytes, record_id: RecordId) -> bool:
        return self._tree.delete((self._key_of(record), record_id))

    def flush(self) -> None:
        self._tree.flush()

    def close(self) -> None:
        self._tree.close()

    def __enter__(self) -> UnclusteredBPlusIndex:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _range(self, low: Key | None, high: Key | None) -> Iterator[tuple[Key, RecordId]]:
        lower = None if low is None else (low, LOWEST_RECORD_ID)
        upper = None if high is None else (high, HIGHEST_RECORD_ID)
        for (value, record_id), _ in self._tree.range_scan(lower, upper):
            yield value, record_id

    def _key_of(self, record: bytes) -> Key:
        return self._serializer.unpack_field(record, self._key_position)
