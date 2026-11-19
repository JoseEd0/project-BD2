"""Server-Sent Events: el formato de cada evento y una respuesta que corta su fuente cuando el
cliente se va.

Cada evento es `event: <nombre>` y una línea `data:` con un objeto JSON, seguidos de una línea
en blanco. El JSON va en una sola línea (`json` no deja saltos de línea sin escapar), así que
nunca hace falta partir `data:`.

`EventStreamResponse` escucha la desconexión del cliente mientras envía y, cuando llega,
cancela el envío y cierra el generador de eventos: el `finally` y los `async with` del
generador corren en el acto, y con ellos se cierra la petición al modelo de lenguaje en vez de
seguir pagando tokens que nadie va a leer.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator

import anyio
from pydantic import BaseModel
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

EVENT_STREAM_MEDIA_TYPE = "text/event-stream"
# `X-Accel-Buffering: no` le dice al nginx de la app que no junte la respuesta en su búfer:
# cada evento sale hacia el navegador en cuanto la API lo escribe. Sin él, nginx guardaría
# los trozos hasta llenar su búfer o terminar.
EVENT_STREAM_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


def sse_frame(event: str, data: BaseModel) -> bytes:
    """Un evento SSE con `data` serializado en JSON en una sola línea."""
    return f"event: {event}\ndata: {data.model_dump_json()}\n\n".encode()


class EventStreamResponse(StreamingResponse):
    """Una respuesta `text/event-stream` que cierra `events` al terminar, al fallar o cuando
    el cliente se desconecta."""

    def __init__(self, events: AsyncGenerator[bytes, None]) -> None:
        super().__init__(events, headers=EVENT_STREAM_HEADERS, media_type=EVENT_STREAM_MEDIA_TYPE)
        self._events = events

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            async with anyio.create_task_group() as group:
                group.start_soon(self._send_all_then_stop, send, group.cancel_scope)
                await self.listen_for_disconnect(receive)
                group.cancel_scope.cancel()
        finally:
            await self._events.aclose()

    async def _send_all_then_stop(self, send: Send, scope: anyio.CancelScope) -> None:
        # Con ASGI 2.4 el servidor avisa que el cliente se fue lanzando OSError al enviar: no
        # queda a quién responder.
        with contextlib.suppress(OSError):
            await self.stream_response(send)
        scope.cancel()
