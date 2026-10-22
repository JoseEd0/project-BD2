"""Dónde viven las filas de una tabla: un heap file o un archivo ordenado por la clave.

Las tres organizaciones se reducen a dos formas de localizar una fila:

* un **heap file** la localiza por su dirección `(página, ranura)`, que no cambia mientras
  la fila exista; por eso solo él admite índices secundarios, que guardan esas direcciones;
* un **archivo secuencial** o un **B+ agrupado** la localizan por su clave primaria: las
  filas se mueven al reorganizar o al dividir nodos, y una dirección dejaría de valer.

`Table` habla con las dos a través de `RowStore` y no pregunta qué estructura hay debajo.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Iterator, Sequence
from typing import Any

from index.bplustree import ClusteredBPlusIndex
from storage.heap import HeapFile
from storage.record_id import RecordId
from storage.sequential import SequentialFile
from storage.types import Key, StorageError

# Una fila tal como está guardada, con su dirección si la tabla es un heap file.
AddressedRecord = tuple[RecordId | None, bytes]


class UnorderedStoreError(StorageError):
    """Se pidió un orden o una dirección que la organización de la tabla no tiene."""


class RowStore(ABC):
    """Las filas de una tabla, guardadas de alguna de las dos formas."""

    @property
    @abstractmethod
    def keeps_addresses(self) -> bool:
        """Si cada fila tiene una dirección estable, que un índice secundario pueda guardar."""

    @property
    @abstractmethod
    def record_count(self) -> int: ...

    @abstractmethod
    def scan(self) -> Iterator[AddressedRecord]:
        """Todas las filas, en el orden físico de la organización."""

    @abstractmethod
    def with_keys(self, keys: Sequence[Key]) -> Iterator[AddressedRecord]:
        """Las filas cuya clave primaria es una de `keys`."""

    @abstractmethod
    def in_key_range(self, low: Key | None, high: Key | None) -> Iterator[AddressedRecord]:
        """Las filas con la clave en el rango, en orden de clave.

        Raises:
            UnorderedStoreError: si la organización no guarda las filas en orden.
        """

    @abstractmethod
    def at(self, addresses: Iterable[RecordId]) -> Iterator[bytes]:
        """Las filas de esas direcciones, en el orden pedido.

        Raises:
            UnorderedStoreError: si la organización no tiene direcciones estables.
        """

    @abstractmethod
    def insert(self, record: bytes) -> RecordId | None:
        """Guarda la fila; devuelve su dirección si la organización las tiene."""

    @abstractmethod
    def delete(self, address: RecordId | None, record: bytes) -> None: ...

    @abstractmethod
    def update(self, address: RecordId | None, old: bytes, new: bytes) -> None: ...

    @abstractmethod
    def describe(self) -> dict[str, Any]: ...

    @abstractmethod
    def flush(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...


class HeapStore(RowStore):
    """Filas de un heap file, localizadas por su dirección."""

    def __init__(self, heap: HeapFile, key_of: Callable[[bytes], Key] | None) -> None:
        self._heap = heap
        self._key_of = key_of

    @property
    def keeps_addresses(self) -> bool:
        return True

    @property
    def record_count(self) -> int:
        return self._heap.record_count

    def scan(self) -> Iterator[AddressedRecord]:
        return self._heap.scan()

    def with_keys(self, keys: Sequence[Key]) -> Iterator[AddressedRecord]:
        """Sin un índice sobre la clave, buscarla en un heap es recorrerlo entero. `Table`
        usa el índice de la clave cuando lo hay; esto es lo que queda si se borró."""
        key_of = self._key_of
        if key_of is None:
            return
        for address, record in self._heap.scan():
            if key_of(record) in keys:
                yield address, record

    def in_key_range(self, low: Key | None, high: Key | None) -> Iterator[AddressedRecord]:
        raise UnorderedStoreError("un heap file no guarda las filas en orden de clave")

    def at(self, addresses: Iterable[RecordId]) -> Iterator[bytes]:
        return self._heap.read_many(addresses)

    def insert(self, record: bytes) -> RecordId | None:
        return self._heap.insert(record)

    def delete(self, address: RecordId | None, record: bytes) -> None:
        self._heap.delete(_required(address))

    def update(self, address: RecordId | None, old: bytes, new: bytes) -> None:
        self._heap.update(_required(address), new)

    def describe(self) -> dict[str, Any]:
        return self._heap.describe()

    def flush(self) -> None:
        self._heap.flush()

    def close(self) -> None:
        self._heap.close()


class KeyedStore(RowStore):
    """Filas de un archivo secuencial o de un B+ agrupado, localizadas por su clave."""

    def __init__(
        self, storage: SequentialFile | ClusteredBPlusIndex, key_of: Callable[[bytes], Key]
    ) -> None:
        self._storage = storage
        self._key_of = key_of

    @property
    def keeps_addresses(self) -> bool:
        return False

    @property
    def record_count(self) -> int:
        return self._storage.record_count

    def scan(self) -> Iterator[AddressedRecord]:
        for record in self._storage.scan():
            yield None, record

    def with_keys(self, keys: Sequence[Key]) -> Iterator[AddressedRecord]:
        for key in keys:
            for record in self._records_with(key):
                yield None, record

    def in_key_range(self, low: Key | None, high: Key | None) -> Iterator[AddressedRecord]:
        for record in self._storage.range_search(low, high):
            yield None, record

    def at(self, addresses: Iterable[RecordId]) -> Iterator[bytes]:
        raise UnorderedStoreError("las filas de esta organización no tienen dirección estable")

    def insert(self, record: bytes) -> RecordId | None:
        self._storage.insert(record)
        return None

    def delete(self, address: RecordId | None, record: bytes) -> None:
        self._storage.delete(self._key_of(record))

    def update(self, address: RecordId | None, old: bytes, new: bytes) -> None:
        self._storage.delete(self._key_of(old))
        self._storage.insert(new)

    def describe(self) -> dict[str, Any]:
        return self._storage.describe()

    def flush(self) -> None:
        self._storage.flush()

    def close(self) -> None:
        self._storage.close()

    def _records_with(self, key: Key) -> list[bytes]:
        """El secuencial admite claves repetidas y devuelve todas; el B+ agrupado no, y
        devuelve la única o ninguna."""
        if isinstance(self._storage, SequentialFile):
            return self._storage.search(key)
        found = self._storage.search(key)
        return [] if found is None else [found]


def _required(address: RecordId | None) -> RecordId:
    if address is None:
        raise UnorderedStoreError("falta la dirección de la fila en el heap file")
    return address
