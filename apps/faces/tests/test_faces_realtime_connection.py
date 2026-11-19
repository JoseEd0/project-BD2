"""La conexión de reconocimiento en vivo con un reconocedor de mentira: el protocolo, la
contrapresión, el tope de cuadros por segundo, la espera y el cupo de la réplica."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from typing import Any

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from faces.api.errors import UNREADABLE_IMAGE, UnreadableImageError
from faces.api.realtime.connection import (
    IDLE_CLOSE_CODE,
    TRY_AGAIN_LATER_CLOSE_CODE,
    ConnectionSlots,
    StreamLimits,
    StreamServices,
    serve_recognition,
)
from faces.api.realtime.mailbox import LatestFrame
from faces.api.realtime.protocol import (
    FRAME_HEADER,
    Frame,
    FrameResult,
    FrameTooLargeError,
    InvalidFrameError,
    StageTimings,
    decode_frame,
    encode_frame,
)

PATH = "/ws"
IMAGE = b"cuadro"
BROKEN_IMAGE = b"roto"
SESSION = "0123456789abcdef0123456789abcdef"
LIMITS = StreamLimits(
    max_frame_bytes=64,
    max_frame_pixels=1000,
    max_fps=1000.0,
    idle_timeout_seconds=5.0,
    max_connections=4,
)
PROCESSING_SECONDS = 0.05
FAST_PRODUCER_FRAMES = 60
FAST_PRODUCER_INTERVAL_SECONDS = 0.005
# Con un solo cuadro en espera, uno tarda a lo sumo el que está en curso más el suyo; con
# una cola, el último de 60 tardaría unos 3 s.
BOUNDED_LATENCY_SECONDS = 4 * PROCESSING_SECONDS
TIMINGS = StageTimings(wait=0, decode=0, detect=0, track=0, embed=0, search=0, total=0)


@dataclass
class FakeRecognizer:
    """Tarda `seconds` por cuadro y anota con qué sesión y opciones lo recibió."""

    seconds: float = 0.0
    calls: list[tuple[int, str, bool]] = field(default_factory=list)
    started: list[float] = field(default_factory=list)

    async def recognize(
        self, frame: Frame, session: str, use_tracks: bool, dropped: int
    ) -> FrameResult:
        self.started.append(time.monotonic())
        self.calls.append((frame.number, session, use_tracks))
        if frame.image == BROKEN_IMAGE:
            raise UnreadableImageError(UNREADABLE_IMAGE)
        await asyncio.sleep(self.seconds)
        return FrameResult(frame=frame.number, faces=[], dropped=dropped, timings=TIMINGS)


def stream_app(recognizer: FakeRecognizer, limits: StreamLimits = LIMITS) -> FastAPI:
    services = StreamServices(recognizer, limits, ConnectionSlots(limits.max_connections))
    app = FastAPI()

    @app.websocket(PATH)
    async def recognize(websocket: WebSocket) -> None:
        await serve_recognition(websocket, services)

    return app


@pytest.fixture
def recognizer() -> FakeRecognizer:
    return FakeRecognizer()


@pytest.fixture
def client(recognizer: FakeRecognizer) -> Iterator[TestClient]:
    with TestClient(stream_app(recognizer)) as test_client:
        yield test_client


def test_a_frame_round_trips_with_its_number(client: TestClient, recognizer: FakeRecognizer):
    with client.websocket_connect(PATH) as socket:
        ready = socket.receive_json()
        socket.send_bytes(encode_frame(7, IMAGE))
        result = socket.receive_json()
    assert ready["type"] == "ready"
    assert ready["track_cache"] is True
    assert ready["limits"]["max_frame_bytes"] == LIMITS.max_frame_bytes
    assert len(ready["session"]) == len(SESSION)
    assert (result["type"], result["frame"], result["faces"], result["dropped"]) == (
        "result",
        7,
        [],
        0,
    )
    assert recognizer.calls == [(7, ready["session"], True)]


def test_hello_resumes_a_session_and_can_skip_the_track_cache(
    client: TestClient, recognizer: FakeRecognizer
):
    with client.websocket_connect(PATH) as socket:
        socket.receive_json()
        socket.send_json({"type": "hello", "session": SESSION, "track_cache": False})
        ready = socket.receive_json()
        socket.send_bytes(encode_frame(1, IMAGE))
        socket.receive_json()
    assert (ready["session"], ready["track_cache"]) == (SESSION, False)
    assert recognizer.calls == [(1, SESSION, False)]


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        ({"text": "hola"}, "InvalidMessageError"),
        ({"text": '{"type": "hello", "session": "../otra"}'}, "InvalidMessageError"),
        ({"bytes": b"\x01"}, "InvalidFrameError"),
        ({"bytes": FRAME_HEADER.pack(2, 1) + IMAGE}, "InvalidFrameError"),
        ({"bytes": encode_frame(1, b"x" * (LIMITS.max_frame_bytes + 1))}, "FrameTooLargeError"),
    ],
)
def test_a_bad_message_is_answered_and_the_connection_goes_on(
    client: TestClient, message: dict[str, Any], kind: str
):
    with client.websocket_connect(PATH) as socket:
        socket.receive_json()
        socket.send({"type": "websocket.receive", **message})
        error = socket.receive_json()
        socket.send_bytes(encode_frame(2, IMAGE))
        result = socket.receive_json()
    assert (error["type"], error["kind"], error["frame"]) == ("error", kind, None)
    assert (result["type"], result["frame"]) == ("result", 2)


def test_an_unreadable_frame_is_an_error_for_that_frame(client: TestClient):
    with client.websocket_connect(PATH) as socket:
        socket.receive_json()
        socket.send_bytes(encode_frame(3, BROKEN_IMAGE))
        error = socket.receive_json()
        socket.send_bytes(encode_frame(4, IMAGE))
        result = socket.receive_json()
    assert (error["frame"], error["kind"]) == (3, "UnreadableImageError")
    assert result["frame"] == 4


def test_a_fast_producer_drops_old_frames_and_latency_stays_bounded():
    recognizer = FakeRecognizer(seconds=PROCESSING_SECONDS)
    with TestClient(stream_app(recognizer)) as client, client.websocket_connect(PATH) as socket:
        socket.receive_json()
        sent_at: dict[int, float] = {}
        results: list[tuple[dict[str, Any], float]] = []
        reader = threading.Thread(target=read_until_last, args=(socket, results))
        reader.start()
        for number in range(1, FAST_PRODUCER_FRAMES + 1):
            sent_at[number] = time.monotonic()
            socket.send_bytes(encode_frame(number, IMAGE))
            time.sleep(FAST_PRODUCER_INTERVAL_SECONDS)
        reader.join()
    latencies = [received - sent_at[result["frame"]] for result, received in results]
    last = results[-1][0]
    assert last["frame"] == FAST_PRODUCER_FRAMES
    assert len(results) < FAST_PRODUCER_FRAMES / 2
    assert last["dropped"] == FAST_PRODUCER_FRAMES - len(results)
    assert [result["frame"] for result, _ in results] == sorted(r["frame"] for r, _ in results)
    assert max(latencies) < BOUNDED_LATENCY_SECONDS


def read_until_last(
    socket: WebSocketTestSession, results: list[tuple[dict[str, Any], float]]
) -> None:
    while True:
        message = socket.receive_json()
        results.append((message, time.monotonic()))
        if message["frame"] == FAST_PRODUCER_FRAMES:
            return


def test_frames_per_second_are_capped(recognizer: FakeRecognizer):
    limits = replace(LIMITS, max_fps=10.0)
    with TestClient(stream_app(recognizer, limits)) as client, client.websocket_connect(PATH) as ws:
        ws.receive_json()
        for number in (1, 2):
            ws.send_bytes(encode_frame(number, IMAGE))
            ws.receive_json()
    first, second = recognizer.started
    assert second - first >= 1 / limits.max_fps * 0.95


def test_an_idle_connection_is_closed(recognizer: FakeRecognizer):
    limits = replace(LIMITS, idle_timeout_seconds=0.1)
    with TestClient(stream_app(recognizer, limits)) as client, client.websocket_connect(PATH) as ws:
        ws.receive_json()
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == IDLE_CLOSE_CODE


def test_a_full_replica_turns_new_connections_away(recognizer: FakeRecognizer):
    limits = replace(LIMITS, max_connections=1)
    with TestClient(stream_app(recognizer, limits)) as client:
        with client.websocket_connect(PATH) as first:
            first.receive_json()
            with (
                client.websocket_connect(PATH) as second,
                pytest.raises(WebSocketDisconnect) as closed,
            ):
                second.receive_json()
            assert closed.value.code == TRY_AGAIN_LATER_CLOSE_CODE
        with client.websocket_connect(PATH) as again:
            assert again.receive_json()["type"] == "ready"


def test_the_frame_header_is_five_bytes():
    message = encode_frame(258, IMAGE)
    assert message[: FRAME_HEADER.size] == b"\x01\x00\x00\x01\x02"
    frame = decode_frame(message, 1.5, LIMITS.max_frame_bytes)
    assert (frame.number, frame.image, frame.received_at) == (258, IMAGE, 1.5)
    with pytest.raises(InvalidFrameError):
        decode_frame(FRAME_HEADER.pack(1, 1), 0.0, LIMITS.max_frame_bytes)
    with pytest.raises(FrameTooLargeError):
        decode_frame(encode_frame(1, IMAGE), 0.0, len(IMAGE) - 1)


@pytest.mark.anyio
async def test_the_mailbox_keeps_only_the_newest_frame():
    latest = LatestFrame()
    for number in (1, 2, 3):
        latest.put(Frame(number, IMAGE, 0.0))
    await asyncio.wait_for(latest.wait(), timeout=1)
    assert latest.take().number == 3
    assert latest.dropped == 2
    with pytest.raises(LookupError):
        latest.take()
