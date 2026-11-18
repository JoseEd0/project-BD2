"""Reintentos: solo sin respuesta, solo en lo que no escribe y con el mismo `X-Request-ID`."""

from __future__ import annotations

import httpx
import pytest
from tests.client.conftest import IMMEDIATE_RETRIES
from tests.client.engine_server import unused_port_url

from minigestor_client import (
    REQUEST_ID_HEADER,
    Client,
    MediaFile,
    NotFoundError,
    RetryPolicy,
    TransportError,
)


class FlakyTransport(httpx.BaseTransport):
    """La red real, salvo que las primeras `failures` conexiones se rechazan."""

    def __init__(self, failures: int) -> None:
        self._network = httpx.HTTPTransport()
        self._failures = failures
        self.request_ids: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.request_ids.append(request.headers[REQUEST_ID_HEADER])
        if len(self.request_ids) <= self._failures:
            raise httpx.ConnectError("conexión rechazada", request=request)
        return self._network.handle_request(request)

    def close(self) -> None:
        self._network.close()


def flaky_client(url: str, failures: int) -> tuple[Client, FlakyTransport]:
    transport = FlakyTransport(failures)
    return Client(url, retry=IMMEDIATE_RETRIES, transport=transport), transport


@pytest.fixture
def table(client: Client) -> Client:
    client.execute("CREATE TABLE t (id INT PRIMARY KEY)")
    return client


def test_a_read_is_retried_until_it_gets_an_answer(table: Client, engine_url: str):
    flaky, transport = flaky_client(engine_url, failures=IMMEDIATE_RETRIES.attempts - 1)
    with flaky:
        assert flaky.fetch_all("SELECT * FROM t") == []
    assert len(transport.request_ids) == IMMEDIATE_RETRIES.attempts
    assert len(set(transport.request_ids)) == 1


def test_a_write_is_never_retried(table: Client, engine_url: str):
    flaky, transport = flaky_client(engine_url, failures=1)
    with flaky, pytest.raises(TransportError) as raised:
        flaky.execute("INSERT INTO t VALUES (1)")
    assert raised.value.attempts == 1
    assert len(transport.request_ids) == 1
    assert table.scalar("SELECT COUNT(*) FROM t") == 0


def test_the_caller_can_declare_a_write_idempotent(table: Client, engine_url: str):
    flaky, transport = flaky_client(engine_url, failures=1)
    with flaky:
        flaky.execute("CREATE TABLE IF NOT EXISTS t (id INT PRIMARY KEY)", idempotent=True)
    assert len(transport.request_ids) == 2


def test_the_caller_can_forbid_retrying_a_read(table: Client, engine_url: str):
    flaky, transport = flaky_client(engine_url, failures=1)
    with flaky, pytest.raises(TransportError):
        flaky.query("SELECT * FROM t", idempotent=False)
    assert len(transport.request_ids) == 1


def test_a_bulk_load_is_never_retried(table: Client, engine_url: str):
    flaky, transport = flaky_client(engine_url, failures=1)
    with flaky, pytest.raises(TransportError):
        flaky.bulk_insert("t", [{"id": 1}])
    assert len(transport.request_ids) == 1


def test_a_media_upload_is_retried(client: Client, engine_url: str):
    flaky, transport = flaky_client(engine_url, failures=1)
    with flaky:
        uploaded = flaky.upload_media(MediaFile("consulta.wav", b"RIFF"))
    assert uploaded.media == "audio"
    assert len(transport.request_ids) == 2


def test_an_answer_with_an_error_is_never_retried(client: Client, engine_url: str):
    flaky, transport = flaky_client(engine_url, failures=0)
    with flaky, pytest.raises(NotFoundError):
        flaky.query("SELECT * FROM fantasma")
    assert len(transport.request_ids) == 1


def test_an_engine_nobody_listens_to_fails_after_every_attempt():
    policy = RetryPolicy(attempts=2, base_delay_seconds=0.0)
    with Client(unused_port_url(), retry=policy) as lost, pytest.raises(TransportError) as raised:
        lost.tables()
    assert raised.value.attempts == 2
    assert raised.value.request_id
    assert isinstance(raised.value.__cause__, httpx.ConnectError)
