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
from typing import Any

from config import EngineConfig
from index.keys import CompositeKeyCodec, RecordIdKeyCodec, ScalarKeyCodec
from index.secondary import ColumnIndex
from storage.record import RecordSerializer
from storage.record_id import HIGHEST_RECORD_ID, LOWEST_RECORD_ID, RecordId
from storage.types import Key

from .tree import BPlusTree

EMPTY_VALUE = b""


class UnclusteredBPlusIndex(ColumnIndex):
    """Índice secundario que apunta a las filas de otro archivo, en orden de valor."""

    def __init__(
        self,
        path: Path,
        serializer: RecordSerializer,
        key_field: str,
        config: EngineConfig,
    ) -> None:
        super().__init__(serializer, key_field)
        key_field_definition = serializer.schema.fields[self._position]
        codec = CompositeKeyCodec([ScalarKeyCodec(key_field_definition), RecordIdKeyCodec()])
        self._tree = BPlusTree(path, codec, len(EMPTY_VALUE), config)

    @property
    def entry_count(self) -> int:
        return self._tree.entry_count

    @property
    def height(self) -> int:
        return self._tree.height

    def range_search(self, low: Key | None, high: Key | None) -> Iterator[tuple[Key, RecordId]]:
        """Pares `(valor, dirección)` con `low <= valor <= high`, en orden de valor."""
        return self._range(low, high)

    def scan(self) -> Iterator[tuple[Key, RecordId]]:
        return self._range(None, None)

    def describe(self) -> dict[str, Any]:
        return self._tree.describe()

    def flush(self) -> None:
        self._tree.flush()

    def close(self) -> None:
        self._tree.close()

    def _add(self, value: Key, record_id: RecordId) -> None:
        self._tree.insert((value, record_id), EMPTY_VALUE)

    def _remove(self, value: Key, record_id: RecordId) -> bool:
        return self._tree.delete((value, record_id))

    def _find(self, value: Key) -> list[RecordId]:
        return [record_id for _, record_id in self._range(value, value)]

    def _range(self, low: Key | None, high: Key | None) -> Iterator[tuple[Key, RecordId]]:
        lower = None if low is None else (low, LOWEST_RECORD_ID)
        upper = None if high is None else (high, HIGHEST_RECORD_ID)
        for (value, record_id), _ in self._tree.range_scan(lower, upper):
            yield value, record_id
