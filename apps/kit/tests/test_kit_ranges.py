"""Lectura por rangos: contra un servidor HTTP local que entiende `Range` y mantiene la
conexión abierta, y un `.zip` servido desde memoria."""

from __future__ import annotations

import io
import threading
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from appkit.download import DOWNLOAD_ATTEMPTS, DownloadError
from appkit.ranges import HttpRangeSource, RangeReader

CONTENT = bytes(range(256)) * 40
LOCALHOST = "127.0.0.1"
POLL_SECONDS = 0.01
KEEP_ALIVE_PROTOCOL = "HTTP/1.1"
MEMBERS = {
    "fma_small/README.txt": b"FMA",
    "fma_small/000/000002.mp3": b"ID3" + bytes(range(256)) * 30,
    "fma_small/000/000005.mp3": b"ID3" + bytes(reversed(range(256))) * 20,
    "fma_small/155/155066.mp3": b"ID3 final",
}
# Lo que se pide además del contenido de un miembro para traer también su cabecera local.
HEADER_ALLOWANCE = 1024


@dataclass
class Server:
    """Qué pasó en el servidor: conexiones abiertas, rutas pedidas y cuántas peticiones
    más se cortan sin respuesta."""

    connections: int = 0
    targets: list[str] = field(default_factory=list)
    drops: int = 0


def handler_for(server: Server) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = KEEP_ALIVE_PROTOCOL

        def setup(self) -> None:
            super().setup()
            server.connections += 1

        def do_HEAD(self) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Length", str(len(CONTENT)))
            self.end_headers()

        def do_GET(self) -> None:
            if server.drops:
                server.drops -= 1
                self.close_connection = True
                return
            server.targets.append(self.path)
            first, _, last = self.headers["Range"].removeprefix("bytes=").partition("-")
            body = CONTENT[int(first) : int(last) + 1]
            self.send_response(HTTPStatus.PARTIAL_CONTENT)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_: object) -> None:
            return

    return Handler


@pytest.fixture
def server() -> Server:
    return Server()


@pytest.fixture
def url(server: Server) -> Iterator[str]:
    http_server = ThreadingHTTPServer((LOCALHOST, 0), handler_for(server))
    thread = threading.Thread(target=http_server.serve_forever, args=(POLL_SECONDS,), daemon=True)
    thread.start()
    host, port = http_server.server_address[:2]
    try:
        yield f"http://{host!s}:{port}/archivo"
    finally:
        http_server.shutdown()
        http_server.server_close()


class MemorySource:
    """Un archivo en memoria que se lee por rangos y cuenta las peticiones."""

    def __init__(self, content: bytes) -> None:
        self.content = content
        self.fetches = 0

    @property
    def size(self) -> int:
        return len(self.content)

    def fetch(self, start: int, stop: int) -> bytes:
        self.fetches += 1
        return self.content[start:stop]


def zip_with_members(members: dict[str, bytes]) -> bytes:
    """Un `.zip` con los miembros dados, alternando sin comprimir y bzip2 (el del FMA)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for index, (name, data) in enumerate(members.items()):
            method = zipfile.ZIP_BZIP2 if index % 2 else zipfile.ZIP_STORED
            archive.writestr(name, data, compress_type=method)
    return buffer.getvalue()


def test_ranges_reuse_one_open_connection(url: str, server: Server):
    with HttpRangeSource(f"{url}?v=1") as source:
        assert source.size == len(CONTENT)
        assert source.fetch(10, 20) == CONTENT[10:20]
        assert source.fetch(0, 3) == CONTENT[:3]
    assert server.targets == ["/archivo?v=1", "/archivo?v=1"]
    head_and_ranges = 2
    assert server.connections == head_and_ranges


def test_a_dropped_connection_is_reopened(url: str, server: Server):
    with HttpRangeSource(url) as source:
        server.drops = 2
        assert source.fetch(5, 9) == CONTENT[5:9]
    assert server.targets == ["/archivo"]


def test_a_range_that_never_arrives_fails(url: str, server: Server):
    with HttpRangeSource(url) as source:
        server.drops = DOWNLOAD_ATTEMPTS
        with pytest.raises(DownloadError, match="intentos"):
            source.fetch(0, 10)


def test_an_http_range_must_arrive_whole(url: str):
    with HttpRangeSource(url) as source, pytest.raises(DownloadError, match="llegaron 5"):
        source.fetch(len(CONTENT) - 5, len(CONTENT) + 5)


def test_a_zip_is_read_member_by_member_through_ranges():
    source = MemorySource(zip_with_members(MEMBERS))
    with zipfile.ZipFile(RangeReader(source)) as archive:
        assert archive.namelist() == list(MEMBERS)
        assert archive.read("fma_small/000/000005.mp3") == MEMBERS["fma_small/000/000005.mp3"]
    assert source.fetches < len(source.content)


def test_a_prefetched_member_is_read_with_a_single_request():
    source = MemorySource(zip_with_members(MEMBERS))
    reader = RangeReader(source)
    with zipfile.ZipFile(reader) as archive:
        for info in archive.infolist():
            before = source.fetches
            reader.prefetch(
                info.header_offset, info.header_offset + HEADER_ALLOWANCE + info.compress_size
            )
            assert archive.read(info) == MEMBERS[info.filename]
            assert source.fetches == before + 1


def test_reading_past_the_end_gives_nothing():
    reader = RangeReader(MemorySource(b"abc"))
    reader.seek(0, io.SEEK_END)
    assert reader.read(10) == b""
    reader.seek(1)
    assert reader.read(10) == b"bc"
