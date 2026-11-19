"""Archivos remotos que se leen por rangos HTTP, sin bajarlos enteros.

Los datasets pesan gigabytes y sus servidores aceptan pedir un trozo (`Range`). Con eso, de
un `.zip` remoto se leen solo el índice del final y los miembros que hacen falta: el FMA
(7,7 GB) del servicio de copyright, o el shapefile de OpenStreetMap de la Parte 2.
"""

from __future__ import annotations

import http.client
import io
import urllib.parse
import urllib.request
from http import HTTPStatus
from typing import Any, Protocol

from appkit.download import (
    DOWNLOAD_ATTEMPTS,
    TIMEOUT_SECONDS,
    TRANSIENT_ERRORS,
    USER_AGENT,
    DownloadError,
)

HTTPS = "https"


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


class RangeReader(io.RawIOBase):
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


def _head(url: str) -> tuple[str, int]:
    """La dirección final del archivo, tras las redirecciones, y sus bytes."""
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.url, int(response.headers["Content-Length"])
