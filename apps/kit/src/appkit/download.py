"""Descargas verificadas: archivos con una suma SHA-256 conocida que se reanudan si se cortan.

Los modelos y los datasets de los servicios se bajan de URL fijadas junto con su SHA-256 y
su tamaño. Así una descarga cortada sigue desde su último byte (`Range`), y un archivo que
no es exactamente el esperado —otra versión, un espejo que cambió, una página de error—
nunca llega a tener el nombre final.
"""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import urllib.error
import urllib.request
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path

USER_AGENT = "minigestor-bd2"
TIMEOUT_SECONDS = 120
CHUNK_BYTES = 1024 * 1024
PARTIAL_SUFFIX = ".parcial"
# Cada intento sigue desde lo ya escrito: un corte a mitad de un archivo grande solo repite
# el trozo en curso.
DOWNLOAD_ATTEMPTS = 5
TRANSIENT_ERRORS = (urllib.error.URLError, http.client.HTTPException, ConnectionError, TimeoutError)


class DownloadError(Exception):
    """El servidor no entregó el archivo completo."""


class ChecksumMismatchError(DownloadError):
    """El archivo llegó completo, pero su SHA-256 no es el fijado."""


@dataclass(frozen=True, slots=True)
class RemoteFile:
    """Un archivo remoto fijado: dónde está, su SHA-256 en hexadecimal y sus bytes."""

    url: str
    sha256: str
    size: int


def fetch_verified(remote: RemoteFile, destination: Path) -> Path:
    """Baja `remote` a `destination` y comprueba su SHA-256 antes de darle el nombre final.

    Si `destination` ya existe con la suma correcta, no se baja nada; si existe con otra,
    se reemplaza. Mientras baja, el archivo se llama `<destination>.parcial` y, si ese
    archivo existe, la descarga sigue desde su último byte.

    Raises:
        DownloadError: si tras `DOWNLOAD_ATTEMPTS` intentos no llegaron `remote.size` bytes.
        ChecksumMismatchError: si llegaron, pero con otra suma; el parcial se borra.
    """
    if destination.is_file() and sha256_of(destination) == remote.sha256:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + PARTIAL_SUFFIX)
    for _ in range(DOWNLOAD_ATTEMPTS):
        if _bytes_in(partial) == remote.size:
            break
        with contextlib.suppress(*TRANSIENT_ERRORS):
            _continue_download(remote, partial)
    if _bytes_in(partial) != remote.size:
        raise DownloadError(
            f"de {remote.url} llegaron {_bytes_in(partial)} bytes de los {remote.size} "
            f"esperados tras {DOWNLOAD_ATTEMPTS} intentos"
        )
    received = sha256_of(partial)
    if received != remote.sha256:
        partial.unlink()
        raise ChecksumMismatchError(
            f"{remote.url} tiene SHA-256 {received} y se esperaba {remote.sha256}"
        )
    return partial.replace(destination)


def sha256_of(path: Path) -> str:
    """El SHA-256 de `path` en hexadecimal, leído por trozos."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _bytes_in(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def _continue_download(remote: RemoteFile, partial: Path) -> None:
    """Escribe en `partial` lo que le falta. Si sobra (el remoto cambió) o el servidor
    ignora el rango y manda el archivo entero, se empieza de cero."""
    written = _bytes_in(partial)
    if written > remote.size:
        written = 0
    headers = {"User-Agent": USER_AGENT}
    if written:
        headers["Range"] = f"bytes={written}-"
    request = urllib.request.Request(remote.url, headers=headers)
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        resumed = written > 0 and response.status == HTTPStatus.PARTIAL_CONTENT
        with partial.open("ab" if resumed else "wb") as handle:
            while chunk := response.read(CHUNK_BYTES):
                handle.write(chunk)
