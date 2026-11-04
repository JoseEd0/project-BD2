"""El hash extendible como índice secundario sobre una columna de una tabla."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from config import EngineConfig
from index.keys import ScalarKeyCodec
from index.secondary import ColumnIndex
from storage.record import FieldReader
from storage.record_id import RECORD_ID_SIZE, RecordId
from storage.types import Key

from .extendible_hash import DIRECTORY_SUFFIX, ExtendibleHashIndex


class HashSecondaryIndex(ColumnIndex):
    """Índice secundario por igualdad: a cada valor, las direcciones de sus filas."""

    def __init__(
        self,
        path: Path,
        serializer: FieldReader,
        column: str,
        config: EngineConfig,
    ) -> None:
        super().__init__(serializer, column)
        codec = ScalarKeyCodec(serializer.schema.fields[self._position])
        self._index = ExtendibleHashIndex(path, codec, RECORD_ID_SIZE, config)

    @staticmethod
    def files_of(path: Path) -> list[Path]:
        """Los archivos que ocupa un índice guardado en `path`: las cubetas y el directorio."""
        return [path, path.with_name(path.name + DIRECTORY_SUFFIX)]

    def describe(self) -> dict[str, Any]:
        return self._index.describe()

    def flush(self) -> None:
        self._index.flush()

    def close(self) -> None:
        self._index.close()

    def _add(self, value: Key, record_id: RecordId) -> None:
        self._index.insert(value, record_id.pack())

    def _remove(self, value: Key, record_id: RecordId) -> bool:
        return self._index.delete_entry(value, record_id.pack())

    def _find(self, value: Key) -> list[RecordId]:
        return [RecordId.unpack(raw) for raw in self._index.search(value)]
