"""Descargas por HTTP de los datasets: archivos enteros que se reanudan y archivos remotos
que se leen por rangos.

Los datasets pesan gigabytes y los servidores aceptan pedir un trozo (`Range`). Eso da dos
cosas: una descarga cortada sigue desde donde quedó, y de un `.zip` remoto se leen solo
el índice del final y los archivos que hacen falta, sin bajarlo entero.
"""

from __future__ import annotations

import contextlib
import http.client
import io
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from pathlib import Path
from typing import Any, Protocol

from errors import DomainError
from storage.atomic import write_atomically

USER_AGENT = "minigestor-bd2 (proyecto universitario)"
TIMEOUT_SECONDS = 180
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
PARTIAL_SUFFIX = ".parcial"
HTTPS = "https"
# Intentos de una descarga antes de rendirse. Cada uno sigue desde lo ya escrito, así que
# un corte de la conexión a mitad de varios gigabytes solo repite el trozo en curso.
DOWNLOAD_ATTEMPTS = 5
TRANSIENT_ERRORS = (urllib.error.URLError, http.client.HTTPException, ConnectionError, TimeoutError)


class DownloadError(DomainError):
    """El servidor no entregó lo que se le pidió."""


class RangeSource(Protocol):
    """Algo de lo que se pueden pedir bytes por posición: un archivo remoto o, en los
    tests, uno en memoria."""

    @property
    def size(self) -> int: ...

    def fetch(self, start: int, stop: int) -> bytes:
        """Los bytes de `start` (incluido) a `stop` (excluido)."""
        ...


class HttpRangeSource:
    """Un archivo remoto que se pide por rangos HTTP sobre una conexión que se mantiene
    abierta.

    Abrir una conexión TLS por petición cuesta más que traer el rango: contra el servidor
    del FMA, un megabyte tarda 3,3 s con conexión nueva y 1,1 s reusándola. Un objeto no se
    comparte entre hilos; cada hilo abre el suyo.
    """

    def __init__(self, url: str) -> None:
        self._url, self._size = _head(url)
        self._connection: http.client.HTTPConnection | None = None

    def __enter__(self) -> HttpRangeSource:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def size(self) -> int:
        return self._size

    def fetch(self, start: int, stop: int) -> bytes:
        """Los bytes de `[start, stop)`. Si la conexión se cae, la reabre y lo reintenta
        hasta `DOWNLOAD_ATTEMPTS` veces.

        Raises:
            DownloadError: si el servidor no devuelve exactamente ese rango.
        """
        for _ in range(DOWNLOAD_ATTEMPTS):
            try:
                return self._request(start, stop)
            except TRANSIENT_ERRORS:
                self.close()
        raise DownloadError(f"no se pudo leer {self._url} tras {DOWNLOAD_ATTEMPTS} intentos")

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _request(self, start: int, stop: int) -> bytes:
        parts = urllib.parse.urlsplit(self._url)
        if self._connection is None:
            connection_class = (
                http.client.HTTPSConnection if parts.scheme == HTTPS else http.client.HTTPConnection
            )
            self._connection = connection_class(parts.netloc, timeout=TIMEOUT_SECONDS)
        target = parts.path + (f"?{parts.query}" if parts.query else "")
        headers = {"Range": f"bytes={start}-{stop - 1}", "User-Agent": USER_AGENT}
        self._connection.request("GET", target, headers=headers)
        response = self._connection.getresponse()
        received = response.read()
        if response.status != HTTPStatus.PARTIAL_CONTENT or len(received) != stop - start:
            raise DownloadError(
                f"se pidieron {stop - start} bytes de {self._url} y llegaron {len(received)} "
                f"(estado {response.status})"
            )
        return received


class RemoteFile(io.RawIOBase):
    """Archivo de solo lectura sobre un `RangeSource`: cada lectura pide solo sus bytes.

    Es lo que permite abrir con `zipfile` un `.zip` de gigabytes y sacar de él unos pocos
    archivos: `zipfile` lee el índice del final y después solo los miembros pedidos.
    `prefetch` trae de una vez un tramo que se va a leer en pedazos —la cabecera y el
    contenido de un miembro—, para pagar una sola petición en vez de una por pedazo.
    """

    def __init__(self, source: RangeSource) -> None:
        self._source = source
        self._position = 0
        self._window_start = 0
        self._window = b""

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        origin = {io.SEEK_SET: 0, io.SEEK_CUR: self._position, io.SEEK_END: self._source.size}
        self._position = origin[whence] + offset
        return self._position

    def prefetch(self, start: int, stop: int) -> None:
        """Trae `[start, stop)` en una sola petición; las lecturas dentro de ese tramo ya
        no van a la red."""
        stop = min(stop, self._source.size)
        self._window_start = start
        self._window = self._source.fetch(start, stop) if start < stop else b""

    def readinto(self, buffer: Any, /) -> int:
        if self._position >= self._source.size:
            return 0
        wanted = min(len(buffer), self._source.size - self._position)
        offset = self._position - self._window_start
        if offset >= 0 and offset + wanted <= len(self._window):
            received = self._window[offset : offset + wanted]
        else:
            received = self._source.fetch(self._position, self._position + wanted)
        buffer[: len(received)] = received
        self._position += len(received)
        return len(received)


def remote_size(url: str) -> int:
    """Bytes del archivo remoto, según su `Content-Length`."""
    return _head(url)[1]


def _head(url: str) -> tuple[str, int]:
    """La dirección final del archivo, tras las redirecciones, y sus bytes."""
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.url, int(response.headers["Content-Length"])


def download(url: str, destination: Path) -> Path:
    """Baja `url` a `destination`, salvo que ya esté completo (mismo tamaño que el remoto).

    Se escribe primero a un archivo `.parcial`, que un corte deja a medias en vez de dejar
    uno con el nombre final. Si ese `.parcial` existe, la descarga sigue desde su último
    byte, y si la conexión se corta se reintenta desde ahí hasta `DOWNLOAD_ATTEMPTS` veces.

    Raises:
        DownloadError: si tras los reintentos el archivo no tiene el tamaño del remoto.
    """
    expected = remote_size(url)
    if destination.exists() and destination.stat().st_size == expected:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + PARTIAL_SUFFIX)
    for _ in range(DOWNLOAD_ATTEMPTS):
        if _bytes_in(partial) == expected:
            break
        with contextlib.suppress(*TRANSIENT_ERRORS):
            _continue_download(url, partial, expected)
    if _bytes_in(partial) != expected:
        raise DownloadError(f"descarga incompleta de {url} tras {DOWNLOAD_ATTEMPTS} intentos")
    return partial.replace(destination)


def save_file(destination: Path, data: bytes) -> None:
    """Guarda `data` en `destination`, creando su carpeta. Lo reemplaza de una vez
    (`storage.atomic`): un corte no deja con el nombre final un archivo a medias, así que
    basta con que exista para darlo por bueno."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_atomically(destination, lambda file: file.write(data))


def _bytes_in(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def _continue_download(url: str, partial: Path, expected: int) -> None:
    """Escribe en `partial` lo que le falta. Si el servidor ignora el rango y manda el
    archivo entero, lo reescribe desde el principio."""
    written = _bytes_in(partial)
    if written > expected:
        written = 0
    headers = {"User-Agent": USER_AGENT}
    if written:
        headers["Range"] = f"bytes={written}-"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        resumed = written > 0 and getattr(response, "status", None) == HTTPStatus.PARTIAL_CONTENT
        with partial.open("ab" if resumed else "wb") as handle:
            while chunk := response.read(DOWNLOAD_CHUNK_BYTES):
                handle.write(chunk)
