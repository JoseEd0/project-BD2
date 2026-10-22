"""Estado compartido del servidor: un motor, un gestor de bloqueos y las sesiones."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Annotated

from fastapi import Depends, Request

from api.schemas import ColumnInfo, TableInfo, TablePoints
from config import EngineConfig
from query.catalog import CatalogError, TableDefinition
from query.engine import Engine
from storage.schema import Field
from storage.types import FieldType
from txn import LockManager, Session, TransactionManager


class Service:
    """Lo que todas las rutas comparten durante la vida del servidor."""

    def __init__(self, config: EngineConfig) -> None:
        self.config = config
        self.engine = Engine(config)
        self.locks = LockManager(config)
        self.transactions = TransactionManager()
        self._sessions: dict[str, Session] = {}

    def session(self, identifier: str | None) -> Session:
        """Sesión del cliente; una efímera si no envió identificador."""
        if identifier is None:
            return Session(self.engine, self.locks, self.transactions)
        if identifier not in self._sessions:
            self._sessions[identifier] = Session(self.engine, self.locks, self.transactions)
        return self._sessions[identifier]

    def close_session(self, identifier: str) -> bool:
        session = self._sessions.pop(identifier, None)
        if session is None:
            return False
        session.close()
        return True

    def tables(self) -> Iterator[TableInfo]:
        for name in self.engine.table_names():
            yield self._describe(name)

    def points(self, name: str, column: str | None, limit: int | None) -> TablePoints:
        """Puntos de la tabla, como mucho `map_points` de la configuración.

        Raises:
            CatalogError: si la tabla no existe o no tiene ninguna columna POINT.
        """
        table = self.engine.table(name)
        chosen = column or _first_point_column(table.definition.schema.fields)
        if chosen is None:
            raise CatalogError(f"la tabla '{table.name}' no tiene ninguna columna POINT")
        allowed = min(limit or self.config.map_points, self.config.map_points)
        sample = table.sample_points(chosen, allowed)
        return TablePoints(
            table=table.name,
            column=table.schema.field_of(chosen).name,
            total=table.row_count,
            points=[(point.lat, point.lon) for point in sample],
        )

    def close(self) -> None:
        for session in self._sessions.values():
            session.close()
        self._sessions.clear()
        self.engine.close()

    def _describe(self, name: str) -> TableInfo:
        table = self.engine.table(name)
        definition = table.definition
        return TableInfo(
            name=definition.name,
            organization=definition.organization.value,
            primary_key=definition.primary_key,
            row_count=table.row_count,
            columns=[
                ColumnInfo(
                    name=item.name,
                    type=item.type.value,
                    length=item.length,
                    nullable=item.nullable,
                    primary_key=item.name == definition.primary_key,
                    indexed_with=_index_method(definition, item.name),
                    unique=_is_unique(definition, item.name),
                )
                for item in definition.schema
            ],
            indexes=[index.name for index in definition.indexes],
        )


def service_of(request: Request) -> Service:
    service: Service = request.app.state.service
    return service


ServiceDependency = Annotated[Service, Depends(service_of)]


def _first_point_column(fields: Iterable[Field]) -> str | None:
    return next((field.name for field in fields if field.type is FieldType.POINT), None)


def _index_method(definition: TableDefinition, column: str) -> str | None:
    index = definition.index_on(column)
    return None if index is None else index.method.value


def _is_unique(definition: TableDefinition, column: str) -> bool:
    index = definition.index_on(column)
    return index is not None and index.unique
