"""Índices secundarios: lo que el motor necesita de cualquier índice sobre una columna.

Un índice secundario apunta a filas de un heap file por su dirección. Los tres que hay
—B+, hash y R-Tree— comparten la misma forma: sacan de cada fila el valor de su columna,
dejan fuera las filas en las que es NULL y guardan el par `(valor, dirección)` a su manera.
`ColumnIndex` reúne esa parte común; cada estructura aporta solo cómo guarda y busca.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from types import TracebackType
from typing import Any, Protocol, Self, runtime_checkable

from storage.record import RecordSerializer
from storage.record_id import RecordId
from storage.types import Key


class SecondaryIndex(Protocol):
    """Lo que el motor necesita de cualquier índice secundario."""

    def build(self, rows: Iterable[tuple[bytes, RecordId]]) -> None: ...

    def insert(self, record: bytes, record_id: RecordId) -> None: ...

    def delete(self, record: bytes, record_id: RecordId) -> bool: ...

    def search(self, value: Key) -> list[RecordId]: ...

    def describe(self) -> dict[str, Any]: ...

    def flush(self) -> None: ...

    def close(self) -> None: ...


@runtime_checkable
class RangeIndex(SecondaryIndex, Protocol):
    """Un índice que guarda sus valores en orden y puede recorrer un rango."""

    def range_search(self, low: Key | None, high: Key | None) -> Iterator[tuple[Key, RecordId]]:
        ...


class ColumnIndex(ABC):
    """Base de los índices sobre una columna de una tabla.

    Las filas cuyo valor es NULL no entran: ninguna búsqueda por igualdad, por rango ni
    por cercanía puede devolverlas, y un NULL no tiene sitio en ningún orden.
    """

    def __init__(self, serializer: RecordSerializer, column: str) -> None:
        self._serializer = serializer
        self._position = serializer.schema.position_of(column)

    def build(self, rows: Iterable[tuple[bytes, RecordId]]) -> None:
        """Indexa de una vez todas las filas de la tabla."""
        for record, record_id in rows:
            self.insert(record, record_id)

    def insert(self, record: bytes, record_id: RecordId) -> None:
        value = self._value_of(record)
        if value is not None:
            self._add(value, record_id)

    def delete(self, record: bytes, record_id: RecordId) -> bool:
        """Quita la fila del índice. Devuelve si estaba."""
        value = self._value_of(record)
        return value is not None and self._remove(value, record_id)

    def search(self, value: Key) -> list[RecordId]:
        """Direcciones de las filas cuyo valor es `value`; ninguna si es NULL."""
        if value is None:
            return []
        return self._find(value)

    @abstractmethod
    def describe(self) -> dict[str, Any]:
        """Forma física del índice, para inspeccionarla."""

    @abstractmethod
    def flush(self) -> None:
        """Escribe en disco lo modificado."""

    @abstractmethod
    def close(self) -> None:
        """Escribe en disco lo modificado y suelta el archivo."""

    @abstractmethod
    def _add(self, value: Key, record_id: RecordId) -> None: ...

    @abstractmethod
    def _remove(self, value: Key, record_id: RecordId) -> bool: ...

    @abstractmethod
    def _find(self, value: Key) -> list[RecordId]: ...

    def _value_of(self, record: bytes) -> Key:
        return self._serializer.unpack_field(record, self._position)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
