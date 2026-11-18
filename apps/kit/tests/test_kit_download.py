"""Descargas verificadas contra un servidor HTTP local que entiende `Range`."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from appkit.download import (
    PARTIAL_SUFFIX,
    ChecksumMismatchError,
    DownloadError,
    RemoteFile,
    fetch_verified,
    sha256_of,
)

PAYLOAD = bytes(range(256)) * 1000
HALF = len(PAYLOAD) // 2
FILE_PATH = "/modelo.onnx"
LOCALHOST = "127.0.0.1"
# Lo que tarda `shutdown` en ver que el servidor debe parar; el de omisión (0,5 s) se paga
# en cada test.
POLL_SECONDS = 0.01


@dataclass
class Behaviour:
    """Lo que hace el servidor: qué sirve, si atiende `Range` y cuántas respuestas corta."""

    payload: bytes = PAYLOAD
    honours_range: bool = True
    responses_to_cut: int = 0
    ranges: list[str | None] = field(default_factory=list)


def handler_for(behaviour: Behaviour) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requested = self.headers.get("Range")
            behaviour.ranges.append(requested)
            start = int(requested.removeprefix("bytes=").rstrip("-")) if requested else 0
            if not behaviour.honours_range:
                start = 0
            body = behaviour.payload[start:]
            self.send_response(HTTPStatus.PARTIAL_CONTENT if start else HTTPStatus.OK)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if behaviour.responses_to_cut:
                behaviour.responses_to_cut -= 1
                self.wfile.write(body[: len(body) // 2])
                self.close_connection = True
                return
            self.wfile.write(body)

        def log_message(self, *_: object) -> None:
            return

    return Handler


@pytest.fixture
def behaviour() -> Behaviour:
    return Behaviour()


@pytest.fixture
def remote(behaviour: Behaviour) -> Iterator[RemoteFile]:
    server = ThreadingHTTPServer((LOCALHOST, 0), handler_for(behaviour))
    thread = threading.Thread(target=server.serve_forever, args=(POLL_SECONDS,), daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield RemoteFile(
            url=f"http://{host!s}:{port}{FILE_PATH}",
            sha256=hashlib.sha256(PAYLOAD).hexdigest(),
            size=len(PAYLOAD),
        )
    finally:
        server.shutdown()
        server.server_close()


def partial_of(destination: Path) -> Path:
    return destination.with_name(destination.name + PARTIAL_SUFFIX)


def test_a_download_lands_complete_and_verified(remote: RemoteFile, tmp_path: Path):
    destination = tmp_path / "modelos" / "modelo.onnx"

    assert fetch_verified(remote, destination) == destination
    assert destination.read_bytes() == PAYLOAD
    assert not partial_of(destination).exists()


def test_a_verified_file_is_not_downloaded_again(
    remote: RemoteFile, behaviour: Behaviour, tmp_path: Path
):
    destination = tmp_path / "modelo.onnx"
    destination.write_bytes(PAYLOAD)

    fetch_verified(remote, destination)

    assert behaviour.ranges == []


def test_a_file_with_another_checksum_is_replaced(remote: RemoteFile, tmp_path: Path):
    destination = tmp_path / "modelo.onnx"
    destination.write_bytes(b"otra version")

    fetch_verified(remote, destination)

    assert destination.read_bytes() == PAYLOAD


def test_a_partial_download_resumes_from_its_last_byte(
    remote: RemoteFile, behaviour: Behaviour, tmp_path: Path
):
    destination = tmp_path / "modelo.onnx"
    partial_of(destination).write_bytes(PAYLOAD[:HALF])

    fetch_verified(remote, destination)

    assert behaviour.ranges == [f"bytes={HALF}-"]
    assert destination.read_bytes() == PAYLOAD


def test_a_server_that_ignores_the_range_restarts_the_file(
    remote: RemoteFile, behaviour: Behaviour, tmp_path: Path
):
    behaviour.honours_range = False
    destination = tmp_path / "modelo.onnx"
    partial_of(destination).write_bytes(PAYLOAD[:HALF])

    fetch_verified(remote, destination)

    assert destination.read_bytes() == PAYLOAD


def test_a_cut_connection_is_retried_from_where_it_stopped(
    remote: RemoteFile, behaviour: Behaviour, tmp_path: Path
):
    behaviour.responses_to_cut = 1
    destination = tmp_path / "modelo.onnx"

    fetch_verified(remote, destination)

    assert behaviour.ranges == [None, f"bytes={HALF}-"]
    assert destination.read_bytes() == PAYLOAD


def test_a_different_file_is_rejected_and_never_gets_the_final_name(
    remote: RemoteFile, behaviour: Behaviour, tmp_path: Path
):
    behaviour.payload = bytes(reversed(PAYLOAD))
    destination = tmp_path / "modelo.onnx"

    with pytest.raises(ChecksumMismatchError):
        fetch_verified(remote, destination)

    assert not destination.exists()
    assert not partial_of(destination).exists()


def test_a_server_that_never_completes_the_file_fails(
    remote: RemoteFile, behaviour: Behaviour, tmp_path: Path
):
    behaviour.payload = PAYLOAD[:HALF]

    with pytest.raises(DownloadError, match="llegaron"):
        fetch_verified(remote, tmp_path / "modelo.onnx")


def test_the_checksum_is_the_hexadecimal_sha256(tmp_path: Path):
    path = tmp_path / "datos.bin"
    path.write_bytes(PAYLOAD)

    assert sha256_of(path) == hashlib.sha256(PAYLOAD).hexdigest()
