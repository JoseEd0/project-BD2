"""Catálogo: qué tablas existen, con qué columnas y con qué índices.

Es la única pieza que traduce los tipos del dialecto SQL a los tipos del almacenamiento.
Se guarda como JSON junto a los datos, para que el motor sepa al arrancar qué hay en disco.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum, unique
from pathlib import Path
from typing import Any

from config import EngineConfig
from sql.nodes import ColumnDefinition, DataTypeKind, IndexType
from storage.schema import Field, Schema
from storage.types import FieldType

CATALOG_FILENAME = "catalog.json"

SQL_TO_STORAGE_TYPE: dict[DataTypeKind, FieldType] = {
    DataTypeKind.INT: FieldType.INT,
    DataTypeKind.BIGINT: FieldType.INT,
    DataTypeKind.FLOAT: FieldType.FLOAT,
    DataTypeKind.DOUBLE: FieldType.FLOAT,
    DataTypeKind.BOOLEAN: FieldType.BOOL,
    DataTypeKind.CHAR: FieldType.STRING,
    DataTypeKind.VARCHAR: FieldType.STRING,
    DataTypeKind.TEXT: FieldType.STRING,
    DataTypeKind.DATE: FieldType.DATE,
    DataTypeKind.POINT: FieldType.POINT,
    DataTypeKind.VECTOR: FieldType.VECTOR,
    DataTypeKind.BLOB: FieldType.BYTES,
}

CLUSTERING_METHODS = frozenset({IndexType.SEQUENTIAL, IndexType.BTREE})
SECONDARY_METHODS = frozenset({IndexType.BTREE, IndexType.HASH})


class CatalogError(Exception):
    """Error de definición o de consulta del catálogo."""


class UnknownTableError(CatalogError):
    """La tabla no está registrada."""


class DuplicateTableError(CatalogError):
    """Ya existe una tabla con ese nombre."""


class UnknownIndexError(CatalogError):
    """El índice no está registrado."""


class DuplicateIndexError(CatalogError):
    """Ya existe un índice con ese nombre o sobre esa columna."""


@unique
class Organization(Enum):
    """Cómo se guardan físicamente las filas de una tabla."""

    HEAP = "heap"
    SEQUENTIAL = "sequential"
    CLUSTERED_BTREE = "clustered_btree"


ORGANIZATION_BY_METHOD: dict[IndexType, Organization] = {
    IndexType.SEQUENTIAL: Organization.SEQUENTIAL,
    IndexType.BTREE: Organization.CLUSTERED_BTREE,
}


@dataclass(frozen=True, slots=True)
class IndexDefinition:
    """Índice secundario sobre una columna."""

    name: str
    column: str
    method: IndexType

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "column": self.column, "method": self.method.value}

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> IndexDefinition:
        return cls(name=raw["name"], column=raw["column"], method=IndexType(raw["method"]))


@dataclass(frozen=True, slots=True)
class TableDefinition:
    """Todo lo que el motor necesita saber de una tabla para abrirla."""

    name: str
    schema: Schema
    organization: Organization
    primary_key: str | None = None
    indexes: tuple[IndexDefinition, ...] = ()

    def index_on_name(self, name: str) -> IndexDefinition:
        """Índice con ese nombre.

        Raises:
            UnknownIndexError: si la tabla no lo tiene.
        """
        for index in self.indexes:
            if index.name.lower() == name.lower():
                return index
        raise UnknownIndexError(f"la tabla '{self.name}' no tiene el índice '{name}'")

    def index_on(self, column: str) -> IndexDefinition | None:
        for index in self.indexes:
            if index.column.lower() == column.lower():
                return index
        return None

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "organization": self.organization.value,
            "primary_key": self.primary_key,
            "columns": [
                {
                    "name": item.name,
                    "type": item.type.value,
                    "length": item.length,
                    "nullable": item.nullable,
                }
                for item in self.schema
            ],
            "indexes": [index.to_json() for index in self.indexes],
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> TableDefinition:
        columns = [
            Field(
                name=str(item["name"]),
                type=FieldType(item["type"]),
                length=None if item["length"] is None else int(item["length"]),
                nullable=bool(item["nullable"]),
            )
            for item in raw["columns"]
        ]
        return cls(
            name=str(raw["name"]),
            schema=Schema(columns),
            organization=Organization(str(raw["organization"])),
            primary_key=None if raw["primary_key"] is None else str(raw["primary_key"]),
            indexes=tuple(IndexDefinition.from_json(item) for item in raw["indexes"]),
        )


@dataclass
class Catalog:
    """Registro persistente de las tablas del motor."""

    config: EngineConfig
    _tables: dict[str, TableDefinition] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._load()

    @property
    def path(self) -> Path:
        return self.config.data_directory / CATALOG_FILENAME

    def table_names(self) -> list[str]:
        return sorted(definition.name for definition in self._tables.values())

    def has_table(self, name: str) -> bool:
        return name.lower() in self._tables

    def table(self, name: str) -> TableDefinition:
        """Definición de la tabla.

        Raises:
            UnknownTableError: si no está registrada.
        """
        try:
            return self._tables[name.lower()]
        except KeyError:
            raise UnknownTableError(f"la tabla '{name}' no existe") from None

    def create_table(self, definition: TableDefinition) -> None:
        """Registra una tabla nueva.

        Raises:
            DuplicateTableError: si el nombre ya está en uso.
        """
        if self.has_table(definition.name):
            raise DuplicateTableError(f"la tabla '{definition.name}' ya existe")
        self._tables[definition.name.lower()] = definition
        self._save()

    def drop_table(self, name: str) -> TableDefinition:
        definition = self.table(name)
        del self._tables[name.lower()]
        self._save()
        return definition

    def add_index(self, table: str, index: IndexDefinition) -> None:
        """Registra un índice secundario.

        Raises:
            DuplicateIndexError: si el nombre o la columna ya tienen índice.
            UnknownTableError: si la tabla no existe.
        """
        definition = self.table(table)
        if any(existing.name.lower() == index.name.lower() for existing in definition.indexes):
            raise DuplicateIndexError(f"el índice '{index.name}' ya existe")
        if definition.index_on(index.column) is not None:
            raise DuplicateIndexError(f"la columna '{index.column}' ya tiene un índice")
        self._replace(definition, indexes=(*definition.indexes, index))

    def drop_index(self, table: str, name: str) -> IndexDefinition:
        definition = self.table(table)
        remaining = tuple(item for item in definition.indexes if item.name.lower() != name.lower())
        if len(remaining) == len(definition.indexes):
            raise UnknownIndexError(f"el índice '{name}' no existe en '{table}'")
        removed = next(item for item in definition.indexes if item.name.lower() == name.lower())
        self._replace(definition, indexes=remaining)
        return removed

    def find_index(self, name: str) -> tuple[TableDefinition, IndexDefinition]:
        """Busca un índice por su nombre en todas las tablas.

        Raises:
            UnknownIndexError: si ninguna tabla lo tiene.
        """
        for definition in self._tables.values():
            for index in definition.indexes:
                if index.name.lower() == name.lower():
                    return definition, index
        raise UnknownIndexError(f"el índice '{name}' no existe")

    def _replace(self, definition: TableDefinition, indexes: tuple[IndexDefinition, ...]) -> None:
        self._tables[definition.name.lower()] = TableDefinition(
            name=definition.name,
            schema=definition.schema,
            organization=definition.organization,
            primary_key=definition.primary_key,
            indexes=indexes,
        )
        self._save()

    def _load(self) -> None:
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self._tables = {
            str(item["name"]).lower(): TableDefinition.from_json(item) for item in raw["tables"]
        }

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"tables": [definition.to_json() for definition in self._tables.values()]}
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def field_from_column(column: ColumnDefinition, config: EngineConfig) -> Field:
    """Traduce una columna del `CREATE TABLE` a un campo del almacenamiento.

    Raises:
        CatalogError: si el tipo no tiene equivalente en el almacenamiento.
    """
    kind = column.data_type.kind
    if kind not in SQL_TO_STORAGE_TYPE:
        raise CatalogError(f"el tipo {kind.value} no se puede almacenar todavía")
    return Field(
        name=column.name,
        type=SQL_TO_STORAGE_TYPE[kind],
        length=_length_of(column, config),
        nullable=column.nullable and not column.primary_key,
    )


def _length_of(column: ColumnDefinition, config: EngineConfig) -> int | None:
    kind = column.data_type.kind
    if kind is DataTypeKind.TEXT:
        return config.text_length
    if kind is DataTypeKind.BLOB:
        return config.blob_length
    return column.data_type.size
