"""El formato de un evento SSE y la respuesta que cierra su generador al terminar o cuando el
cliente se desconecta."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import anyio
from pydantic import BaseModel
from rag_sse import parse_sse

from rag.sse import EVENT_STREAM_MEDIA_TYPE, EventStreamResponse, sse_frame

SCOPE: dict[str, Any] = {
    "type": "http",
    "asgi": {"version": "3.0", "spec_version": "2.3"},
    "http_version": "1.1",
    "method": "POST",
    "path": "/answers",
    "headers": [],
}


class Text(BaseModel):
    text: str


class Source:
    """Un generador de eventos que recuerda cuántos entregó y si lo cerraron."""

    def __init__(self, frames: int, hang: bool = False) -> None:
        self.frames = frames
        self.hang = hang
        self.sent = 0
        self.closed = False

    async def events(self) -> AsyncGenerator[bytes, None]:
        try:
            for position in range(self.frames):
                self.sent += 1
                yield sse_frame("delta", Text(text=f"t{position}"))
            if self.hang:
                await anyio.sleep_forever()
        finally:
            self.closed = True


async def serve(response: EventStreamResponse, disconnect: anyio.Event) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []
    request_read = False

    async def receive() -> dict[str, Any]:
        nonlocal request_read
        if not request_read:
            request_read = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)
        if message["type"] == "http.response.body" and message.get("body"):
            disconnect.set()

    await response(SCOPE, receive, send)
    return sent


def test_a_frame_is_one_event_line_and_one_json_line():
    frame = sse_frame("delta", Text(text="dos\nlíneas"))
    assert frame == 'event: delta\ndata: {"text":"dos\\nlíneas"}\n\n'.encode()
    assert parse_sse(frame.decode()) == [("delta", {"text": "dos\nlíneas"})]


def test_the_response_is_an_unbuffered_event_stream_that_ends_and_closes():
    source = Source(frames=2)
    response = EventStreamResponse(source.events())

    async def run() -> list[dict[str, Any]]:
        sent: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            await anyio.sleep_forever()
            return {}

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await response(SCOPE, receive, send)
        return sent

    start, *bodies = anyio.run(run)
    headers = dict(start["headers"])
    assert headers[b"content-type"].startswith(EVENT_STREAM_MEDIA_TYPE.encode())
    assert headers[b"x-accel-buffering"] == b"no"
    assert headers[b"cache-control"] == b"no-cache"
    body = b"".join(message.get("body", b"") for message in bodies).decode()
    assert parse_sse(body) == [("delta", {"text": "t0"}), ("delta", {"text": "t1"})]
    assert bodies[-1]["more_body"] is False
    assert source.closed


def test_a_disconnect_closes_the_generator_while_it_waits():
    source = Source(frames=1, hang=True)

    async def run() -> list[dict[str, Any]]:
        with anyio.fail_after(5):
            return await serve(EventStreamResponse(source.events()), anyio.Event())

    sent = anyio.run(run)
    assert source.sent == 1
    assert source.closed
    assert not any(message.get("more_body") is False for message in sent)


def test_a_send_failure_closes_the_generator():
    source = Source(frames=3)

    async def run() -> None:
        async def receive() -> dict[str, Any]:
            await anyio.sleep_forever()
            return {}

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.body":
                raise OSError("el cliente se fue")

        await EventStreamResponse(source.events())(SCOPE, receive, send)

    anyio.run(run)
    assert source.sent == 1
    assert source.closed
