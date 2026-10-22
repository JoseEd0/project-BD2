"""Estado compartido del servidor: un motor, un gestor de bloqueos y las sesiones."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, Request

from api.schemas import ColumnInfo, TableInfo, TablePoints
from config import EngineConfig
from query.catalog import CatalogError, TableDefinition
from query.engine import Engine
from storage.schema import Field
from storage.types import FieldType
from txn import LockManager, Session, TransactionManager


@dataclass
class _SessionSlot:
    """Una sesión con nombre que el servidor recuerda entre peticiones.

    `guard` hace que dos peticiones con el mismo `session_id` se atiendan una tras otra: una
    sesión lleva una sola transacción y no admite dos sentencias a la vez.
    """

    session: Session
    guard: threading.Lock = field(default_factory=threading.Lock)
    last_used: float = field(default_factory=time.monotonic)
    requests: int = 0


class Service:
    """Lo que todas las rutas comparten durante la vida del servidor.

    Las rutas corren en hilos distintos, así que el diccionario de sesiones tiene su propio
    lock. Una sesión que pasa `session_idle_seconds` sin usarse se cierra y aborta lo que
    tuviera abierto: sin eso, una pestaña cerrada a mitad de una transacción dejaría sus
    tablas bloqueadas hasta reiniciar el servidor.
    """

    def __init__(self, config: EngineConfig) -> None:
        self.config = config
        self.engine = Engine(config)
        self.locks = LockManager(config)
        self.transactions = TransactionManager()
        self._sessions: dict[str, _SessionSlot] = {}
        self._sessions_guard = threading.Lock()

    @contextmanager
    def session(self, identifier: str | None) -> Iterator[Session]:
        """La sesión del cliente mientras dura la petición.

        Sin identificador es una sesión efímera, en autocommit, que se cierra al terminar.
        """
        if identifier is None:
            with Session(self.engine, self.locks, self.transactions) as ephemeral:
                yield ephemeral
            return
        slot = self._claim(identifier)
        try:
            with slot.guard:
                yield slot.session
        finally:
            self._release(slot)

    def close_session(self, identifier: str) -> bool:
        with self._sessions_guard:
            slot = self._sessions.pop(identifier, None)
        if slot is None:
            return False
        with slot.guard:
            slot.session.close()
        return True

    def tables(self) -> Iterator[TableInfo]:
        """Tablas del catálogo para el panel de archivos.

        No toma bloqueos: el catálogo tiene su propio lock y el número de filas es un
        contador que se lee de una vez. Por eso puede contar filas que otra transacción
        aún no confirmó, igual que una estadística de `pg_class` en PostgreSQL.
        """
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
        """Cierra todas las sesiones y después el motor.

        El motor se cierra aunque falle el cierre de alguna sesión: es él quien escribe en
        disco lo que queda en memoria, y no hacerlo perdería datos ya confirmados.
        """
        with self._sessions_guard:
            slots = list(self._sessions.values())
            self._sessions.clear()
        with ExitStack() as closing:
            closing.callback(self.engine.close)
            for slot in slots:
                closing.callback(slot.session.close)

    def _claim(self, identifier: str) -> _SessionSlot:
        with self._sessions_guard:
            expired = self._take_expired(identifier)
            slot = self._sessions.get(identifier)
            if slot is None:
                slot = _SessionSlot(Session(self.engine, self.locks, self.transactions))
                self._sessions[identifier] = slot
            slot.requests += 1
        for stale in expired:
            stale.session.close()
        return slot

    def _release(self, slot: _SessionSlot) -> None:
        with self._sessions_guard:
            slot.requests -= 1
            slot.last_used = time.monotonic()

    def _take_expired(self, keep: str) -> list[_SessionSlot]:
        """Quita del registro las sesiones inactivas para cerrarlas fuera del lock."""
        limit = time.monotonic() - self.config.session_idle_seconds
        expired = [
            identifier
            for identifier, slot in self._sessions.items()
            if identifier != keep and slot.requests == 0 and slot.last_used < limit
        ]
        return [self._sessions.pop(identifier) for identifier in expired]

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
                    indexed_with=_index_methods(definition, item.name),
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


def _index_methods(definition: TableDefinition, column: str) -> list[str]:
    return [index.method.value for index in definition.indexes_on(column)]


def _is_unique(definition: TableDefinition, column: str) -> bool:
    return any(index.unique for index in definition.indexes_on(column))
