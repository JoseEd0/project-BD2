"""Una tabla: su organización física más sus índices secundarios.

Es la pieza que decide qué estructura abre cada tabla y la que mantiene los índices
coherentes con los datos: si una fila entra o sale, todos sus índices se enteran.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol

from config import EngineConfig
from index.bplustree import ClusteredBPlusIndex, UnclusteredBPlusIndex
from index.hash import ExtendibleHashIndex
from index.hash.extendible_hash import DIRECTORY_SUFFIX
from index.keys import Key, ScalarKeyCodec
from query.catalog import IndexDefinition, Organization, TableDefinition
from sql.nodes import IndexType
from storage.heap import HeapFile
from storage.record import Record, RecordSerializer
from storage.record_id import RECORD_ID_SIZE, RecordId
from storage.schema import Schema
from storage.sequential import SequentialFile
from storage.sequential.sequential_file import OVERFLOW_SUFFIX, REBUILD_SUFFIX
from storage.types import StorageError

HEAP_SUFFIX = ".heap"
SEQUENTIAL_SUFFIX = ".seq"
CLUSTERED_SUFFIX = ".bpt"
INDEX_SUFFIX = ".idx"
PRIMARY_KEY_INDEX_PREFIX = "pk_"
OWNED_SUFFIXES = (
    HEAP_SUFFIX,
    SEQUENTIAL_SUFFIX,
    OVERFLOW_SUFFIX,
    REBUILD_SUFFIX,
    CLUSTERED_SUFFIX,
    INDEX_SUFFIX,
    DIRECTORY_SUFFIX,
)


class TableError(StorageError):
    """Error de operación sobre una tabla."""


class DuplicatePrimaryKeyError(TableError):
    """Ya existe una fila con esa clave primaria."""


class UnsupportedIndexError(TableError):
    """La organización de la tabla no admite ese índice."""


class SecondaryIndex(Protocol):
    """Lo que el motor necesita de cualquier índice secundario."""

    def insert(self, record: bytes, record_id: RecordId) -> None: ...

    def delete(self, record: bytes, record_id: RecordId) -> bool: ...

    def search(self, value: Key) -> list[RecordId]: ...

    def describe(self) -> dict[str, Any]: ...

    def close(self) -> None: ...


class HashSecondaryIndex:
    """Adapta el hash extendible a la interfaz de índice secundario."""

    def __init__(
        self,
        path: Path,
        serializer: RecordSerializer,
        column: str,
        config: EngineConfig,
    ) -> None:
        self._serializer = serializer
        self._position = serializer.schema.position_of(column)
        codec = ScalarKeyCodec(serializer.schema.fields[self._position])
        self._index = ExtendibleHashIndex(path, codec, RECORD_ID_SIZE, config)

    def insert(self, record: bytes, record_id: RecordId) -> None:
        self._index.insert(self._key_of(record), record_id.pack())

    def delete(self, record: bytes, record_id: RecordId) -> bool:
        """El hash borra por clave, así que se reinsertan las direcciones que sobreviven."""
        key = self._key_of(record)
        stored = self._index.search(key)
        remaining = [value for value in stored if RecordId.unpack(value) != record_id]
        if len(remaining) == len(stored):
            return False
        self._index.delete(key)
        for value in remaining:
            self._index.insert(key, value)
        return True

    def search(self, value: Key) -> list[RecordId]:
        return [RecordId.unpack(raw) for raw in self._index.search(value)]

    def describe(self) -> dict[str, Any]:
        return self._index.describe()

    def close(self) -> None:
        self._index.close()

    def _key_of(self, record: bytes) -> Key:
        return self._serializer.unpack_field(record, self._position)


class Table:
    """Acceso a las filas de una tabla por su organización o por sus índices."""

    def __init__(self, definition: TableDefinition, config: EngineConfig) -> None:
        self._definition = definition
        self._config = config
        self._serializer = RecordSerializer(definition.schema)
        self._directory = config.data_directory
        self._storage = self._open_storage()
        self._indexes: dict[str, SecondaryIndex] = {
            index.name: self._open_index(index) for index in definition.indexes
        }

    @property
    def definition(self) -> TableDefinition:
        return self._definition

    @property
    def name(self) -> str:
        return self._definition.name

    @property
    def schema(self) -> Schema:
        return self._definition.schema

    @property
    def serializer(self) -> RecordSerializer:
        return self._serializer

    @property
    def organization(self) -> Organization:
        return self._definition.organization

    @property
    def primary_key(self) -> str | None:
        return self._definition.primary_key

    @property
    def row_count(self) -> int:
        return self._storage.record_count

    def insert(self, values: Record) -> None:
        """Inserta la fila y actualiza todos los índices.

        Raises:
            DuplicatePrimaryKeyError: si la clave primaria ya existe.
        """
        record = self._serializer.pack(values)
        self._reject_duplicate_key(record)
        if not isinstance(self._storage, HeapFile):
            self._storage.insert(record)
            return
        record_id = self._storage.insert(record)
        for index in self._indexes.values():
            index.insert(record, record_id)

    def scan(self) -> Iterator[Record]:
        """Todas las filas. El orden depende de la organización."""
        for _, record in self._scan_raw():
            yield self._serializer.unpack(record)

    def search_primary_key(self, key: Key) -> Iterator[Record]:
        """Filas cuya clave primaria vale `key`, por el camino más corto que haya.

        Un heap file no ordena nada, así que sin índice esto es un recorrido completo. Como
        toda tabla con clave primaria recibe un índice B+ sobre ella, el caso normal es que
        aquí se use ese índice: sin esto, cada inserción pagaría un recorrido entero al
        comprobar que la clave no se repite, y cargar N filas costaría O(N²).
        """
        if isinstance(self._storage, HeapFile):
            index = self._primary_key_index()
            if index is None:
                yield from self._filter_scan(key)
            else:
                yield from self.search_index(index.name, key)
        elif isinstance(self._storage, SequentialFile):
            for record in self._storage.search(key):
                yield self._serializer.unpack(record)
        else:
            found = self._storage.search(key)
            if found is not None:
                yield self._serializer.unpack(found)

    def range_primary_key(self, low: Key | None, high: Key | None) -> Iterator[Record]:
        """Filas con la clave primaria en el rango, en orden. Solo si la organización ordena.

        Raises:
            UnsupportedIndexError: si la tabla es un heap file, que no guarda orden.
        """
        if isinstance(self._storage, SequentialFile):
            bounds = self._resolved_bounds(low, high)
            for record in self._storage.range_search(*bounds):
                yield self._serializer.unpack(record)
        elif isinstance(self._storage, ClusteredBPlusIndex):
            for record in self._storage.range_search(low, high):
                yield self._serializer.unpack(record)
        else:
            raise UnsupportedIndexError(f"'{self.name}' no guarda las filas en orden de clave")

    def search_index(self, index_name: str, value: Key) -> Iterator[Record]:
        """Filas que el índice indicado asocia a ese valor."""
        addresses = self._indexes[index_name].search(value)
        heap = self._require_heap()
        for address in addresses:
            yield self._serializer.unpack(heap.read(address))

    def range_index(self, index_name: str, low: Key | None, high: Key | None) -> Iterator[Record]:
        """Filas del índice en un rango de valores. Solo para índices B+.

        Raises:
            UnsupportedIndexError: si el índice es hash, que no guarda orden.
        """
        index = self._indexes[index_name]
        if not isinstance(index, UnclusteredBPlusIndex):
            raise UnsupportedIndexError(f"el índice '{index_name}' no admite consultas por rango")
        heap = self._require_heap()
        for _, address in index.range_search(low, high):
            yield self._serializer.unpack(heap.read(address))

    def delete_where(self, matches: Callable[[Record], bool]) -> int:
        """Borra las filas que cumplen el predicado y devuelve cuántas."""
        victims = [
            (address, record)
            for address, record in self._scan_raw()
            if matches(self._serializer.unpack(record))
        ]
        for address, record in victims:
            self._delete_one(address, record)
        return len(victims)

    def update_where(
        self,
        matches: Callable[[Record], bool],
        transform: Callable[[Record], Record],
    ) -> int:
        """Reemplaza las filas que cumplen el predicado y devuelve cuántas.

        En un heap file la fila se reescribe en su sitio y solo se tocan los índices; en
        las organizaciones ordenadas por clave hay que borrar e insertar, porque la clave
        puede cambiar de valor y con ella el lugar que le toca.
        """
        victims = [
            (address, record, self._serializer.unpack(record))
            for address, record in self._scan_raw()
            if matches(self._serializer.unpack(record))
        ]
        for address, record, row in victims:
            self._update_one(address, record, self._serializer.pack(transform(row)))
        return len(victims)

    def _update_one(self, address: RecordId | None, old: bytes, new: bytes) -> None:
        if isinstance(self._storage, HeapFile):
            if address is None:
                raise TableError("falta la dirección de la fila que se quiere actualizar")
            for index in self._indexes.values():
                index.delete(old, address)
            self._storage.update(address, new)
            for index in self._indexes.values():
                index.insert(new, address)
            return
        self._storage.delete(self._primary_key_of(old))
        self._storage.insert(new)

    def delete_first(self, target: Record) -> bool:
        """Borra una sola fila igual a `target`. Lo usa el deshacer de una inserción."""
        for address, record in self._scan_raw():
            if self._serializer.unpack(record) == target:
                self._delete_one(address, record)
                return True
        return False

    def update_first(self, target: Record, replacement: Record) -> bool:
        """Reemplaza una sola fila igual a `target`. Lo usa el deshacer de un UPDATE."""
        for address, record in self._scan_raw():
            if self._serializer.unpack(record) == target:
                self._update_one(address, record, self._serializer.pack(replacement))
                return True
        return False

    def build_index(self, definition: IndexDefinition) -> SecondaryIndex:
        """Crea el índice y lo llena con las filas que ya existen.

        Raises:
            UnsupportedIndexError: si la tabla no es un heap file.
        """
        self._require_heap()
        index = self._open_index(definition)
        for address, record in self._scan_raw():
            if address is None:
                raise UnsupportedIndexError("un índice secundario necesita direcciones estables")
            index.insert(record, address)
        self._indexes[definition.name] = index
        return index

    def drop_index(self, name: str) -> None:
        index = self._indexes.pop(name)
        index.close()
        self.discard_index_files(name)

    def discard_index_files(self, name: str) -> None:
        """Borra los archivos de un índice, incluido el directorio de un índice hash."""
        path = self._index_path(name)
        path.unlink(missing_ok=True)
        path.with_name(path.name + DIRECTORY_SUFFIX).unlink(missing_ok=True)

    def close(self) -> None:
        self._storage.close()
        for index in self._indexes.values():
            index.close()

    def describe_structure(self) -> dict[str, Any]:
        """Organización física y forma real de cada índice, para inspeccionarlas."""
        return {
            "table": self.name,
            "organization": self.organization.value,
            "rows": self.row_count,
            "storage": self._describe_storage(),
            "indexes": [
                {
                    "name": definition.name,
                    "column": definition.column,
                    "method": definition.method.value,
                    "structure": self._indexes[definition.name].describe(),
                }
                for definition in self._definition.indexes
            ],
        }

    def _describe_storage(self) -> dict[str, Any]:
        if isinstance(self._storage, HeapFile):
            return {
                "kind": "heap",
                "pages": self._storage.page_count,
                "slots_per_page": self._storage.slots_per_page,
                "records": self._storage.record_count,
            }
        if isinstance(self._storage, SequentialFile):
            return {
                "kind": "sequential",
                "main_pages": self._storage.main_page_count,
                "slots_per_page": self._storage.slots_per_page,
                "records": self._storage.record_count,
                "overflow_records": self._storage.overflow_count,
                "deleted_records": self._storage.deleted_count,
                "waste_ratio": self._storage.waste_ratio,
            }
        return self._storage.describe()

    def remove_files(self) -> None:
        """Borra del disco los archivos que creó la tabla, y solo esos.

        Filtrar por sufijo importa: un `productos.csv` del usuario en el mismo directorio
        también empieza por `productos.`, y no es de la tabla.
        """
        self.close()
        for path in self._directory.glob(f"{self.name}.*"):
            if path.name.endswith(OWNED_SUFFIXES):
                path.unlink(missing_ok=True)

    def __enter__(self) -> Table:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _scan_raw(self) -> Iterator[tuple[RecordId | None, bytes]]:
        if isinstance(self._storage, HeapFile):
            yield from self._storage.scan()
        elif isinstance(self._storage, SequentialFile):
            for record in self._storage.scan():
                yield None, record
        else:
            for record in self._storage.scan():
                yield None, record

    def _delete_one(self, address: RecordId | None, record: bytes) -> None:
        if isinstance(self._storage, HeapFile):
            if address is None:
                raise TableError("falta la dirección de la fila que se quiere borrar")
            for index in self._indexes.values():
                index.delete(record, address)
            self._storage.delete(address)
            return
        key = self._primary_key_of(record)
        self._storage.delete(key)

    def _primary_key_index(self) -> IndexDefinition | None:
        if self._definition.primary_key is None:
            return None
        return self._definition.index_on(self._definition.primary_key)

    def _filter_scan(self, key: Key) -> Iterator[Record]:
        position = self._primary_key_position()
        for _, record in self._scan_raw():
            if self._serializer.unpack_field(record, position) == key:
                yield self._serializer.unpack(record)

    def _reject_duplicate_key(self, record: bytes) -> None:
        if self._definition.primary_key is None:
            return
        key = self._primary_key_of(record)
        if next(iter(self.search_primary_key(key)), None) is not None:
            raise DuplicatePrimaryKeyError(
                f"la clave primaria {key!r} ya existe en '{self.name}'"
            )

    def _primary_key_of(self, record: bytes) -> Key:
        return self._serializer.unpack_field(record, self._primary_key_position())

    def _primary_key_position(self) -> int:
        if self._definition.primary_key is None:
            raise TableError(f"'{self.name}' no declara clave primaria")
        return self.schema.position_of(self._definition.primary_key)

    def _resolved_bounds(self, low: Key | None, high: Key | None) -> tuple[Key, Key]:
        """El archivo secuencial necesita ambos extremos; se toman de los datos si faltan."""
        if low is not None and high is not None:
            return low, high
        keys = [self._primary_key_of(record) for _, record in self._scan_raw()]
        if not keys:
            return 0, 0
        return (min(keys) if low is None else low, max(keys) if high is None else high)

    def _require_heap(self) -> HeapFile:
        if not isinstance(self._storage, HeapFile):
            raise UnsupportedIndexError(
                f"'{self.name}' no es un heap file: sus filas no tienen dirección estable"
            )
        return self._storage

    def _open_storage(self) -> HeapFile | SequentialFile | ClusteredBPlusIndex:
        organization = self._definition.organization
        if organization is Organization.HEAP:
            path = self._directory / f"{self.name}{HEAP_SUFFIX}"
            return HeapFile(path, self._serializer.size, self._config)
        key_field = self._definition.primary_key
        if key_field is None:
            raise TableError(f"'{self.name}' necesita clave primaria para su organización")
        if organization is Organization.SEQUENTIAL:
            path = self._directory / f"{self.name}{SEQUENTIAL_SUFFIX}"
            return SequentialFile(path, self._serializer, key_field, self._config)
        path = self._directory / f"{self.name}{CLUSTERED_SUFFIX}"
        return ClusteredBPlusIndex(path, self._serializer, key_field, self._config)

    def _open_index(self, definition: IndexDefinition) -> SecondaryIndex:
        path = self._index_path(definition.name)
        if definition.method is IndexType.HASH:
            return HashSecondaryIndex(path, self._serializer, definition.column, self._config)
        if definition.method is IndexType.BTREE:
            return UnclusteredBPlusIndex(path, self._serializer, definition.column, self._config)
        raise UnsupportedIndexError(
            f"el método {definition.method.value} no sirve como índice secundario"
        )

    def _index_path(self, name: str) -> Path:
        return self._directory / f"{self.name}.{name}{INDEX_SUFFIX}"


def primary_key_index_name(table: str) -> str:
    """Nombre del índice que un heap file mantiene sobre su clave primaria."""
    return f"{PRIMARY_KEY_INDEX_PREFIX}{table}"
