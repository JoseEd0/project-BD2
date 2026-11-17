"""Estado compartido del servidor: un motor, un gestor de bloqueos y las sesiones."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, Request

from api.schemas import ColumnInfo, TableInfo, TablePoints
from api.tracing import Stage, record_stage
from config import EngineConfig
from errors import DomainError
from query.catalog import CatalogError, TableDefinition, UnknownTableError
from query.deadline import Deadline, statement_deadline
from query.engine import Engine, QueryResult
from query.plan import MILLISECONDS_PER_SECOND
from sql.nodes import Statement
from storage.schema import Field
from storage.types import FieldType
from txn import LockManager, Session, TransactionManager

SHUTDOWN_REASON = "el servidor se está apagando"


class ServiceStoppingError(Exception):
    """El servidor se está apagando y ya no admite peticiones al motor."""


@dataclass
class _SessionSlot:
    """Una sesión con nombre que el servidor recuerda entre peticiones.

    `guard` hace que dos peticiones con el mismo `session_id` se atiendan una tras otra: una
    sesión lleva una sola transacción y no admite dos sentencias a la vez. `closing` marca
    una sesión que se pidió cerrar mientras aún tenía peticiones en curso: la cierra la
    última en terminar.
    """

    session: Session
    guard: threading.Lock = field(default_factory=threading.Lock)
    last_used: float = field(default_factory=time.monotonic)
    requests: int = 0
    closing: bool = False


class Service:
    """Lo que todas las rutas comparten durante la vida del servidor.

    Las rutas corren en hilos distintos, así que el diccionario de sesiones tiene su propio
    lock. Una sesión que pasa `session_idle_seconds` sin usarse se cierra y aborta lo que
    tuviera abierto: sin eso, una pestaña cerrada a mitad de una transacción dejaría sus
    tablas bloqueadas hasta reiniciar el servidor.

    `_activity` cuenta las peticiones que están usando el motor y los plazos de las
    sentencias en curso, para que el apagado pueda cancelarlas y esperar a que salgan.
    """

    def __init__(self, config: EngineConfig) -> None:
        self.config = config
        self.engine = Engine(config)
        self.locks = LockManager(config)
        self.transactions = TransactionManager()
        self._sessions: dict[str, _SessionSlot] = {}
        self._sessions_guard = threading.Lock()
        self._activity = threading.Condition()
        self._in_flight = 0
        self._running: set[Deadline] = set()
        self._stopping = False

    @contextmanager
    def session(self, identifier: str | None) -> Iterator[Session]:
        """La sesión del cliente mientras dura la petición.

        Sin identificador es una sesión efímera, en autocommit, que se cierra al terminar.

        Raises:
            ServiceStoppingError: si el servidor se está apagando.
        """
        with self._admitted():
            if identifier is None:
                with Session(self.engine, self.locks, self.transactions) as ephemeral:
                    yield ephemeral
                return
            slot, expired = self._claim(identifier)
            try:
                _close_all(expired)
                with slot.guard:
                    yield slot.session
            finally:
                self._release(slot)

    def run(self, session: Session, statement: Statement) -> QueryResult:
        """Ejecuta la sentencia en la sesión con `statement_timeout_seconds` de plazo y anota
        sus etapas en la traza de la petición.

        `txn` es lo que la sentencia pasó fuera del motor: esperando bloqueos y, en
        autocommit, escribiendo en disco al confirmar.

        Raises:
            StatementTimeoutError: si pasa del plazo; la sesión ya la deshizo.
            StatementCancelledError: si el servidor se apaga mientras corre.
            ServiceStoppingError: si el servidor ya se estaba apagando.
        """
        deadline = Deadline(self.config.statement_timeout_seconds)
        started = time.perf_counter()
        with self._tracked(deadline), statement_deadline(deadline):
            result = session.run(statement)
        wall_ms = (time.perf_counter() - started) * MILLISECONDS_PER_SECOND
        record_stage(Stage.PLAN, result.planning_ms)
        record_stage(Stage.EXECUTE, result.elapsed_ms - result.planning_ms)
        record_stage(Stage.TRANSACTION, max(wall_ms - result.elapsed_ms, 0.0))
        return result

    def is_ready(self) -> bool:
        """Si atiende consultas: no se está apagando y el catálogo responde."""
        if self._stopping:
            return False
        try:
            self.engine.table_names()
        except DomainError:
            return False
        return True

    def close_session(self, identifier: str) -> bool:
        """Cierra la sesión y aborta lo que tuviera abierto.

        Si alguna petición de la sesión sigue en curso o esperando su turno, el cierre
        queda para cuando termine la última: cerrarla antes dejaría que esa petición
        abriera una transacción en una sesión que ya nadie recuerda, con sus bloqueos
        retenidos para siempre.
        """
        with self._sessions_guard:
            slot = self._sessions.pop(identifier, None)
            if slot is None:
                return False
            slot.closing = True
            idle = slot.requests == 0
        if idle:
            slot.session.close()
        return True

    def tables(self) -> Iterator[TableInfo]:
        """Tablas del catálogo para el panel de archivos.

        No toma bloqueos: el catálogo tiene su propio lock y el número de filas es un
        contador que se lee de una vez. Por eso puede contar filas que otra transacción
        aún no confirmó, igual que una estadística de `pg_class` en PostgreSQL. Una tabla
        que otro cliente borra mientras tanto simplemente no aparece.
        """
        for name in self.engine.table_names():
            try:
                described = self._describe(name)
            except UnknownTableError:
                continue
            yield described

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
        """Apagado ordenado: deja de admitir peticiones, cancela las sentencias en curso,
        espera como mucho `shutdown_grace_seconds` a que salgan, cierra todas las sesiones
        y después el motor, que escribe en disco las páginas y los índices.

        El motor se cierra aunque falle el cierre de alguna sesión: es él quien escribe en
        disco lo que queda en memoria, y no hacerlo perdería datos ya confirmados. Cerrar
        dos veces no hace nada.
        """
        if not self._stop_admitting():
            return
        self._drain()
        with self._sessions_guard:
            slots = list(self._sessions.values())
            self._sessions.clear()
        with ExitStack() as closing:
            closing.callback(self.engine.close)
            closing.callback(_close_all, slots)

    @contextmanager
    def _admitted(self) -> Iterator[None]:
        with self._activity:
            if self._stopping:
                raise ServiceStoppingError(SHUTDOWN_REASON)
            self._in_flight += 1
        try:
            yield
        finally:
            with self._activity:
                self._in_flight -= 1
                self._activity.notify_all()

    @contextmanager
    def _tracked(self, deadline: Deadline) -> Iterator[None]:
        """Registra el plazo para que el apagado pueda cancelarlo; una petición que esperaba
        su turno cuando empezó el apagado ya no arranca su sentencia."""
        with self._activity:
            if self._stopping:
                raise ServiceStoppingError(SHUTDOWN_REASON)
            self._running.add(deadline)
        try:
            yield
        finally:
            with self._activity:
                self._running.discard(deadline)

    def _stop_admitting(self) -> bool:
        """Cancela las sentencias en curso; `False` si el apagado ya había empezado."""
        with self._activity:
            if self._stopping:
                return False
            self._stopping = True
            for deadline in self._running:
                deadline.cancel(SHUTDOWN_REASON)
        return True

    def _drain(self) -> None:
        """Espera a que salgan las peticiones; una sentencia que no lee filas (un CREATE
        INDEX) no se puede cancelar, y pasado el plazo se cierra igual."""
        with self._activity:
            drained = self._activity.wait_for(
                lambda: self._in_flight == 0, timeout=self.config.shutdown_grace_seconds
            )
            pending = self._in_flight
        if not drained:
            logging.getLogger(__name__).warning(
                "apagado con %d petición(es) aún en curso tras %g s",
                pending,
                self.config.shutdown_grace_seconds,
            )

    def _claim(self, identifier: str) -> tuple[_SessionSlot, list[_SessionSlot]]:
        """La sesión pedida, ya contada como en uso, y las caducadas que hay que cerrar."""
        with self._sessions_guard:
            expired = self._take_expired(identifier)
            slot = self._sessions.get(identifier)
            if slot is None:
                slot = _SessionSlot(Session(self.engine, self.locks, self.transactions))
                self._sessions[identifier] = slot
            slot.requests += 1
        return slot, expired

    def _release(self, slot: _SessionSlot) -> None:
        with self._sessions_guard:
            slot.requests -= 1
            slot.last_used = time.monotonic()
            pending_close = slot.closing and slot.requests == 0
        if pending_close:
            slot.session.close()

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


def _close_all(slots: Iterable[_SessionSlot]) -> None:
    """Cierra todas aunque falle alguna: una sesión que no se cierra no puede dejar a las
    demás con sus bloqueos tomados. Si alguna falló, el error se propaga al final."""
    with ExitStack() as closing:
        for slot in slots:
            closing.callback(slot.session.close)


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
