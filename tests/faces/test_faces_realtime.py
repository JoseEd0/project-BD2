"""El reconocimiento en vivo de punta a punta: el WebSocket del servicio con los modelos
reales, el gestor real y Redis de mentira (`fakeredis`), compartido entre réplicas."""

from __future__ import annotations

from typing import Any

import pytest
from apps.faces.tests.faces_support import crew_member, jpeg, selfie, solid
from fakeredis import FakeAsyncRedis, FakeServer
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession
from tests.faces.conftest import FakeClock, ServiceFactory
from tests.faces.test_faces_service_enrollment import NAME, enroll_collins

from appkit import RedisFactory
from faces.api.realtime.protocol import UNKNOWN_LABEL, encode_frame
from faces.api.realtime.routes import RECOGNIZE_PATH

VERIFY_SECONDS = 2.0
BLANK_SIDE = 480
BLANK_GRAY = 0
STAGES = {"wait", "decode", "detect", "track", "embed", "search", "total"}


@pytest.fixture
def redis_server() -> FakeServer:
    return FakeServer()


@pytest.fixture
def redis(redis_server: FakeServer) -> RedisFactory:
    return lambda: FakeAsyncRedis(server=redis_server)


def recognize(socket: WebSocketTestSession, number: int, image: bytes) -> dict[str, Any]:
    socket.send_bytes(encode_frame(number, image))
    result = socket.receive_json()
    assert result["type"] == "result", result
    assert result["frame"] == number
    return dict(result)


def only_face(result: dict[str, Any]) -> dict[str, Any]:
    (face,) = result["faces"]
    return dict(face)


def collins_frame() -> bytes:
    return jpeg(crew_member("collins"))


def test_a_known_face_is_tracked_and_verified_again_when_its_identity_expires(
    start_service: ServiceFactory, redis: RedisFactory, clock: FakeClock
):
    with start_service(redis=redis, track_verify_seconds=VERIFY_SECONDS) as service:
        enroll_collins(service)
        with service.websocket_connect(RECOGNIZE_PATH) as socket:
            ready = socket.receive_json()
            first = recognize(socket, 1, collins_frame())
            second = recognize(socket, 2, collins_frame())
            clock.advance(VERIFY_SECONDS)
            third = recognize(socket, 3, collins_frame())
    assert ready["track_cache"] is True
    face = only_face(first)
    assert (face["status"], face["label"], face["display_name"]) == ("known", NAME, NAME)
    assert (face["track"], face["cached"]) == (1, False)
    assert set(first["timings"]) == STAGES
    assert first["timings"]["embed"] > 0
    cached = only_face(second)
    assert (cached["status"], cached["track"], cached["cached"]) == ("known", 1, True)
    assert second["timings"]["embed"] == 0
    assert (only_face(third)["track"], only_face(third)["cached"]) == (1, False)


def test_a_stranger_is_unknown(start_service: ServiceFactory, redis: RedisFactory):
    with start_service(redis=redis) as service:
        enroll_collins(service)
        with service.websocket_connect(RECOGNIZE_PATH) as socket:
            socket.receive_json()
            face = only_face(recognize(socket, 1, jpeg(crew_member("other"))))
    assert (face["status"], face["label"], face["display_name"]) == (
        "unknown",
        UNKNOWN_LABEL,
        None,
    )
    assert face["score"] is not None


def test_a_covered_camera_gives_no_faces_and_no_error(service: TestClient):
    with service.websocket_connect(RECOGNIZE_PATH) as socket:
        socket.receive_json()
        result = recognize(socket, 1, jpeg(solid(BLANK_SIDE, BLANK_SIDE, BLANK_GRAY)))
    assert result["faces"] == []


def test_a_frame_with_too_many_pixels_is_an_error_and_the_stream_goes_on(
    start_service: ServiceFactory,
):
    with (
        start_service(stream_max_frame_pixels=100) as service,
        service.websocket_connect(RECOGNIZE_PATH) as socket,
    ):
        socket.receive_json()
        socket.send_bytes(encode_frame(1, collins_frame()))
        error = socket.receive_json()
        socket.send_bytes(encode_frame(2, b"no es una imagen"))
        unreadable = socket.receive_json()
    assert (error["type"], error["frame"], error["kind"]) == ("error", 1, "TooManyPixelsError")
    assert (unreadable["frame"], unreadable["kind"]) == (2, "UnreadableImageError")


def test_without_redis_every_frame_is_searched_and_the_service_says_degraded(
    start_service: ServiceFactory, redis: RedisFactory, redis_server: FakeServer
):
    with start_service(redis=redis) as service:
        enroll_collins(service)
        redis_server.connected = False
        with service.websocket_connect(RECOGNIZE_PATH) as socket:
            socket.receive_json()
            faces = [only_face(recognize(socket, number, collins_frame())) for number in (1, 2)]
        health = service.get("/health").json()
    assert [(face["status"], face["track"], face["cached"]) for face in faces] == [
        ("known", None, False)
    ] * 2
    assert "redis" in health["degraded"]


def test_without_a_configured_redis_there_are_no_tracks(service: TestClient):
    enroll_collins(service)
    with service.websocket_connect(RECOGNIZE_PATH) as socket:
        socket.receive_json()
        face = only_face(recognize(socket, 1, collins_frame()))
    assert (face["status"], face["track"]) == ("known", None)
    assert "degraded" not in service.get("/health").json()


def test_a_session_resumes_its_tracks_on_another_replica(
    start_service: ServiceFactory, redis: RedisFactory
):
    with start_service(redis=redis) as first, start_service(redis=redis) as second:
        enroll_collins(first)
        with first.websocket_connect(RECOGNIZE_PATH) as socket:
            session = socket.receive_json()["session"]
            before = only_face(recognize(socket, 1, collins_frame()))
        with second.websocket_connect(RECOGNIZE_PATH) as socket:
            socket.receive_json()
            socket.send_json({"type": "hello", "session": session})
            assert socket.receive_json()["session"] == session
            after = only_face(recognize(socket, 2, collins_frame()))
    assert (before["track"], before["cached"]) == (1, False)
    assert (after["track"], after["cached"], after["display_name"]) == (1, True, NAME)


def test_track_cache_can_be_skipped_per_connection(
    start_service: ServiceFactory, redis: RedisFactory
):
    with start_service(redis=redis) as service:
        enroll_collins(service)
        with service.websocket_connect(RECOGNIZE_PATH) as socket:
            socket.receive_json()
            socket.send_json({"type": "hello", "track_cache": False})
            assert socket.receive_json()["track_cache"] is False
            faces = [only_face(recognize(socket, number, collins_frame())) for number in (1, 2)]
    assert [(face["track"], face["cached"]) for face in faces] == [(None, False)] * 2


def test_the_selfie_frame_is_recognized_too(start_service: ServiceFactory, redis: RedisFactory):
    with start_service(redis=redis) as service:
        enroll_collins(service)
        with service.websocket_connect(RECOGNIZE_PATH) as socket:
            socket.receive_json()
            assert only_face(recognize(socket, 1, jpeg(selfie())))["display_name"] == NAME
