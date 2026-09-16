"""Índice B+ agrupado: el árbol **es** el archivo de datos.

Las hojas guardan el registro completo, no una dirección. Recuperar una fila por su clave
no cuesta ningún acceso extra, y un recorrido por rango devuelve las filas directamente en
orden. A cambio, solo puede haber un índice agrupado por tabla y las hojas son grandes, así
que el árbol tiene menos entradas por página y crece más en altura.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import TracebackType
from typing import Any

from config import EngineConfig
from index.keys import Key, ScalarKeyCodec
from storage.record import RecordSerializer

from .tree import BPlusTree


class ClusteredBPlusIndex:
    """Almacena las filas dentro del propio árbol, ordenadas por la clave.

    Raises:
        DuplicateKeyError: al insertar una clave que ya existe.
    """

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
        self._tree = BPlusTree(path, ScalarKeyCodec(key_field_definition), serializer.size, config)

    @property
    def record_count(self) -> int:
        return self._tree.entry_count

    @property
    def height(self) -> int:
        return self._tree.height

    @property
    def page_count(self) -> int:
        return self._tree.page_count

    def insert(self, record: bytes) -> None:
        self._tree.insert(self._key_of(record), record)

    def search(self, key: Key) -> bytes | None:
        return self._tree.search(key)

    def range_search(self, low: Key | None, high: Key | None) -> Iterator[bytes]:
        for _, record in self._tree.range_scan(low, high):
            yield record

    def scan(self) -> Iterator[bytes]:
        return self.range_search(None, None)

    def delete(self, key: Key) -> bool:
        return self._tree.delete(key)

    def describe(self) -> dict[str, Any]:
        return self._tree.describe()

    def flush(self) -> None:
        self._tree.flush()

    def close(self) -> None:
        self._tree.close()

    def __enter__(self) -> ClusteredBPlusIndex:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _key_of(self, record: bytes) -> Key:
        return self._serializer.unpack_field(record, self._key_position)
