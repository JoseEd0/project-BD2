"""Una tabla: su organización física más sus índices secundarios.

Es la pieza que decide qué estructura abre cada tabla y la que mantiene los índices
coherentes con los datos: si una fila entra o sale, todos sus índices se enteran.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence, Set
from dataclasses import dataclass, replace
from itertools import tee
from math import ceil
from pathlib import Path
from typing import Any, Protocol

from config import EngineConfig
from index.bplustree import ClusteredBPlusIndex
from index.inverted import TextSearchIndex
from index.rtree import SearchStats, SpatialIndex
from index.secondary import RangeIndex, RebuildableIndex, SecondaryIndex
from index.similarity import SimilarityIndex
from query.catalog import IndexDefinition, Organization, TableDefinition
from query.index_methods import Capability, method_named
from query.row_store import AddressedRecord, HeapStore, KeyedStore, RowStore
from spatial.geometry import Point, Polygon
from spatial.metrics import Metric
from storage.atomic import mark_durably, remove_durably
from storage.heap import HeapFile
from storage.out_of_row import OutOfRowSerializer, out_of_row_positions
from storage.record import Record, RecordSerializer
from storage.record_id import RecordId
from storage.schema import Schema
from storage.sequential import SequentialFile
from storage.types import FieldType, Key, StorageError, Value
from storage.varlen import VariableLengthFile

HEAP_SUFFIX = ".heap"
SEQUENTIAL_SUFFIX = ".seq"
CLUSTERED_SUFFIX = ".bpt"
INDEX_SUFFIX = ".idx"
TEXT_VALUES_SUFFIX = ".text"
# Marca de un índice que una carga dejó de mantener y aún no reconstruyó (ver
# `defer_bulk_indexes`).
STALE_SUFFIX = ".obsoleto"
_STORAGE_SUFFIXES = {
    Organization.HEAP: HEAP_SUFFIX,
    Organization.SEQUENTIAL: SEQUENTIAL_SUFFIX,
    Organization.CLUSTERED_BTREE: CLUSTERED_SUFFIX,
}
PRIMARY_KEY_INDEX_PREFIX = "pk_"
# Lo que había antes en el lugar de una fila que se inserta: nada, distinto de cualquier valor.
_NO_ROW = object()


class TableError(StorageError):
    """Error de operación sobre una tabla."""


class DuplicateError(TableError):
    """Una fila repetiría un valor que no se puede repetir.

    `position` es el lugar de esa fila entre las que se escribían, para que quien carga
    un lote sepa cuál fue.
    """

    def __init__(self, message: str, position: int) -> None:
        super().__init__(message)
        self.position = position


class DuplicatePrimaryKeyError(DuplicateError):
    """Ya existe una fila con esa clave primaria."""


class DuplicateValueError(DuplicateError):
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
        duplicate: el error con que se rechaza un valor repetido por la fila de esa posición.
    """

    position: int
    holds: Callable[[Key], bool]
    duplicate: Callable[[Key, int], TableError]


def table_files(definition: TableDefinition, directory: Path) -> list[Path]:
    """Los archivos de la tabla: los de su organización y los de cada uno de sus índices.

    Se calculan de la definición, no buscando por nombre: un `productos.csv` del usuario en
    el mismo directorio también empieza por `productos.`, y no es de la tabla.
    """
    files = _storage_files(definition, directory)
    for index in definition.indexes:
        method = method_named(index.method)
        path = index_path(directory, definition.name, index.name)
        if method is not None:
            files.extend([*method.files_of(path), stale_marker(path)])
    return files


def discard_table_files(definition: TableDefinition, directory: Path) -> None:
    """Borra del disco los archivos de la tabla, y solo esos."""
    for path in table_files(definition, directory):
        path.unlink(missing_ok=True)


def index_path(directory: Path, table: str, index: str) -> Path:
    return directory / f"{table}.{index}{INDEX_SUFFIX}"


def stale_marker(path: Path) -> Path:
    """La marca de que el índice guardado en `path` no tiene todas las filas de la tabla."""
    return path.with_name(path.name + STALE_SUFFIX)


def _storage_path(definition: TableDefinition, directory: Path) -> Path:
    suffix = _STORAGE_SUFFIXES[definition.organization]
    return directory / f"{definition.name}{suffix}"


