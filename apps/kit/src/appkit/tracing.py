"""`X-Request-ID` y `Server-Timing` de cada petición que atiende un servicio.

El middleware toma el id que trae la petición (el que puso el navegador o el nginx de la app)
o genera uno, lo deja en la variable de contexto que lee `minigestor_client` —así cada
llamada al gestor lleva el mismo— y lo devuelve en la respuesta. Las etapas que el servicio
mide con `timed("embed")` o `record_stage` salen en `Server-Timing` junto al total.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from appkit.errors import unexpected_failure_response
from appkit.logs import log_request
from appkit.metrics import observe_request, route_of
from minigestor_client import (
    REQUEST_ID_HEADER,
    SERVER_TIMING_HEADER,
    new_request_id,
    request_id_scope,
)

MILLISECONDS_PER_SECOND = 1000.0
SERVER_TIMING_DECIMALS = 3
TOTAL_STAGE = "total"
UNANSWERED_STATUS = 500
# El mismo patrón que acepta el gestor: un UUID, un ULID o el `$request_id` de nginx, sin
# espacios ni comillas que rompan una línea de log o una cabecera.
REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}")
STAGE_NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]+")

logger = logging.getLogger(__name__)


class InvalidStageNameError(ValueError):
    """Un nombre de etapa que no cabe en `Server-Timing` sin comillas."""


@dataclass
class RequestTrace:
    """El id de una petición y los milisegundos de cada etapa medida, en orden."""

    request_id: str
    stages: dict[str, float] = field(default_factory=dict)

    def record(self, stage: str, milliseconds: float) -> None:
        """Suma `milliseconds` a `stage`: una etapa medida varias veces se acumula.

        Raises:
            InvalidStageNameError: si `stage` no es un token de letras, dígitos, `_` o `-`.
        """
        if not STAGE_NAME_PATTERN.fullmatch(stage):
            raise InvalidStageNameError(f"'{stage}' no sirve como nombre de etapa")
        self.stages[stage] = self.stages.get(stage, 0.0) + milliseconds

    def server_timing(self) -> str:
        return ", ".join(
            f"{stage};dur={milliseconds:.{SERVER_TIMING_DECIMALS}f}"
            for stage, milliseconds in self.stages.items()
        )


_current: ContextVar[RequestTrace | None] = ContextVar("appkit_request_trace", default=None)


def current_trace() -> RequestTrace | None:
    """La traza de la petición en curso; `None` fuera de una."""
    return _current.get()


def record_stage(stage: str, milliseconds: float) -> None:
    """Anota una etapa en la petición en curso; fuera de una petición no hace nada."""
    trace = _current.get()
    if trace is not None:
        trace.record(stage, milliseconds)


@contextmanager
def timed(stage: str) -> Iterator[None]:
    """Mide el bloque y lo anota como `stage`, también si termina con una excepción."""
    started = time.perf_counter()
    try:
        yield
    finally:
        record_stage(stage, (time.perf_counter() - started) * MILLISECONDS_PER_SECOND)


def request_id_from(received: str | None) -> str:
    """El id recibido si es válido; si no, uno nuevo, sin rechazar la petición."""
    if received is not None and REQUEST_ID_PATTERN.fullmatch(received):
        return received
    return new_request_id()


@contextmanager
def tracing(trace: RequestTrace) -> Iterator[RequestTrace]:
    """Deja `trace` como la de la petición en curso, y su id como el de las llamadas al
    gestor, mientras dura el bloque."""
    token = _current.set(trace)
    try:
        with request_id_scope(trace.request_id):
            yield trace
    finally:
        _current.reset(token)


class _TracedSend:
    """El `send` de ASGI que pone las cabeceras de la traza al empezar la respuesta y
    recuerda su estado."""

    def __init__(self, send: Send, trace: RequestTrace, started: float) -> None:
        self._send = send
        self._trace = trace
        self._started = started
        self.status = UNANSWERED_STATUS
        self.response_started = False

    async def __call__(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            self.response_started = True
            self.status = message["status"]
            self._trace.record(TOTAL_STAGE, _elapsed_ms(self._started))
            headers = MutableHeaders(scope=message)
            headers[REQUEST_ID_HEADER] = self._trace.request_id
            headers[SERVER_TIMING_HEADER] = self._trace.server_timing()
        await self._send(message)


class RequestTracingMiddleware:
    """Middleware ASGI: id y etapas de la petición, cabeceras de la respuesta, su línea de
    log y sus métricas. Va por fuera de las rutas, así que también un `500` sale con su id:
    una excepción que nadie atendió se responde aquí con la forma de error de siempre."""

    def __init__(self, app: ASGIApp, service: str) -> None:
        self._app = app
        self._service = service

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        started = time.perf_counter()
        trace = RequestTrace(request_id_from(Headers(scope=scope).get(REQUEST_ID_HEADER)))
        traced_send = _TracedSend(send, trace, started)
        try:
            with tracing(trace):
                await self._answer(scope, receive, traced_send, trace)
        finally:
            duration_ms = trace.stages.get(TOTAL_STAGE, _elapsed_ms(started))
            log_request(
                self._service,
                scope["method"],
                scope["path"],
                traced_send.status,
                trace.request_id,
                duration_ms,
            )
            observe_request(
                scope["method"],
                route_of(scope),
                traced_send.status,
                duration_ms / MILLISECONDS_PER_SECOND,
            )

    async def _answer(
        self, scope: Scope, receive: Receive, send: _TracedSend, trace: RequestTrace
    ) -> None:
        try:
            await self._app(scope, receive, send)
        except Exception:
            logger.exception("fallo no previsto atendiendo una petición")
            if send.response_started:
                raise
            await unexpected_failure_response(trace.request_id)(scope, receive, send)


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * MILLISECONDS_PER_SECOND
