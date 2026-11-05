"""Las descargas de los datasets, sin tocar la red.

El servidor se sustituye por un `urlopen` falso que entiende rangos, y el `.zip` remoto por
uno armado aquí mismo que se sirve desde memoria.
"""

import io
import zipfile
from http import HTTPStatus
from pathlib import Path
from typing import Any, ClassVar

import pytest
from tests.benchmarks.remote_support import MemorySource, zip_with_members

from benchmarks import remote

CONTENT = bytes(range(256)) * 40


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, status: int, size: int, url: str = "") -> None:
        super().__init__(body)
        self.status = status
        self.url = url
        self.headers = {"Content-Length": str(size)}


class FakeServer:
    """Sirve `content` y entiende `Range: bytes=a-` y `bytes=a-b`. Si `cut_after` tiene
    valor, la primera descarga se corta tras entregar esos bytes."""

    def __init__(self, content: bytes, cut_after: int | None = None) -> None:
        self.content = content
        self.cut_after = cut_after
        self.requests: list[str | None] = []

    def urlopen(self, request: Any, timeout: float) -> FakeResponse:
        if request.get_method() == "HEAD":
            return FakeResponse(b"", HTTPStatus.OK, len(self.content), request.full_url)
        requested = request.get_header("Range")
        self.requests.append(requested)
        if requested is None:
            body, status = self.content, HTTPStatus.OK
        else:
            first, _, last = requested.removeprefix("bytes=").partition("-")
            stop = int(last) + 1 if last else len(self.content)
            body, status = self.content[int(first) : stop], HTTPStatus.PARTIAL_CONTENT
        if self.cut_after is not None:
            body, self.cut_after = body[: self.cut_after], None
            return CutResponse(body, status, len(self.content))
        return FakeResponse(body, status, len(self.content))


class CutResponse(FakeResponse):
    """Entrega lo que tiene y luego la conexión se cae."""

    def read(self, size: int | None = -1) -> bytes:
        chunk = super().read(size)
        if not chunk:
            raise ConnectionResetError("conexión cortada")
        return chunk


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> FakeServer:
    fake = FakeServer(CONTENT)
    monkeypatch.setattr(remote.urllib.request, "urlopen", fake.urlopen)
    return fake


def test_the_download_is_reused_when_it_is_complete(tmp_path: Path):
    source = tmp_path / "remoto.parquet"
    source.write_bytes(b"PAR1 datos")
    destination = tmp_path / "cache" / "local.parquet"
    assert remote.download(source.as_uri(), destination) == destination
    assert destination.read_bytes() == b"PAR1 datos"
    assert not (tmp_path / "cache" / "local.parquet.parcial").exists()
    destination.write_bytes(b"PAR1 otro!")
    remote.download(source.as_uri(), destination)
    assert destination.read_bytes() == b"PAR1 otro!"
    destination.write_bytes(b"corto")
    remote.download(source.as_uri(), destination)
    assert destination.read_bytes() == b"PAR1 datos"


def test_a_partial_download_continues_from_its_last_byte(tmp_path: Path, server: FakeServer):
    destination = tmp_path / "archivo.tar"
    partial = tmp_path / "archivo.tar.parcial"
    partial.write_bytes(CONTENT[:1000])
    remote.download("https://datos.example/archivo", destination)
    assert destination.read_bytes() == CONTENT
    assert server.requests == ["bytes=1000-"]
    assert not partial.exists()


def test_a_finished_partial_is_only_renamed(tmp_path: Path, server: FakeServer):
    (tmp_path / "archivo.tar.parcial").write_bytes(CONTENT)
    remote.download("https://datos.example/archivo", tmp_path / "archivo.tar")
    assert (tmp_path / "archivo.tar").read_bytes() == CONTENT
    assert server.requests == []


def test_a_cut_connection_is_retried_from_where_it_stopped(tmp_path: Path, server: FakeServer):
    server.cut_after = 3000
    remote.download("https://datos.example/archivo", tmp_path / "archivo.tar")
    assert (tmp_path / "archivo.tar").read_bytes() == CONTENT
    assert server.requests == [None, "bytes=3000-"]


def test_a_partial_longer_than_the_remote_starts_over(tmp_path: Path, server: FakeServer):
    (tmp_path / "archivo.tar.parcial").write_bytes(CONTENT + b"sobra")
    remote.download("https://datos.example/archivo", tmp_path / "archivo.tar")
    assert (tmp_path / "archivo.tar").read_bytes() == CONTENT
    assert server.requests == [None]


