from __future__ import annotations

import email.message
import itertools
import urllib.error
import urllib.request

import pytest

from appkit.download import DownloadError
from rag.fetch import PoliteClient
from rag.settings import HttpSettings

DELAY = 3.0


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *details: object) -> None:
        return None


class FakeNetwork:
    """Un reloj y una red de mentira: cada petición tarda `latency` y responde, en orden, lo
    que haya en `outcomes` (bytes o una excepción)."""

    def __init__(self, outcomes: list[bytes | Exception], latency: float = 0.5) -> None:
        self.outcomes = outcomes
        self.latency = latency
        self.now = 0.0
        self.request_times: list[float] = []
        self.user_agents: list[str | None] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def open(self, request: urllib.request.Request, timeout: float) -> FakeResponse:
        self.request_times.append(self.now)
        self.user_agents.append(request.get_header("User-agent"))
        self.now += self.latency
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)

    def client(self, settings: HttpSettings | None = None) -> PoliteClient:
        return PoliteClient(
            "agent/1.0",
            DELAY,
            settings or HttpSettings(),
            clock=self.clock,
            sleep=self.sleep,
            opener=self.open,
        )


def http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://x.test", code, "error", headers, None)


def test_requests_are_spaced_by_the_delay_and_identify_the_client() -> None:
    network = FakeNetwork([b"a", b"b", b"c"])
    client = network.client()
    assert [client.get("https://x.test") for _ in range(3)] == [b"a", b"b", b"c"]
    gaps = [later - earlier for earlier, later in itertools.pairwise(network.request_times)]
    assert all(gap >= DELAY for gap in gaps)
    assert network.user_agents == ["agent/1.0"] * 3


def test_a_busy_server_is_retried_after_what_it_asks() -> None:
    network = FakeNetwork([http_error(503, "20"), b"ok"])
    assert network.client().get("https://x.test") == b"ok"
    assert network.request_times[1] - network.request_times[0] >= 20


def test_a_retry_after_beyond_the_cap_is_capped() -> None:
    network = FakeNetwork([http_error(429, "99999"), b"ok"])
    network.client(HttpSettings(max_retry_after_seconds=60)).get("https://x.test")
    assert network.request_times[1] - network.request_times[0] < 61


def test_a_missing_document_fails_at_once() -> None:
    network = FakeNetwork([http_error(404), b"never"])
    with pytest.raises(DownloadError, match="404"):
        network.client().get("https://x.test")
    assert len(network.request_times) == 1


def test_network_failures_are_retried_and_then_reported() -> None:
    failures: list[bytes | Exception] = [urllib.error.URLError("down") for _ in range(3)]
    network = FakeNetwork(failures)
    with pytest.raises(DownloadError, match="3 intentos"):
        network.client(HttpSettings(attempts=3)).get("https://x.test")
    assert len(network.request_times) == 3
