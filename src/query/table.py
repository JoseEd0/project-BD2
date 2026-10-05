"""Una tabla: su organización física más sus índices secundarios.

Es la pieza que decide qué estructura abre cada tabla y la que mantiene los índices
coherentes con los datos: si una fila entra o sale, todos sus índices se enteran.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import Any, Protocol

from config import EngineConfig
from index.bplustree import ClusteredBPlusIndex, UnclusteredBPlusIndex
from index.hash import ExtendibleHashIndex
from index.hash.extendible_hash import DIRECTORY_SUFFIX
from index.keys import Key, ScalarKeyCodec
from index.rtree import SearchStats, SpatialIndex
from query.catalog import IndexDefinition, Organization, TableDefinition
from spatial.geometry import Point, Polygon
from spatial.metrics import Metric
from sql.nodes import IndexType
from storage.heap import HeapFile
from storage.record import Record, RecordSerializer
from storage.record_id import RECORD_ID_SIZE, RecordId
from storage.schema import Schema
from storage.sequential import SequentialFile
from storage.sequential.sequential_file import OVERFLOW_SUFFIX, REBUILD_SUFFIX
from storage.types import FieldType, StorageError

HEAP_SUFFIX = ".heap"
SEQUENTIAL_SUFFIX = ".seq"
CLUSTERED_SUFFIX = ".bpt"
INDEX_SUFFIX = ".idx"
PRIMARY_KEY_INDEX_PREFIX = "pk_"
# Lo que había antes en el lugar de una fila que se inserta: nada, distinto de cualquier valor.
_NO_ROW = object()
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


class DuplicateValueError(TableError):
    """Ya existe una fila con ese valor en una columna declarada `UNIQUE`."""


class UnsupportedIndexError(TableError):
    """La organización de la tabla no admite ese índice."""


class RowChanges(Protocol):
    """Quien quiere enterarse de cada fila que una escritura crea, quita o modifica.

    Recibe las filas tal como están guardadas, y cada aviso llega cuando el cambio ya
    está hecho: es lo que necesita un registro de deshacer.
    """

    def inserted(self, row: Record) -> None: ...

    def deleted(self, row: Record) -> None: ...

    def updated(self, before: Record, after: Record) -> None: ...


@dataclass(frozen=True, slots=True)
class _UniqueColumn:
    """Una columna que no admite valores repetidos: la clave primaria o una `UNIQUE`.

    Attributes:
        position: lugar de la columna en la fila.
        holds: dice si alguna fila de la tabla tiene ya ese valor.
        duplicate: el error con que se rechaza un valor repetido.
    """

    position: int
    holds: Callable[[Key], bool]
    duplicate: Callable[[Key], TableError]


def discard_table_files(directory: Path, name: str) -> None:
    """Borra del disco los archivos de la tabla con ese nombre, y solo esos.

    Filtrar por sufijo importa: un `productos.csv` del usuario en el mismo directorio
    también empieza por `productos.`, y no es de la tabla.
    """
    for path in directory.glob(f"{name}.*"):
        if path.name.endswith(OWNED_SUFFIXES):
            path.unlink(missing_ok=True)


class SecondaryIndex(Protocol):
    """Lo que el motor necesita de cualquier índice secundario."""

    def build(self, rows: Iterable[tuple[bytes, RecordId]]) -> None: ...

    def insert(self, record: bytes, record_id: RecordId) -> None: ...

    def delete(self, record: bytes, record_id: RecordId) -> bool: ...

    def search(self, value: Key) -> list[RecordId]: ...

    def describe(self) -> dict[str, Any]: ...

    def close(self) -> None: ...


class HashSecondaryIndex:
    """Adapta el hash extendible a la interfaz de índice secundario.

    Igual que el B+ no agrupado, deja fuera las filas cuyo campo indexado es NULL.
    """

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

    def build(self, rows: Iterable[tuple[bytes, RecordId]]) -> None:
        for record, record_id in rows:
            self.insert(record, record_id)

    def insert(self, record: bytes, record_id: RecordId) -> None:
        key = self._key_of(record)
        if key is not None:
            self._index.insert(key, record_id.pack())

    def delete(self, record: bytes, record_id: RecordId) -> bool:
        key = self._key_of(record)
        return key is not None and self._index.delete_entry(key, record_id.pack())

    def search(self, value: Key) -> list[RecordId]:
        if value is None:
            return []
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
        self._indexes: dict[str, SecondaryIndex] = {}
        try:
            for index in definition.indexes:
                self._indexes[index.name] = self._open_index(index)
        except Exception:
            self.close()
            raise

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
        self._reject_repeated_values([record])
        self._store(record)

    def insert_all(self, rows: Sequence[Record], changes: RowChanges) -> None:
        """Inserta todas las filas o ninguna: los valores y las claves se comprueban antes
        de escribir la primera.

        Raises:
            DuplicatePrimaryKeyError: si una clave ya existe o se repite entre las filas.
            DuplicateValueError: si lo que se repite es el valor de una columna `UNIQUE`.
            StorageError: si algún valor no corresponde a su columna.
        """
        records = [self._serializer.pack(values) for values in rows]
        self._reject_repeated_values(records)
        for record in records:
            self._store(record)
            changes.inserted(self._serializer.unpack(record))

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
        if isinstance(self._storage, HeapFile):
            raise UnsupportedIndexError(f"'{self.name}' no guarda las filas en orden de clave")
        for record in self._storage.range_search(low, high):
            yield self._serializer.unpack(record)

    def search_index(self, index_name: str, value: Key) -> Iterator[Record]:
        """Filas que el índice indicado asocia a ese valor."""
        return self._rows_at(self._indexes[index_name].search(value))

    def range_index(self, index_name: str, low: Key | None, high: Key | None) -> Iterator[Record]:
        """Filas del índice en un rango de valores. Solo para índices B+.

        Raises:
            UnsupportedIndexError: si el índice es hash, que no guarda orden.
        """
        index = self._indexes[index_name]
        if not isinstance(index, UnclusteredBPlusIndex):
            raise UnsupportedIndexError(f"el índice '{index_name}' no admite consultas por rango")
        return self._rows_at(address for _, address in index.range_search(low, high))

    def spatial_index(self, index_name: str) -> SpatialIndex:
        """Índice R-Tree con ese nombre.

        Raises:
            UnsupportedIndexError: si el índice no es espacial.
        """
        index = self._indexes[index_name]
        if not isinstance(index, SpatialIndex):
            raise UnsupportedIndexError(f"el índice '{index_name}' no es un índice espacial")
        return index

    def rows_within_radius(
        self,
        index_name: str,
        center: Point,
        radius: float,
        metric: Metric,
        stats: SearchStats | None = None,
    ) -> Iterator[Record]:
        """Filas a distancia menor o igual que `radius` del centro, por el R-Tree.

        Salen en orden físico, no de distancia: ver `_rows_in_page_order`.
        """
        found = self.spatial_index(index_name).within_radius(center, radius, metric, stats)
        return self._rows_in_page_order(neighbor.record_id for neighbor in found)

    def rows_by_distance(
        self, index_name: str, center: Point, metric: Metric, stats: SearchStats | None = None
    ) -> Iterator[Record]:
        """Filas de la más cercana a la más lejana. Es perezoso: quien solo quiere las `k`
        primeras deja de pedir y el resto del R-Tree no se llega a abrir.

        Las filas sin ubicación no están en el índice y su distancia es NULL. Salen
        primero, que es donde un `ORDER BY` ascendente pone los NULL, para que la
        consulta responda lo mismo con índice que sin él.
        """
        index = self.spatial_index(index_name)
        column = self._definition.index_on_name(index_name).column
        yield from self._rows_with_null(column, self.row_count - index.entry_count)
        found = index.nearest(center, metric, stats)
        yield from self._rows_at(neighbor.record_id for neighbor in found)

    def rows_within_polygon(
        self, index_name: str, polygon: Polygon, stats: SearchStats | None = None
    ) -> Iterator[Record]:
        """Filas cuyo punto cae dentro del polígono, por el R-Tree, en orden físico."""
        found = self.spatial_index(index_name).within_polygon(polygon, stats)
        return self._rows_in_page_order(found)

    def sample_points(self, column: str, limit: int) -> list[Point]:
        """Hasta `limit` puntos de la columna, tomados a intervalos regulares de la tabla.

        Raises:
            TableError: si la columna no es de tipo POINT.
        """
        field = self.schema.field_of(column)
        if field.type is not FieldType.POINT:
            raise TableError(f"la columna '{field.name}' no guarda puntos")
        position = self.schema.position_of(column)
        stride = max(1, ceil(self.row_count / limit))
        points = [
            self._serializer.unpack_field(record, position)
            for number, (_, record) in enumerate(self._scan_raw())
            if number % stride == 0
        ]
        return [point for point in points if isinstance(point, Point)]

    def delete_where(self, matches: Callable[[Record], bool], changes: RowChanges) -> int:
        """Borra las filas que cumplen el predicado y devuelve cuántas."""
        victims = self._matching(matches)
        for address, record, row in victims:
            self._delete_one(address, record)
            changes.deleted(row)
        return len(victims)

    def update_where(
        self,
        matches: Callable[[Record], bool],
        transform: Callable[[Record], Record],
        changes: RowChanges,
    ) -> int:
        """Reemplaza las filas que cumplen el predicado y devuelve cuántas.

        Cambia todas o ninguna: las filas nuevas se calculan y sus claves se comprueban
        antes de escribir la primera, así que un valor inválido en la fila mil no deja
        cambiadas las novecientas noventa y nueve anteriores.

        Las filas que conservan su clave se reescriben. Las que la cambian se quitan
        todas y después se insertan todas con la clave nueva: así `SET id = id + 1` o un
        intercambio de claves funcionan aunque una fila ocupe la clave que otra deja,
        porque en ningún momento dos filas comparten clave. Por eso se avisan como un
        borrado y una inserción, que es también el orden en que hay que deshacerlas.

        Raises:
            DuplicatePrimaryKeyError: si al terminar habría dos filas con la misma clave.
            DuplicateValueError: si coincidirían en una columna `UNIQUE`.
            StorageError: si algún valor nuevo no corresponde a su columna.
        """
        victims = self._matching(matches)
        replacements = [self._serializer.pack(transform(row)) for _, _, row in victims]
        moving = self._rows_changing_key([record for _, record, _ in victims], replacements)
        for position, (address, record, row) in enumerate(victims):
            if position not in moving:
                self._update_one(address, record, replacements[position])
                changes.updated(row, self._serializer.unpack(replacements[position]))
        for position in moving:
            address, record, row = victims[position]
            self._delete_one(address, record)
            changes.deleted(row)
        for position in moving:
            self._store(replacements[position])
            changes.inserted(self._serializer.unpack(replacements[position]))
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
        heap = self._require_heap()
        index = self._open_index(definition)
        index.build((record, address) for address, record in heap.scan())
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
        """Cierra la tabla y borra del disco los archivos que creó."""
        self.close()
        discard_table_files(self._directory, self.name)

    def _rows_at(self, addresses: Iterable[RecordId]) -> Iterator[Record]:
        """El salto al heap: la fila que vive en cada dirección que devolvió un índice."""
        for record in self._require_heap().read_many(addresses):
            yield self._serializer.unpack(record)

    def _rows_with_null(self, column: str, expected: int) -> Iterator[Record]:
        """Las `expected` filas cuyo valor en la columna es NULL, recorriendo la tabla.

        Quien llama sabe cuántas hay sin leer nada —las que le faltan a su índice—, así
        que una tabla sin NULL no paga ningún recorrido y una con pocos deja de leer en
        cuanto los encuentra.
        """
        if expected <= 0:
            return
        position = self.schema.position_of(column)
        pending = expected
        for _, record in self._scan_raw():
            if self._serializer.unpack_field(record, position) is not None:
                continue
            yield self._serializer.unpack(record)
            pending -= 1
            if pending == 0:
                return

    def _rows_in_page_order(self, addresses: Iterable[RecordId]) -> Iterator[Record]:
        """El salto al heap con las direcciones ordenadas por página.

        Un índice espacial devuelve las filas de una zona, repartidas al azar por el heap:
        ir a buscarlas en ese orden relee la misma página una y otra vez. Ordenadas, cada
        página se lee una sola vez. Es la idea del *bitmap heap scan* de PostgreSQL, y
        solo vale cuando el orden del resultado no importa; el k-NN no puede usarla.
        """
        return self._rows_at(sorted(addresses))

    def _scan_raw(self) -> Iterator[tuple[RecordId | None, bytes]]:
        if isinstance(self._storage, HeapFile):
            yield from self._storage.scan()
        elif isinstance(self._storage, SequentialFile):
            for record in self._storage.scan():
                yield None, record
        else:
            for record in self._storage.scan():
                yield None, record

    def _matching(
        self, matches: Callable[[Record], bool]
    ) -> list[tuple[RecordId | None, bytes, Record]]:
        """Filas que cumplen el predicado, con su dirección y sus bytes.

        Se reúnen antes de tocar nada: modificar la tabla mientras se la recorre haría
        que el recorrido viera sus propios cambios.
        """
        found = []
        for address, record in self._scan_raw():
            row = self._serializer.unpack(record)
            if matches(row):
                found.append((address, record, row))
        return found

    def _store(self, record: bytes) -> None:
        if not isinstance(self._storage, HeapFile):
            self._storage.insert(record)
            return
        record_id = self._storage.insert(record)
        for index in self._indexes.values():
            index.insert(record, record_id)

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

    def _reject_repeated_values(self, records: Sequence[bytes]) -> None:
        """Comprueba que unas filas nuevas no repiten la clave ni un valor `UNIQUE`."""
        for column in self._unique_columns():
            incoming = [
                self._serializer.unpack_field(record, column.position) for record in records
            ]
            self._changing(column, [_NO_ROW] * len(records), incoming)

    def _rows_changing_key(
        self, current: Sequence[bytes], replacements: Sequence[bytes]
    ) -> list[int]:
        """Posiciones de las filas de un UPDATE cuya clave primaria cambia.

        De paso comprueba que el cambio no repite ningún valor que deba ser único.

        Raises:
            DuplicatePrimaryKeyError: si dos filas acabarían con la misma clave.
            DuplicateValueError: si acabarían con el mismo valor en una columna `UNIQUE`.
        """
        moving: list[int] = []
        for column in self._unique_columns():
            before = [self._serializer.unpack_field(record, column.position) for record in current]
            after = [
                self._serializer.unpack_field(record, column.position) for record in replacements
            ]
            changing = self._changing(column, before, after)
            if self._is_primary_key(column):
                moving = changing
        return moving

    @staticmethod
    def _changing(column: _UniqueColumn, before: Sequence[Any], after: Sequence[Key]) -> list[int]:
        """Posiciones cuyo valor en la columna cambia, tras comprobar que no se repite.

        Cuenta el resultado final, no los pasos intermedios: un valor nuevo vale si ninguna
        otra fila lo va a tener al terminar, es decir, si está libre o si lo ocupa una fila
        que la misma sentencia cambia a otro. Un NULL no choca con nada.

        Raises:
            TableError: el error de la columna, si dos filas acabarían con el mismo valor.
        """
        changing = [position for position, value in enumerate(after) if value != before[position]]
        vacated = {before[position] for position in changing}
        claimed: set[Key] = set()
        for position in changing:
            value = after[position]
            if value is None:
                continue
            if value in claimed or (value not in vacated and column.holds(value)):
                raise column.duplicate(value)
            claimed.add(value)
        return changing

    def _unique_columns(self) -> list[_UniqueColumn]:
        columns = []
        if self._definition.primary_key is not None:
            columns.append(
                _UniqueColumn(self._primary_key_position(), self._has_key, self._duplicate_key)
            )
        for definition in self._definition.indexes:
            if definition.unique:
                columns.append(self._unique_index_column(definition))
        return columns

    def _unique_index_column(self, definition: IndexDefinition) -> _UniqueColumn:
        index = self._indexes[definition.name]

        def duplicate(value: Key) -> TableError:
            return DuplicateValueError(
                f"el valor {value!r} ya existe en '{self.name}.{definition.column}', que es UNIQUE"
            )

        return _UniqueColumn(
            self.schema.position_of(definition.column),
            lambda value: bool(index.search(value)),
            duplicate,
        )

    def _is_primary_key(self, column: _UniqueColumn) -> bool:
        key = self._definition.primary_key
        return key is not None and column.position == self.schema.position_of(key)

    def _duplicate_key(self, key: Key) -> TableError:
        return DuplicatePrimaryKeyError(f"la clave primaria {key!r} ya existe en '{self.name}'")

    def _has_key(self, key: Key) -> bool:
        return next(iter(self.search_primary_key(key)), None) is not None

    def _primary_key_of(self, record: bytes) -> Key:
        return self._serializer.unpack_field(record, self._primary_key_position())

    def _primary_key_position(self) -> int:
        if self._definition.primary_key is None:
            raise TableError(f"'{self.name}' no declara clave primaria")
        return self.schema.position_of(self._definition.primary_key)

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
        if definition.method is IndexType.RTREE:
            return SpatialIndex(path, self._serializer, definition.column, self._config)
        raise UnsupportedIndexError(
            f"el método {definition.method.value} no sirve como índice secundario"
        )

    def _index_path(self, name: str) -> Path:
        return self._directory / f"{self.name}.{name}{INDEX_SUFFIX}"


def primary_key_index_name(table: str) -> str:
    """Nombre del índice que un heap file mantiene sobre su clave primaria."""
    return f"{PRIMARY_KEY_INDEX_PREFIX}{table}"
