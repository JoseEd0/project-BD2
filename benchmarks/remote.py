"""Descargas por HTTP de los datasets: archivos enteros que se reanudan si se cortan.

Los datasets pesan gigabytes y los servidores aceptan pedir un trozo (`Range`): una descarga
cortada sigue desde donde quedó. Leer solo unos miembros de un `.zip` remoto, sin bajarlo,
es `appkit.ranges`, que comparte con los servicios de la Parte 5.
"""

from __future__ import annotations

import contextlib
import http.client
import urllib.error
import urllib.request
from http import HTTPStatus
from pathlib import Path

from errors import DomainError
from storage.atomic import write_atomically

USER_AGENT = "minigestor-bd2 (proyecto universitario)"
TIMEOUT_SECONDS = 180
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
PARTIAL_SUFFIX = ".parcial"
# Intentos de una descarga antes de rendirse. Cada uno sigue desde lo ya escrito, así que
# un corte de la conexión a mitad de varios gigabytes solo repite el trozo en curso.
DOWNLOAD_ATTEMPTS = 5
TRANSIENT_ERRORS = (urllib.error.URLError, http.client.HTTPException, ConnectionError, TimeoutError)


class DownloadError(DomainError):
    """El servidor no entregó lo que se le pidió."""


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
