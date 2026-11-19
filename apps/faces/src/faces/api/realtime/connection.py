"""Una conexión de reconocimiento en vivo: recibir cuadros, procesar el más nuevo, responder.

Dos tareas por conexión. La que recibe lee el socket sin parar —así los cuadros no se
acumulan en el búfer TCP— y deja cada cuadro en `LatestFrame`; la que procesa toma el más
nuevo, no antes de `1 / max_fps` desde el anterior, lo reconoce y responde. Lo que llega
mientras tanto reemplaza al que esperaba (ver `mailbox.py`).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Protocol

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError
from starlette.websockets import WebSocketState

from appkit import ServiceError
from faces.api.realtime.mailbox import LatestFrame
from faces.api.realtime.protocol import (
    Frame,
    FrameResult,
    Hello,
    InvalidMessageError,
    Ready,
    StreamError,
    StreamLimitsView,
    StreamProtocolError,
    decode_frame,
)

# 1013 es «inténtalo más tarde» (RFC 6455, registro de IANA); los 4000-4999 son de la app:
# 4408 imita al 408 de HTTP, la espera agotada.
TRY_AGAIN_LATER_CLOSE_CODE = 1013
IDLE_CLOSE_CODE = 4408
INTERNAL_ERROR_CLOSE_CODE = 1011
FULL_REASON = "La réplica atiende el máximo de cámaras. Reintenta en unos segundos."
IDLE_REASON = "Sin cuadros por un rato: se cierra la conexión."

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StreamLimits:
    """Los límites de cada conexión y de la réplica."""

    max_frame_bytes: int
    max_frame_pixels: int
    max_fps: float
    idle_timeout_seconds: float
    max_connections: int

    def view(self) -> StreamLimitsView:
        return StreamLimitsView(
            max_frame_bytes=self.max_frame_bytes,
            max_frame_pixels=self.max_frame_pixels,
            max_fps=self.max_fps,
            idle_timeout_seconds=self.idle_timeout_seconds,
        )


class ConnectionSlots:
    """Cuántas conexiones atiende la réplica a la vez. Todo corre en un bucle de eventos,
    así que basta un contador."""

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self.active = 0

    def acquire(self) -> bool:
        if self.active >= self._capacity:
            return False
        self.active += 1
        return True

    def release(self) -> None:
        self.active -= 1


class Recognizer(Protocol):
    """Lo que la conexión necesita de `FrameRecognizer`."""

    async def recognize(
        self, frame: Frame, session: str, use_tracks: bool, dropped: int
    ) -> FrameResult: ...


@dataclass(frozen=True, slots=True)
class StreamServices:
    recognizer: Recognizer
    limits: StreamLimits
    slots: ConnectionSlots


async def serve_recognition(websocket: WebSocket, services: StreamServices) -> None:
    """Atiende una conexión hasta que el cliente la cierre o se agote la espera; si la
    réplica está llena, la cierra con 1013 tras aceptarla, para que el cliente sepa por
    qué."""
    await websocket.accept()
    if not services.slots.acquire():
        await websocket.close(code=TRY_AGAIN_LATER_CLOSE_CODE, reason=FULL_REASON)
        return
    try:
        await RecognitionConnection(websocket, services).run()
    finally:
        services.slots.release()


class RecognitionConnection:
    def __init__(self, websocket: WebSocket, services: StreamServices) -> None:
        self._websocket = websocket
        self._recognizer = services.recognizer
        self._limits = services.limits
        self._latest = LatestFrame()
        self._send_lock = asyncio.Lock()
        self._session = uuid.uuid4().hex
        self._use_tracks = True
        self._processed = 0

    async def run(self) -> None:
        await self._send(self._ready())
        processor = asyncio.create_task(self._process())
        try:
            with contextlib.suppress(WebSocketDisconnect):
                await self._receive()
        finally:
            processor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await processor
            logger.info(
                "reconocimiento en vivo terminado: %d cuadros respondidos, %d descartados",
                self._processed,
                self._latest.dropped,
            )

    async def _receive(self) -> None:
        while True:
            try:
                async with asyncio.timeout(self._limits.idle_timeout_seconds):
                    message = await self._websocket.receive()
            except TimeoutError:
                await self._close(IDLE_CLOSE_CODE, IDLE_REASON)
                return
            if message["type"] == "websocket.disconnect":
                return
            await self._dispatch(message.get("bytes"), message.get("text"))

    async def _dispatch(self, binary: bytes | None, text: str | None) -> None:
        try:
            if binary is not None:
                self._latest.put(
                    decode_frame(binary, time.monotonic(), self._limits.max_frame_bytes)
                )
            elif text is not None:
                self._greet(text)
                await self._send(self._ready())
        except StreamProtocolError as error:
            await self._send(StreamError(frame=None, kind=error.kind, error=error.message))

    def _greet(self, text: str) -> None:
        try:
            hello = Hello.model_validate_json(text)
        except ValidationError as error:
            raise InvalidMessageError(
                'Se espera {"type": "hello", "session": "<32 hex>", "track_cache": true}.'
            ) from error
        if hello.session is not None:
            self._session = hello.session
        self._use_tracks = hello.track_cache

    async def _process(self) -> None:
        minimum_interval = 1.0 / self._limits.max_fps
        next_start = time.monotonic()
        try:
            while True:
                await self._latest.wait()
                await asyncio.sleep(max(0.0, next_start - time.monotonic()))
                frame = self._latest.take()
                next_start = time.monotonic() + minimum_interval
                await self._answer(frame)
        except WebSocketDisconnect:
            return
        except Exception:
            logger.exception("fallo no previsto en el reconocimiento en vivo")
            await self._close(INTERNAL_ERROR_CLOSE_CODE, "Error interno del servicio.")

    async def _answer(self, frame: Frame) -> None:
        try:
            result: BaseModel = await self._recognizer.recognize(
                frame, self._session, self._use_tracks, self._latest.dropped
            )
        except ServiceError as error:
            result = StreamError(frame=frame.number, kind=error.kind, error=error.message)
        await self._send(result)
        self._processed += 1

    def _ready(self) -> Ready:
        return Ready(
            session=self._session, track_cache=self._use_tracks, limits=self._limits.view()
        )

    async def _send(self, message: BaseModel) -> None:
        async with self._send_lock:
            if self._websocket.application_state is not WebSocketState.CONNECTED:
                raise WebSocketDisconnect()
            await self._websocket.send_text(message.model_dump_json())

    async def _close(self, code: int, reason: str) -> None:
        async with self._send_lock:
            if self._websocket.application_state is WebSocketState.CONNECTED:
                await self._websocket.close(code=code, reason=reason)