def _storage_files(definition: TableDefinition, directory: Path) -> list[Path]:
    path = _storage_path(definition, directory)
    sequential = definition.organization is Organization.SEQUENTIAL
    files = SequentialFile.files_of(path) if sequential else [path]
    if _has_text_values(definition):
        files.append(_text_values_path(definition, directory))
    return files


def _text_values_path(definition: TableDefinition, directory: Path) -> Path:
    return directory / f"{definition.name}{TEXT_VALUES_SUFFIX}"


def _has_text_values(definition: TableDefinition) -> bool:
    return bool(out_of_row_positions(definition.schema))


class Table:
    """Acceso a las filas de una tabla por su organización o por sus índices."""

    def __init__(self, definition: TableDefinition, config: EngineConfig) -> None:
        self._definition = definition
        self._config = config
        self._serializer = RecordSerializer(definition.schema)
        self._directory = config.data_directory
        self._storage = self._open_storage()
        self._indexes: dict[str, SecondaryIndex] = {}
        self._deferred: dict[str, SecondaryIndex] = {}
        self._text_values: VariableLengthFile | None = None
        try:
            self._text_values = self._open_text_values()
            self._rows = OutOfRowSerializer(self._serializer, self._text_values)
            for index in definition.indexes:
                self._indexes[index.name] = self._reopened_index(index)
        except Exception:
            self.close()
            raise

    @property
    def definition(self) -> TableDefinition:
        """La definición con los índices que pueden responder ahora: uno diferido por una
        carga, o que no se pudo volver a abrir, no aparece, así que nadie que planifique con
        ella lo elige."""
        indexes = self._definition.indexes
        if len(self._indexes) == len(indexes):
            return self._definition
        live = tuple(index for index in indexes if index.name in self._indexes)
        return replace(self._definition, indexes=live)

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
        row = self._rows.normalized(values)
        self._reject_repeated_values([row])
        self._store(row)

    def insert_all(self, rows: Sequence[Record], changes: RowChanges) -> None:
        """Inserta las filas. Los valores, las claves y lo que los índices pueden rechazar
        de antemano se comprueban antes de escribir la primera. Cada fila entra en la tabla
        y en todos sus índices o en ninguno; si una falla a mitad, las anteriores ya están
        avisadas en `changes`, y deshacerlas es de quien lleva el registro.

        Raises:
            DuplicatePrimaryKeyError: si una clave ya existe o se repite entre las filas.
            DuplicateValueError: si lo que se repite es el valor de una columna `UNIQUE`.
            StorageError: si algún valor no corresponde a su columna.
        """
        self.insert_normalized([self.normalized(values) for values in rows], changes)

    def normalized(self, values: Sequence[Value]) -> Record:
        """La fila tal como quedaría guardada, sin escribir nada.

        Raises:
            StorageError: si algún valor no corresponde a su columna o falta uno que no
                admite NULL.
        """
        return self._rows.normalized(values)

    def insert_normalized(self, rows: Sequence[Record], changes: RowChanges) -> None:
        """Como `insert_all`, con filas que ya pasaron por `normalized`: quien las valida
        de una en una para saber cuál falla no paga dos veces la conversión.

        Raises:
            DuplicateError: si una clave o un valor `UNIQUE` ya existe o se repite entre
                las filas; su `position` dice cuál.
        """
        self._reject_repeated_values(rows)
        self._check_new_rows(rows)
        for row in rows:
            self._store(row)
            changes.inserted(row)

    def scan(self) -> Iterator[Record]:
        """Todas las filas. El orden depende de la organización."""
        return self._unpacked(self.addressed_scan())

    def addressed_scan(self) -> Iterator[AddressedRecord]:
        """Todas las filas guardadas, con su dirección en un heap file (`None` en las demás
        organizaciones, que localizan una fila por su clave)."""
        return self._storage.scan()

    def addressed_by_primary_key(self, keys: Sequence[Key]) -> Iterator[AddressedRecord]:
        """Filas cuya clave primaria es una de `keys`, por el camino más corto que haya.

        Un heap file no ordena nada, así que sin índice esto es un recorrido completo. Como
        toda tabla con clave primaria recibe un índice sobre ella, el caso normal es que
        aquí se use ese índice: sin esto, cada inserción pagaría un recorrido entero al
        comprobar que la clave no se repite, y cargar N filas costaría O(N²).
        """
        wanted = list(dict.fromkeys(keys))
        index = self._primary_key_index()
        if index is None:
            return self._storage.with_keys(wanted)
        return self.addressed_by_index(index.name, wanted)

    def addressed_by_primary_key_range(
        self, low: Key | None, high: Key | None
    ) -> Iterator[AddressedRecord]:
        """Filas con la clave primaria en el rango, en orden. Solo si la organización ordena.

        Raises:
            UnorderedStoreError: si la tabla es un heap file, que no guarda orden.
        """
        return self._storage.in_key_range(low, high)

    def addressed_by_index(
        self, index_name: str, values: Sequence[Key]
    ) -> Iterator[AddressedRecord]:
        """Filas que el índice indicado asocia a alguno de esos valores."""
        index = self._indexes[index_name]
        return self.addressed_at(
            address for value in dict.fromkeys(values) for address in index.search(value)
        )

    def addressed_by_index_range(
        self, index_name: str, low: Key | None, high: Key | None
    ) -> Iterator[AddressedRecord]:
        """Filas del índice en un rango de valores. Solo para índices B+.

        Raises:
            UnsupportedIndexError: si el índice es hash, que no guarda orden.
        """
        index = self._indexes[index_name]
        if not isinstance(index, RangeIndex):
            raise UnsupportedIndexError(f"el índice '{index_name}' no admite consultas por rango")
        return self.addressed_at(address for _, address in index.range_search(low, high))

    def spatial_index(self, index_name: str) -> SpatialIndex:
        """Índice R-Tree con ese nombre.

        Raises:
            UnsupportedIndexError: si el índice no es espacial.
        """
        index = self._indexes[index_name]
        if not isinstance(index, SpatialIndex):
            raise UnsupportedIndexError(f"el índice '{index_name}' no es un índice espacial")
        return index

    def text_search_index(self, index_name: str) -> TextSearchIndex:
        """Índice invertido con ese nombre.

        Raises:
            UnsupportedIndexError: si el índice no es de texto.
        """
        index = self._indexes[index_name]
        if not isinstance(index, TextSearchIndex):
            raise UnsupportedIndexError(f"el índice '{index_name}' no es un índice de texto")
        return index

    def similarity_index(self, index_name: str) -> SimilarityIndex:
        """Índice HNSW o IVF con ese nombre.

        Raises:
            UnsupportedIndexError: si el índice no es de similitud.
        """
        index = self._indexes[index_name]
        if not isinstance(index, SimilarityIndex):
            raise UnsupportedIndexError(f"el índice '{index_name}' no es un índice de similitud")
        return index

    def addressed_within_radius(
        self,
        index_name: str,
        center: Point,
        radius: float,
        metric: Metric,
        stats: SearchStats | None = None,
    ) -> Iterator[AddressedRecord]:
        """Filas a distancia menor o igual que `radius` del centro, por el R-Tree.

        Salen en orden físico, no de distancia: ver `_addressed_in_page_order`.
        """
        found = self.spatial_index(index_name).within_radius(center, radius, metric, stats)
        return self._addressed_in_page_order(neighbor.record_id for neighbor in found)

    def addressed_by_distance(
        self, index_name: str, center: Point, metric: Metric, stats: SearchStats | None = None
    ) -> Iterator[AddressedRecord]:
        """Filas de la más cercana a la más lejana. Es perezoso: quien solo quiere las `k`
        primeras deja de pedir y el resto del R-Tree no se llega a abrir.

        Las filas sin ubicación no están en el índice y su distancia es NULL. Salen
        primero, que es donde un `ORDER BY` ascendente pone los NULL, para que la
        consulta responda lo mismo con índice que sin él.
        """
        index = self.spatial_index(index_name)
        column = self._definition.index_on_name(index_name).column
        yield from self._with_null(column, self.row_count - index.entry_count)
        found = index.nearest(center, metric, stats)
        yield from self.addressed_at(neighbor.record_id for neighbor in found)

    def addressed_within_polygon(
        self, index_name: str, polygon: Polygon, stats: SearchStats | None = None
    ) -> Iterator[AddressedRecord]:
        """Filas cuyo punto cae dentro del polígono, por el R-Tree, en orden físico."""
        found = self.spatial_index(index_name).within_polygon(polygon, stats)
        return self._addressed_in_page_order(found)

    def unpack(self, record: bytes) -> Record:
        """La fila lógica de un registro guardado, con cada TEXT leído de su archivo."""
        return self._rows.unpack(record)

    def unpack_columns(self, record: bytes, positions: Iterable[int]) -> Record:
        """La fila de un registro con solo las columnas de `positions` leídas; el resto,
        NULL. Es lo que necesita un filtro que lee pocas columnas de una fila con un
        `VECTOR` de cientos de componentes."""
        values: list[Value] = [None] * len(self.schema)
        for position in positions:
            values[position] = self._rows.unpack_field(record, position)
        return tuple(values)

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
            for number, (_, record) in enumerate(self.addressed_scan())
            if number % stride == 0
        ]
        return [point for point in points if isinstance(point, Point)]

    def delete_where(
        self,
        candidates: Iterable[AddressedRecord],
        matches: Callable[[Record], bool],
        changes: RowChanges,
    ) -> int:
        """Borra las filas que cumplen el predicado y devuelve cuántas.

        `candidates` son las filas donde buscarlas: toda la tabla, o lo que haya devuelto el
        camino de acceso que eligió el planificador para el WHERE.
        """
        victims = self._matching(candidates, matches)
        for address, record, row in victims:
            self._delete_one(address, record)
            changes.deleted(row)
        return len(victims)

    def update_where(
        self,
        candidates: Iterable[AddressedRecord],
        matches: Callable[[Record], bool],
        transform: Callable[[Record], Record],
        changes: RowChanges,
    ) -> int:
        """Reemplaza las filas que cumplen el predicado y devuelve cuántas.

        Las filas nuevas se calculan, y sus claves y lo que los índices pueden rechazar de
        antemano se comprueban, antes de escribir la primera: un valor inválido en la fila
        mil no deja cambiadas las novecientas noventa y nueve anteriores. Si aun así un
        índice rechaza una fila a mitad, esa fila queda como estaba y las anteriores ya
        están avisadas en `changes`, como en `insert_all`.

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
        victims = self._matching(candidates, matches)
        replacements = [self._rows.normalized(transform(row)) for _, _, row in victims]
        moving = self._rows_changing_key([row for _, _, row in victims], replacements)
        self._check_replacements([row for _, _, row in victims], replacements, set(moving))
        for position, (address, record, row) in enumerate(victims):
            if position not in moving:
                self._update_one(address, record, row, replacements[position])
                changes.updated(row, replacements[position])
        for position in moving:
            address, record, row = victims[position]
            self._delete_one(address, record)
            changes.deleted(row)
        for position in moving:
            self._store(replacements[position])
            changes.inserted(replacements[position])
        return len(victims)

    def _update_one(
        self, address: RecordId | None, old: bytes, before: Record, after: Record
    ) -> None:
        """Reescribe la fila en la tabla y en sus índices, o no la cambia: si un índice
        rechaza el valor nuevo, la fila vuelve a `before` en la tabla y en los índices.

        Solo se tocan los índices cuya columna cambia, y quitan la fila antes de reescribir
        sus TEXT: uno sobre un TEXT lee el texto viejo del archivo de valores para saber qué
        quitar, y `repack` puede reemplazarlo en su sitio."""
        stale = [index for index in self._indexes.values() if index.changes(before, after)]
        _unindex_row(stale, old, address)
        try:
            new = self._rewrite(address, old, before, after)
        except Exception:
            _index_row(stale, old, address)
            raise
        try:
            _index_row(stale, new, address)
        except Exception:
            _index_row(stale, self._rewrite(address, new, after, before), address)
            raise

    def _rewrite(
        self, address: RecordId | None, old: bytes, before: Record, after: Record
    ) -> bytes:
        """Cambia en la tabla la fila guardada en `old` de `before` a `after` y devuelve su
        registro nuevo, sin tocar los índices."""
        new = self._rows.repack(old, before, after)
        self._storage.update(address, old, new)
        return new

    def delete_first(self, target: Record) -> bool:
        """Borra una sola fila igual a `target`. Lo usa el deshacer de una inserción."""
        for address, record in self._addressed_like(target):
            if self._rows.unpack(record) == target:
                self._delete_one(address, record)
                return True
        return False

    def update_first(self, target: Record, replacement: Record) -> bool:
        """Reemplaza una sola fila igual a `target`. Lo usa el deshacer de un UPDATE."""
        for address, record in self._addressed_like(target):
            if self._rows.unpack(record) == target:
                self._update_one(address, record, target, self._rows.normalized(replacement))
                return True
        return False

    def build_index(self, definition: IndexDefinition) -> SecondaryIndex:
        """Crea el índice y lo llena con las filas que ya existen.

        Raises:
            UnsupportedIndexError: si la tabla no es un heap file.
        """
        self._require_addresses()
        index = self._open_index(definition)
        index.build(self._indexed_rows())
        self._indexes[definition.name] = index
        return index

    def defer_bulk_indexes(self) -> tuple[str, ...]:
        """Deja de mantener fila a fila los índices que se construyen mejor de una vez —el
        R-Tree con STR, el invertido con SPIMI, HNSW e IVF con todos sus vectores— y
        devuelve sus nombres. Es para cargar una tabla vacía: `build_deferred_indexes` los
        construye al final con todas las filas.

        Los `UNIQUE`, el de la clave primaria incluido, siguen al día: son los que
        comprueban que una fila nueva no repite un valor. Mientras haya índices diferidos
        `definition` no los muestra, así que ninguna consulta los usa; además, quien los
        difiere tiene la tabla bloqueada en exclusiva.

        Antes de dejar de mantener un índice se deja en el disco la marca de que está
        viejo: si el proceso termina antes de `build_deferred_indexes`, al abrir la tabla
        el índice se reconstruye con sus filas en vez de responder con filas de menos. Con
        la marca puesta, lo que el índice guardaba ya no sirve y se descarta: una tabla
        vaciada con `DELETE` deja en él lápidas, y su construcción de una vez empieza desde
        un índice sin nada.
        """
        for definition in self._definition.indexes:
            if self._defers(definition):
                mark_durably(stale_marker(self._index_path(definition.name)))
                self._indexes.pop(definition.name).close()
                self._deferred[definition.name] = self._emptied_index(definition)
        return tuple(self._deferred)

    def build_deferred_indexes(self) -> None:
        """Construye con las filas que hay los índices que `defer_bulk_indexes` dejó de
        mantener, y vuelve a mantenerlos. Un índice que no se pudo construir sigue diferido
        y el error se propaga: devolverlo a medias respondería consultas con filas de menos.
        """
        for name in list(self._deferred):
            self._build_from_rows(self._deferred[name], name)
            self._indexes[name] = self._deferred.pop(name)

    def drop_index(self, name: str) -> None:
        index = self._indexes.pop(name)
        index.close()
        self.discard_index_files(self._definition.index_on_name(name))
        stale_marker(self._index_path(name)).unlink(missing_ok=True)

    def discard_index_files(self, definition: IndexDefinition) -> None:
        """Borra los archivos del índice, sean los que sean según su método."""
        method = method_named(definition.method)
        if method is None:
            return
        for path in method.files_of(self._index_path(definition.name)):
            path.unlink(missing_ok=True)

    def flush(self) -> None:
        """Escribe en disco las páginas modificadas de la tabla y de todos sus índices."""
        self._storage.flush()
        if self._text_values is not None:
            self._text_values.flush()
        for index in self._indexes.values():
            index.flush()

    def close(self) -> None:
        self._storage.close()
        if self._text_values is not None:
            self._text_values.close()
        for index in [*self._indexes.values(), *self._deferred.values()]:
            index.close()

    def describe_structure(self) -> dict[str, Any]:
        """Organización física y forma real de cada índice, para inspeccionarlas."""
        return {
            "table": self.name,
            "organization": self.organization.value,
            "rows": self.row_count,
            "storage": self._storage.describe(),
            "text_values": None if self._text_values is None else self._text_values.describe(),
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

    def remove_files(self) -> None:
        """Cierra la tabla y borra del disco los archivos que creó."""
        self.close()
        discard_table_files(self._definition, self._directory)

    def _unpacked(self, rows: Iterable[AddressedRecord]) -> Iterator[Record]:
        for _, record in rows:
            yield self._rows.unpack(record)

    def addressed_at(self, addresses: Iterable[RecordId]) -> Iterator[AddressedRecord]:
        """El salto al heap: la fila que vive en cada dirección que devolvió un índice, en
        el orden en que llegan.

        Pide las direcciones de una en una: quien deja de pedir filas —un `LIMIT` sobre un
        k-NN, sobre un rango o sobre una búsqueda de texto— deja también de recorrer el
        índice.

        Raises:
            UnorderedStoreError: si la tabla no es un heap file.
        """
        requested, located = tee(addresses)
        return zip(requested, self._storage.at(located), strict=True)

    def _with_null(self, column: str, expected: int) -> Iterator[AddressedRecord]:
        """Las `expected` filas cuyo valor en la columna es NULL, recorriendo la tabla.

        Quien llama sabe cuántas hay sin leer nada —las que le faltan a su índice—, así
        que una tabla sin NULL no paga ningún recorrido y una con pocos deja de leer en
        cuanto los encuentra.
        """
        if expected <= 0:
            return
        position = self.schema.position_of(column)
        pending = expected
        for address, record in self.addressed_scan():
            if self._serializer.unpack_field(record, position) is not None:
                continue
            yield address, record
            pending -= 1
            if pending == 0:
                return

    def _addressed_in_page_order(
        self, addresses: Iterable[RecordId]
    ) -> Iterator[AddressedRecord]:
        """El salto al heap con las direcciones ordenadas por página.

        Un índice espacial devuelve las filas de una zona, repartidas al azar por el heap:
        ir a buscarlas en ese orden relee la misma página una y otra vez. Ordenadas, cada
        página se lee una sola vez. Es la idea del *bitmap heap scan* de PostgreSQL, y
        solo vale cuando el orden del resultado no importa; el k-NN no puede usarla.
        """
        return self.addressed_at(sorted(addresses))

    def _addressed_like(self, target: Record) -> Iterator[AddressedRecord]:
        """Dónde buscar una fila igual a `target`: por su clave primaria si la tiene, que es
        una búsqueda por índice, y recorriendo la tabla si no. Así deshacer una transacción
        de `m` cambios cuesta `m` búsquedas, no `m` recorridos."""
        key = self._definition.primary_key
        if key is None:
            return self.addressed_scan()
        return self.addressed_by_primary_key([target[self.schema.position_of(key)]])

    def _matching(
        self, candidates: Iterable[AddressedRecord], matches: Callable[[Record], bool]
    ) -> list[tuple[RecordId | None, bytes, Record]]:
        """Filas que cumplen el predicado, con su dirección y sus bytes.

        Se reúnen antes de tocar nada: modificar la tabla mientras se la recorre haría
        que el recorrido viera sus propios cambios.
        """
        found = []
        for address, record in candidates:
            row = self._rows.unpack(record)
            if matches(row):
                found.append((address, record, row))
        return found

    def _store(self, row: Record) -> None:
        """Guarda la fila en la tabla y en todos sus índices, o en ninguno: si un índice
        la rechaza, sale de los que ya la tenían y de la tabla antes de propagar el error.
        Así cada aviso al registro de deshacer es el de una fila completa."""
        record = self._rows.pack(row)
        try:
            address = self._storage.insert(record)
        except Exception:
            self._rows.release(record)
            raise
        try:
            _index_row(self._indexes.values(), record, address)
        except Exception:
            self._storage.delete(address, record)
            self._rows.release(record)
            raise

    def _delete_one(self, address: RecordId | None, record: bytes) -> None:
        """Quita la fila de sus índices y de la tabla, o de ninguno, y libera en el acto sus
        TEXT: si una transacción la devuelve, el deshacer la inserta otra vez, con sus
        textos, desde la fila lógica que guardó."""
        _unindex_row(self._indexes.values(), record, address)
        try:
            self._storage.delete(address, record)
        except Exception:
            _index_row(self._indexes.values(), record, address)
            raise
        self._rows.release(record)

    def _primary_key_index(self) -> IndexDefinition | None:
        """Un índice de la clave que sepa buscar por igualdad, si lo hay."""
        if self._definition.primary_key is None:
            return None
        key = self._definition.primary_key
        return index_with_capability(self._definition, key, Capability.EQUALITY)

    def _reject_repeated_values(self, rows: Sequence[Record]) -> None:
        """Comprueba que unas filas nuevas no repiten la clave ni un valor `UNIQUE`."""
        for column in self._unique_columns():
            incoming = [row[column.position] for row in rows]
            self._changing(column, [_NO_ROW] * len(rows), incoming)

    def _check_new_rows(self, rows: Sequence[Record]) -> None:
        """Lo que algún índice rechazaría de unas filas nuevas, antes de escribir ninguna."""
        for row in rows:
            for index in self._indexes.values():
                index.check(row)

    def _check_replacements(
        self, current: Sequence[Record], replacements: Sequence[Record], moving: Set[int]
    ) -> None:
        """Lo mismo para las filas que reescribe un UPDATE: cada índice mira solo las que le
        llegan, las que cambian su columna o su clave primaria."""
        for position, (before, after) in enumerate(zip(current, replacements, strict=True)):
            for index in self._indexes.values():
                if position in moving or index.changes(before, after):
                    index.check(after)

    def _rows_changing_key(
        self, current: Sequence[Record], replacements: Sequence[Record]
    ) -> list[int]:
        """Posiciones de las filas de un UPDATE cuya clave primaria cambia.

        De paso comprueba que el cambio no repite ningún valor que deba ser único.

        Raises:
            DuplicatePrimaryKeyError: si dos filas acabarían con la misma clave.
            DuplicateValueError: si acabarían con el mismo valor en una columna `UNIQUE`.
        """
        moving: list[int] = []
        for column in self._unique_columns():
            before = [row[column.position] for row in current]
            after = [row[column.position] for row in replacements]
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
                raise column.duplicate(value, position)
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

        def duplicate(value: Key, position: int) -> TableError:
            return DuplicateValueError(
                f"el valor {value!r} ya existe en '{self.name}.{definition.column}', que es "
                "UNIQUE",
                position,
            )

        return _UniqueColumn(
            self.schema.position_of(definition.column),
            lambda value: bool(index.search(value)),
            duplicate,
        )

    def _is_primary_key(self, column: _UniqueColumn) -> bool:
        key = self._definition.primary_key
        return key is not None and column.position == self.schema.position_of(key)

    def _duplicate_key(self, key: Key, position: int) -> TableError:
        return DuplicatePrimaryKeyError(
            f"la clave primaria {key!r} ya existe en '{self.name}'", position
        )

    def _has_key(self, key: Key) -> bool:
        return next(iter(self.addressed_by_primary_key([key])), None) is not None

    def _primary_key_of(self, record: bytes) -> Key:
        return self._serializer.unpack_field(record, self._primary_key_position())

    def _primary_key_position(self) -> int:
        if self._definition.primary_key is None:
            raise TableError(f"'{self.name}' no declara clave primaria")
        return self.schema.position_of(self._definition.primary_key)

    def _require_addresses(self) -> None:
        if not self._storage.keeps_addresses:
            raise UnsupportedIndexError(
                f"'{self.name}' no es un heap file: sus filas no tienen dirección estable"
            )

    def _open_storage(self) -> RowStore:
        organization = self._definition.organization
        path = _storage_path(self._definition, self._directory)
        key_field = self._definition.primary_key
        key_of = None if key_field is None else self._primary_key_of
        if organization is Organization.HEAP:
            return HeapStore(HeapFile(path, self._serializer.size, self._config), key_of)
        if key_field is None or key_of is None:
            raise TableError(f"'{self.name}' necesita clave primaria para su organización")
        if organization is Organization.SEQUENTIAL:
            sequential = SequentialFile(path, self._serializer, key_field, self._config)
            return KeyedStore(sequential, key_of)
        clustered = ClusteredBPlusIndex(path, self._serializer, key_field, self._config)
        return KeyedStore(clustered, key_of)

    def _reopened_index(self, definition: IndexDefinition) -> SecondaryIndex:
        """Un índice de la tabla al abrirla; el que perdió su archivo o quedó marcado como
        viejo por una carga que no terminó, reconstruido con las filas de la tabla."""
        if stale_marker(self._index_path(definition.name)).exists():
            return self._rebuilt_index(definition)
        index = self._open_index(definition)
        if isinstance(index, RebuildableIndex) and index.needs_rows:
            index.build(self._indexed_rows())
        return index

    def _rebuilt_index(self, definition: IndexDefinition) -> SecondaryIndex:
        """El índice desde cero. La marca se borra solo después de escribirlo: un corte a
        mitad de la reconstrucción la deja, y la próxima apertura vuelve a empezar."""
        index = self._emptied_index(definition)
        try:
            self._build_from_rows(index, definition.name)
        except Exception:
            index.close()
            raise
        return index

    def _emptied_index(self, definition: IndexDefinition) -> SecondaryIndex:
        """El índice abierto sin nada, en lugar de los archivos que tuviera."""
        self.discard_index_files(definition)
        return self._open_index(definition)

    def _build_from_rows(self, index: SecondaryIndex, name: str) -> None:
        """Llena el índice vacío con las filas de la tabla, lo escribe en el disco y solo
        entonces quita su marca de viejo."""
        index.build(self._indexed_rows())
        index.flush()
        remove_durably(stale_marker(self._index_path(name)))

    def _defers(self, definition: IndexDefinition) -> bool:
        method = method_named(definition.method)
        if definition.unique or method is None:
            return False
        return method.builds_in_bulk(self.schema.field_of(definition.column))

    def _indexed_rows(self) -> Iterator[tuple[bytes, RecordId]]:
        """Las filas guardadas con la dirección que recibe un índice secundario."""
        return ((record, _located(address)) for address, record in self._storage.scan())

    def _open_index(self, definition: IndexDefinition) -> SecondaryIndex:
        method = method_named(definition.method)
        if method is None:
            raise UnsupportedIndexError(
                f"el método {definition.method.value} no sirve como índice secundario"
            )
        path = self._index_path(definition.name)
        return method.opener(
            path,
            self._rows,
            definition.column,
            self._config,
            definition.options,
            self._indexed_rows,
        )

    def _open_text_values(self) -> VariableLengthFile | None:
        """El archivo donde viven los valores de las columnas TEXT, si la tabla tiene alguna."""
        if not _has_text_values(self._definition):
            return None
        path = _text_values_path(self._definition, self._directory)
        return VariableLengthFile(path, self._config)

    def _index_path(self, name: str) -> Path:
        return index_path(self._directory, self.name, name)


def _located(address: RecordId | None) -> RecordId:
    """La dirección de una fila que un índice secundario va a guardar o a quitar. Solo hay
    índices secundarios sobre heap files, y en un heap toda fila tiene dirección."""
    if address is None:
        raise TableError("falta la dirección de una fila indexada")
    return address


def _index_row(indexes: Iterable[SecondaryIndex], record: bytes, address: RecordId | None) -> None:
    """Añade la fila a todos los índices o a ninguno. Cuenta con que un índice cuya
    inserción falla queda como estaba."""
    added: list[SecondaryIndex] = []
    try:
        for index in indexes:
            index.insert(record, _located(address))
            added.append(index)
    except Exception:
        for index in reversed(added):
            index.delete(record, _located(address))
        raise


def _unindex_row(
    indexes: Iterable[SecondaryIndex], record: bytes, address: RecordId | None
) -> None:
    """Quita la fila de todos los índices o de ninguno."""
    removed: list[SecondaryIndex] = []
    try:
        for index in indexes:
            if index.delete(record, _located(address)):
                removed.append(index)
    except Exception:
        for index in reversed(removed):
            index.insert(record, _located(address))
        raise


def index_with_capability(
    definition: TableDefinition, column: str, capability: Capability
) -> IndexDefinition | None:
    """El índice de la columna que mejor responde esa clase de condición.

    Si hay varios, gana el más especializado —el de menos capacidades—: para una igualdad,
    un hash antes que un B+, que además ordena. Entre dos iguales, el que se creó antes.
    """
    candidates = []
    for index in definition.indexes_on(column):
        method = method_named(index.method)
        if method is not None and method.supports(capability):
            candidates.append((len(method.capabilities), index))
    if not candidates:
        return None
    return min(candidates, key=lambda candidate: candidate[0])[1]


def primary_key_index_name(table: str) -> str:
    """Nombre del índice que un heap file mantiene sobre su clave primaria."""
    return f"{PRIMARY_KEY_INDEX_PREFIX}{table}"
