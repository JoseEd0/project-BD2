"""El archivo de una consulta multimedia, tal como se sube al gestor."""

from __future__ import annotations

import mimetypes
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar

FALLBACK_CONTENT_TYPE = "application/octet-stream"
# Lo que describe el archivo o el trozo que llega, y lo que un reproductor necesita para
# saltar: se reenvía tal cual, sin las cabeceras de la conexión con el gestor.
REPRESENTATION_HEADERS = (
    "Content-Type",
    "Content-Length",
    "Content-Range",
    "Accept-Ranges",
    "ETag",
    "Last-Modified",
    "Cache-Control",
)

Chunks = TypeVar("Chunks")


@dataclass(frozen=True, slots=True)
class MediaFile:
    """Una imagen o un audio en memoria. El gestor reconoce el tipo por la extensión de
    `name`; el tamaño lo limita su `max_upload_bytes`, así que tenerlo entero no pesa."""

    name: str
    content: bytes
    content_type: str = FALLBACK_CONTENT_TYPE

    @classmethod
    def from_path(cls, path: Path) -> MediaFile:
        """Lee el archivo en `path`.

        Raises:
            OSError: si no se puede leer.
        """
        content_type = mimetypes.guess_type(path.name)[0] or FALLBACK_CONTENT_TYPE
        return cls(path.name, path.read_bytes(), content_type)


def as_media_file(file: MediaFile | Path) -> MediaFile:
    return file if isinstance(file, MediaFile) else MediaFile.from_path(file)


@dataclass(frozen=True, slots=True)
class FileStream(Generic[Chunks]):
    """Un archivo del gestor que llega por trozos (`GET /media/file`).

    `status_code` es `206` si se pidió un rango y `200` si llega entero; `headers`, las de
    `REPRESENTATION_HEADERS` que trajo la respuesta, para reenviarlas.
    """

    status_code: int
    headers: Mapping[str, str]
    chunks: Chunks


def representation_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {name: headers[name] for name in REPRESENTATION_HEADERS if name in headers}
