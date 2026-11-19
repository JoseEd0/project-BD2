"""Las descargas de los datasets, sin tocar la red.

El servidor se sustituye por un `urlopen` falso que entiende rangos. La lectura de un `.zip`
remoto por rangos se prueba con el kit (`apps/kit/tests/test_kit_ranges.py`).
"""

import io
from http import HTTPStatus
from pathlib import Path
from typing import Any

import pytest

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


def test_a_saved_file_creates_its_folder_and_leaves_nothing_staged(tmp_path: Path):
    destination = tmp_path / "imagenes" / "c8" / "c8fa4944.jpg"
    remote.save_file(destination, b"JPEG")
    assert destination.read_bytes() == b"JPEG"
    assert [path.name for path in destination.parent.iterdir()] == ["c8fa4944.jpg"]
