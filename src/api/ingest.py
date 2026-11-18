"""Carga masiva: filas en NDJSON que llegan por partes y entran a la tabla por lotes.

`LineBatcher` corta los bytes del cuerpo en líneas numeradas y las junta en lotes sin
tener nunca más de un lote en memoria. `RowLoad` carga cada lote en una sesión propia:

- **Tabla con filas, o sin índices que se construyan de una vez:** cada lote es una
  transacción, como un `INSERT` de varias filas en autocommit. Bloquea la tabla mientras
  escribe su lote y la suelta entre uno y otro, y los índices se mantienen fila a fila.
- **Tabla vacía con un R-Tree, un invertido, un HNSW o un IVF:** la carga la bloquea en
  exclusiva de principio a fin, deja de mantener esos índices y los construye al terminar
  con todas las filas (ver `Table.defer_bulk_indexes`). Cada lote se confirma igual
  (`Session.checkpoint`), pero sin soltar la tabla: con los índices a medio construir,
  nadie más puede leerla.

En los dos casos un lote entra entero o no entra: si una fila falla, se deshace lo que el
lote alcanzó a escribir, índices incluidos, y la carga sigue con el siguiente.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass

from api.schemas import RejectedBatch, RowLoadResponse
from api.service import Service, ServiceStoppingError
from api.tracing import Stage, timed
from errors import DomainError
from query.deadline import StatementCancelledError
from query.ingest import JsonRow, LoadRowsStatement, RejectedRowError, RowFormatError, decode_row
from query.plan import MILLISECONDS_PER_SECOND
from sql.nodes import BeginTransactionStatement, CommitTransactionStatement
from txn import Session

NEWLINE = b"\n"


@dataclass(frozen=True, slots=True)
class Line:
    """Una línea del cuerpo, con su número contando desde 1; `content` es `None` si pasó
    del límite y no se guardó."""

    number: int
    content: bytes | None


class LineBatcher:
    """Corta en líneas los trozos del cuerpo y las agrupa en lotes de `batch_rows`.

    Las líneas en blanco no son filas, pero cuentan para numerar las demás como las
    numera un editor. Una línea que pasa de `line_limit` bytes no se acumula: se descarta
    lo que sigue hasta el salto de línea y queda como una línea sin contenido, para que el
    lote que la contiene se rechace sin que el servidor la haya guardado entera.
    """

    def __init__(self, line_limit: int, batch_rows: int) -> None:
        self._line_limit = line_limit
        self._batch_rows = batch_rows
        self._partial = bytearray()
        self._oversize = False
        self._number = 0
        self._batch: list[Line] = []

    @property
    def pending(self) -> int:
        """Filas del lote que aún no se completó."""
        return len(self._batch)

    @property
    def lines_read(self) -> int:
        """Líneas completas leídas hasta ahora."""
        return self._number

    def feed(self, chunk: bytes) -> list[list[Line]]:
        """Los lotes que `chunk` completa."""
        ready: list[list[Line]] = []
        start = 0
        while (end := chunk.find(NEWLINE, start)) != -1:
            self._accumulate(chunk[start:end])
            self._close_line(ready)
            start = end + len(NEWLINE)
        self._accumulate(chunk[start:])
        return ready

    def finish(self) -> list[list[Line]]:
        """Los lotes que quedan al terminar el cuerpo: la última línea, aunque no acabe en
        salto de línea, y el lote incompleto."""
        ready: list[list[Line]] = []
        if self._partial or self._oversize:
            self._close_line(ready)
        if self._batch:
            ready.append(self._batch)
            self._batch = []
        return ready

    def _accumulate(self, piece: bytes) -> None:
        if self._oversize:
            return
        if len(self._partial) + len(piece) > self._line_limit:
            self._oversize = True
            self._partial.clear()
            return
        self._partial += piece

    def _close_line(self, ready: list[list[Line]]) -> None:
        self._number += 1
        line = self._finished_line()
        if line is None:
            return
        self._batch.append(line)
        if len(self._batch) == self._batch_rows:
            ready.append(self._batch)
            self._batch = []

    def _finished_line(self) -> Line | None:
        if self._oversize:
            self._oversize = False
            return Line(self._number, None)
        content = bytes(self._partial).strip()
        self._partial.clear()
        return Line(self._number, content) if content else None


class _LineError(DomainError):
    """Una línea del lote no se pudo leer como fila."""

    def __init__(self, line: int, reason: str) -> None:
        super().__init__(reason)
        self.line = line


class RowLoad:
    """Una carga masiva en la tabla `table`, sobre una sesión sin transacción abierta.

    Se usa así: `start`, `load` por cada lote mientras `stopped` sea falso, y `finish`
    siempre, también si la carga se cortó, porque es quien construye los índices diferidos
    y suelta la tabla.

    Raises:
        UnknownTableError: al crearla, si la tabla no existe.
    """

    def __init__(self, service: Service, session: Session, table: str) -> None:
        self._service = service
        self._session = session
        self._table = service.engine.table(table).name
        self._started = time.perf_counter()
        self._deferred: tuple[str, ...] = ()
        self._loaded = 0
        self._committed = 0
        self._last_line = 0
        self._rejected: list[RejectedBatch] = []
        self._interrupted: str | None = None

    @property
    def stopped(self) -> bool:
        return self._interrupted is not None

    def start(self) -> None:
        """Decide cómo se carga: si la tabla está vacía y tiene índices que se construyen de
        una vez, la retiene en exclusiva y los difiere; si no, la suelta y cada lote será
        una transacción propia.

        Raises:
            LockError: si la tabla no se puede bloquear a tiempo.
        """
        self._session.run(BeginTransactionStatement())
        self._session.lock_exclusively(self._table)
        table = self._service.engine.table(self._table)
        if table.row_count == 0:
            self._deferred = table.defer_bulk_indexes()
        if not self._deferred:
            self._session.run(CommitTransactionStatement())

    def load(self, batch: Sequence[Line]) -> None:
        """Carga el lote entero o lo rechaza; si el servidor se está apagando, lo deshace
        y detiene la carga."""
        try:
            rows = [_decoded(line, self._service.config.max_ingest_line_bytes) for line in batch]
            self._service.run(self._session, LoadRowsStatement(self._table, tuple(rows)))
        except _LineError as error:
            self._reject(batch, error.line, str(error))
        except RejectedRowError as error:
            self._reject(batch, batch[error.position].number, str(error))
        except (ServiceStoppingError, StatementCancelledError) as error:
            self.interrupt(str(error), len(batch))
        except DomainError as error:
            self._reject(batch, None, str(error))
        else:
            self._commit(batch)

    def interrupt(self, reason: str, discarded: int) -> None:
        """La carga termina antes de leer todo el cuerpo; lo deja en el log, porque quien
        cortó la conexión ya no va a leer la respuesta."""
        self._interrupted = reason
        logging.getLogger(__name__).warning(
            "carga masiva en '%s' interrumpida: %s; quedan %d fila(s) en %d lote(s) "
            "confirmados hasta la línea %d y se descartan %d fila(s) del lote en curso",
            self._table,
            reason,
            self._loaded,
            self._committed,
            self._last_line,
            discarded,
        )

    def finish(self) -> RowLoadResponse:
        """Construye los índices diferidos con las filas confirmadas, suelta la tabla y
        resume la carga."""
        if self._session.in_transaction:
            table = self._service.engine.table(self._table)
            with timed(Stage.EXECUTE):
                table.build_deferred_indexes()
                table.flush()
            self._session.run(CommitTransactionStatement())
        return RowLoadResponse(
            table=self._table,
            rows_loaded=self._loaded,
            rows_rejected=sum(batch.rows for batch in self._rejected),
            batches_committed=self._committed,
            batches_rejected=len(self._rejected),
            last_line=self._last_line,
            elapsed_ms=(time.perf_counter() - self._started) * MILLISECONDS_PER_SECOND,
            rejected=self._rejected,
            deferred_indexes=list(self._deferred),
            interrupted=self._interrupted,
        )

    def _commit(self, batch: Sequence[Line]) -> None:
        if self._session.in_transaction:
            self._session.checkpoint()
        self._loaded += len(batch)
        self._committed += 1
        self._last_line = batch[-1].number

    def _reject(self, batch: Sequence[Line], line: int | None, reason: str) -> None:
        self._rejected.append(
            RejectedBatch(
                first_line=batch[0].number,
                last_line=batch[-1].number,
                rows=len(batch),
                line=line,
                reason=reason,
            )
        )
        self._last_line = batch[-1].number


def _decoded(line: Line, limit: int) -> JsonRow:
    if line.content is None:
        raise _LineError(
            line.number, f"la línea pasa de {limit} bytes, el máximo (max_ingest_line_bytes)"
        )
    try:
        return decode_row(line.content)
    except RowFormatError as error:
        raise _LineError(line.number, str(error)) from error