def test_a_download_that_never_completes_fails(
    tmp_path: Path, server: FakeServer, monkeypatch: pytest.MonkeyPatch
):
    def always_cut(request: Any, timeout: float) -> FakeResponse:
        if request.get_method() == "HEAD":
            return FakeResponse(b"", HTTPStatus.OK, len(CONTENT))
        raise ConnectionResetError("sin red")

    monkeypatch.setattr(remote.urllib.request, "urlopen", always_cut)
    with pytest.raises(remote.DownloadError):
        remote.download("https://datos.example/archivo", tmp_path / "archivo.tar")


class FakeConnection:
    """Una conexión HTTP que sirve el contenido del servidor falso. `drops` cuenta cuántas
    peticiones más se van a cortar antes de responder."""

    opened: ClassVar[list["FakeConnection"]] = []
    drops = 0

    def __init__(self, host: str, timeout: float) -> None:
        self.host = host
        self.targets: list[str] = []
        self.closed = False
        self._pending: FakeResponse | None = None
        FakeConnection.opened.append(self)

    def request(self, method: str, target: str, headers: dict[str, str]) -> None:
        if FakeConnection.drops:
            FakeConnection.drops -= 1
            raise ConnectionResetError("el servidor cerró la conexión")
        self.targets.append(target)
        first, _, last = headers["Range"].removeprefix("bytes=").partition("-")
        body = CONTENT[int(first) : int(last) + 1]
        self._pending = FakeResponse(body, HTTPStatus.PARTIAL_CONTENT, len(CONTENT))

    def getresponse(self) -> FakeResponse:
        assert self._pending is not None
        return self._pending

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def connections(server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> type[FakeConnection]:
    FakeConnection.opened = []
    FakeConnection.drops = 0
    monkeypatch.setattr(remote.http.client, "HTTPSConnection", FakeConnection)
    return FakeConnection


def test_ranges_reuse_one_open_connection(connections: type[FakeConnection]):
    source = remote.HttpRangeSource("https://datos.example/archivo?v=1")
    assert source.size == len(CONTENT)
    assert source.fetch(10, 20) == CONTENT[10:20]
    assert source.fetch(0, 3) == CONTENT[:3]
    [connection] = connections.opened
    assert connection.host == "datos.example"
    assert connection.targets == ["/archivo?v=1", "/archivo?v=1"]


def test_a_dropped_connection_is_reopened(connections: type[FakeConnection]):
    source = remote.HttpRangeSource("https://datos.example/archivo")
    connections.drops = 2
    assert source.fetch(5, 9) == CONTENT[5:9]
    assert len(connections.opened) == 3
    assert [connection.closed for connection in connections.opened] == [True, True, False]


def test_a_range_that_never_arrives_fails(connections: type[FakeConnection]):
    source = remote.HttpRangeSource("https://datos.example/archivo")
    connections.drops = remote.DOWNLOAD_ATTEMPTS
    with pytest.raises(remote.DownloadError):
        source.fetch(0, 10)


def test_an_http_range_must_arrive_whole(connections: type[FakeConnection]):
    source = remote.HttpRangeSource("https://datos.example/archivo")
    with pytest.raises(remote.DownloadError):
        source.fetch(len(CONTENT) - 5, len(CONTENT) + 5)


MEMBERS = {
    "fma_small/README.txt": b"FMA",
    "fma_small/000/000002.mp3": b"ID3" + bytes(range(256)) * 30,
    "fma_small/000/000005.mp3": b"ID3" + bytes(reversed(range(256))) * 20,
    "fma_small/155/155066.mp3": b"ID3 final",
}


def test_a_zip_is_read_member_by_member_through_ranges():
    source = MemorySource(zip_with_members(MEMBERS))
    with zipfile.ZipFile(remote.RemoteFile(source)) as archive:
        assert archive.namelist() == list(MEMBERS)
        assert archive.read("fma_small/000/000005.mp3") == MEMBERS["fma_small/000/000005.mp3"]
    assert source.fetches < len(source.content)


def test_a_prefetched_member_is_read_with_a_single_request():
    source = MemorySource(zip_with_members(MEMBERS))
    file = remote.RemoteFile(source)
    with zipfile.ZipFile(file) as archive:
        for info in archive.infolist():
            before = source.fetches
            file.prefetch(info.header_offset, info.header_offset + 1024 + info.compress_size)
            assert archive.read(info) == MEMBERS[info.filename]
            assert source.fetches == before + 1


def test_reading_past_the_end_gives_nothing():
    file = remote.RemoteFile(MemorySource(b"abc"))
    file.seek(0, io.SEEK_END)
    assert file.read(10) == b""
    file.seek(1)
    assert file.read(10) == b"bc"


def test_a_saved_file_creates_its_folder_and_leaves_nothing_staged(tmp_path: Path):
    destination = tmp_path / "imagenes" / "c8" / "c8fa4944.jpg"
    remote.save_file(destination, b"JPEG")
    assert destination.read_bytes() == b"JPEG"
    assert [path.name for path in destination.parent.iterdir()] == ["c8fa4944.jpg"]
