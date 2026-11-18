"""Las peticiones de las fotos: qué se reintenta y cuánto se espera."""

import pytest
from apps.shop.tests.shop_support import FakeTransport

from shop.dataset.errors import HttpStatusError
from shop.dataset.transport import fetch_bytes, is_retryable

URL = "https://cdn/photo.jpg"
CONTENT = b"\xff\xd8 not really a jpeg"


@pytest.mark.parametrize(
    ("status", "retryable"), [(404, False), (403, False), (429, True), (500, True), (503, True)]
)
def test_which_statuses_are_retried(status: int, retryable: bool) -> None:
    assert is_retryable(HttpStatusError(URL, status)) is retryable


def test_a_cut_connection_is_retried() -> None:
    assert is_retryable(ConnectionResetError())
    assert not is_retryable(ValueError())


def test_fetch_bytes_waits_longer_after_each_retryable_failure(transport: FakeTransport) -> None:
    waits: list[float] = []
    transport.serve(URL, 500, 503, CONTENT)
    assert fetch_bytes(URL, transport, attempts=3, backoff_seconds=1.0, sleep=waits.append) == (
        CONTENT
    )
    assert waits == [1.0, 2.0]


def test_fetch_bytes_gives_up_after_its_attempts(transport: FakeTransport) -> None:
    transport.serve(URL, 503)
    with pytest.raises(HttpStatusError):
        fetch_bytes(URL, transport, attempts=2, backoff_seconds=0.0, sleep=lambda _: None)
    assert len(transport.requests) == 2


def test_a_missing_photo_is_not_retried(transport: FakeTransport) -> None:
    transport.serve(URL, 404)
    with pytest.raises(HttpStatusError):
        fetch_bytes(URL, transport, attempts=4, backoff_seconds=0.0, sleep=lambda _: None)
    assert transport.requests == [URL]
