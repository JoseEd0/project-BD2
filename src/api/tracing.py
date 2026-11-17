"""Lo que se sabe de la petición en curso: su `X-Request-ID`, quién llama y cuánto tardó
cada etapa, para el log y la cabecera `Server-Timing`.

El middleware crea un `RequestTrace` por petición y lo deja en una variable de contexto.
La ruta corre en otro hilo con una copia de ese contexto: la copia apunta al mismo objeto,
así que lo que la ruta anota lo lee el middleware al responder.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum

from query.plan import MILLISECONDS_PER_SECOND

REQUEST_ID_HEADER = "X-Request-ID"
SERVER_TIMING_HEADER = "Server-Timing"
# Lo que caben un UUID, un ULID o el `$request_id` de nginx, sin espacios ni comillas que
# puedan romper una línea de log o colarse en una cabecera.
REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}")
SERVER_TIMING_DECIMALS = 3
ANONYMOUS_CALLER = "-"


class Stage(Enum):
    """Etapas de una petición, con el nombre que muestran las herramientas del navegador."""

    PARSE = "parse"
    PLAN = "plan"
    EXECUTE = "exec"
    TRANSACTION = "txn"
    SERIALIZE = "serialize"
    TOTAL = "total"


@dataclass
class RequestTrace:
    """Una petición: su identificador, el servicio que llama y los milisegundos por etapa."""

    request_id: str
    caller: str = ANONYMOUS_CALLER
    stages: dict[Stage, float] = field(default_factory=dict)

    def record(self, stage: Stage, milliseconds: float) -> None:
        """Suma `milliseconds` a la etapa: un script de varias sentencias planifica varias veces."""
        self.stages[stage] = self.stages.get(stage, 0.0) + milliseconds

    def server_timing(self) -> str:
        """El valor de `Server-Timing`, una métrica por etapa medida."""
        return ", ".join(
            f"{stage.value};dur={milliseconds:.{SERVER_TIMING_DECIMALS}f}"
            for stage, milliseconds in self.stages.items()
        )


_current: ContextVar[RequestTrace | None] = ContextVar("request_trace", default=None)


def request_id_from(received: str | None) -> str:
    """El identificador que mandó el cliente si es válido; si no, uno nuevo.

    Uno inválido se reemplaza en vez de rechazar la petición: la traza es una ayuda y no
    debe tumbar una consulta que por lo demás está bien.
    """
    if received is not None and REQUEST_ID_PATTERN.fullmatch(received):
        return received
    return uuid.uuid4().hex


def current_trace() -> RequestTrace | None:
    """La traza de la petición en curso; `None` fuera de una petición."""
    return _current.get()


@contextmanager
def tracing(trace: RequestTrace) -> Iterator[RequestTrace]:
    """Deja `trace` como la de la petición mientras dura el bloque."""
    token = _current.set(trace)
    try:
        yield trace
    finally:
        _current.reset(token)


def record_stage(stage: Stage, milliseconds: float) -> None:
    """Anota la etapa en la petición en curso; fuera de una petición no hace nada."""
    trace = _current.get()
    if trace is not None:
        trace.record(stage, milliseconds)


@contextmanager
def timed(stage: Stage) -> Iterator[None]:
    """Mide el bloque y lo anota como `stage`, también si termina con una excepción."""
    started = time.perf_counter()
    try:
        yield
    finally:
        record_stage(stage, (time.perf_counter() - started) * MILLISECONDS_PER_SECOND)
